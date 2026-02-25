"""Public fire map page -- the main view of the FireSentinel dashboard.

Shows an interactive Folium map with color-coded fire markers, a stats
bar, and a filterable data table. All user-facing text in SPANISH.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import select
from sqlalchemy.orm import Session
from streamlit_folium import st_folium

from firesentinel.dashboard.components.charts import severity_distribution, timeline_chart
from firesentinel.dashboard.components.fire_map import create_fire_map
from firesentinel.db.models import FireEvent, PipelineRun

# ---------------------------------------------------------------------------
# Severity and intent label translations for display
# ---------------------------------------------------------------------------

_SEVERITY_LABEL_ES: dict[str, str] = {
    "low": "Baja",
    "medium": "Media",
    "high": "Alta",
    "critical": "Critica",
}

_INTENT_LABEL_ES: dict[str, str] = {
    "natural": "Natural",
    "uncertain": "Incierto",
    "suspicious": "Sospechoso",
    "likely_intentional": "Probable intencional",
}

_SEVERITY_COLORS: dict[str, str] = {
    "low": "#2ecc71",
    "medium": "#f1c40f",
    "high": "#e67e22",
    "critical": "#e74c3c",
}


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300)
def get_fire_events(
    _db_url: str,
    date_from: str,
    date_to: str,
    severities: list[str],
    min_intent: int,
    provinces: list[str],
) -> list[dict[str, Any]]:
    """Query fire events with filters from the database.

    Uses a sync SQLAlchemy session. Results are cached for 5 minutes.

    Args:
        _db_url: Database URL (used as cache key prefix, leading underscore
            tells Streamlit not to hash it).
        date_from: Start date as ISO string (YYYY-MM-DD).
        date_to: End date as ISO string (YYYY-MM-DD).
        severities: List of severity enum values to include.
        min_intent: Minimum intentionality score (0-100).
        provinces: List of province names to include.

    Returns:
        List of fire event dicts for display.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    results: list[dict[str, Any]] = []

    with Session(engine) as session:
        query = select(FireEvent).where(
            FireEvent.first_detected_at >= date_from,
            FireEvent.first_detected_at <= date_to + " 23:59:59",
        )

        if severities:
            query = query.where(FireEvent.severity.in_(severities))

        if min_intent > 0:
            query = query.where(FireEvent.intent_score >= min_intent)

        if provinces:
            query = query.where(FireEvent.province.in_(provinces))

        query = query.order_by(FireEvent.first_detected_at.desc())

        rows = session.execute(query).scalars().all()

        for row in rows:
            results.append(
                {
                    "id": row.id,
                    "center_lat": row.center_lat,
                    "center_lon": row.center_lon,
                    "province": row.province,
                    "nearest_town": row.nearest_town,
                    "severity": row.severity,
                    "hotspot_count": row.hotspot_count,
                    "max_frp": row.max_frp,
                    "first_detected_at": row.first_detected_at.strftime("%Y-%m-%d %H:%M")
                    if row.first_detected_at
                    else "",
                    "last_updated_at": row.last_updated_at.strftime("%Y-%m-%d %H:%M")
                    if row.last_updated_at
                    else "",
                    "intent_score": row.intent_score,
                    "intent_label": row.intent_label,
                    "is_active": row.is_active,
                }
            )

    engine.dispose()
    return results


@st.cache_data(ttl=300)
def get_last_pipeline_run(_db_url: str) -> dict[str, Any] | None:
    """Fetch the most recent pipeline run from the database.

    Args:
        _db_url: Database URL string.

    Returns:
        Dict with pipeline run info, or None if no runs exist.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    result: dict[str, Any] | None = None

    with Session(engine) as session:
        query = select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(1)
        row = session.execute(query).scalar_one_or_none()
        if row:
            result = {
                "started_at": row.started_at.strftime("%Y-%m-%d %H:%M UTC")
                if row.started_at
                else "N/A",
                "status": row.status,
                "duration_ms": row.duration_ms,
            }

    engine.dispose()
    return result


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


def render_stats_bar(events: list[dict[str, Any]], pipeline_run: dict[str, Any] | None) -> None:
    """Render the stats bar above the map."""
    total_fires = len(events)

    # Count by severity
    severity_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    suspicious_count = 0

    for ev in events:
        sev = ev.get("severity", "medium")
        severity_counts[sev] = severity_counts.get(sev, 0) + 1
        label = ev.get("intent_label", "")
        if label in ("suspicious", "likely_intentional"):
            suspicious_count += 1

    suspicious_pct = (suspicious_count / total_fires * 100) if total_fires > 0 else 0

    # Render metrics row
    col1, col2, col3, col4, col5, col6 = st.columns(6)

    with col1:
        st.metric("Incendios activos", total_fires)

    with col2:
        st.metric(
            "Baja",
            severity_counts["low"],
        )

    with col3:
        st.metric(
            "Media",
            severity_counts["medium"],
        )

    with col4:
        st.metric(
            "Alta",
            severity_counts["high"],
        )

    with col5:
        st.metric(
            "Critica",
            severity_counts["critical"],
        )

    with col6:
        st.metric(
            "Sospechosos",
            f"{suspicious_count} ({suspicious_pct:.0f}%)",
        )

    # Pipeline status
    if pipeline_run:
        status = pipeline_run.get("status", "unknown")
        status_map = {
            "success": "\U0001f7e2",
            "partial": "\U0001f7e1",
            "failed": "\U0001f534",
        }
        status_emoji = status_map.get(status, "\u26aa")
        st.caption(
            f"Ultimo ciclo: {pipeline_run.get('started_at', 'N/A')} "
            f"{status_emoji} {status.upper()}"
        )


def render_data_table(events: list[dict[str, Any]]) -> str | None:
    """Render a sortable table of fire events and return selected event ID.

    Returns:
        Event ID if a row is selected, None otherwise.
    """
    if not events:
        st.info("No hay eventos de incendio que coincidan con los filtros seleccionados.")
        return None

    # Build display DataFrame
    rows = []
    for ev in events:
        sev_es = _SEVERITY_LABEL_ES.get(ev["severity"], ev["severity"])
        intent_es = _INTENT_LABEL_ES.get(ev.get("intent_label") or "", "N/A")
        intent_score = ev.get("intent_score")
        intent_display = f"{intent_score}/100 - {intent_es}" if intent_score is not None else "N/A"
        status = "Activo" if ev.get("is_active") else "Resuelto"

        rows.append(
            {
                "Ubicacion": ev.get("nearest_town")
                or f"{ev['center_lat']:.3f}, {ev['center_lon']:.3f}",
                "Severidad": sev_es,
                "Intencionalidad": intent_display,
                "Detecciones": ev.get("hotspot_count", 0),
                "FRP Max": f"{ev.get('max_frp', 0):.1f} MW",
                "Primera Deteccion": ev.get("first_detected_at", ""),
                "Estado": status,
                "_event_id": ev["id"],
            }
        )

    df = pd.DataFrame(rows)
    display_df = df.drop(columns=["_event_id"])

    st.subheader("Tabla de eventos")

    # Use st.dataframe for sortable display
    event_selection = st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
    )

    # Check if user selected a row
    selected_rows = event_selection.get("selection", {}).get("rows", [])
    if selected_rows:
        selected_idx = selected_rows[0]
        if selected_idx < len(rows):
            return rows[selected_idx]["_event_id"]

    return None


def render_map_page(db_url: str, filters: dict[str, Any]) -> None:
    """Render the full public fire map page.

    Args:
        db_url: SQLAlchemy database URL string.
        filters: Dict with keys: date_from, date_to, severities,
            min_intent, provinces.
    """
    st.title("\U0001f5fa Mapa de Incendios")

    # Fetch data
    events = get_fire_events(
        _db_url=db_url,
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        severities=filters["severities"],
        min_intent=filters["min_intent"],
        provinces=filters["provinces"],
    )

    pipeline_run = get_last_pipeline_run(_db_url=db_url)

    # Stats bar
    render_stats_bar(events, pipeline_run)

    st.divider()

    # Map
    if events:
        fire_map = create_fire_map(events)
        st_folium(fire_map, use_container_width=True, height=500)
    else:
        st.info("No hay incendios detectados para los filtros seleccionados.")
        # Show empty map
        fire_map = create_fire_map([])
        st_folium(fire_map, use_container_width=True, height=500)

    st.divider()

    # Charts
    if events:
        chart_col1, chart_col2 = st.columns(2)
        with chart_col1:
            st.subheader("Distribucion por severidad")
            severity_distribution(events)
        with chart_col2:
            st.subheader("Detecciones en el tiempo")
            timeline_chart(events)

        st.divider()

    # Data table
    selected_event_id = render_data_table(events)

    if selected_event_id:
        st.session_state["selected_event_id"] = selected_event_id
        st.session_state["page"] = "detail"
        st.rerun()
