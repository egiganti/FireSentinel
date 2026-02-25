"""FireSentinel Patagonia -- Streamlit dashboard entry point.

Main application with sidebar navigation, filters, and page routing.
Uses synchronous SQLAlchemy for Streamlit compatibility.

All user-facing text in SPANISH. Code and variable names in English.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

import streamlit as st

st.set_page_config(
    page_title="FireSentinel Patagonia",
    page_icon="\U0001f525",
    layout="wide",
    initial_sidebar_state="expanded",
)

from firesentinel.dashboard.pages.admin import render_admin_page  # noqa: E402
from firesentinel.dashboard.pages.detail import render_detail_page  # noqa: E402
from firesentinel.dashboard.pages.map import render_map_page  # noqa: E402

# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

_DB_PATH = os.environ.get("DB_PATH", "./data/firesentinel.db")


@st.cache_resource
def get_db_url() -> str:
    """Create a synchronous SQLAlchemy database URL for the dashboard.

    Streamlit does not support async engines natively, so we use a sync
    SQLite connection string. The path is resolved from the DB_PATH
    environment variable or defaults to ./data/firesentinel.db.

    Returns:
        SQLAlchemy database URL string (sync SQLite).
    """
    return f"sqlite:///{_DB_PATH}"


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


def render_sidebar() -> dict:
    """Render the sidebar with navigation and filters.

    Returns:
        Dict with filter values: page, date_from, date_to, severities,
        min_intent, provinces.
    """
    with st.sidebar:
        st.title("\U0001f525 FireSentinel Patagonia")
        st.caption("Deteccion de incendios e intencionalidad")

        st.divider()

        # Navigation
        st.subheader("Navegacion")
        page_options = {
            "Mapa": "map",
            "Detalle de Evento": "detail",
            "Panel Admin": "admin",
        }

        # Check if page was set programmatically (e.g. from table click)
        current_page = st.session_state.get("page", "map")

        # Find display name for current page
        current_display = "Mapa"
        for display_name, page_key in page_options.items():
            if page_key == current_page:
                current_display = display_name
                break

        selected_display = st.radio(
            "Seccion",
            options=list(page_options.keys()),
            index=list(page_options.keys()).index(current_display),
            label_visibility="collapsed",
        )
        selected_page = page_options[selected_display]
        st.session_state["page"] = selected_page

        st.divider()

        # Filters (visible on map page)
        st.subheader("Filtros")

        # Date range
        default_end = date.today()
        default_start = default_end - timedelta(days=7)

        date_from = st.date_input(
            "Fecha desde",
            value=default_start,
            key="filter_date_from",
        )
        date_to = st.date_input(
            "Fecha hasta",
            value=default_end,
            key="filter_date_to",
        )

        # Severity filter
        severity_options = {
            "Baja": "low",
            "Media": "medium",
            "Alta": "high",
            "Critica": "critical",
        }
        selected_severities_display = st.multiselect(
            "Severidad",
            options=list(severity_options.keys()),
            default=list(severity_options.keys()),
            key="filter_severities",
        )
        selected_severities = [severity_options[s] for s in selected_severities_display]

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
        province_options = ["Chubut", "Rio Negro", "Neuquen", "Santa Cruz"]
        selected_provinces = st.multiselect(
            "Provincia",
            options=province_options,
            default=province_options,
            key="filter_provinces",
        )

        st.divider()

        # Footer
        now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        st.caption(f"Datos: NASA FIRMS | Ultima actualizacion: {now_str}")
        st.caption("FireSentinel Patagonia v0.1.0")

    return {
        "page": selected_page,
        "date_from": str(date_from),
        "date_to": str(date_to),
        "severities": selected_severities,
        "min_intent": min_intent,
        "provinces": selected_provinces,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Main dashboard entry point. Routes to the selected page."""
    db_url = get_db_url()
    filters = render_sidebar()

    page = st.session_state.get("page", "map")

    if page == "map":
        render_map_page(db_url=db_url, filters=filters)

    elif page == "detail":
        event_id = st.session_state.get("selected_event_id")

        # Also check URL query params
        params = st.query_params
        if "event_id" in params:
            event_id = params["event_id"]

        render_detail_page(db_url=db_url, event_id=event_id)

    elif page == "admin":
        render_admin_page(db_url=db_url, db_path=_DB_PATH)


if __name__ == "__main__":
    main()
else:
    # Streamlit runs this file as a module; ensure main() is called
    main()
