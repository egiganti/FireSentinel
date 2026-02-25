"""Public fire map page -- the main view of the FireSentinel dashboard.

Shows an interactive Folium map with premium metric cards, a scrollable
event list panel, and analytics charts. All user-facing text in SPANISH.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from html import escape as html_escape
from typing import Any

import streamlit as st
from sqlalchemy import create_engine, text
from streamlit_folium import st_folium

from firesentinel.dashboard.components.charts import (
    intent_distribution_chart,
    severity_distribution_chart,
    timeline_chart,
)
from firesentinel.dashboard.components.fire_map import add_monitoring_zones, create_fire_map
from firesentinel.dashboard.theme import (
    COLORS,
    INTENT_COLORS,
    SEVERITY_COLORS,
    SEVERITY_LABELS_ES,
    render_metric_card,
    render_section_header,
)

# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300)
def _get_fire_events(
    _db_url: str,
    date_from: str,
    date_to: str,
    severities: list[str],
    min_intent: int,
    provinces: list[str],
) -> list[dict[str, Any]]:
    """Query fire events with filters from the database.

    Uses raw SQL via sync SQLAlchemy for Streamlit compatibility.
    Results are cached for 5 minutes.

    Args:
        _db_url: Database URL (leading underscore tells Streamlit not to hash).
        date_from: Start date as ISO string (YYYY-MM-DD).
        date_to: End date as ISO string (YYYY-MM-DD).
        severities: List of severity enum values to include.
        min_intent: Minimum intentionality score (0-100).
        provinces: List of province names to include.

    Returns:
        List of fire event dicts sorted by intent_score descending.
    """
    engine = create_engine(_db_url)

    # Build parameterized query — no string interpolation of user values
    params: dict[str, Any] = {
        "date_from": date_from,
        "date_to": date_to + " 23:59:59",
        "min_intent": min_intent,
    }

    # Severity filter: use individual bind params for safe IN clause
    sev_filter = "1=1"
    if severities:
        sev_binds = []
        for i, s in enumerate(severities):
            key = f"sev_{i}"
            params[key] = s
            sev_binds.append(f":{key}")
        sev_filter = f"severity IN ({','.join(sev_binds)})"

    # Province filter: same pattern
    prov_filter = "1=1"
    if provinces:
        prov_binds = []
        for i, p in enumerate(provinces):
            key = f"prov_{i}"
            params[key] = p
            prov_binds.append(f":{key}")
        prov_filter = f"province IN ({','.join(prov_binds)})"

    query = f"""
        SELECT id, center_lat, center_lon, province, nearest_town, severity,
               hotspot_count, max_frp, first_detected_at, last_updated_at,
               intent_score, intent_label, is_active, intent_breakdown, weather_data
        FROM fire_events
        WHERE first_detected_at >= :date_from
          AND first_detected_at <= :date_to
          AND {sev_filter}
          AND intent_score >= :min_intent
          AND {prov_filter}
        ORDER BY intent_score DESC
    """

    results: list[dict[str, Any]] = []
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()

        for row in rows:
            # Parse intent_breakdown if stored as JSON string
            breakdown = row.get("intent_breakdown")
            if isinstance(breakdown, str):
                try:
                    breakdown = json.loads(breakdown)
                except (json.JSONDecodeError, TypeError):
                    breakdown = None

            # Parse weather_data if stored as JSON string
            weather = row.get("weather_data")
            if isinstance(weather, str):
                try:
                    weather = json.loads(weather)
                except (json.JSONDecodeError, TypeError):
                    weather = None

            first_det = row.get("first_detected_at")
            last_upd = row.get("last_updated_at")

            results.append({
                "id": row["id"],
                "center_lat": row["center_lat"],
                "center_lon": row["center_lon"],
                "province": row["province"],
                "nearest_town": row["nearest_town"],
                "severity": row["severity"],
                "hotspot_count": row["hotspot_count"],
                "max_frp": row["max_frp"],
                "first_detected_at": str(first_det) if first_det else "",
                "last_updated_at": str(last_upd) if last_upd else "",
                "intent_score": row["intent_score"],
                "intent_label": row["intent_label"],
                "is_active": row["is_active"],
                "intent_breakdown": breakdown,
                "weather_data": weather,
            })

    engine.dispose()
    return results


@st.cache_data(ttl=300)
def _get_last_pipeline_run(_db_url: str) -> dict[str, Any] | None:
    """Fetch the most recent pipeline run from the database.

    Args:
        _db_url: Database URL string.

    Returns:
        Dict with pipeline run info, or None if no runs exist.
    """
    engine = create_engine(_db_url)
    result: dict[str, Any] | None = None

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT started_at, status, duration_ms, hotspots_fetched, new_hotspots "
                "FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
            )
        ).mappings().all()

        if rows:
            row = rows[0]
            result = {
                "started_at": row["started_at"],
                "status": row["status"],
                "duration_ms": row.get("duration_ms"),
                "hotspots_fetched": row.get("hotspots_fetched"),
                "new_hotspots": row.get("new_hotspots"),
            }

    engine.dispose()
    return result


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _format_time_ago(timestamp_str: str | None) -> str:
    """Convert an ISO timestamp to a human-readable 'hace Xm' string."""
    if not timestamp_str:
        return "N/D"
    try:
        dt = datetime.fromisoformat(str(timestamp_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        delta = now - dt
        total_seconds = int(delta.total_seconds())

        if total_seconds < 0:
            return "ahora"
        if total_seconds < 60:
            return f"hace {total_seconds}s"
        if total_seconds < 3600:
            return f"hace {total_seconds // 60}m"
        if total_seconds < 86400:
            return f"hace {total_seconds // 3600}h"
        return f"hace {total_seconds // 86400}d"
    except (ValueError, TypeError):
        return "N/D"


def _severity_breakdown_text(events: list[dict[str, Any]]) -> str:
    """Build a compact severity breakdown string like 'C:2 A:5 M:12 B:3'."""
    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for ev in events:
        sev = ev.get("severity", "medium")
        counts[sev] = counts.get(sev, 0) + 1

    parts = []
    abbrev = {"critical": "C", "high": "A", "medium": "M", "low": "B"}
    for key in ("critical", "high", "medium", "low"):
        if counts.get(key, 0) > 0:
            parts.append(f"{abbrev[key]}:{counts[key]}")

    return " | ".join(parts) if parts else "Sin eventos"


# ---------------------------------------------------------------------------
# Event list panel rendering
# ---------------------------------------------------------------------------


def _render_event_card(event: dict[str, Any], index: int) -> None:
    """Render a single event as a styled mini card in the event list."""
    intent_label = event.get("intent_label", "uncertain")
    border_color = INTENT_COLORS.get(intent_label, COLORS["border_default"])
    severity = event.get("severity", "medium")
    sev_color = SEVERITY_COLORS.get(severity, "#6B7280")
    sev_label = SEVERITY_LABELS_ES.get(severity, severity)
    town = html_escape(event.get("nearest_town") or "Ubicacion desconocida")
    province = html_escape(event.get("province") or "")
    intent_score = event.get("intent_score", 0)
    hotspot_count = event.get("hotspot_count", 0)
    max_frp = event.get("max_frp", 0.0)

    # Intent score color
    if intent_score >= 76:
        score_color = "#EF4444"
    elif intent_score >= 51:
        score_color = "#F97316"
    elif intent_score >= 26:
        score_color = "#FBBF24"
    else:
        score_color = "#22C55E"

    st.markdown(f"""
    <div style="
        background:rgba(30,41,59,0.4);
        border:1px solid rgba(255,255,255,0.06);
        border-left:3px solid {border_color};
        border-radius:10px; padding:12px 14px; margin-bottom:8px;
        transition:background 0.2s;
    ">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;">
            <div>
                <p style="font-size:13px;font-weight:600;color:#F1F5F9;margin:0;">
                    {town}</p>
                <p style="font-size:11px;color:#64748B;margin:2px 0 0 0;">{province}</p>
            </div>
            <span style="font-size:14px;font-weight:700;color:{score_color};">
                {intent_score}</span>
        </div>
        <div style="display:flex;align-items:center;gap:12px;margin-top:8px;">
            <div style="display:flex;align-items:center;gap:4px;">
                <div style="width:6px;height:6px;border-radius:50%;background:{sev_color};"></div>
                <span style="font-size:10px;color:{sev_color};font-weight:600;
                    text-transform:uppercase;">{sev_label}</span>
            </div>
            <span style="font-size:10px;color:#94A3B8;">
                {hotspot_count} focos</span>
            <span style="font-size:10px;color:#94A3B8;">
                FRP {max_frp:.1f}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("Ver detalle", key=f"detail_btn_{index}_{event['id']}", use_container_width=True):
        st.session_state["selected_event"] = event["id"]
        st.session_state["page"] = "detail"
        st.rerun()


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


def render_map_page(filters: dict[str, Any]) -> None:
    """Render the full public fire map page.

    Args:
        filters: Dict with keys: date_from, date_to, severities,
            min_intent, provinces.
    """
    from firesentinel.config import get_settings

    settings = get_settings()
    db_url = f"sqlite:///{settings.db_path}"

    # Load zones from YAML config for map overlays
    zone_list: list[dict] = []
    try:
        yaml_config = settings.load_yaml_config()
        zone_list = [
            {"name": name, "center": zone.center, "radius_km": zone.radius_km}
            for name, zone in yaml_config.zones.items()
        ]
    except (FileNotFoundError, Exception):
        pass

    # Fetch data
    events = _get_fire_events(
        _db_url=db_url,
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        severities=filters["severities"],
        min_intent=filters["min_intent"],
        provinces=filters["provinces"],
    )

    pipeline_run = _get_last_pipeline_run(_db_url=db_url)

    # -----------------------------------------------------------------------
    # Metric cards row
    # -----------------------------------------------------------------------
    total_events = len(events)
    suspicious_count = sum(
        1 for ev in events
        if ev.get("intent_label") in ("suspicious", "likely_intentional")
    )
    severity_text = _severity_breakdown_text(events)

    # Pipeline status
    pipeline_status = "N/D"
    pipeline_icon = "check_circle"
    pipeline_accent = COLORS["status_online"]
    if pipeline_run:
        status = pipeline_run.get("status", "unknown")
        if status == "success":
            pipeline_status = "Operativo"
            pipeline_icon = "check_circle"
            pipeline_accent = COLORS["status_online"]
        elif status == "partial":
            pipeline_status = "Parcial"
            pipeline_icon = "warning"
            pipeline_accent = COLORS["status_warning"]
        else:
            pipeline_status = "Error"
            pipeline_icon = "error"
            pipeline_accent = COLORS["status_error"]
    else:
        pipeline_status = "Sin datos"
        pipeline_icon = "help_outline"
        pipeline_accent = COLORS["text_muted"]

    last_scan_text = _format_time_ago(
        pipeline_run.get("started_at") if pipeline_run else None
    )

    mc1, mc2, mc3, mc4 = st.columns(4)

    with mc1:
        render_metric_card(
            icon="local_fire_department",
            label="Focos Activos",
            value=str(total_events),
            accent="#FF6B35",
            subtitle=severity_text,
        )

    with mc2:
        render_metric_card(
            icon="warning",
            label="Sospechosos",
            value=str(suspicious_count),
            accent="#F97316",
            subtitle=f"{(suspicious_count / total_events * 100):.0f}% del total"
            if total_events > 0 else "Sin eventos",
        )

    with mc3:
        render_metric_card(
            icon="satellite_alt",
            label="Ultimo Escaneo",
            value=last_scan_text,
            accent="#60A5FA",
            subtitle=str(pipeline_run.get("started_at", ""))[:16]
            if pipeline_run else "",
        )

    with mc4:
        render_metric_card(
            icon=pipeline_icon,
            label="Estado Pipeline",
            value=pipeline_status,
            accent=pipeline_accent,
        )

    # -----------------------------------------------------------------------
    # Spacer
    # -----------------------------------------------------------------------
    st.markdown("<div style='height:16px'></div>", unsafe_allow_html=True)

    # -----------------------------------------------------------------------
    # Map + Event list (two-column layout)
    # -----------------------------------------------------------------------
    if not events:
        st.markdown("""
        <div style="
            background:rgba(30,41,59,0.4);
            border:1px solid rgba(255,255,255,0.06);
            border-radius:14px; padding:48px; text-align:center;
        ">
            <span class="material-icons-round" style="font-size:48px;color:#64748B;">
                satellite_alt</span>
            <p style="font-size:16px;color:#94A3B8;margin:16px 0 4px 0;">
                No se detectaron incendios</p>
            <p style="font-size:13px;color:#64748B;">
                No hay eventos que coincidan con los filtros seleccionados.
                Ajuste las fechas o criterios de busqueda.</p>
        </div>
        """, unsafe_allow_html=True)
        return

    map_col, list_col = st.columns([3, 1])

    with map_col:
        fire_map = create_fire_map(events)
        if zone_list:
            add_monitoring_zones(fire_map, zone_list)
        st_folium(
            fire_map,
            use_container_width=True,
            height=600,
            returned_objects=[],
        )

    with list_col:
        render_section_header(
            "Eventos Activos",
            "list",
            subtitle=f"{total_events} eventos",
        )

        with st.container(height=540):
            for idx, event in enumerate(events):
                _render_event_card(event, idx)

    # -----------------------------------------------------------------------
    # Charts section
    # -----------------------------------------------------------------------
    render_section_header("Analisis", "analytics")

    chart_col1, chart_col2, chart_col3 = st.columns(3)

    with chart_col1:
        severity_distribution_chart(events)

    with chart_col2:
        timeline_chart(events)

    with chart_col3:
        intent_distribution_chart(events)
