"""Chart components for the FireSentinel dashboard.

Renders intent breakdowns, severity distributions, timeline charts, and
intent distribution donuts using Plotly with a premium dark theme.

All user-facing text in SPANISH. Code and variable names in English.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from firesentinel.core.types import IntentLabel, Severity

# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

_INTENT_COLORS: dict[str, str] = {
    IntentLabel.NATURAL.value: "#22C55E",
    IntentLabel.UNCERTAIN.value: "#FBBF24",
    IntentLabel.SUSPICIOUS.value: "#F97316",
    IntentLabel.LIKELY_INTENTIONAL.value: "#EF4444",
}

_SEVERITY_COLORS: dict[str, str] = {
    Severity.LOW.value: "#60A5FA",
    Severity.MEDIUM.value: "#FBBF24",
    Severity.HIGH.value: "#F97316",
    Severity.CRITICAL.value: "#EF4444",
}

_INTENT_LABEL_ES: dict[str, str] = {
    IntentLabel.NATURAL.value: "Natural",
    IntentLabel.UNCERTAIN.value: "Incierto",
    IntentLabel.SUSPICIOUS.value: "Sospechoso",
    IntentLabel.LIKELY_INTENTIONAL.value: "Probable intencional",
}

_SEVERITY_LABEL_ES: dict[str, str] = {
    Severity.LOW.value: "Baja",
    Severity.MEDIUM.value: "Media",
    Severity.HIGH.value: "Alta",
    Severity.CRITICAL.value: "Critica",
}

# Shared layout defaults
_TEXT_COLOR = "#94A3B8"
_GRID_COLOR = "#1E293B"
_FONT_FAMILY = "Inter, sans-serif"
_NO_MODE_BAR = {"displayModeBar": False}

# Signal name translations (scoring signal -> Spanish label)
_SIGNAL_NAMES_ES: dict[str, str] = {
    "lightning": "Sin rayos",
    "road": "Cercania a ruta",
    "night": "Ignicion nocturna",
    "history": "Repeticion historica",
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

# Signal keys in display order (bottom to top on horizontal bar)
_SIGNAL_KEYS = ["dry_conditions", "multi_point", "history", "night", "road", "lightning"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_layout(**overrides: Any) -> dict[str, Any]:
    """Return base Plotly layout kwargs for dark transparent charts."""
    layout: dict[str, Any] = {
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "font": {"family": _FONT_FAMILY, "color": _TEXT_COLOR, "size": 12},
        "margin": {"l": 0, "r": 0, "t": 30, "b": 0},
    }
    layout.update(overrides)
    return layout


def _score_to_color(score: float, max_score: float) -> str:
    """Interpolate from green (#22C55E) to red (#EF4444) based on score ratio."""
    if max_score <= 0:
        return "#22C55E"
    ratio = min(max(score / max_score, 0.0), 1.0)

    # Green -> Yellow -> Orange -> Red gradient
    if ratio <= 0.33:
        t = ratio / 0.33
        r = int(0x22 + (0xFB - 0x22) * t)
        g = int(0xC5 + (0xBF - 0xC5) * t)
        b = int(0x5E + (0x24 - 0x5E) * t)
    elif ratio <= 0.66:
        t = (ratio - 0.33) / 0.33
        r = int(0xFB + (0xF9 - 0xFB) * t)
        g = int(0xBF + (0x73 - 0xBF) * t)
        b = int(0x24 + (0x16 - 0x24) * t)
    else:
        t = (ratio - 0.66) / 0.34
        r = int(0xF9 + (0xEF - 0xF9) * t)
        g = int(0x73 + (0x44 - 0x73) * t)
        b = int(0x16 + (0x44 - 0x16) * t)

    return f"#{r:02X}{g:02X}{b:02X}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def intent_breakdown_chart(breakdown: dict[str, Any] | None) -> None:
    """Render a horizontal bar chart showing each intent signal score vs max.

    Args:
        breakdown: Dict from FireEvent.intent_breakdown JSON column with keys
            like 'lightning', 'road', 'night', etc. May be None.
    """
    if breakdown is None:
        st.info("No hay datos de intencionalidad disponibles.")
        return

    labels = [_SIGNAL_NAMES_ES.get(k, k) for k in _SIGNAL_KEYS]
    scores = [breakdown.get(k, 0) for k in _SIGNAL_KEYS]
    max_weights = [_DEFAULT_MAX_WEIGHTS.get(k, 25) for k in _SIGNAL_KEYS]
    bar_colors = [_score_to_color(s, m) for s, m in zip(scores, max_weights, strict=False)]

    fig = go.Figure()

    # Background reference bars (max weight)
    fig.add_trace(
        go.Bar(
            y=labels,
            x=max_weights,
            orientation="h",
            marker={"color": "rgba(30,41,59,0.5)"},
            hoverinfo="skip",
            showlegend=False,
            name="Maximo",
        )
    )

    # Actual score bars
    fig.add_trace(
        go.Bar(
            y=labels,
            x=scores,
            orientation="h",
            marker={"color": bar_colors},
            text=[f"{s}" for s in scores],
            textposition="outside",
            textfont={"color": _TEXT_COLOR, "size": 11},
            hovertemplate="%{y}: %{x}/%{customdata}<extra></extra>",
            customdata=max_weights,
            showlegend=False,
            name="Puntaje",
        )
    )

    fig.update_layout(
        **_base_layout(
            barmode="overlay",
            height=220,
            xaxis={
                "range": [0, 28],
                "showgrid": True,
                "gridcolor": _GRID_COLOR,
                "zeroline": False,
                "tickfont": {"color": _TEXT_COLOR, "size": 10},
            },
            yaxis={
                "tickfont": {"color": _TEXT_COLOR, "size": 11},
                "automargin": True,
            },
            margin={"l": 0, "r": 30, "t": 10, "b": 10},
        )
    )

    st.plotly_chart(fig, use_container_width=True, config=_NO_MODE_BAR)

    # Summary caption
    active = breakdown.get("active_signals", 0)
    total = breakdown.get("total_signals", 6)
    st.caption(f"Basado en {active}/{total} senales disponibles")


def severity_distribution_chart(events: list[dict[str, Any]]) -> None:
    """Render a bar chart of fire events grouped by severity level.

    Args:
        events: List of fire event dicts, each with a 'severity' key.
    """
    if not events:
        st.info("No hay datos para mostrar la distribucion de severidad.")
        return

    severity_order = [Severity.LOW.value, Severity.MEDIUM.value, Severity.HIGH.value, Severity.CRITICAL.value]
    label_order = [_SEVERITY_LABEL_ES[s] for s in severity_order]
    color_order = [_SEVERITY_COLORS[s] for s in severity_order]

    severity_counts: Counter[str] = Counter()
    for ev in events:
        sev = ev.get("severity", "medium")
        severity_counts[sev] += 1

    counts = [severity_counts.get(s, 0) for s in severity_order]

    # Only show bars with data, but keep order
    filtered_labels = []
    filtered_counts = []
    filtered_colors = []
    for label, count, color in zip(label_order, counts, color_order, strict=False):
        if count > 0:
            filtered_labels.append(label)
            filtered_counts.append(count)
            filtered_colors.append(color)

    if not filtered_labels:
        st.info("No hay datos para mostrar la distribucion de severidad.")
        return

    fig = go.Figure()

    fig.add_trace(
        go.Bar(
            x=filtered_labels,
            y=filtered_counts,
            marker={
                "color": filtered_colors,
                "line": {"width": 0},
            },
            text=filtered_counts,
            textposition="outside",
            textfont={"color": _TEXT_COLOR, "size": 12},
            hovertemplate="%{x}: %{y} eventos<extra></extra>",
            showlegend=False,
        )
    )

    fig.update_layout(
        **_base_layout(
            height=250,
            xaxis={
                "tickfont": {"color": _TEXT_COLOR, "size": 11},
            },
            yaxis={
                "showgrid": True,
                "gridcolor": _GRID_COLOR,
                "zeroline": False,
                "tickfont": {"color": _TEXT_COLOR, "size": 10},
            },
            margin={"l": 0, "r": 0, "t": 10, "b": 0},
        )
    )

    st.plotly_chart(fig, use_container_width=True, config=_NO_MODE_BAR)


def timeline_chart(events: list[dict[str, Any]]) -> None:
    """Render an area chart showing fire event detections over time.

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

    sorted_dates = sorted(date_counts.items())
    dates = [d for d, _ in sorted_dates]
    counts = [c for _, c in sorted_dates]

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=dates,
            y=counts,
            mode="lines",
            fill="tozeroy",
            line={"color": "#FF6B35", "width": 2},
            fillcolor="rgba(255,107,53,0.15)",
            hovertemplate="Fecha: %{x}<br>Eventos: %{y}<extra></extra>",
        )
    )

    fig.update_layout(
        **_base_layout(
            height=250,
            xaxis={
                "showgrid": True,
                "gridcolor": _GRID_COLOR,
                "tickfont": {"color": _TEXT_COLOR, "size": 10},
                "type": "category",
            },
            yaxis={
                "showgrid": True,
                "gridcolor": _GRID_COLOR,
                "zeroline": False,
                "tickfont": {"color": _TEXT_COLOR, "size": 10},
                "dtick": 1,
            },
            margin={"l": 0, "r": 0, "t": 10, "b": 0},
            showlegend=False,
        )
    )

    st.plotly_chart(fig, use_container_width=True, config=_NO_MODE_BAR)


def intent_distribution_chart(events: list[dict[str, Any]]) -> None:
    """Render a donut chart showing the distribution of intent labels.

    Args:
        events: List of fire event dicts, each with an 'intent_label' key.
    """
    if not events:
        st.info("No hay datos para mostrar la distribucion de intencionalidad.")
        return

    label_counts: Counter[str] = Counter()
    for ev in events:
        label = ev.get("intent_label")
        if label is not None:
            label_counts[label] += 1

    if not label_counts:
        st.info("No hay datos para mostrar la distribucion de intencionalidad.")
        return

    # Maintain consistent order
    intent_order = [
        IntentLabel.NATURAL.value,
        IntentLabel.UNCERTAIN.value,
        IntentLabel.SUSPICIOUS.value,
        IntentLabel.LIKELY_INTENTIONAL.value,
    ]

    labels = []
    values = []
    colors = []
    for intent_val in intent_order:
        count = label_counts.get(intent_val, 0)
        if count > 0:
            labels.append(_INTENT_LABEL_ES[intent_val])
            values.append(count)
            colors.append(_INTENT_COLORS[intent_val])

    total_count = sum(values)

    fig = go.Figure()

    fig.add_trace(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.6,
            marker={
                "colors": colors,
                "line": {"color": "#111827", "width": 2},
            },
            textinfo="label+percent",
            textfont={"color": "#E2E8F0", "size": 11},
            hovertemplate="%{label}: %{value} eventos (%{percent})<extra></extra>",
            showlegend=False,
        )
    )

    # Center annotation with total count
    fig.add_annotation(
        text=f"<b>{total_count}</b><br><span style='font-size:11px;color:#64748B'>eventos</span>",
        x=0.5,
        y=0.5,
        font={"size": 24, "color": "#F1F5F9", "family": _FONT_FAMILY},
        showarrow=False,
        xref="paper",
        yref="paper",
    )

    fig.update_layout(
        **_base_layout(
            height=280,
            margin={"l": 0, "r": 0, "t": 10, "b": 10},
            showlegend=False,
        )
    )

    st.plotly_chart(fig, use_container_width=True, config=_NO_MODE_BAR)
