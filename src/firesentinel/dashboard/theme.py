"""FireSentinel Design System — premium dark theme with fire accents.

Provides all CSS injection, color constants, and reusable HTML component
renderers for a cohesive, professional monitoring dashboard aesthetic.
All user-facing text in Spanish.
"""

from __future__ import annotations

import streamlit as st

# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

COLORS = {
    # Backgrounds
    "bg_primary": "#0A0E17",
    "bg_secondary": "#111827",
    "bg_surface": "#1E293B",
    "bg_hover": "#243044",
    # Fire accents
    "fire_critical": "#EF4444",
    "fire_high": "#F97316",
    "fire_warning": "#FBBF24",
    "fire_low": "#4ADE80",
    # Intent classification
    "intent_natural": "#22C55E",
    "intent_uncertain": "#FBBF24",
    "intent_suspicious": "#F97316",
    "intent_likely_intentional": "#EF4444",
    # Severity
    "severity_low": "#60A5FA",
    "severity_medium": "#FBBF24",
    "severity_high": "#F97316",
    "severity_critical": "#EF4444",
    # Text
    "text_primary": "#F1F5F9",
    "text_secondary": "#94A3B8",
    "text_muted": "#64748B",
    "text_accent": "#FF6B35",
    # Status
    "status_online": "#22C55E",
    "status_warning": "#FBBF24",
    "status_error": "#EF4444",
    # Borders
    "border_subtle": "#1E293B",
    "border_default": "#334155",
}

INTENT_COLORS: dict[str, str] = {
    "natural": COLORS["intent_natural"],
    "uncertain": COLORS["intent_uncertain"],
    "suspicious": COLORS["intent_suspicious"],
    "likely_intentional": COLORS["intent_likely_intentional"],
}

SEVERITY_COLORS: dict[str, str] = {
    "low": COLORS["severity_low"],
    "medium": COLORS["severity_medium"],
    "high": COLORS["severity_high"],
    "critical": COLORS["severity_critical"],
}

INTENT_LABELS_ES: dict[str, str] = {
    "natural": "Natural",
    "uncertain": "Incierto",
    "suspicious": "Sospechoso",
    "likely_intentional": "Prob. Intencional",
}

SEVERITY_LABELS_ES: dict[str, str] = {
    "low": "Baja",
    "medium": "Media",
    "high": "Alta",
    "critical": "Critica",
}

SEVERITY_ICONS: dict[str, str] = {
    "low": "info",
    "medium": "warning",
    "high": "local_fire_department",
    "critical": "crisis_alert",
}

# ---------------------------------------------------------------------------
# Global CSS injection
# ---------------------------------------------------------------------------

_GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
@import url('https://fonts.googleapis.com/icon?family=Material+Icons+Round');

:root {
    --fs-bg-primary: #0A0E17;
    --fs-bg-secondary: #111827;
    --fs-bg-surface: #1E293B;
    --fs-fire: #FF6B35;
    --fs-text-primary: #F1F5F9;
    --fs-text-secondary: #94A3B8;
    --fs-text-muted: #64748B;
    --fs-border: #334155;
    --fs-border-subtle: #1E293B;
}

.stApp {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* Hide default chrome */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden !important;}
.stDeployButton {display: none;}

/* Sidebar */
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0D1321 0%, #111827 100%) !important;
    border-right: 1px solid var(--fs-border-subtle) !important;
}
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {
    font-family: 'Inter', sans-serif !important;
}

/* Typography */
h1, h2, h3 {
    font-family: 'Inter', sans-serif !important;
    font-weight: 700 !important;
    letter-spacing: -0.01em !important;
}
h1 { font-weight: 800 !important; letter-spacing: -0.02em !important; }

/* Tighter main area padding */
.block-container {
    padding-top: 1rem !important;
    padding-left: 1.5rem !important;
    padding-right: 1.5rem !important;
    max-width: 100% !important;
}

/* Scrollbar */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: var(--fs-bg-primary); }
::-webkit-scrollbar-thumb { background: var(--fs-border); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--fs-text-muted); }

/* Streamlit metric overrides — flatten out */
[data-testid="stMetric"] {
    background: transparent !important;
    padding: 0 !important;
}

/* Tabs styling */
.stTabs [data-baseweb="tab-list"] {
    gap: 0;
    border-bottom: 1px solid var(--fs-border-subtle);
}
.stTabs [data-baseweb="tab"] {
    font-family: 'Inter', sans-serif !important;
    font-weight: 500;
    padding: 10px 20px;
}

/* Dataframe styling */
[data-testid="stDataFrame"] {
    border: 1px solid var(--fs-border-subtle) !important;
    border-radius: 12px !important;
    overflow: hidden;
}

/* Pulse animation */
@keyframes pulse-ring {
    0% { box-shadow: 0 0 0 0 rgba(255,107,53,0.4); }
    70% { box-shadow: 0 0 0 10px rgba(255,107,53,0); }
    100% { box-shadow: 0 0 0 0 rgba(255,107,53,0); }
}
@keyframes status-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}
@keyframes fire-pulse {
    0% { transform: translate(-50%,-50%) scale(0.5); opacity: 0.8; }
    100% { transform: translate(-50%,-50%) scale(1.5); opacity: 0; }
}
</style>
"""


def inject_css() -> None:
    """Inject global CSS into the Streamlit app. Call once at the top of app.py."""
    st.markdown(_GLOBAL_CSS, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Reusable HTML components
# ---------------------------------------------------------------------------


def render_header(last_scan_text: str = "") -> None:
    """Render the premium top header with logo, title, and live indicator."""
    st.markdown(f"""
    <div style="
        display:flex; align-items:center; justify-content:space-between;
        padding:12px 0 16px 0; margin-bottom:20px;
        border-bottom:1px solid #1E293B;
    ">
        <div style="display:flex; align-items:center; gap:14px;">
            <div style="
                width:44px; height:44px;
                background:linear-gradient(135deg, #FF3B30, #FF6B35, #FFBA08);
                border-radius:12px;
                display:flex; align-items:center; justify-content:center;
                box-shadow:0 4px 16px rgba(255,107,53,0.3);
            ">
                <span class="material-icons-round" style="font-size:26px;color:white;">
                    local_fire_department
                </span>
            </div>
            <div>
                <h1 style="
                    font-size:22px; font-weight:800; margin:0; line-height:1.2;
                    background:linear-gradient(135deg,#FF6B35,#FFBA08);
                    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
                    background-clip:text;
                ">FireSentinel Patagonia</h1>
                <p style="font-size:12px; color:#64748B; margin:0;">
                    Monitoreo satelital de incendios forestales
                </p>
            </div>
        </div>
        <div style="display:flex; align-items:center; gap:16px;">
            <div style="display:flex; align-items:center; gap:6px;">
                <div style="position:relative; width:8px; height:8px;">
                    <div style="position:absolute;inset:0;background:#22C55E;border-radius:50%;"></div>
                    <div style="position:absolute;inset:-2px;background:#22C55E;border-radius:50%;
                        opacity:0.4;animation:status-pulse 2s ease-in-out infinite;"></div>
                </div>
                <span style="font-size:11px;color:#94A3B8;font-weight:600;
                    letter-spacing:0.05em;">EN VIVO</span>
            </div>
            <span style="font-size:11px;color:#64748B;">{last_scan_text}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_metric_card(
    icon: str,
    label: str,
    value: str,
    accent: str = "#FF6B35",
    subtitle: str = "",
) -> None:
    """Render a glass-morphism metric card with Material icon."""
    subtitle_html = (
        f'<p style="font-size:11px;color:#64748B;margin:4px 0 0 0;">{subtitle}</p>'
        if subtitle else ""
    )
    st.markdown(f"""
    <div style="
        background:linear-gradient(135deg,rgba(30,41,59,0.6),rgba(15,23,42,0.8));
        backdrop-filter:blur(20px); -webkit-backdrop-filter:blur(20px);
        border:1px solid rgba(255,255,255,0.08);
        border-radius:14px; padding:18px 20px;
        position:relative; overflow:hidden;
    ">
        <div style="position:absolute;top:0;left:0;right:0;height:3px;
            background:{accent};border-radius:14px 14px 0 0;"></div>
        <div style="position:absolute;top:-30px;right:-30px;width:80px;height:80px;
            background:radial-gradient(circle,{accent}12 0%,transparent 70%);
            border-radius:50%;"></div>
        <div style="display:flex;align-items:flex-start;justify-content:space-between;">
            <div>
                <p style="font-size:11px;font-weight:500;text-transform:uppercase;
                    letter-spacing:0.06em;color:#94A3B8;margin:0 0 6px 0;">{label}</p>
                <p style="font-size:28px;font-weight:800;color:#F1F5F9;margin:0;
                    line-height:1;letter-spacing:-0.02em;">{value}</p>
                {subtitle_html}
            </div>
            <div style="width:40px;height:40px;background:{accent}18;border-radius:10px;
                display:flex;align-items:center;justify-content:center;">
                <span class="material-icons-round" style="font-size:22px;color:{accent};">
                    {icon}
                </span>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_intent_badge(score: int, label: str) -> None:
    """Render a colored intent classification badge."""
    cfg: dict[str, tuple[str, str]] = {
        "natural": ("#22C55E", "check_circle"),
        "uncertain": ("#FBBF24", "help"),
        "suspicious": ("#F97316", "warning"),
        "likely_intentional": ("#EF4444", "dangerous"),
    }
    color, icon = cfg.get(label, ("#6B7280", "help"))
    label_es = INTENT_LABELS_ES.get(label, label)
    st.markdown(f"""
    <div style="display:inline-flex;align-items:center;gap:8px;
        background:{color}18;border:1px solid {color}40;border-radius:20px;padding:6px 14px;">
        <span class="material-icons-round" style="font-size:16px;color:{color};">{icon}</span>
        <span style="font-size:14px;font-weight:700;color:{color};">{score}/100</span>
        <span style="font-size:11px;font-weight:600;color:{color};text-transform:uppercase;
            letter-spacing:0.05em;">{label_es}</span>
    </div>
    """, unsafe_allow_html=True)


def render_severity_badge(severity: str) -> None:
    """Render a colored severity badge."""
    color = SEVERITY_COLORS.get(severity, "#6B7280")
    icon = SEVERITY_ICONS.get(severity, "info")
    label_es = SEVERITY_LABELS_ES.get(severity, severity)
    st.markdown(f"""
    <div style="display:inline-flex;align-items:center;gap:6px;
        background:{color}18;border:1px solid {color}40;border-radius:16px;padding:4px 12px;">
        <span class="material-icons-round" style="font-size:14px;color:{color};">{icon}</span>
        <span style="font-size:12px;font-weight:600;color:{color};text-transform:uppercase;
            letter-spacing:0.04em;">{label_es}</span>
    </div>
    """, unsafe_allow_html=True)


def render_status_dot(status: str, label: str) -> None:
    """Render a pulsing status indicator dot with label."""
    color_map = {"online": "#22C55E", "warning": "#FBBF24", "error": "#EF4444", "offline": "#6B7280"}
    color = color_map.get(status, "#6B7280")
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:8px;">
        <div style="position:relative;width:10px;height:10px;">
            <div style="position:absolute;inset:0;background:{color};border-radius:50%;"></div>
            <div style="position:absolute;inset:-2px;background:{color};border-radius:50%;
                opacity:0.4;animation:status-pulse 2s ease-in-out infinite;"></div>
        </div>
        <span style="font-size:12px;color:#94A3B8;font-weight:500;">{label}</span>
    </div>
    """, unsafe_allow_html=True)


def render_signal_breakdown(breakdown: dict[str, float | None]) -> None:
    """Render the intent scoring signal breakdown as a visual component."""
    signals = [
        ("lightning_absence", "flash_off", "Sin rayos", 25),
        ("road_proximity", "add_road", "Cercania a ruta", 20),
        ("nighttime_ignition", "dark_mode", "Ignicion nocturna", 20),
        ("historical_repeat", "history", "Repeticion historica", 15),
        ("multi_point_ignition", "scatter_plot", "Multiples focos", 10),
        ("dry_conditions", "water_drop", "Condiciones secas", 10),
    ]
    active = sum(1 for key, *_ in signals if breakdown.get(key) is not None)

    rows = ""
    for key, icon, label, max_val in signals:
        score = breakdown.get(key)
        is_active = score is not None
        pct = (score / max_val * 100) if is_active and max_val > 0 else 0
        display = f"{score:.0f}" if is_active else "N/D"

        if not is_active:
            bar_color = "#374151"
        elif pct >= 75:
            bar_color = "#EF4444"
        elif pct >= 50:
            bar_color = "#F97316"
        elif pct >= 25:
            bar_color = "#FBBF24"
        else:
            bar_color = "#22C55E"

        rows += f"""
        <div style="display:flex;align-items:center;gap:10px;padding:7px 0;
            {'opacity:0.35;' if not is_active else ''}">
            <span class="material-icons-round" style="font-size:17px;color:{bar_color};
                width:20px;text-align:center;">{icon}</span>
            <div style="flex:1;">
                <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
                    <span style="font-size:12px;color:#94A3B8;">{label}</span>
                    <span style="font-size:12px;font-weight:600;color:#F1F5F9;">
                        {display}/{max_val}
                    </span>
                </div>
                <div style="background:#1E293B;border-radius:3px;height:4px;overflow:hidden;">
                    <div style="width:{pct}%;height:100%;background:{bar_color};
                        border-radius:3px;"></div>
                </div>
            </div>
        </div>"""

    st.markdown(f"""
    <div style="background:rgba(30,41,59,0.5);backdrop-filter:blur(10px);
        border:1px solid rgba(255,255,255,0.06);border-radius:14px;padding:18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
            <span style="font-size:14px;font-weight:600;color:#F1F5F9;">
                Desglose de Senales
            </span>
            <span style="font-size:11px;color:#64748B;background:#1E293B;padding:3px 8px;
                border-radius:8px;">{active}/6 senales</span>
        </div>
        {rows}
        <div style="margin-top:10px;padding-top:10px;border-top:1px solid #1E293B;">
            <span style="font-size:10px;color:#64748B;font-style:italic;">
                Modelo basado en patrones 2025-2026. No reemplaza investigacion forense.
            </span>
        </div>
    </div>
    """, unsafe_allow_html=True)


def render_section_header(title: str, icon: str = "", subtitle: str = "") -> None:
    """Render a styled section header with optional icon and subtitle."""
    icon_html = (
        f'<span class="material-icons-round" style="font-size:20px;color:#FF6B35;">'
        f"{icon}</span>"
        if icon else ""
    )
    sub_html = (
        f'<span style="font-size:12px;color:#64748B;margin-left:12px;">{subtitle}</span>'
        if subtitle else ""
    )
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:8px;margin:24px 0 12px 0;">
        {icon_html}
        <span style="font-size:16px;font-weight:700;color:#F1F5F9;">{title}</span>
        {sub_html}
    </div>
    """, unsafe_allow_html=True)


def render_card_container(content_html: str, accent: str = "") -> None:
    """Wrap HTML content in a glass card container."""
    border_style = f"border-left:3px solid {accent};" if accent else ""
    st.markdown(f"""
    <div style="background:rgba(30,41,59,0.4);
        border:1px solid rgba(255,255,255,0.06);{border_style}
        border-radius:14px;padding:18px;">
        {content_html}
    </div>
    """, unsafe_allow_html=True)


def render_kpi_row(items: list[dict[str, str]]) -> None:
    """Render a compact horizontal row of small KPI values.

    Each item: {"label": str, "value": str, "icon": str, "color": str}
    """
    cells = ""
    for item in items:
        color = item.get("color", "#94A3B8")
        cells += f"""
        <div style="flex:1;text-align:center;padding:10px 8px;">
            <span class="material-icons-round" style="font-size:18px;color:{color};
                display:block;margin-bottom:4px;">{item['icon']}</span>
            <p style="font-size:18px;font-weight:700;color:#F1F5F9;margin:0;">
                {item['value']}</p>
            <p style="font-size:10px;color:#64748B;margin:2px 0 0 0;text-transform:uppercase;
                letter-spacing:0.05em;">{item['label']}</p>
        </div>"""
    st.markdown(f"""
    <div style="display:flex;background:rgba(30,41,59,0.3);
        border:1px solid rgba(255,255,255,0.06);border-radius:12px;overflow:hidden;">
        {cells}
    </div>
    """, unsafe_allow_html=True)
