"""Admin panel page -- password-protected system health dashboard.

Premium dark-themed admin panel with pipeline health, API status,
alert statistics, and system information. Uses synchronous SQLAlchemy
and the FireSentinel design system theme components.

All user-facing text in SPANISH. Code and variable names in English.
"""

from __future__ import annotations

import hmac
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

from firesentinel.config import get_settings
from firesentinel.dashboard.theme import (
    COLORS,
    render_card_container,
    render_kpi_row,
    render_metric_card,
    render_section_header,
)

# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

_STATUS_MAP: dict[str, str] = {
    "success": "online",
    "partial": "warning",
    "failed": "error",
}

_STATUS_LABELS_ES: dict[str, str] = {
    "success": "Exitoso",
    "partial": "Parcial",
    "failed": "Fallido",
}

_API_ICONS: dict[str, str] = {
    "FIRMS": "satellite_alt",
    "Open-Meteo": "thermostat",
    "Overpass": "map",
}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


def _render_login_form() -> bool:
    """Render the styled admin login form.

    Returns:
        True if the user is authenticated, False otherwise.
    """
    if st.session_state.get("admin_authenticated", False):
        return True

    settings = get_settings()
    admin_password = settings.admin_password

    if not admin_password:
        st.markdown(
            """
            <div style="display:flex;justify-content:center;padding:80px 0;">
                <div style="
                    background:rgba(30,41,59,0.5);
                    backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
                    border:1px solid rgba(255,255,255,0.08);
                    border-radius:16px;padding:40px;
                    max-width:400px;width:100%;text-align:center;
                ">
                    <span class="material-icons-round"
                          style="font-size:48px;color:#EF4444;margin-bottom:16px;display:block;">
                        error
                    </span>
                    <h3 style="font-size:18px;font-weight:700;color:#F1F5F9;margin:0 0 8px 0;">
                        Panel no disponible</h3>
                    <p style="font-size:13px;color:#94A3B8;margin:0;">
                        La variable ADMIN_PASSWORD no esta configurada.
                        Contacte al administrador del sistema.</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        return False

    # Centered login card
    st.markdown(
        """
        <div style="display:flex;justify-content:center;padding:60px 0 20px 0;">
            <div style="
                background:rgba(30,41,59,0.5);
                backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
                border:1px solid rgba(255,255,255,0.08);
                border-radius:16px;padding:40px 36px 28px 36px;
                max-width:380px;width:100%;text-align:center;
            ">
                <span class="material-icons-round"
                      style="font-size:44px;color:#FF6B35;margin-bottom:12px;display:block;">
                    lock
                </span>
                <h3 style="font-size:20px;font-weight:700;color:#F1F5F9;margin:0 0 4px 0;">
                    Panel de Administracion</h3>
                <p style="font-size:13px;color:#94A3B8;margin:0 0 24px 0;">
                    Ingrese la contrasena de administrador</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # Password input and button (centered with columns)
    _left, center, _right = st.columns([1, 2, 1])
    with center:
        password = st.text_input(
            "Contrasena",
            type="password",
            key="admin_password_input",
            label_visibility="collapsed",
            placeholder="Contrasena de administrador",
        )

        # Rate limiting: track failed attempts
        if "auth_failures" not in st.session_state:
            st.session_state["auth_failures"] = 0
            st.session_state["auth_lockout_until"] = 0.0

        locked_out = time.time() < st.session_state["auth_lockout_until"]
        if locked_out:
            remaining = int(st.session_state["auth_lockout_until"] - time.time())
            st.warning(f"Demasiados intentos. Espere {remaining}s.")

        if st.button("Ingresar", use_container_width=True, type="primary", disabled=locked_out):
            # Timing-safe comparison to prevent timing attacks
            if hmac.compare_digest(password, admin_password):
                st.session_state["admin_authenticated"] = True
                st.session_state["auth_failures"] = 0
                st.rerun()
            else:
                st.session_state["auth_failures"] += 1
                failures = st.session_state["auth_failures"]
                if failures >= 5:
                    # Lock out for 5 minutes after 5 failures
                    st.session_state["auth_lockout_until"] = time.time() + 300
                    st.error("Demasiados intentos fallidos. Bloqueado por 5 minutos.")
                else:
                    st.error(f"Contrasena incorrecta. Intento {failures}/5.")

    return False


# ---------------------------------------------------------------------------
# Data queries (cached)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=300)
def _get_pipeline_runs(_db_url: str, limit: int = 20) -> list[dict[str, Any]]:
    """Fetch recent pipeline runs from the database.

    Args:
        _db_url: Database URL string (underscore prefix for Streamlit caching).
        limit: Maximum number of runs to return.

    Returns:
        List of pipeline run dicts, most recent first.
    """
    engine = create_engine(_db_url)
    results: list[dict[str, Any]] = []

    try:
        with engine.connect() as conn:
            rows = (
                conn.execute(
                    text(
                        "SELECT started_at, completed_at, status, hotspots_fetched, "
                        "new_hotspots, events_created, events_updated, alerts_sent, "
                        "errors, duration_ms "
                        "FROM pipeline_runs ORDER BY started_at DESC LIMIT :limit"
                    ),
                    {"limit": limit},
                )
                .mappings()
                .all()
            )

        for row in rows:
            # Parse errors field
            errors_raw = row["errors"]
            error_list: list[str] = []
            if isinstance(errors_raw, list):
                error_list = errors_raw
            elif isinstance(errors_raw, dict):
                error_list = errors_raw.get("errors", [])
            elif isinstance(errors_raw, str):
                import json

                try:
                    parsed = json.loads(errors_raw)
                    if isinstance(parsed, list):
                        error_list = parsed
                    elif isinstance(parsed, dict):
                        error_list = parsed.get("errors", [])
                except (json.JSONDecodeError, TypeError):
                    error_list = [errors_raw] if errors_raw else []

            results.append(
                {
                    "started_at": row["started_at"],
                    "completed_at": row["completed_at"],
                    "status": row["status"] or "unknown",
                    "hotspots_fetched": row["hotspots_fetched"] or 0,
                    "new_hotspots": row["new_hotspots"] or 0,
                    "events_created": row["events_created"] or 0,
                    "events_updated": row["events_updated"] or 0,
                    "alerts_sent": row["alerts_sent"] or 0,
                    "errors": error_list,
                    "duration_ms": row["duration_ms"],
                }
            )
    except Exception:
        pass
    finally:
        engine.dispose()

    return results


@st.cache_data(ttl=300)
def _get_alert_stats(_db_url: str) -> dict[str, Any]:
    """Fetch alert statistics for the admin panel.

    Args:
        _db_url: Database URL string.

    Returns:
        Dict with keys: total_24h, total_7d, success_rate, active_subscriptions.
    """
    engine = create_engine(_db_url)
    stats: dict[str, Any] = {
        "total_24h": 0,
        "total_7d": 0,
        "success_rate": 0.0,
        "active_subscriptions": 0,
    }

    now = datetime.utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    try:
        with engine.connect() as conn:
            # Alerts in last 24h
            row_24h = (
                conn.execute(
                    text("SELECT COUNT(*) AS cnt FROM alerts_sent WHERE sent_at >= :since"),
                    {"since": day_ago.isoformat()},
                )
                .mappings()
                .first()
            )
            stats["total_24h"] = row_24h["cnt"] if row_24h else 0

            # Alerts in last 7d
            row_7d = (
                conn.execute(
                    text("SELECT COUNT(*) AS cnt FROM alerts_sent WHERE sent_at >= :since"),
                    {"since": week_ago.isoformat()},
                )
                .mappings()
                .first()
            )
            stats["total_7d"] = row_7d["cnt"] if row_7d else 0

            # Delivery success rate (last 7d)
            total_sent = stats["total_7d"]
            if total_sent > 0:
                row_delivered = (
                    conn.execute(
                        text(
                            "SELECT COUNT(*) AS cnt FROM alerts_sent "
                            "WHERE sent_at >= :since AND delivered = 1"
                        ),
                        {"since": week_ago.isoformat()},
                    )
                    .mappings()
                    .first()
                )
                delivered = row_delivered["cnt"] if row_delivered else 0
                stats["success_rate"] = (delivered / total_sent) * 100

            # Active subscriptions
            row_subs = (
                conn.execute(
                    text("SELECT COUNT(*) AS cnt FROM alert_subscriptions WHERE is_active = 1")
                )
                .mappings()
                .first()
            )
            stats["active_subscriptions"] = row_subs["cnt"] if row_subs else 0

    except Exception:
        pass
    finally:
        engine.dispose()

    return stats


@st.cache_data(ttl=300)
def _get_system_info(_db_url: str, _db_path: str) -> dict[str, Any]:
    """Fetch system information for the admin panel.

    Args:
        _db_url: Database URL string.
        _db_path: Path to the SQLite database file.

    Returns:
        Dict with keys: db_size_mb, total_hotspots, total_events,
        active_events, environment.
    """
    settings = get_settings()
    engine = create_engine(_db_url)
    info: dict[str, Any] = {
        "db_size_mb": 0.0,
        "total_hotspots": 0,
        "total_events": 0,
        "active_events": 0,
        "environment": settings.environment,
    }

    # Database file size
    db_file = Path(_db_path)
    if db_file.exists():
        info["db_size_mb"] = round(db_file.stat().st_size / (1024 * 1024), 2)

    try:
        with engine.connect() as conn:
            row_hs = conn.execute(text("SELECT COUNT(*) AS cnt FROM hotspots")).mappings().first()
            info["total_hotspots"] = row_hs["cnt"] if row_hs else 0

            row_ev = (
                conn.execute(text("SELECT COUNT(*) AS cnt FROM fire_events")).mappings().first()
            )
            info["total_events"] = row_ev["cnt"] if row_ev else 0

            row_active = (
                conn.execute(text("SELECT COUNT(*) AS cnt FROM fire_events WHERE is_active = 1"))
                .mappings()
                .first()
            )
            info["active_events"] = row_active["cnt"] if row_active else 0
    except Exception:
        pass
    finally:
        engine.dispose()

    return info


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


def _render_pipeline_health(pipeline_runs: list[dict[str, Any]]) -> None:
    """Render Section A: Pipeline Health with status card and runs table."""
    render_section_header("Salud del Pipeline", "monitor_heart")

    if not pipeline_runs:
        st.info("No hay ejecuciones del pipeline registradas.")
        return

    # Latest run summary card
    last_run = pipeline_runs[0]
    status_key = _STATUS_MAP.get(last_run["status"], "error")

    # Format timestamps
    started = last_run["started_at"]
    if isinstance(started, str):
        started_display = started[:16].replace("T", " ")
    elif isinstance(started, datetime):
        started_display = started.strftime("%Y-%m-%d %H:%M")
    else:
        started_display = "N/A"

    duration_display = (
        f"{last_run['duration_ms']}ms" if last_run["duration_ms"] is not None else "N/A"
    )

    # Build summary card HTML
    status_color = {
        "online": COLORS["status_online"],
        "warning": COLORS["status_warning"],
        "error": COLORS["status_error"],
    }.get(status_key, COLORS["status_error"])

    summary_html = f"""
    <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
        <div style="display:flex;align-items:center;gap:8px;">
            <div style="position:relative;width:10px;height:10px;">
                <div style="position:absolute;inset:0;background:{status_color};
                    border-radius:50%;"></div>
                <div style="position:absolute;inset:-2px;background:{status_color};
                    border-radius:50%;opacity:0.4;
                    animation:status-pulse 2s ease-in-out infinite;"></div>
            </div>
            <span style="font-size:14px;font-weight:600;color:#F1F5F9;">
                Pipeline {_STATUS_LABELS_ES.get(last_run["status"], last_run["status"]).upper()}
            </span>
        </div>
        <span style="font-size:12px;color:#94A3B8;">
            Ultima ejecucion: {started_display}
        </span>
        <span style="font-size:12px;color:#64748B;">
            Duracion: {duration_display}
        </span>
        <span style="font-size:12px;color:#94A3B8;">
            Focos: {last_run["hotspots_fetched"]} ({last_run["new_hotspots"]} nuevos)
        </span>
    </div>
    """
    render_card_container(summary_html, accent=status_color)

    st.markdown('<div style="height:12px;"></div>', unsafe_allow_html=True)

    # Recent pipeline runs table
    rows_data: list[dict[str, Any]] = []
    for run in pipeline_runs:
        run_started = run["started_at"]
        if isinstance(run_started, str):
            fecha = run_started[:16].replace("T", " ")
        elif isinstance(run_started, datetime):
            fecha = run_started.strftime("%Y-%m-%d %H:%M")
        else:
            fecha = "N/A"

        dur = f"{run['duration_ms']}ms" if run["duration_ms"] is not None else "N/A"
        status_label = _STATUS_LABELS_ES.get(run["status"], run["status"])

        rows_data.append(
            {
                "Fecha": fecha,
                "Duracion": dur,
                "Estado": status_label.upper(),
                "Focos": run["hotspots_fetched"],
                "Nuevos": run["new_hotspots"],
                "Eventos": run["events_created"],
                "Alertas": run["alerts_sent"],
            }
        )

    if rows_data:
        df = pd.DataFrame(rows_data)
        st.dataframe(df, use_container_width=True, hide_index=True)


def _render_api_health(pipeline_runs: list[dict[str, Any]]) -> None:
    """Render Section B: API Health with status cards for each service."""
    render_section_header("Estado de APIs", "cloud")

    data_sources = ["FIRMS", "Open-Meteo", "Overpass"]

    # Determine status per API based on recent pipeline errors
    source_status: dict[str, str] = {}
    source_last_ok: dict[str, str] = {}

    for source in data_sources:
        has_recent_error = False
        last_ok_time = ""
        source_lower = source.lower()

        for run in pipeline_runs[:5]:
            # Check if this source had an error
            run_has_error = False
            for error in run.get("errors", []):
                if isinstance(error, str) and source_lower in error.lower():
                    run_has_error = True
                    break

            if not run_has_error and run["status"] in ("success", "partial"):
                started = run["started_at"]
                if isinstance(started, str):
                    last_ok_time = started[:16].replace("T", " ")
                elif isinstance(started, datetime):
                    last_ok_time = started.strftime("%Y-%m-%d %H:%M")
                break

            if run_has_error:
                has_recent_error = True

        source_status[source] = "error" if has_recent_error else "online"
        source_last_ok[source] = last_ok_time or "N/A"

    cols = st.columns(3)
    for i, source in enumerate(data_sources):
        with cols[i]:
            status = source_status[source]
            accent = COLORS["status_online"] if status == "online" else COLORS["status_error"]
            icon = _API_ICONS.get(source, "cloud")
            status_label = "Operativo" if status == "online" else "Error reciente"

            card_html = f"""
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
                <span class="material-icons-round"
                      style="font-size:20px;color:{accent};">{icon}</span>
                <span style="font-size:14px;font-weight:600;color:#F1F5F9;">
                    {source}</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
                <div style="position:relative;width:8px;height:8px;">
                    <div style="position:absolute;inset:0;background:{accent};
                        border-radius:50%;"></div>
                    <div style="position:absolute;inset:-2px;background:{accent};
                        border-radius:50%;opacity:0.4;
                        animation:status-pulse 2s ease-in-out infinite;"></div>
                </div>
                <span style="font-size:12px;color:#94A3B8;">{status_label}</span>
            </div>
            <p style="font-size:11px;color:#64748B;margin:0;">
                Ultimo OK: {source_last_ok[source]}</p>
            """
            render_card_container(card_html, accent=accent)


def _render_alert_stats(alert_stats: dict[str, Any]) -> None:
    """Render Section C: Alert Statistics with metric cards."""
    render_section_header("Estadisticas de Alertas", "notifications")

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        render_metric_card(
            icon="notifications_active",
            label="Alertas 24h",
            value=str(alert_stats["total_24h"]),
            accent=COLORS["fire_warning"],
        )

    with col2:
        render_metric_card(
            icon="date_range",
            label="Alertas 7d",
            value=str(alert_stats["total_7d"]),
            accent=COLORS["fire_high"],
        )

    with col3:
        render_metric_card(
            icon="verified",
            label="Tasa de entrega",
            value=f"{alert_stats['success_rate']:.1f}%",
            accent=COLORS["status_online"],
        )

    with col4:
        render_metric_card(
            icon="people",
            label="Suscripciones activas",
            value=str(alert_stats["active_subscriptions"]),
            accent=COLORS["severity_low"],
        )


def _render_system_info(system_info: dict[str, Any]) -> None:
    """Render Section D: System Information with KPI row."""
    render_section_header("Sistema", "storage")

    env_display = {
        "dev": "Desarrollo",
        "staging": "Staging",
        "prod": "Produccion",
    }.get(system_info["environment"], system_info["environment"])

    render_kpi_row(
        [
            {
                "label": "Base de datos",
                "value": f"{system_info['db_size_mb']:.2f} MB",
                "icon": "storage",
                "color": COLORS["text_accent"],
            },
            {
                "label": "Total hotspots",
                "value": f"{system_info['total_hotspots']:,}",
                "icon": "whatshot",
                "color": COLORS["fire_high"],
            },
            {
                "label": "Total eventos",
                "value": f"{system_info['total_events']:,}",
                "icon": "layers",
                "color": COLORS["fire_warning"],
            },
            {
                "label": "Eventos activos",
                "value": str(system_info["active_events"]),
                "icon": "local_fire_department",
                "color": COLORS["fire_critical"],
            },
            {
                "label": "Entorno",
                "value": env_display,
                "icon": "dns",
                "color": COLORS["text_secondary"],
            },
        ]
    )


# ---------------------------------------------------------------------------
# Main page render
# ---------------------------------------------------------------------------


def render_admin_page() -> None:
    """Render the admin panel page with authentication gate.

    Uses get_settings() for admin password and database path.
    All data queries are cached with a 5-minute TTL.
    """
    # Authentication gate
    if not _render_login_form():
        return

    # Logout button (top-right aligned)
    _cols_top = st.columns([8, 1])
    with _cols_top[1]:
        if st.button("Cerrar sesion", key="admin_logout"):
            st.session_state["admin_authenticated"] = False
            st.rerun()

    # Build database URL
    settings = get_settings()
    db_url = f"sqlite:///{settings.db_path}"
    db_path = settings.db_path

    # Fetch all data
    pipeline_runs = _get_pipeline_runs(_db_url=db_url)
    alert_stats = _get_alert_stats(_db_url=db_url)
    system_info = _get_system_info(_db_url=db_url, _db_path=db_path)

    # Section A: Pipeline Health
    _render_pipeline_health(pipeline_runs)

    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

    # Section B: API Health
    _render_api_health(pipeline_runs)

    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

    # Section C: Alert Statistics
    _render_alert_stats(alert_stats)

    st.markdown('<div style="height:8px;"></div>', unsafe_allow_html=True)

    # Section D: System Information
    _render_system_info(system_info)
