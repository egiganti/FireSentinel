"""Chart components for the FireSentinel dashboard.

Renders intent breakdowns, severity distributions, and timeline charts
using Streamlit's native charting (no Plotly dependency).

All user-facing text in SPANISH. Code and variable names in English.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd
import streamlit as st

from firesentinel.core.types import Severity

# ---------------------------------------------------------------------------
# Signal name translations (scoring signal -> Spanish label)
# ---------------------------------------------------------------------------

_SIGNAL_NAMES_ES: dict[str, str] = {
    "lightning": "Ausencia de rayos",
    "road": "Proximidad a caminos",
    "night": "Ignicion nocturna",
    "history": "Historial de incendios",
    "multi_point": "Multiples focos",
    "dry_conditions": "Condiciones secas",
}

# Default max weights from monitoring.yml
_DEFAULT_MAX_WEIGHTS: dict[str, int] = {
    "lightning": 25,
    "road": 20,
    "night": 20,
    "history": 15,
    "multi_point": 10,
    "dry_conditions": 10,
}

# Severity label translations
_SEVERITY_LABEL_ES: dict[str, str] = {
    Severity.LOW.value: "Baja",
    Severity.MEDIUM.value: "Media",
    Severity.HIGH.value: "Alta",
    Severity.CRITICAL.value: "Critica",
}

# Severity display colors
_SEVERITY_COLORS: dict[str, str] = {
    Severity.LOW.value: "#2ecc71",
    Severity.MEDIUM.value: "#f1c40f",
    Severity.HIGH.value: "#e67e22",
    Severity.CRITICAL.value: "#e74c3c",
}


def intent_breakdown_chart(breakdown: dict[str, Any] | None) -> None:
    """Render a horizontal bar chart showing each intent signal score vs max.

    Args:
        breakdown: Dict from FireEvent.intent_breakdown JSON column with keys
            like 'lightning', 'road', 'night', etc.
    """
    if breakdown is None:
        st.info("No hay datos de intencionalidad disponibles.")
        return

    signal_keys = ["lightning", "road", "night", "history", "multi_point", "dry_conditions"]

    rows = []
    for key in signal_keys:
        score = breakdown.get(key, 0)
        max_score = _DEFAULT_MAX_WEIGHTS.get(key, 0)
        label = _SIGNAL_NAMES_ES.get(key, key)
        rows.append(
            {
                "Senal": label,
                "Puntaje": score,
                "Maximo": max_score,
            }
        )

    df = pd.DataFrame(rows)

    st.dataframe(
        df.style.format({"Puntaje": "{:.0f}", "Maximo": "{:.0f}"}).bar(
            subset=["Puntaje"],
            color="#e74c3c",
            vmin=0,
            vmax=25,
        ),
        use_container_width=True,
        hide_index=True,
    )

    # Summary line
    active = breakdown.get("active_signals", 0)
    total = breakdown.get("total_signals", 6)
    st.caption(f"Basado en {active}/{total} senales")


def severity_distribution(events: list[dict[str, Any]]) -> None:
    """Render a bar chart of fire events grouped by severity level.

    Args:
        events: List of fire event dicts, each with a 'severity' key.
    """
    if not events:
        st.info("No hay datos para mostrar la distribucion de severidad.")
        return

    severity_counts: Counter[str] = Counter()
    for ev in events:
        sev = ev.get("severity", "medium")
        label = _SEVERITY_LABEL_ES.get(sev, sev)
        severity_counts[label] += 1

    # Build DataFrame in severity order
    ordered_labels = ["Baja", "Media", "Alta", "Critica"]
    rows = []
    for label in ordered_labels:
        count = severity_counts.get(label, 0)
        if count > 0:
            rows.append({"Severidad": label, "Cantidad": count})

    if not rows:
        st.info("No hay datos para mostrar la distribucion de severidad.")
        return

    df = pd.DataFrame(rows)
    st.bar_chart(df, x="Severidad", y="Cantidad")


def timeline_chart(events: list[dict[str, Any]]) -> None:
    """Render a line chart showing fire event detections over time.

    Args:
        events: List of fire event dicts, each with a 'first_detected_at' key
            (datetime string or datetime object).
    """
    if not events:
        st.info("No hay datos para mostrar la linea de tiempo.")
        return

    date_counts: Counter[str] = Counter()
    for ev in events:
        detected = ev.get("first_detected_at")
        if detected is None:
            continue
        date_str = detected[:10] if isinstance(detected, str) else str(detected)[:10]
        date_counts[date_str] += 1

    if not date_counts:
        st.info("No hay datos para mostrar la linea de tiempo.")
        return

    rows = [{"Fecha": d, "Eventos": c} for d, c in sorted(date_counts.items())]
    df = pd.DataFrame(rows)
    df["Fecha"] = pd.to_datetime(df["Fecha"])
    df = df.set_index("Fecha")
    st.line_chart(df)
