"""Reusable Folium map components for fire event visualization.

Creates interactive dark-themed maps with pulsing fire markers, heatmaps,
monitoring zone overlays, and rich popups. Used by both the overview map
and event detail pages.

All user-facing text in SPANISH. Code and variable names in English.
"""

from __future__ import annotations

from html import escape as html_escape
from typing import Any

import folium
import folium.plugins

from firesentinel.core.types import IntentLabel, Severity

# ---------------------------------------------------------------------------
# Color maps (intent label -> marker color)
# ---------------------------------------------------------------------------

_INTENT_COLORS: dict[str, str] = {
    IntentLabel.NATURAL.value: "#22C55E",
    IntentLabel.UNCERTAIN.value: "#FBBF24",
    IntentLabel.SUSPICIOUS.value: "#F97316",
    IntentLabel.LIKELY_INTENTIONAL.value: "#EF4444",
}

# Severity -> marker pixel size
_SEVERITY_SIZE: dict[str, int] = {
    Severity.LOW.value: 12,
    Severity.MEDIUM.value: 18,
    Severity.HIGH.value: 24,
    Severity.CRITICAL.value: 32,
}

# Severity -> Spanish translation
_SEVERITY_LABEL_ES: dict[str, str] = {
    Severity.LOW.value: "Baja",
    Severity.MEDIUM.value: "Media",
    Severity.HIGH.value: "Alta",
    Severity.CRITICAL.value: "Critica",
}

# Intent label -> Spanish translation
_INTENT_LABEL_ES: dict[str, str] = {
    IntentLabel.NATURAL.value: "Natural",
    IntentLabel.UNCERTAIN.value: "Incierto",
    IntentLabel.SUSPICIOUS.value: "Sospechoso",
    IntentLabel.LIKELY_INTENTIONAL.value: "Probable intencional",
}

# Severity colors for popup badge
_SEVERITY_COLORS: dict[str, str] = {
    Severity.LOW.value: "#60A5FA",
    Severity.MEDIUM.value: "#FBBF24",
    Severity.HIGH.value: "#F97316",
    Severity.CRITICAL.value: "#EF4444",
}

# Dark tile URL
_DARK_TILES = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"
_DARK_ATTR = (
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> '
    'contributors &copy; <a href="https://carto.com/">CARTO</a>'
)

_SATELLITE_TILES = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)

_TOPO_TILES = "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png"
_TOPO_ATTR = (
    'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, '
    '<a href="http://viewfinderpanoramas.org">SRTM</a> | '
    'Style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a>'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_marker_color(intent_label: str | None) -> str:
    """Return hex color for an intent label, defaulting to yellow (uncertain)."""
    if intent_label is None:
        return _INTENT_COLORS[IntentLabel.UNCERTAIN.value]
    return _INTENT_COLORS.get(intent_label, _INTENT_COLORS[IntentLabel.UNCERTAIN.value])


def _get_marker_size(severity: str) -> int:
    """Return marker pixel size for a severity level, defaulting to medium."""
    return _SEVERITY_SIZE.get(severity, _SEVERITY_SIZE[Severity.MEDIUM.value])


def _build_pulsing_icon_html(color: str, size: int) -> str:
    """Build HTML for a pulsing DivIcon marker with glow effect."""
    outer_size = size + 16
    half_outer = outer_size // 2
    half_inner = size // 2

    return f"""
    <div style="position:relative;width:{outer_size}px;height:{outer_size}px;">
        <style>
            @keyframes fire-pulse {{
                0% {{ transform: scale(1); opacity: 0.7; }}
                50% {{ transform: scale(1.6); opacity: 0.0; }}
                100% {{ transform: scale(1); opacity: 0.0; }}
            }}
        </style>
        <div style="
            position:absolute;
            top:0; left:0;
            width:{outer_size}px;
            height:{outer_size}px;
            border-radius:50%;
            border:2px solid {color};
            animation: fire-pulse 2s ease-out infinite;
            box-sizing:border-box;
        "></div>
        <div style="
            position:absolute;
            top:{half_outer - half_inner}px;
            left:{half_outer - half_inner}px;
            width:{size}px;
            height:{size}px;
            border-radius:50%;
            background: radial-gradient(circle at 35% 35%, {color}, {color}88 70%, {color}33);
            box-shadow: 0 0 {size}px {color}99, 0 0 {size * 2}px {color}44;
        "></div>
    </div>
    """


def _build_popup_html(event: dict[str, Any]) -> str:
    """Build rich dark-themed HTML popup content for a fire event marker."""
    town = html_escape(event.get("nearest_town") or "Ubicacion desconocida")
    severity = event.get("severity", "medium")
    severity_es = html_escape(_SEVERITY_LABEL_ES.get(severity, severity))
    severity_color = _SEVERITY_COLORS.get(severity, "#FBBF24")
    intent_score = event.get("intent_score")
    intent_label = event.get("intent_label")
    intent_label_es = _INTENT_LABEL_ES.get(intent_label or "", intent_label or "N/A")
    intent_color = _get_marker_color(intent_label)
    hotspot_count = event.get("hotspot_count", 0)
    max_frp = event.get("max_frp", 0.0)
    first_detected = event.get("first_detected_at", "")
    event_id = event.get("id", "")
    center_lat = event.get("center_lat", 0.0)
    center_lon = event.get("center_lon", 0.0)

    score_val = intent_score if intent_score is not None else 0
    score_display = f"{intent_score}/100" if intent_score is not None else "N/A"
    score_pct = min(max(score_val, 0), 100)

    # Format detected date
    detected_str = str(first_detected)[:10] if first_detected else "N/A"

    html = f"""
    <div style="
        font-family: 'Inter', 'Segoe UI', sans-serif;
        background: #111827;
        color: #E2E8F0;
        border-radius: 12px;
        padding: 16px;
        min-width: 260px;
        max-width: 300px;
        border: 1px solid #1E293B;
        box-shadow: 0 8px 32px rgba(0,0,0,0.5);
    ">
        <!-- Header -->
        <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
            <span style="
                font-size:15px;
                font-weight:700;
                color:#F1F5F9;
                flex:1;
                margin-right:8px;
            ">{town}</span>
            <span style="
                background:{intent_color}22;
                color:{intent_color};
                border:1px solid {intent_color}55;
                border-radius:6px;
                padding:2px 8px;
                font-size:11px;
                font-weight:600;
                white-space:nowrap;
            ">{intent_label_es}</span>
        </div>

        <!-- Score bar -->
        <div style="margin-bottom:14px;">
            <div style="display:flex; justify-content:space-between; font-size:11px; color:#94A3B8; margin-bottom:4px;">
                <span>Intencionalidad</span>
                <span style="color:{intent_color}; font-weight:600;">{score_display}</span>
            </div>
            <div style="
                background:#1E293B;
                border-radius:4px;
                height:6px;
                overflow:hidden;
            ">
                <div style="
                    width:{score_pct}%;
                    height:100%;
                    background: linear-gradient(90deg, #22C55E, {intent_color});
                    border-radius:4px;
                    transition: width 0.3s;
                "></div>
            </div>
        </div>

        <!-- 2x2 detail grid -->
        <div style="
            display:grid;
            grid-template-columns: 1fr 1fr;
            gap:8px;
            margin-bottom:14px;
        ">
            <div style="background:#0F172A; border-radius:8px; padding:8px;">
                <div style="font-size:10px; color:#64748B; text-transform:uppercase; letter-spacing:0.5px;">Severidad</div>
                <div style="font-size:14px; font-weight:700; color:{severity_color}; margin-top:2px;">{severity_es}</div>
            </div>
            <div style="background:#0F172A; border-radius:8px; padding:8px;">
                <div style="font-size:10px; color:#64748B; text-transform:uppercase; letter-spacing:0.5px;">FRP Max</div>
                <div style="font-size:14px; font-weight:700; color:#FF6B35; margin-top:2px;">{max_frp:.1f} MW</div>
            </div>
            <div style="background:#0F172A; border-radius:8px; padding:8px;">
                <div style="font-size:10px; color:#64748B; text-transform:uppercase; letter-spacing:0.5px;">Detectado</div>
                <div style="font-size:13px; font-weight:600; color:#CBD5E1; margin-top:2px;">{detected_str}</div>
            </div>
            <div style="background:#0F172A; border-radius:8px; padding:8px;">
                <div style="font-size:10px; color:#64748B; text-transform:uppercase; letter-spacing:0.5px;">Hotspots</div>
                <div style="font-size:14px; font-weight:700; color:#60A5FA; margin-top:2px;">{hotspot_count}</div>
            </div>
        </div>

        <!-- Footer -->
        <div style="
            border-top:1px solid #1E293B;
            padding-top:10px;
            display:flex;
            justify-content:space-between;
            align-items:center;
        ">
            <span style="font-size:10px; color:#64748B;">
                {center_lat:.4f}, {center_lon:.4f}
            </span>
            <a href="?page=detail&event_id={event_id}" style="
                color:#FF6B35;
                text-decoration:none;
                font-size:12px;
                font-weight:600;
            ">Ver detalle &rarr;</a>
        </div>
    </div>
    """
    return html


def _build_hotspot_popup_html(hotspot: dict[str, Any]) -> str:
    """Build dark-themed popup HTML for an individual hotspot marker."""
    satellite = hotspot.get("satellite", "N/A")
    brightness = hotspot.get("brightness", 0)
    frp = hotspot.get("frp", 0)
    acq_date = hotspot.get("acq_date", "")
    acq_time = hotspot.get("acq_time", "")

    return f"""
    <div style="
        font-family: 'Inter', 'Segoe UI', sans-serif;
        background: #111827;
        color: #E2E8F0;
        border-radius: 10px;
        padding: 12px;
        min-width: 180px;
        border: 1px solid #1E293B;
        box-shadow: 0 4px 16px rgba(0,0,0,0.4);
    ">
        <div style="font-size:13px; font-weight:700; color:#F1F5F9; margin-bottom:8px;">
            Hotspot - {satellite}
        </div>
        <div style="display:grid; gap:4px; font-size:12px;">
            <div style="display:flex; justify-content:space-between;">
                <span style="color:#64748B;">Brillo</span>
                <span style="color:#FBBF24; font-weight:600;">{brightness:.1f} K</span>
            </div>
            <div style="display:flex; justify-content:space-between;">
                <span style="color:#64748B;">FRP</span>
                <span style="color:#FF6B35; font-weight:600;">{frp:.1f} MW</span>
            </div>
            <div style="display:flex; justify-content:space-between;">
                <span style="color:#64748B;">Fecha</span>
                <span style="color:#CBD5E1;">{acq_date}</span>
            </div>
            <div style="display:flex; justify-content:space-between;">
                <span style="color:#64748B;">Hora</span>
                <span style="color:#CBD5E1;">{acq_time}</span>
            </div>
        </div>
    </div>
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def create_fire_map(
    events: list[dict[str, Any]],
    center_lat: float = -43.0,
    center_lon: float = -70.0,
    zoom: int = 7,
) -> folium.Map:
    """Create a dark-themed Folium map with pulsing fire event markers.

    Args:
        events: List of fire event dicts with keys: center_lat, center_lon,
            severity, intent_score, intent_label, hotspot_count, max_frp,
            nearest_town, first_detected_at, id, province, is_active.
        center_lat: Map center latitude.
        center_lon: Map center longitude.
        zoom: Initial zoom level.

    Returns:
        Configured Folium Map object with fire markers, heatmap, and controls.
    """
    fire_map = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles=None,
    )

    # Base layer: CartoDB Dark Matter
    folium.TileLayer(
        tiles=_DARK_TILES,
        attr=_DARK_ATTR,
        name="Oscuro",
        control=True,
    ).add_to(fire_map)

    # Additional layer: ArcGIS Satellite
    folium.TileLayer(
        tiles=_SATELLITE_TILES,
        attr="Esri",
        name="Satelite",
        overlay=False,
    ).add_to(fire_map)

    # Additional layer: OpenTopoMap terrain
    folium.TileLayer(
        tiles=_TOPO_TILES,
        attr=_TOPO_ATTR,
        name="Terreno",
        overlay=False,
    ).add_to(fire_map)

    # Fullscreen control
    folium.plugins.Fullscreen(
        position="topleft",
        title="Pantalla completa",
        title_cancel="Salir de pantalla completa",
    ).add_to(fire_map)

    # MiniMap with dark tiles
    minimap = folium.plugins.MiniMap(
        tile_layer=folium.TileLayer(
            tiles=_DARK_TILES,
            attr=_DARK_ATTR,
        ),
        toggle_display=True,
        zoom_level_offset=-5,
    )
    fire_map.add_child(minimap)

    # Collect heatmap data
    heat_data: list[list[float]] = []

    # Fire event markers
    for event in events:
        lat = event.get("center_lat")
        lon = event.get("center_lon")
        if lat is None or lon is None:
            continue

        color = _get_marker_color(event.get("intent_label"))
        size = _get_marker_size(event.get("severity", "medium"))
        outer_size = size + 16

        # DivIcon with pulsing animation
        icon_html = _build_pulsing_icon_html(color, size)
        icon = folium.DivIcon(
            html=icon_html,
            icon_size=(outer_size, outer_size),
            icon_anchor=(outer_size // 2, outer_size // 2),
        )

        popup_html = _build_popup_html(event)
        frp = event.get("max_frp", 0.0)
        score = event.get("intent_score", 0) or 0
        tooltip_text = f"Intencionalidad: {score}/100 | FRP: {frp:.1f} MW"

        folium.Marker(
            location=[lat, lon],
            icon=icon,
            popup=folium.Popup(popup_html, max_width=320),
            tooltip=tooltip_text,
        ).add_to(fire_map)

        # Collect for heatmap (weight by FRP)
        weight = max(frp, 1.0)
        heat_data.append([lat, lon, weight])

    # HeatMap layer
    if heat_data:
        heat_group = folium.FeatureGroup(name="Mapa de Calor", show=False)
        folium.plugins.HeatMap(
            data=heat_data,
            min_opacity=0.3,
            radius=25,
            blur=20,
            gradient={
                "0.0": "#0A1628",
                "0.3": "#1E3A5F",
                "0.5": "#F97316",
                "0.7": "#FF6B35",
                "1.0": "#FBBF24",
            },
        ).add_to(heat_group)
        heat_group.add_to(fire_map)

    # Layer control
    folium.LayerControl(position="topright", collapsed=False).add_to(fire_map)

    return fire_map


def create_event_detail_map(
    event: dict[str, Any],
    hotspots: list[dict[str, Any]],
    center_lat: float | None = None,
    center_lon: float | None = None,
) -> folium.Map:
    """Create a dark-themed detail map for a single fire event with hotspots.

    Args:
        event: Fire event dict with center_lat, center_lon, severity,
            intent_label, nearest_town, and other standard keys.
        hotspots: List of hotspot dicts with latitude, longitude, frp,
            brightness, satellite, acq_date, acq_time, confidence, daynight.
        center_lat: Override center latitude (defaults to event centroid).
        center_lon: Override center longitude (defaults to event centroid).

    Returns:
        Folium Map object zoomed to the event area with hotspot markers.
    """
    map_lat = center_lat if center_lat is not None else event.get("center_lat", -43.0)
    map_lon = center_lon if center_lon is not None else event.get("center_lon", -70.0)

    detail_map = folium.Map(
        location=[map_lat, map_lon],
        zoom_start=13,
        tiles=None,
    )

    # Dark base tiles
    folium.TileLayer(
        tiles=_DARK_TILES,
        attr=_DARK_ATTR,
        name="Oscuro",
        control=True,
    ).add_to(detail_map)

    # Satellite layer
    folium.TileLayer(
        tiles=_SATELLITE_TILES,
        attr="Esri",
        name="Satelite",
        overlay=False,
    ).add_to(detail_map)

    # Fullscreen control
    folium.plugins.Fullscreen(
        position="topleft",
        title="Pantalla completa",
        title_cancel="Salir de pantalla completa",
    ).add_to(detail_map)

    folium.LayerControl(position="topright", collapsed=False).add_to(detail_map)

    # Centroid marker: larger pulsing icon with 0.5 opacity ring
    intent_color = _get_marker_color(event.get("intent_label"))
    centroid_size = 36
    outer_centroid = centroid_size + 20
    half_outer = outer_centroid // 2
    half_inner = centroid_size // 2

    centroid_html = f"""
    <div style="position:relative;width:{outer_centroid}px;height:{outer_centroid}px;">
        <style>
            @keyframes fire-pulse {{
                0% {{ transform: scale(1); opacity: 0.5; }}
                50% {{ transform: scale(1.8); opacity: 0.0; }}
                100% {{ transform: scale(1); opacity: 0.0; }}
            }}
        </style>
        <div style="
            position:absolute;
            top:0; left:0;
            width:{outer_centroid}px;
            height:{outer_centroid}px;
            border-radius:50%;
            border:2px solid {intent_color};
            opacity:0.5;
            animation: fire-pulse 2.5s ease-out infinite;
            box-sizing:border-box;
        "></div>
        <div style="
            position:absolute;
            top:{half_outer - half_inner}px;
            left:{half_outer - half_inner}px;
            width:{centroid_size}px;
            height:{centroid_size}px;
            border-radius:50%;
            background: radial-gradient(circle at 35% 35%, {intent_color}, {intent_color}88 70%, {intent_color}33);
            box-shadow: 0 0 {centroid_size}px {intent_color}99, 0 0 {centroid_size * 2}px {intent_color}44;
        "></div>
    </div>
    """

    centroid_icon = folium.DivIcon(
        html=centroid_html,
        icon_size=(outer_centroid, outer_centroid),
        icon_anchor=(half_outer, half_outer),
    )

    folium.Marker(
        location=[map_lat, map_lon],
        icon=centroid_icon,
        tooltip="Centroide del evento",
    ).add_to(detail_map)

    # Individual hotspot markers
    for hs in hotspots:
        hs_lat = hs.get("latitude")
        hs_lon = hs.get("longitude")
        if hs_lat is None or hs_lon is None:
            continue

        hs_frp = hs.get("frp", 0.0)
        hs_satellite = hs.get("satellite", "N/A")
        hs_size = 10
        hs_outer = hs_size + 8
        hs_half_outer = hs_outer // 2
        hs_half_inner = hs_size // 2

        hs_icon_html = f"""
        <div style="position:relative;width:{hs_outer}px;height:{hs_outer}px;">
            <div style="
                position:absolute;
                top:{hs_half_outer - hs_half_inner}px;
                left:{hs_half_outer - hs_half_inner}px;
                width:{hs_size}px;
                height:{hs_size}px;
                border-radius:50%;
                background: radial-gradient(circle at 35% 35%, #EF4444, #EF444488 70%, #EF444433);
                box-shadow: 0 0 8px #EF444499;
            "></div>
        </div>
        """

        hs_icon = folium.DivIcon(
            html=hs_icon_html,
            icon_size=(hs_outer, hs_outer),
            icon_anchor=(hs_half_outer, hs_half_outer),
        )

        popup_html = _build_hotspot_popup_html(hs)
        tooltip_text = f"Hotspot - {hs_satellite} | FRP: {hs_frp:.1f} MW"

        folium.Marker(
            location=[hs_lat, hs_lon],
            icon=hs_icon,
            popup=folium.Popup(popup_html, max_width=240),
            tooltip=tooltip_text,
        ).add_to(detail_map)

    return detail_map


def add_monitoring_zones(
    fire_map: folium.Map,
    zones: list[dict[str, Any]],
) -> None:
    """Add monitoring zone circle overlays to an existing fire map.

    Args:
        fire_map: The Folium Map to add zones to.
        zones: List of zone dicts with keys: name, center_lat, center_lon,
            radius_km (or radius_m).
    """
    zone_group = folium.FeatureGroup(name="Zonas de Monitoreo")

    for zone in zones:
        lat = zone.get("center_lat")
        lon = zone.get("center_lon")
        if lat is None or lon is None:
            continue

        # Support radius in km or meters
        radius_m = zone.get("radius_m")
        if radius_m is None:
            radius_km = zone.get("radius_km", 50)
            radius_m = radius_km * 1000

        zone_name = zone.get("name", "zona")
        display_name = zone_name.replace("_", " ").title()

        folium.Circle(
            location=[lat, lon],
            radius=radius_m,
            color="#FF6B3560",
            fill=True,
            fill_color="#FF6B35",
            fill_opacity=0.05,
            dash_array="5 5",
            tooltip=display_name,
        ).add_to(zone_group)

    zone_group.add_to(fire_map)
