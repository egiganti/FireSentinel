"""Admin panel page -- password-protected system health dashboard.

Shows pipeline health, API status, alert stats, and system information.
All user-facing text in SPANISH.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from firesentinel.db.models import AlertSent, AlertSubscription, FireEvent, Hotspot, PipelineRun

# ---------------------------------------------------------------------------
# Pipeline status display maps
# ---------------------------------------------------------------------------

_STATUS_COLORS: dict[str, str] = {
    "success": "\U0001f7e2",
    "partial": "\U0001f7e1",
    "failed": "\U0001f534",
}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _check_admin_auth() -> bool:
    """Check if the user is authenticated as admin.

    Uses ADMIN_PASSWORD env var for password verification and
    Streamlit session state to persist auth status.

    Returns:
        True if authenticated, False otherwise.
    """
    if st.session_state.get("admin_authenticated", False):
        return True

    admin_password = os.environ.get("ADMIN_PASSWORD", "")

    if not admin_password:
        st.warning(
            "La variable de entorno ADMIN_PASSWORD no esta configurada. "
            "El panel de administracion no esta disponible."
        )
        return False

    st.subheader("Acceso al panel de administracion")
    password = st.text_input("Contrasena", type="password", key="admin_password_input")

    if st.button("Ingresar"):
        if password == admin_password:
            st.session_state["admin_authenticated"] = True
            st.rerun()
        else:
            st.error("Contrasena incorrecta.")

    return False


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300)
def get_pipeline_runs(_db_url: str, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch recent pipeline runs.

    Args:
        _db_url: Database URL string.
        limit: Number of runs to fetch.

    Returns:
        List of pipeline run dicts sorted by most recent first.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    results: list[dict[str, Any]] = []

    with Session(engine) as session:
        query = select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(limit)
        rows = session.execute(query).scalars().all()

        for run in rows:
            errors_data = run.errors
            error_list: list[str] = []
            if isinstance(errors_data, list):
                error_list = errors_data
            elif isinstance(errors_data, dict):
                error_list = errors_data.get("errors", [])

            results.append(
                {
                    "id": run.id,
                    "started_at": run.started_at.strftime("%Y-%m-%d %H:%M")
                    if run.started_at
                    else "N/A",
                    "completed_at": run.completed_at.strftime("%Y-%m-%d %H:%M")
                    if run.completed_at
                    else "N/A",
                    "status": run.status,
                    "duration_ms": run.duration_ms,
                    "hotspots_fetched": run.hotspots_fetched,
                    "new_hotspots": run.new_hotspots,
                    "events_created": run.events_created,
                    "events_updated": run.events_updated,
                    "alerts_sent": run.alerts_sent,
                    "errors": error_list,
                }
            )

    engine.dispose()
    return results


@st.cache_data(ttl=300)
def get_alert_stats(_db_url: str) -> dict[str, Any]:
    """Fetch alert statistics for the admin panel.

    Args:
        _db_url: Database URL string.

    Returns:
        Dict with alert stats: total_24h, total_7d, by_channel, success_rate,
        active_subscriptions.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    stats: dict[str, Any] = {
        "total_24h": 0,
        "total_7d": 0,
        "by_channel": {},
        "success_rate": 0.0,
        "active_subscriptions": 0,
    }

    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    with Session(engine) as session:
        # Alerts last 24h
        count_24h = session.execute(
            select(func.count(AlertSent.id)).where(AlertSent.sent_at >= day_ago)
        ).scalar_one_or_none()
        stats["total_24h"] = count_24h or 0

        # Alerts last 7d
        count_7d = session.execute(
            select(func.count(AlertSent.id)).where(AlertSent.sent_at >= week_ago)
        ).scalar_one_or_none()
        stats["total_7d"] = count_7d or 0

        # By channel (last 7d)
        channel_rows = session.execute(
            select(AlertSent.channel, func.count(AlertSent.id))
            .where(AlertSent.sent_at >= week_ago)
            .group_by(AlertSent.channel)
        ).all()
        stats["by_channel"] = {row[0]: row[1] for row in channel_rows}

        # Delivery success rate (last 7d)
        total_sent = stats["total_7d"]
        if total_sent > 0:
            delivered_count = session.execute(
                select(func.count(AlertSent.id)).where(
                    AlertSent.sent_at >= week_ago,
                    AlertSent.delivered.is_(True),
                )
            ).scalar_one_or_none()
            stats["success_rate"] = ((delivered_count or 0) / total_sent) * 100

        # Active subscriptions
        active_subs = session.execute(
            select(func.count(AlertSubscription.id)).where(AlertSubscription.is_active.is_(True))
        ).scalar_one_or_none()
        stats["active_subscriptions"] = active_subs or 0

    engine.dispose()
    return stats


@st.cache_data(ttl=300)
def get_system_info(_db_url: str, db_path: str) -> dict[str, Any]:
    """Fetch system information for the admin panel.

    Args:
        _db_url: Database URL string.
        db_path: Path to the SQLite database file.

    Returns:
        Dict with system info: db_size_mb, total_hotspots, total_events,
        active_events, environment.
    """
    from sqlalchemy import create_engine

    engine = create_engine(_db_url)
    info: dict[str, Any] = {
        "db_size_mb": 0.0,
        "total_hotspots": 0,
        "total_events": 0,
        "active_events": 0,
        "environment": os.environ.get("ENVIRONMENT", "dev"),
    }

    # Database file size
    db_file = Path(db_path)
    if db_file.exists():
        info["db_size_mb"] = db_file.stat().st_size / (1024 * 1024)

    with Session(engine) as session:
        info["total_hotspots"] = (
            session.execute(select(func.count(Hotspot.id))).scalar_one_or_none() or 0
        )
        info["total_events"] = (
            session.execute(select(func.count(FireEvent.id))).scalar_one_or_none() or 0
        )
        info["active_events"] = (
            session.execute(
                select(func.count(FireEvent.id)).where(FireEvent.is_active.is_(True))
            ).scalar_one_or_none()
            or 0
        )

    engine.dispose()
    return info


# ---------------------------------------------------------------------------
# Page rendering
# ---------------------------------------------------------------------------


def _render_pipeline_health(pipeline_runs: list[dict[str, Any]]) -> None:
    """Render the pipeline health section."""
    st.subheader("Salud del pipeline")

    if not pipeline_runs:
        st.info("No hay ejecuciones del pipeline registradas.")
        return

    # Last successful run
    last_success = None
    for run in pipeline_runs:
        if run["status"] == "success":
            last_success = run
            break

    if last_success:
        st.success(f"Ultima ejecucion exitosa: {last_success['started_at']}")
    else:
        st.warning("No hay ejecuciones exitosas recientes.")

    # Pipeline runs table
    rows = []
    for run in pipeline_runs:
        status_emoji = _STATUS_COLORS.get(run["status"], "\u26aa")
        duration = f"{run['duration_ms']}ms" if run["duration_ms"] else "N/A"
        error_count = len(run["errors"]) if run["errors"] else 0
        error_display = f"{error_count} errores" if error_count > 0 else ""

        rows.append(
            {
                "Fecha": run["started_at"],
                "Duracion": duration,
                "Estado": f"{status_emoji} {run['status'].upper()}",
                "Hotspots": run["hotspots_fetched"],
                "Nuevos": run["new_hotspots"],
                "Eventos creados": run["events_created"],
                "Alertas": run["alerts_sent"],
                "Errores": error_display,
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Placeholder button for manual run
    st.button(
        "Ejecutar ciclo ahora",
        disabled=True,
        help="Proximamente: ejecucion manual del pipeline",
    )


def _render_api_health(pipeline_runs: list[dict[str, Any]]) -> None:
    """Render API health section based on pipeline run errors."""
    st.subheader("Estado de APIs externas")

    data_sources = ["FIRMS", "Open-Meteo", "Overpass"]

    # Check errors across recent runs
    source_status: dict[str, str] = {}
    for source in data_sources:
        has_recent_error = False
        source_lower = source.lower()
        for run in pipeline_runs[:5]:
            for error in run.get("errors", []):
                if isinstance(error, str) and source_lower in error.lower():
                    has_recent_error = True
                    break
            if has_recent_error:
                break
        source_status[source] = "error" if has_recent_error else "ok"

    cols = st.columns(len(data_sources))
    for i, source in enumerate(data_sources):
        with cols[i]:
            status = source_status[source]
            if status == "ok":
                st.success(f"{source}: Operativo")
            else:
                st.error(f"{source}: Error reciente")


def _render_alert_stats(stats: dict[str, Any]) -> None:
    """Render alert statistics section."""
    st.subheader("Estadisticas de alertas")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.metric("Alertas (24h)", stats["total_24h"])

    with col2:
        st.metric("Alertas (7 dias)", stats["total_7d"])

    with col3:
        st.metric("Tasa de entrega", f"{stats['success_rate']:.1f}%")

    with col4:
        st.metric("Suscripciones activas", stats["active_subscriptions"])

    # Channel breakdown
    by_channel = stats.get("by_channel", {})
    if by_channel:
        st.markdown("**Alertas por canal (ultimos 7 dias):**")
        channel_rows = []
        for channel, count in by_channel.items():
            channel_display = {
                "telegram": "Telegram",
                "whatsapp": "WhatsApp",
                "email": "Email",
            }.get(channel, channel)
            channel_rows.append({"Canal": channel_display, "Cantidad": count})
        df = pd.DataFrame(channel_rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No hay alertas enviadas en los ultimos 7 dias.")


def _render_system_info(info: dict[str, Any]) -> None:
    """Render system information section."""
    st.subheader("Informacion del sistema")

    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        st.metric("Base de datos", f"{info['db_size_mb']:.2f} MB")

    with col2:
        st.metric("Total hotspots", info["total_hotspots"])

    with col3:
        st.metric("Total eventos", info["total_events"])

    with col4:
        st.metric("Eventos activos", info["active_events"])

    with col5:
        env_display = {
            "dev": "Desarrollo",
            "staging": "Staging",
            "prod": "Produccion",
        }.get(info["environment"], info["environment"])
        st.metric("Entorno", env_display)


def render_admin_page(db_url: str, db_path: str) -> None:
    """Render the admin panel page.

    Args:
        db_url: SQLAlchemy database URL string.
        db_path: Path to the SQLite database file.
    """
    st.title("Panel de administracion")

    # Authentication gate
    if not _check_admin_auth():
        return

    # Logout button
    if st.button("Cerrar sesion"):
        st.session_state["admin_authenticated"] = False
        st.rerun()

    st.divider()

    # Pipeline health
    pipeline_runs = get_pipeline_runs(_db_url=db_url)
    _render_pipeline_health(pipeline_runs)

    st.divider()

    # API health
    _render_api_health(pipeline_runs)

    st.divider()

    # Alert stats
    alert_stats = get_alert_stats(_db_url=db_url)
    _render_alert_stats(alert_stats)

    st.divider()

    # System info
    system_info = get_system_info(_db_url=db_url, db_path=db_path)
    _render_system_info(system_info)
