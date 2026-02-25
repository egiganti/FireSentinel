"""Fire event detail page -- full information for a single fire event.

Shows event metrics, hotspot timeline, intent signal breakdown, weather
context, and export options in a premium dark-themed layout.
All user-facing text in SPANISH.
"""

from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from html import escape as html_escape
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text
from streamlit_folium import st_folium

from firesentinel.dashboard.components.fire_map import create_event_detail_map
from firesentinel.dashboard.theme import (
    COLORS,
    SEVERITY_COLORS,
    SEVERITY_ICONS,
    SEVERITY_LABELS_ES,
    render_card_container,
    render_intent_badge,
    render_kpi_row,
    render_section_header,
    render_severity_badge,
    render_signal_breakdown,
)

# ---------------------------------------------------------------------------
# Translation maps
# ---------------------------------------------------------------------------

_ROAD_TYPES_SPANISH: dict[str, str] = {
    "highway": "autopista",
    "motorway": "autopista",
    "primary": "ruta primaria",
    "secondary": "ruta secundaria",
    "tertiary": "ruta terciaria",
    "track": "camino rural",
    "path": "sendero",
    "residential": "calle residencial",
    "trunk": "ruta troncal",
    "unclassified": "camino sin clasificar",
    "none": "sin camino cercano",
}

_DAYNIGHT_ES: dict[str, str] = {
    "D": "Dia",
    "N": "Noche",
}


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300)
def _get_fire_event_detail(_db_url: str, event_id: str) -> dict[str, Any] | None:
    """Fetch full details for a single fire event.

    Args:
        _db_url: Database URL string.
        event_id: UUID of the fire event.

    Returns:
        Dict with all event fields, or None if not found.
    """
    engine = create_engine(_db_url)
    result: dict[str, Any] | None = None

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT * FROM fire_events WHERE id = :event_id"),
            {"event_id": event_id},
        ).mappings().all()

        if rows:
            row = rows[0]

            # Parse JSON fields if stored as strings
            breakdown = row.get("intent_breakdown")
            if isinstance(breakdown, str):
                try:
                    breakdown = json.loads(breakdown)
                except (json.JSONDecodeError, TypeError):
                    breakdown = None

            weather = row.get("weather_data")
            if isinstance(weather, str):
                try:
                    weather = json.loads(weather)
                except (json.JSONDecodeError, TypeError):
                    weather = None

            result = {
                "id": row["id"],
                "center_lat": row["center_lat"],
                "center_lon": row["center_lon"],
                "province": row.get("province"),
                "nearest_town": row.get("nearest_town"),
                "severity": row.get("severity", "medium"),
                "hotspot_count": row.get("hotspot_count", 0),
                "max_frp": row.get("max_frp", 0.0),
                "first_detected_at": row.get("first_detected_at"),
                "last_updated_at": row.get("last_updated_at"),
                "intent_score": row.get("intent_score"),
                "intent_label": row.get("intent_label"),
                "intent_breakdown": breakdown,
                "weather_data": weather,
                "nearest_road_m": row.get("nearest_road_m"),
                "nearest_road_type": row.get("nearest_road_type"),
                "nearest_road_ref": row.get("nearest_road_ref"),
                "is_active": row.get("is_active"),
            }

    engine.dispose()
    return result


@st.cache_data(ttl=300)
def _get_event_hotspots(_db_url: str, event_id: str) -> list[dict[str, Any]]:
    """Fetch all hotspot detections for a fire event.

    Args:
        _db_url: Database URL string.
        event_id: UUID of the fire event.

    Returns:
        List of hotspot dicts sorted by detection time.
    """
    engine = create_engine(_db_url)
    results: list[dict[str, Any]] = []

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT h.latitude, h.longitude, h.brightness, h.frp, h.confidence, "
                "       h.acq_date, h.acq_time, h.satellite, h.daynight "
                "FROM hotspots h WHERE h.fire_event_id = :event_id "
                "ORDER BY h.acq_date, h.acq_time"
            ),
            {"event_id": event_id},
        ).mappings().all()

        for hs in rows:
            results.append({
                "latitude": hs["latitude"],
                "longitude": hs["longitude"],
                "acq_date": str(hs.get("acq_date", "")),
                "acq_time": str(hs.get("acq_time", "")),
                "satellite": hs.get("satellite", ""),
                "brightness": hs.get("brightness", 0),
                "frp": hs.get("frp", 0),
                "confidence": hs.get("confidence", ""),
                "daynight": hs.get("daynight", ""),
            })

    engine.dispose()
    return results


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _generate_csv(hotspots: list[dict[str, Any]]) -> str:
    """Generate CSV string from hotspot data (9 columns)."""
    output = io.StringIO()
    if not hotspots:
        return ""

    fieldnames = [
        "latitude",
        "longitude",
        "acq_date",
        "acq_time",
        "satellite",
        "brightness",
        "frp",
        "confidence",
        "daynight",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for hs in hotspots:
        writer.writerow(hs)

    return output.getvalue()


def _generate_kml(event: dict[str, Any], hotspots: list[dict[str, Any]]) -> str:
    """Generate KML XML string with Document containing Placemarks."""
    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    document = ET.SubElement(kml, "Document")

    town = event.get("nearest_town") or "Incendio"
    name = ET.SubElement(document, "name")
    name.text = f"FireSentinel - {town}"

    description = ET.SubElement(document, "description")
    sev_es = SEVERITY_LABELS_ES.get(event.get("severity", ""), "N/A")
    description.text = (
        f"Evento de incendio detectado por FireSentinel. "
        f"Severidad: {sev_es}. "
        f"Intencionalidad: {event.get('intent_score', 'N/A')}/100."
    )

    # Event centroid placemark
    centroid_pm = ET.SubElement(document, "Placemark")
    centroid_name = ET.SubElement(centroid_pm, "name")
    centroid_name.text = f"Centroide - {town}"
    centroid_point = ET.SubElement(centroid_pm, "Point")
    centroid_coords = ET.SubElement(centroid_point, "coordinates")
    centroid_coords.text = (
        f"{event.get('center_lon', 0)},{event.get('center_lat', 0)},0"
    )

    # Individual hotspot placemarks
    for i, hs in enumerate(hotspots, 1):
        pm = ET.SubElement(document, "Placemark")
        pm_name = ET.SubElement(pm, "name")
        pm_name.text = f"Hotspot {i} - FRP: {hs.get('frp', 0):.1f} MW"
        pm_desc = ET.SubElement(pm, "description")
        pm_desc.text = (
            f"Satelite: {hs.get('satellite', 'N/A')}, "
            f"Brillo: {hs.get('brightness', 0):.1f} K, "
            f"Fecha: {hs.get('acq_date', '')} {hs.get('acq_time', '')}"
        )
        point = ET.SubElement(pm, "Point")
        coords = ET.SubElement(point, "coordinates")
        coords.text = f"{hs.get('longitude', 0)},{hs.get('latitude', 0)},0"

    tree = ET.ElementTree(kml)
    output = io.BytesIO()
    tree.write(output, encoding="unicode", xml_declaration=True)
    return output.getvalue()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


def render_detail_page() -> None:
    """Render the fire event detail page.

    Reads event ID from st.session_state["selected_event"].
    """
    from firesentinel.config import get_settings

    settings = get_settings()
    db_url = f"sqlite:///{settings.db_path}"

    event_id = st.session_state.get("selected_event")

    # Validate event_id is a valid UUID to prevent XSS
    if event_id and not re.match(r"^[0-9a-fA-F-]{36}$", str(event_id)):
        event_id = None

    # -------------------------------------------------------------------
    # Back button
    # -------------------------------------------------------------------
    if st.button("\u2190 Volver al mapa"):
        st.session_state["page"] = "map"
        st.rerun()

    # -------------------------------------------------------------------
    # Handle missing event
    # -------------------------------------------------------------------
    if not event_id:
        st.markdown("""
        <div style="
            background:rgba(30,41,59,0.4);
            border:1px solid rgba(255,255,255,0.06);
            border-radius:14px; padding:48px; text-align:center;
        ">
            <span class="material-icons-round" style="font-size:48px;color:#64748B;">
                search_off</span>
            <p style="font-size:16px;color:#94A3B8;margin:16px 0 4px 0;">
                No se selecciono ningun evento</p>
            <p style="font-size:13px;color:#64748B;">
                Vuelva al mapa para seleccionar un incendio.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    # Fetch data
    event = _get_fire_event_detail(_db_url=db_url, event_id=event_id)

    if event is None:
        st.markdown(f"""
        <div style="
            background:rgba(239,68,68,0.1);
            border:1px solid rgba(239,68,68,0.3);
            border-radius:14px; padding:32px; text-align:center;
        ">
            <span class="material-icons-round" style="font-size:40px;color:#EF4444;">
                error_outline</span>
            <p style="font-size:14px;color:#F1F5F9;margin:12px 0 4px 0;">
                No se encontro el evento con ID: {html_escape(str(event_id))}</p>
            <p style="font-size:12px;color:#94A3B8;">
                Es posible que el evento haya sido eliminado o el enlace sea invalido.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    hotspots = _get_event_hotspots(_db_url=db_url, event_id=event_id)

    # -------------------------------------------------------------------
    # Event header
    # -------------------------------------------------------------------
    severity = event.get("severity", "medium")
    sev_icon = SEVERITY_ICONS.get(severity, "info")
    town = html_escape(event.get("nearest_town") or "Ubicacion desconocida")
    province = html_escape(event.get("province") or "")
    location = f"{town}, {province}" if province else town

    render_section_header(
        f"Evento de Incendio \u2014 {location}",
        sev_icon,
    )

    # Badges row
    intent_score = event.get("intent_score", 0) or 0
    intent_label = event.get("intent_label", "uncertain")

    badge_col1, badge_col2, _spacer = st.columns([1, 1, 3])
    with badge_col1:
        render_intent_badge(intent_score, intent_label)
    with badge_col2:
        render_severity_badge(severity)

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

    # -------------------------------------------------------------------
    # Map + KPI grid (two-column layout)
    # -------------------------------------------------------------------
    map_col, kpi_col = st.columns([3, 2])

    with map_col:
        detail_map = create_event_detail_map(event, hotspots)
        st_folium(
            detail_map,
            use_container_width=True,
            height=450,
            returned_objects=[],
        )

    with kpi_col:
        # KPI Row 1: Severity, Intent Score, Hotspot Count
        sev_color = SEVERITY_COLORS.get(severity, "#6B7280")
        sev_label = SEVERITY_LABELS_ES.get(severity, severity)

        render_kpi_row([
            {
                "label": "Severidad",
                "value": sev_label,
                "icon": sev_icon,
                "color": sev_color,
            },
            {
                "label": "Intencionalidad",
                "value": f"{intent_score}/100",
                "icon": "psychology",
                "color": COLORS["text_accent"],
            },
            {
                "label": "Focos",
                "value": str(event.get("hotspot_count", 0)),
                "icon": "scatter_plot",
                "color": "#F97316",
            },
        ])

        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)

        # KPI Row 2: Max FRP, First Detection, Road Distance
        first_det = event.get("first_detected_at")
        first_det_str = str(first_det)[:16] if first_det else "N/D"

        road_dist = event.get("nearest_road_m")
        road_type = event.get("nearest_road_type") or "none"
        road_type_es = _ROAD_TYPES_SPANISH.get(road_type, road_type)
        road_display = f"{road_dist:.0f}m" if road_dist is not None else "N/D"

        render_kpi_row([
            {
                "label": "FRP Maximo",
                "value": f"{event.get('max_frp', 0):.1f} MW",
                "icon": "whatshot",
                "color": "#EF4444",
            },
            {
                "label": "Deteccion",
                "value": first_det_str,
                "icon": "schedule",
                "color": "#60A5FA",
            },
            {
                "label": "Dist. Ruta",
                "value": road_display,
                "icon": "add_road",
                "color": "#FBBF24",
            },
        ])

        st.markdown("<div style='height:4px'></div>", unsafe_allow_html=True)

        # Road type subtitle
        if road_dist is not None:
            road_ref = event.get("nearest_road_ref")
            road_detail = road_type_es
            if road_ref:
                road_detail += f" ({road_ref})"
            st.markdown(
                f'<p style="font-size:11px;color:#64748B;text-align:center;margin:0;">'
                f"Tipo: {road_detail}</p>",
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)

        # Signal breakdown
        breakdown = event.get("intent_breakdown")
        if breakdown and isinstance(breakdown, dict):
            render_signal_breakdown(breakdown)
        else:
            st.markdown(
                '<p style="font-size:12px;color:#64748B;text-align:center;">'
                "Sin datos de desglose de senales disponibles.</p>",
                unsafe_allow_html=True,
            )

    # -------------------------------------------------------------------
    # Hotspot timeline
    # -------------------------------------------------------------------
    render_section_header("Detecciones", "timeline")

    if hotspots:
        table_rows = []
        for hs in hotspots:
            daynight_es = _DAYNIGHT_ES.get(hs.get("daynight", ""), hs.get("daynight", ""))
            table_rows.append({
                "Fecha": hs.get("acq_date", ""),
                "Hora": hs.get("acq_time", ""),
                "Satelite": hs.get("satellite", ""),
                "Brillo (K)": f"{hs.get('brightness', 0):.1f}",
                "FRP (MW)": f"{hs.get('frp', 0):.1f}",
                "Confianza": hs.get("confidence", ""),
                "Dia/Noche": daynight_es,
            })

        df = pd.DataFrame(table_rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.markdown(
            '<p style="font-size:13px;color:#64748B;">'
            "No hay detecciones individuales disponibles para este evento.</p>",
            unsafe_allow_html=True,
        )

    # -------------------------------------------------------------------
    # Weather context
    # -------------------------------------------------------------------
    render_section_header("Contexto Meteorologico", "cloud")

    weather = event.get("weather_data")
    if weather and isinstance(weather, dict):
        wx_col1, wx_col2 = st.columns(2)

        with wx_col1:
            temp = weather.get("temperature_c")
            wind = weather.get("wind_speed_kmh")
            humidity = weather.get("humidity_pct")
            cape = weather.get("cape")

            temp_str = f"{temp:.1f} C" if temp is not None else "N/D"
            wind_str = f"{wind:.1f} km/h" if wind is not None else "N/D"
            hum_str = f"{humidity:.0f}%" if humidity is not None else "N/D"
            cape_str = f"{cape:.0f} J/kg" if cape is not None else "N/D"

            content = f"""
            <div style="display:flex;flex-direction:column;gap:12px;">
                <div style="display:flex;justify-content:space-between;">
                    <span style="font-size:13px;color:#94A3B8;">
                        <span class="material-icons-round"
                            style="font-size:16px;vertical-align:middle;margin-right:6px;">
                            thermostat</span>Temperatura</span>
                    <span style="font-size:13px;font-weight:600;color:#F1F5F9;">
                        {temp_str}</span>
                </div>
                <div style="display:flex;justify-content:space-between;">
                    <span style="font-size:13px;color:#94A3B8;">
                        <span class="material-icons-round"
                            style="font-size:16px;vertical-align:middle;margin-right:6px;">
                            air</span>Viento</span>
                    <span style="font-size:13px;font-weight:600;color:#F1F5F9;">
                        {wind_str}</span>
                </div>
                <div style="display:flex;justify-content:space-between;">
                    <span style="font-size:13px;color:#94A3B8;">
                        <span class="material-icons-round"
                            style="font-size:16px;vertical-align:middle;margin-right:6px;">
                            water_drop</span>Humedad</span>
                    <span style="font-size:13px;font-weight:600;color:#F1F5F9;">
                        {hum_str}</span>
                </div>
                <div style="display:flex;justify-content:space-between;">
                    <span style="font-size:13px;color:#94A3B8;">
                        <span class="material-icons-round"
                            style="font-size:16px;vertical-align:middle;margin-right:6px;">
                            bolt</span>CAPE</span>
                    <span style="font-size:13px;font-weight:600;color:#F1F5F9;">
                        {cape_str}</span>
                </div>
            </div>
            """
            render_card_container(content)

        with wx_col2:
            precip_6h = weather.get("precipitation_mm_6h", 0)
            precip_72h = weather.get("precipitation_mm_72h", 0)
            thunderstorm = weather.get("has_thunderstorm", False)
            thunder_es = "Si" if thunderstorm else "No"

            p6_str = f"{precip_6h:.1f} mm" if precip_6h is not None else "N/D"
            p72_str = f"{precip_72h:.1f} mm" if precip_72h is not None else "N/D"

            content = f"""
            <div style="display:flex;flex-direction:column;gap:12px;">
                <div style="display:flex;justify-content:space-between;">
                    <span style="font-size:13px;color:#94A3B8;">
                        <span class="material-icons-round"
                            style="font-size:16px;vertical-align:middle;margin-right:6px;">
                            grain</span>Precipitacion (6h)</span>
                    <span style="font-size:13px;font-weight:600;color:#F1F5F9;">
                        {p6_str}</span>
                </div>
                <div style="display:flex;justify-content:space-between;">
                    <span style="font-size:13px;color:#94A3B8;">
                        <span class="material-icons-round"
                            style="font-size:16px;vertical-align:middle;margin-right:6px;">
                            water</span>Precipitacion (72h)</span>
                    <span style="font-size:13px;font-weight:600;color:#F1F5F9;">
                        {p72_str}</span>
                </div>
                <div style="display:flex;justify-content:space-between;">
                    <span style="font-size:13px;color:#94A3B8;">
                        <span class="material-icons-round"
                            style="font-size:16px;vertical-align:middle;margin-right:6px;">
                            thunderstorm</span>Tormentas electricas</span>
                    <span style="font-size:13px;font-weight:600;color:#F1F5F9;">
                        {thunder_es}</span>
                </div>
            </div>
            """
            render_card_container(content)
    else:
        st.markdown(
            '<p style="font-size:13px;color:#64748B;">'
            "No hay datos meteorologicos disponibles para este evento.</p>",
            unsafe_allow_html=True,
        )

    # -------------------------------------------------------------------
    # Export section
    # -------------------------------------------------------------------
    render_section_header("Exportar", "download")

    exp_col1, exp_col2 = st.columns(2)

    event_id_short = str(event_id)[:8] if event_id else "unknown"

    with exp_col1:
        if hotspots:
            csv_data = _generate_csv(hotspots)
            st.download_button(
                label="Descargar CSV",
                data=csv_data,
                file_name=f"firesentinel_evento_{event_id_short}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.markdown(
                '<p style="font-size:12px;color:#64748B;">No hay datos para exportar CSV.</p>',
                unsafe_allow_html=True,
            )

    with exp_col2:
        if hotspots:
            kml_data = _generate_kml(event, hotspots)
            st.download_button(
                label="Descargar KML (Google Earth)",
                data=kml_data,
                file_name=f"firesentinel_evento_{event_id_short}.kml",
                mime="application/vnd.google-earth.kml+xml",
                use_container_width=True,
            )
        else:
            st.markdown(
                '<p style="font-size:12px;color:#64748B;">No hay datos para exportar KML.</p>',
                unsafe_allow_html=True,
            )
