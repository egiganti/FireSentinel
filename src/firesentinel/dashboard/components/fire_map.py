"""Reusable Folium map components for fire event visualization.

Creates interactive maps with color-coded fire markers, popups, and
satellite tile layers. Used by both the public map and detail pages.

All user-facing text in SPANISH. Code and variable names in English.
"""

from __future__ import annotations

from typing import Any

import folium

from firesentinel.core.types import IntentLabel, Severity

# ---------------------------------------------------------------------------
# Color maps (intent label -> marker color)
# ---------------------------------------------------------------------------

_INTENT_COLORS: dict[str, str] = {
    IntentLabel.NATURAL.value: "#2ecc71",
    IntentLabel.UNCERTAIN.value: "#f1c40f",
    IntentLabel.SUSPICIOUS.value: "#e67e22",
    IntentLabel.LIKELY_INTENTIONAL.value: "#e74c3c",
}

# Severity -> marker radius
_SEVERITY_RADIUS: dict[str, int] = {
    Severity.LOW.value: 6,
    Severity.MEDIUM.value: 9,
    Severity.HIGH.value: 12,
    Severity.CRITICAL.value: 16,
}

# Intent label -> Spanish translation
_INTENT_LABEL_ES: dict[str, str] = {
    IntentLabel.NATURAL.value: "Natural",
    IntentLabel.UNCERTAIN.value: "Incierto",
    IntentLabel.SUSPICIOUS.value: "Sospechoso",
    IntentLabel.LIKELY_INTENTIONAL.value: "Probable intencional",
}

# Severity -> Spanish translation
_SEVERITY_LABEL_ES: dict[str, str] = {
    Severity.LOW.value: "Baja",
    Severity.MEDIUM.value: "Media",
    Severity.HIGH.value: "Alta",
    Severity.CRITICAL.value: "Critica",
}


def _get_marker_color(intent_label: str | None) -> str:
    """Return hex color for an intent label, defaulting to yellow (uncertain)."""
    if intent_label is None:
        return _INTENT_COLORS[IntentLabel.UNCERTAIN.value]
    return _INTENT_COLORS.get(intent_label, _INTENT_COLORS[IntentLabel.UNCERTAIN.value])


def _get_marker_radius(severity: str) -> int:
    """Return marker radius for a severity level, defaulting to medium."""
    return _SEVERITY_RADIUS.get(severity, _SEVERITY_RADIUS[Severity.MEDIUM.value])


def _build_popup_html(event: dict[str, Any]) -> str:
    """Build HTML popup content for a fire event marker."""
    town = event.get("nearest_town") or "Ubicacion desconocida"
    severity = event.get("severity", "medium")
    severity_es = _SEVERITY_LABEL_ES.get(severity, severity)
    intent_score = event.get("intent_score")
    intent_label = event.get("intent_label")
    intent_label_es = _INTENT_LABEL_ES.get(intent_label or "", intent_label or "N/A")
    hotspot_count = event.get("hotspot_count", 0)
    max_frp = event.get("max_frp", 0.0)
    first_detected = event.get("first_detected_at", "")
    event_id = event.get("id", "")

    intent_display = f"{intent_score}/100" if intent_score is not None else "N/A"

    html = f"""
    <div style="font-family: Arial, sans-serif; min-width: 200px;">
        <h4 style="margin: 0 0 8px 0; color: #333;">{town}</h4>
        <table style="font-size: 13px; border-collapse: collapse;">
            <tr><td style="padding: 2px 8px 2px 0; font-weight: bold;">Severidad:</td>
                <td>{severity_es}</td></tr>
            <tr><td style="padding: 2px 8px 2px 0; font-weight: bold;">Intencionalidad:</td>
                <td>{intent_display} - {intent_label_es}</td></tr>
            <tr><td style="padding: 2px 8px 2px 0; font-weight: bold;">Detecciones:</td>
                <td>{hotspot_count}</td></tr>
            <tr><td style="padding: 2px 8px 2px 0; font-weight: bold;">FRP Max:</td>
                <td>{max_frp:.1f} MW</td></tr>
            <tr><td style="padding: 2px 8px 2px 0; font-weight: bold;">Primera deteccion:</td>
                <td>{first_detected}</td></tr>
        </table>
        <div style="margin-top: 8px;">
            <a href="?page=detail&event_id={event_id}"
               style="color: #e74c3c; text-decoration: none; font-weight: bold;">
               Ver detalle &rarr;
            </a>
        </div>
    </div>
    """
    return html


def create_fire_map(
    events: list[dict[str, Any]],
    center_lat: float = -42.2,
    center_lon: float = -71.5,
    zoom: int = 8,
) -> folium.Map:
    """Create a Folium map with fire event markers.

    Args:
        events: List of fire event dicts with keys: center_lat, center_lon,
            severity, intent_score, intent_label, hotspot_count, max_frp,
            nearest_town, first_detected_at, id.
        center_lat: Map center latitude.
        center_lon: Map center longitude.
        zoom: Initial zoom level.

    Returns:
        Configured Folium Map object with fire markers.
    """
    fire_map = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="OpenStreetMap",
    )

    # Add satellite tile layer as an alternative
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satelite",
        overlay=False,
    ).add_to(fire_map)

    folium.LayerControl().add_to(fire_map)

    for event in events:
        lat = event.get("center_lat")
        lon = event.get("center_lon")
        if lat is None or lon is None:
            continue

        color = _get_marker_color(event.get("intent_label"))
        radius = _get_marker_radius(event.get("severity", "medium"))
        popup_html = _build_popup_html(event)

        folium.CircleMarker(
            location=[lat, lon],
            radius=radius,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.7,
            popup=folium.Popup(popup_html, max_width=300),
            tooltip=event.get("nearest_town") or "Incendio detectado",
        ).add_to(fire_map)

    return fire_map


def create_event_detail_map(
    event: dict[str, Any],
    hotspots: list[dict[str, Any]],
) -> folium.Map:
    """Create a detail map showing a single fire event with its hotspots.

    Args:
        event: Fire event dict with center_lat, center_lon, severity,
            intent_label, nearest_town.
        hotspots: List of hotspot dicts with latitude, longitude, frp,
            brightness, satellite, acq_date, acq_time.

    Returns:
        Folium Map object zoomed to the event area.
    """
    center_lat = event.get("center_lat", -42.2)
    center_lon = event.get("center_lon", -71.5)

    detail_map = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=13,
        tiles="OpenStreetMap",
    )

    # Add satellite tile layer
    folium.TileLayer(
        tiles="https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri",
        name="Satelite",
        overlay=False,
    ).add_to(detail_map)

    folium.LayerControl().add_to(detail_map)

    # Event centroid marker (larger)
    color = _get_marker_color(event.get("intent_label"))
    folium.CircleMarker(
        location=[center_lat, center_lon],
        radius=14,
        color=color,
        fill=True,
        fill_color=color,
        fill_opacity=0.5,
        tooltip="Centroide del evento",
    ).add_to(detail_map)

    # Individual hotspot markers (smaller)
    for hs in hotspots:
        hs_lat = hs.get("latitude")
        hs_lon = hs.get("longitude")
        if hs_lat is None or hs_lon is None:
            continue

        hs_popup = (
            f"Satelite: {hs.get('satellite', 'N/A')}<br>"
            f"Brillo: {hs.get('brightness', 0):.1f} K<br>"
            f"FRP: {hs.get('frp', 0):.1f} MW<br>"
            f"Fecha: {hs.get('acq_date', '')} {hs.get('acq_time', '')}"
        )

        folium.CircleMarker(
            location=[hs_lat, hs_lon],
            radius=5,
            color="#e74c3c",
            fill=True,
            fill_color="#e74c3c",
            fill_opacity=0.8,
            popup=folium.Popup(hs_popup, max_width=200),
            tooltip=f"Hotspot - FRP: {hs.get('frp', 0):.1f} MW",
        ).add_to(detail_map)

    return detail_map
