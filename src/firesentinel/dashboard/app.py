"""FireSentinel Patagonia -- Streamlit dashboard entry point.

Premium dark-themed fire monitoring dashboard with sidebar navigation,
filters, and page routing. Uses synchronous SQLAlchemy for Streamlit
compatibility.

All user-facing text in SPANISH. Code and variable names in English.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

import streamlit as st

st.set_page_config(
    page_title="FireSentinel Patagonia",
    page_icon="\U0001f525",
    layout="wide",
    initial_sidebar_state="expanded",
)

# CSS injection MUST be first after set_page_config
from firesentinel.dashboard.theme import (  # noqa: E402
    SEVERITY_LABELS_ES,
    inject_css,
    render_header,
)

inject_css()

from streamlit_autorefresh import st_autorefresh  # noqa: E402

from firesentinel.config import get_settings  # noqa: E402
from firesentinel.dashboard.pages.admin import render_admin_page  # noqa: E402
from firesentinel.dashboard.pages.detail import render_detail_page  # noqa: E402
from firesentinel.dashboard.pages.map import render_map_page  # noqa: E402

# Auto-refresh every 5 minutes
st_autorefresh(interval=300_000, key="main_refresh")

# ---------------------------------------------------------------------------
# Session state initialization
# ---------------------------------------------------------------------------

if "page" not in st.session_state:
    st.session_state["page"] = "map"
if "selected_event" not in st.session_state:
    st.session_state["selected_event"] = None

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

_PAGE_OPTIONS: dict[str, str] = {
    "\U0001f5fa\ufe0f  Mapa de Incendios": "map",
    "\U0001f50d  Detalle de Evento": "detail",
    "\u2699\ufe0f  Administracion": "admin",
}

# Reverse lookup: page key -> display name
_PAGE_DISPLAY: dict[str, str] = {v: k for k, v in _PAGE_OPTIONS.items()}

# Severity mapping: Spanish label -> English key
_SEVERITY_MAP: dict[str, str] = {v: k for k, v in SEVERITY_LABELS_ES.items()}


@st.cache_resource
def _get_db_url() -> str:
    """Build a synchronous SQLAlchemy database URL from settings.

    Returns:
        SQLAlchemy database URL string (sync SQLite).
    """
    settings = get_settings()
    return f"sqlite:///{settings.db_path}"


@st.cache_data(ttl=60)
def _get_last_scan_info(_db_url: str) -> str:
    """Fetch the last pipeline run timestamp for header display.

    Args:
        _db_url: Database URL string (underscore prefix for Streamlit caching).

    Returns:
        Formatted string with last scan info, or empty string if no runs.
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(_db_url)
    try:
        with engine.connect() as conn:
            row = (
                conn.execute(
                    text(
                        "SELECT started_at, status FROM pipeline_runs "
                        "ORDER BY started_at DESC LIMIT 1"
                    )
                )
                .mappings()
                .first()
            )
        if row:
            started = row["started_at"]
            if isinstance(started, str):
                ts = started[:16].replace("T", " ")
            else:
                ts = started.strftime("%Y-%m-%d %H:%M")
            return f"Ultimo escaneo: {ts} UTC"
    except Exception:
        pass
    finally:
        engine.dispose()
    return ""


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def _render_sidebar(db_url: str) -> dict[str, Any]:
    """Render the sidebar with logo, navigation, filters, and footer.

    Args:
        db_url: Database URL string for data queries.

    Returns:
        Dict with filter values: date_from, date_to, severities,
        min_intent, provinces.
    """
    with st.sidebar:
        # ------------------------------------------------------------------
        # Logo block
        # ------------------------------------------------------------------
        st.markdown(
            """
            <div style="display:flex;align-items:center;gap:12px;padding:8px 0 16px 0;">
                <div style="
                    width:40px;height:40px;
                    background:linear-gradient(135deg, #FF3B30, #FF6B35, #FFBA08);
                    border-radius:10px;
                    display:flex;align-items:center;justify-content:center;
                    box-shadow:0 4px 12px rgba(255,107,53,0.3);
                    flex-shrink:0;
                ">
                    <span class="material-icons-round"
                          style="font-size:22px;color:white;">
                        local_fire_department
                    </span>
                </div>
                <div>
                    <span style="font-size:16px;font-weight:700;color:#F1F5F9;
                        line-height:1.2;display:block;">FireSentinel</span>
                    <span style="font-size:10px;color:#64748B;background:#1E293B;
                        padding:1px 6px;border-radius:6px;font-weight:500;
                        letter-spacing:0.03em;">v0.1.0</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # ------------------------------------------------------------------
        # Navigation section
        # ------------------------------------------------------------------
        st.markdown(
            '<p style="font-size:10px;font-weight:600;color:#64748B;'
            'text-transform:uppercase;letter-spacing:0.08em;margin:12px 0 6px 0;">'
            "NAVEGACION</p>",
            unsafe_allow_html=True,
        )

        current_page = st.session_state.get("page", "map")
        current_display = _PAGE_DISPLAY.get(current_page, _PAGE_DISPLAY["map"])

        selected_display = st.radio(
            "Navegacion",
            options=list(_PAGE_OPTIONS.keys()),
            index=list(_PAGE_OPTIONS.keys()).index(current_display),
            label_visibility="collapsed",
            key="nav_radio",
        )
        selected_page = _PAGE_OPTIONS[selected_display]
        st.session_state["page"] = selected_page

        # ------------------------------------------------------------------
        # Filters section (only on map page)
        # ------------------------------------------------------------------
        filters: dict[str, Any] = {}

        if selected_page == "map":
            st.markdown(
                '<p style="font-size:10px;font-weight:600;color:#64748B;'
                "text-transform:uppercase;letter-spacing:0.08em;"
                'margin:20px 0 6px 0;">FILTROS</p>',
                unsafe_allow_html=True,
            )

            # Date range
            default_end = date.today()
            default_start = default_end - timedelta(days=7)

            date_range = st.date_input(
                "Rango de fechas",
                value=(default_start, default_end),
                key="filter_date_range",
            )

            # Parse date_input result (can be tuple or single date)
            if isinstance(date_range, list | tuple) and len(date_range) == 2:
                date_from, date_to = date_range
            elif isinstance(date_range, list | tuple):
                date_from = date_range[0]
                date_to = default_end
            else:
                date_from = date_range
                date_to = default_end

            # Severity filter (Spanish labels -> English keys internally)
            severity_labels_es = list(SEVERITY_LABELS_ES.values())
            selected_severity_labels = st.multiselect(
                "Severidad",
                options=severity_labels_es,
                default=severity_labels_es,
                key="filter_severities",
            )
            selected_severities = [
                _SEVERITY_MAP[label]
                for label in selected_severity_labels
                if label in _SEVERITY_MAP
            ]

            # Intentionality threshold
            min_intent = st.slider(
                "Intencionalidad minima",
                min_value=0,
                max_value=100,
                value=0,
                step=5,
                help=(
                    "Mostrar solo eventos con puntaje de intencionalidad "
                    "igual o superior a este valor. 0 = mostrar todos."
                ),
                key="filter_min_intent",
            )

            # Province filter
            province_options = [
                "Chubut",
                "Rio Negro",
                "Neuquen",
                "Santa Cruz",
                "Tierra del Fuego",
            ]
            selected_provinces = st.multiselect(
                "Provincia",
                options=province_options,
                default=province_options,
                key="filter_provinces",
            )

            filters = {
                "date_from": str(date_from),
                "date_to": str(date_to),
                "severities": selected_severities,
                "min_intent": min_intent,
                "provinces": selected_provinces,
            }

        # ------------------------------------------------------------------
        # Footer
        # ------------------------------------------------------------------
        env_label = get_settings().environment.upper()
        st.markdown(
            f"""
            <div style="position:absolute;bottom:20px;left:16px;right:16px;">
                <div style="border-top:1px solid #1E293B;padding-top:12px;">
                    <p style="font-size:10px;color:#64748B;margin:0 0 2px 0;">
                        Datos: NASA FIRMS / MODIS + VIIRS</p>
                    <p style="font-size:10px;color:#64748B;margin:0 0 2px 0;">
                        FireSentinel Patagonia v0.1.0</p>
                    <p style="font-size:10px;color:#475569;margin:0;">
                        Entorno: {env_label}</p>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    return filters


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Main dashboard entry point. Renders header, sidebar, and routes pages."""
    db_url = _get_db_url()

    # Header with last scan info
    last_scan = _get_last_scan_info(_db_url=db_url)
    render_header(last_scan_text=last_scan)

    # Sidebar navigation and filters
    filters = _render_sidebar(db_url)

    # Page routing
    page = st.session_state.get("page", "map")

    if page == "map":
        render_map_page(filters=filters)

    elif page == "detail":
        # Check URL query params for event_id override
        params = st.query_params
        if "event_id" in params:
            eid = str(params["event_id"])
            # Only accept valid UUIDs to prevent XSS
            if re.match(r"^[0-9a-fA-F-]{36}$", eid):
                st.session_state["selected_event"] = eid

        if st.session_state.get("selected_event") is None:
            # No event selected -- redirect to map
            st.session_state["page"] = "map"
            st.info("No hay evento seleccionado. Seleccione un evento desde el mapa.")
            render_map_page(filters=filters)
        else:
            render_detail_page()

    elif page == "admin":
        render_admin_page()


if __name__ == "__main__":
    main()
else:
    # Streamlit runs this file as a module; ensure main() is called
    main()
