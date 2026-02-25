"""Fire event detail page -- full information for a single fire event.

Shows event metrics, hotspot timeline, intent breakdown, weather context,
and provides CSV/KML export options. All user-facing text in SPANISH.
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session
from streamlit_folium import st_folium

from firesentinel.dashboard.components.charts import intent_breakdown_chart
from firesentinel.dashboard.components.fire_map import create_event_detail_map
from firesentinel.db.models import FireEvent, Hotspot

# ---------------------------------------------------------------------------
# Translation maps
# ---------------------------------------------------------------------------

_SEVERITY_LABEL_ES: dict[str, str] = {
    "low": "Baja",
    "medium": "Media",
    "high": "Alta",
    "critical": "Critica",
}

_SEVERITY_EMOJI: dict[str, str] = {
    "low": "\U0001f7e2",
    "medium": "\U0001f7e1",
    "high": "\U0001f7e0",
    "critical": "\U0001f534",
}

_INTENT_LABEL_ES: dict[str, str] = {
    "natural": "Natural",
    "uncertain": "Incierto",
    "suspicious": "Sospechoso",
    "likely_intentional": "Probable intencional",
}

_ROAD_TYPES_SPANISH: dict[str, str] = {
    "track": "camino rural",
    "path": "sendero",
    "tertiary": "camino terciario",
    "unclassified": "camino sin clasificar",
    "secondary": "ruta secundaria",
    "primary": "ruta principal",
    "trunk": "ruta troncal",
    "motorway": "autopista",
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
def get_fire_event_detail(_db_url: str, event_id: str) -> dict[str, Any] | None:
    """Fetch full details for a single fire event.

    Args:
        _db_url: Database URL string.
        event_id: UUID of the fire event.

    Returns:
        Dict with all event fields, or None if not found.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    result: dict[str, Any] | None = None

    with Session(engine) as session:
        row = session.get(FireEvent, event_id)
        if row:
            result = {
                "id": row.id,
                "center_lat": row.center_lat,
                "center_lon": row.center_lon,
                "province": row.province,
                "nearest_town": row.nearest_town,
                "severity": row.severity,
                "hotspot_count": row.hotspot_count,
                "max_frp": row.max_frp,
                "first_detected_at": row.first_detected_at,
                "last_updated_at": row.last_updated_at,
                "intent_score": row.intent_score,
                "intent_label": row.intent_label,
                "intent_breakdown": row.intent_breakdown,
                "weather_data": row.weather_data,
                "nearest_road_m": row.nearest_road_m,
                "nearest_road_type": row.nearest_road_type,
                "nearest_road_ref": row.nearest_road_ref,
                "is_active": row.is_active,
            }

    engine.dispose()
    return result


@st.cache_data(ttl=300)
def get_event_hotspots(_db_url: str, event_id: str) -> list[dict[str, Any]]:
    """Fetch all hotspot detections for a fire event.

    Args:
        _db_url: Database URL string.
        event_id: UUID of the fire event.

    Returns:
        List of hotspot dicts sorted by detection time.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    results: list[dict[str, Any]] = []

    with Session(engine) as session:
        query = (
            select(Hotspot)
            .where(Hotspot.fire_event_id == event_id)
            .order_by(Hotspot.acq_date, Hotspot.acq_time)
        )
        rows = session.execute(query).scalars().all()

        for hs in rows:
            results.append(
                {
                    "latitude": hs.latitude,
                    "longitude": hs.longitude,
                    "acq_date": str(hs.acq_date) if hs.acq_date else "",
                    "acq_time": str(hs.acq_time) if hs.acq_time else "",
                    "satellite": hs.satellite,
                    "brightness": hs.brightness,
                    "frp": hs.frp,
                    "confidence": hs.confidence,
                    "daynight": hs.daynight,
                    "source": hs.source,
                }
            )

    engine.dispose()
    return results


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _generate_csv(hotspots: list[dict[str, Any]]) -> str:
    """Generate CSV string from hotspot data."""
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
    """Generate KML XML string for Google Earth export."""
    kml = ET.Element("kml", xmlns="http://www.opengis.net/kml/2.2")
    document = ET.SubElement(kml, "Document")

    town = event.get("nearest_town") or "Incendio"
    name = ET.SubElement(document, "name")
    name.text = f"FireSentinel - {town}"

    description = ET.SubElement(document, "description")
    description.text = (
        f"Evento de incendio detectado por FireSentinel. "
        f"Severidad: {_SEVERITY_LABEL_ES.get(event.get('severity', ''), 'N/A')}. "
        f"Intencionalidad: {event.get('intent_score', 'N/A')}/100."
    )

    # Event centroid
    centroid_pm = ET.SubElement(document, "Placemark")
    centroid_name = ET.SubElement(centroid_pm, "name")
    centroid_name.text = f"Centroide - {town}"
    centroid_point = ET.SubElement(centroid_pm, "Point")
    centroid_coords = ET.SubElement(centroid_point, "coordinates")
    centroid_coords.text = f"{event.get('center_lon', 0)},{event.get('center_lat', 0)},0"

    # Individual hotspots
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


def render_metrics_cards(event: dict[str, Any]) -> None:
    """Render key metrics cards in a grid."""
    severity = event.get("severity", "medium")
    sev_es = _SEVERITY_LABEL_ES.get(severity, severity)
    sev_emoji = _SEVERITY_EMOJI.get(severity, "")

    intent_score = event.get("intent_score")
    intent_label = event.get("intent_label", "")
    intent_es = _INTENT_LABEL_ES.get(intent_label, "N/A")

    st.metric("Severidad", f"{sev_emoji} {sev_es}")
    st.metric(
        "Intencionalidad",
        f"{intent_score}/100" if intent_score is not None else "N/A",
        delta=intent_es,
        delta_color="off",
    )
    st.metric("Detecciones", event.get("hotspot_count", 0))
    st.metric("FRP Maximo", f"{event.get('max_frp', 0):.1f} MW")

    first_det = event.get("first_detected_at")
    last_upd = event.get("last_updated_at")
    first_str = first_det.strftime("%Y-%m-%d %H:%M") if first_det else "N/A"
    last_str = last_upd.strftime("%Y-%m-%d %H:%M") if last_upd else "N/A"
    st.metric("Primera deteccion", first_str)
    st.metric("Ultima actualizacion", last_str)

    # Nearest road info
    road_dist = event.get("nearest_road_m")
    road_type = event.get("nearest_road_type") or "none"
    road_ref = event.get("nearest_road_ref")
    road_type_es = _ROAD_TYPES_SPANISH.get(road_type, road_type)

    if road_dist is not None:
        road_display = f"{road_dist:.0f}m - {road_type_es}"
        if road_ref:
            road_display += f" ({road_ref})"
    else:
        road_display = "Sin datos"

    st.metric("Camino mas cercano", road_display)


def render_weather_context(weather: dict[str, Any] | None) -> None:
    """Render weather conditions as a compact info box."""
    if weather is None:
        st.info("No hay datos meteorologicos disponibles para este evento.")
        return

    st.subheader("Contexto meteorologico")

    col1, col2 = st.columns(2)

    with col1:
        temp = weather.get("temperature_c")
        wind = weather.get("wind_speed_kmh")
        humidity = weather.get("humidity_pct")
        cape = weather.get("cape")

        st.markdown(
            f"- **Temperatura:** {temp:.1f} C\n"
            f"- **Viento:** {wind:.1f} km/h\n"
            f"- **Humedad:** {humidity:.0f}%\n"
            f"- **CAPE:** {cape:.0f} J/kg"
            if all(v is not None for v in [temp, wind, humidity, cape])
            else "Datos parciales disponibles."
        )

    with col2:
        precip_6h = weather.get("precipitation_mm_6h", 0)
        precip_72h = weather.get("precipitation_mm_72h", 0)
        thunderstorm = weather.get("has_thunderstorm", False)
        thunder_es = "Si" if thunderstorm else "No"

        st.markdown(
            f"- **Precipitacion (6h):** {precip_6h:.1f} mm\n"
            f"- **Precipitacion (72h):** {precip_72h:.1f} mm\n"
            f"- **Actividad de tormentas:** {thunder_es}"
        )


def render_hotspot_timeline(hotspots: list[dict[str, Any]]) -> None:
    """Render a table of satellite detection records."""
    st.subheader("Detecciones satelitales")

    if not hotspots:
        st.info("No hay detecciones individuales disponibles para este evento.")
        return

    rows = []
    for hs in hotspots:
        daynight_es = _DAYNIGHT_ES.get(hs.get("daynight", ""), hs.get("daynight", ""))
        rows.append(
            {
                "Fecha": hs.get("acq_date", ""),
                "Hora (UTC)": hs.get("acq_time", ""),
                "Satelite": hs.get("satellite", ""),
                "Brillo (K)": f"{hs.get('brightness', 0):.1f}",
                "FRP (MW)": f"{hs.get('frp', 0):.1f}",
                "Confianza": hs.get("confidence", ""),
                "Dia/Noche": daynight_es,
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)


def render_detail_page(db_url: str, event_id: str | None) -> None:
    """Render the fire event detail page.

    Args:
        db_url: SQLAlchemy database URL string.
        event_id: UUID of the fire event to display.
    """
    if not event_id:
        st.warning("No se selecciono ningun evento. Vuelva al mapa para seleccionar un incendio.")
        if st.button("Volver al mapa"):
            st.session_state["page"] = "map"
            st.rerun()
        return

    # Fetch event and hotspots
    event = get_fire_event_detail(_db_url=db_url, event_id=event_id)

    if event is None:
        st.error(f"No se encontro el evento con ID: {event_id}")
        if st.button("Volver al mapa"):
            st.session_state["page"] = "map"
            st.rerun()
        return

    hotspots = get_event_hotspots(_db_url=db_url, event_id=event_id)

    # Header
    severity = event.get("severity", "medium")
    sev_emoji = _SEVERITY_EMOJI.get(severity, "")
    town = event.get("nearest_town") or "Ubicacion desconocida"
    province = event.get("province") or ""
    location_display = f"{town}, {province}" if province else town

    st.title(f"{sev_emoji} Evento de Incendio - {location_display}")

    # Back button
    if st.button("< Volver al mapa"):
        st.session_state["page"] = "map"
        st.rerun()

    st.divider()

    # Two-column layout: map (left) + metrics (right)
    map_col, metrics_col = st.columns([3, 2])

    with map_col:
        detail_map = create_event_detail_map(event, hotspots)
        st_folium(detail_map, use_container_width=True, height=400)

    with metrics_col:
        render_metrics_cards(event)

    st.divider()

    # Intent breakdown section
    st.subheader("Analisis de intencionalidad")

    breakdown = event.get("intent_breakdown")
    intent_score = event.get("intent_score")
    intent_label = event.get("intent_label", "")
    intent_es = _INTENT_LABEL_ES.get(intent_label, "N/A")

    if intent_score is not None:
        st.markdown(f"**Puntaje total:** {intent_score}/100 - **{intent_es}**")

    intent_breakdown_chart(breakdown)

    st.caption(
        "Este modelo esta calibrado con patrones de incendios 2025-2026. "
        "No reemplaza investigacion oficial."
    )

    st.divider()

    # Hotspot timeline
    render_hotspot_timeline(hotspots)

    st.divider()

    # Weather context
    render_weather_context(event.get("weather_data"))

    st.divider()

    # Export options
    st.subheader("Exportar datos")
    export_col1, export_col2 = st.columns(2)

    with export_col1:
        if hotspots:
            csv_data = _generate_csv(hotspots)
            st.download_button(
                label="Descargar CSV",
                data=csv_data,
                file_name=f"firesentinel_evento_{event_id[:8]}.csv",
                mime="text/csv",
            )
        else:
            st.info("No hay datos de hotspots para exportar.")

    with export_col2:
        if hotspots:
            kml_data = _generate_kml(event, hotspots)
            st.download_button(
                label="Descargar KML (Google Earth)",
                data=kml_data,
                file_name=f"firesentinel_evento_{event_id[:8]}.kml",
                mime="application/vnd.google-earth.kml+xml",
            )
        else:
            st.info("No hay datos de hotspots para exportar.")
