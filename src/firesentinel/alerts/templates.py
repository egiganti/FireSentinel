"""Alert message formatting for Telegram, WhatsApp, and escalation alerts.

All user-facing text is in SPANISH. This module converts FireEvent data
into formatted alert messages for each delivery channel.

No external dependencies -- pure string formatting only.
Imports only from firesentinel.core.types (never from ingestion/ or processing/).
"""

from __future__ import annotations

from firesentinel.core.types import (
    FireEvent,
    IntentBreakdown,
    IntentLabel,
    Severity,
)


# ---------------------------------------------------------------------------
# Dashboard URL placeholder (replaced at dispatch time)
# ---------------------------------------------------------------------------

_DASHBOARD_URL_TEMPLATE = "https://firesentinel.app/event/{event_id}"


# ---------------------------------------------------------------------------
# Translation maps
# ---------------------------------------------------------------------------

_SEVERITY_LABELS: dict[Severity, str] = {
    Severity.LOW: "BAJA",
    Severity.MEDIUM: "MEDIA",
    Severity.HIGH: "ALTA",
    Severity.CRITICAL: "CRITICA",
}

_INTENT_LABELS: dict[IntentLabel, str] = {
    IntentLabel.NATURAL: "NATURAL",
    IntentLabel.UNCERTAIN: "INCIERTO",
    IntentLabel.SUSPICIOUS: "SOSPECHOSO",
    IntentLabel.LIKELY_INTENTIONAL: "PROBABLE INTENCIONAL",
}

_SEVERITY_EMOJIS: dict[Severity, str] = {
    Severity.LOW: "\U0001f7e2",       # green circle
    Severity.MEDIUM: "\U0001f7e1",    # yellow circle
    Severity.HIGH: "\U0001f7e0",      # orange circle
    Severity.CRITICAL: "\U0001f534",  # red circle
}

_ROAD_TYPES_SPANISH: dict[str, str] = {
    "track": "camino rural",
    "path": "sendero",
    "tertiary": "camino terciario",
    "unclassified": "camino sin clasificar",
    "secondary": "ruta secundaria",
    "primary": "ruta principal",
    "trunk": "ruta troncal",
    "motorway": "autopista",
    "none": "sin camino cercano",
}

# Argentina timezone offset (UTC-3)
_ARGENTINA_UTC_OFFSET_HOURS = -3


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def severity_label(sev: Severity) -> str:
    """Return Spanish label for a severity level."""
    return _SEVERITY_LABELS[sev]


def intent_label(label: IntentLabel) -> str:
    """Return Spanish label for an intentionality classification."""
    return _INTENT_LABELS[label]


def severity_emoji(sev: Severity) -> str:
    """Return emoji for a severity level."""
    return _SEVERITY_EMOJIS[sev]


def road_type_spanish(highway_tag: str) -> str:
    """Translate an OSM highway tag to Spanish road type name."""
    return _ROAD_TYPES_SPANISH.get(highway_tag, highway_tag)


def format_signal_description(
    breakdown: IntentBreakdown, event: FireEvent
) -> list[str]:
    """Return Spanish-language descriptions for each non-zero signal.

    Only signals with a score > 0 are included in the output.
    """
    descriptions: list[str] = []

    # Signal 1: Lightning
    if breakdown.lightning_score > 0:
        descriptions.append("Sin actividad de rayos en las ultimas 6h")

    # Signal 2: Road proximity
    if breakdown.road_score > 0:
        distance = event.nearest_road_m
        road_tag = event.nearest_road_type or "none"
        road_name = road_type_spanish(road_tag)
        if distance is not None:
            road_ref = event.nearest_road_ref
            if road_ref is not None:
                descriptions.append(
                    f"A {distance:.0f}m de {road_name} ({road_ref})"
                )
            else:
                descriptions.append(f"A {distance:.0f}m de {road_name}")
        else:
            descriptions.append(f"Cerca de {road_name}")

    # Signal 3: Nighttime ignition
    if breakdown.night_score > 0:
        local_time = _format_local_time(event)
        descriptions.append(f"Detectado de noche ({local_time})")

    # Signal 4: Historical repeat
    if breakdown.history_score > 0:
        descriptions.append("Incendio previo en la misma zona")

    # Signal 5: Multi-point ignition
    if breakdown.multi_point_score > 0:
        n_hotspots = len(event.hotspots)
        descriptions.append(
            f"{n_hotspots} focos simultaneos detectados en un radio de 5km"
        )

    # Signal 6: Dry conditions
    if breakdown.dry_conditions_score > 0:
        humidity = None
        if event.weather_data and "humidity_pct" in event.weather_data:
            humidity = event.weather_data["humidity_pct"]
        if humidity is not None:
            descriptions.append(
                f"Condiciones secas: {humidity:.0f}% humedad, sin lluvia en 72h"
            )
        else:
            descriptions.append("Condiciones secas: sin lluvia en 72h")

    return descriptions


def format_telegram_alert(event: FireEvent) -> str:
    """Format a Telegram alert message using Markdown.

    Includes severity header, location, maps link, intent score with
    signal descriptions, satellite source, and calibration disclaimer.
    """
    sev = event.severity
    emoji = severity_emoji(sev)
    label_es = severity_label(sev)

    # Header
    header = f"{emoji} ALERTA {label_es} - Incendio detectado"

    # Location
    lat = event.center_lat
    lon = event.center_lon
    location_str = f"{lat}, {lon}"
    if event.nearest_town:
        town_province = event.nearest_town
        if event.province:
            town_province = f"{event.nearest_town}, {event.province}"
        location_str = f"{lat}, {lon} ({town_province})"

    maps_url = f"https://www.google.com/maps?q={lat},{lon}"

    # Severity detail
    n_hotspots = len(event.hotspots)
    severity_detail = (
        f"Severidad: {label_es} "
        f"({n_hotspots} detecciones, FRP max: {event.max_frp} MW)"
    )

    # Intentionality
    intent_section = ""
    if event.intent is not None:
        score = event.intent.total
        intent_lbl = intent_label(event.intent.label)
        intent_section = f"\u26a0\ufe0f Intencionalidad: {score}/100 - {intent_lbl}"

        signals = format_signal_description(event.intent, event)
        if signals:
            signal_lines = "\n".join(f"\u2022 {s}" for s in signals)
            intent_section += f"\nSenales principales:\n{signal_lines}"

        intent_section += (
            f"\nBasado en {event.intent.active_signals}"
            f"/{event.intent.total_signals} senales"
        )

    # Satellite source and detection time
    satellite = _get_satellite_source(event)
    detected_str = event.first_detected.strftime("%Y-%m-%d %H:%M UTC")
    source_line = f"\U0001f6f0 Fuente: {satellite} | Detectado: {detected_str}"

    # Disclaimer
    disclaimer = (
        "\u26a0\ufe0f Modelo basado en patrones 2025-2026. "
        "No reemplaza investigacion oficial."
    )

    # Dashboard link
    dashboard_url = _DASHBOARD_URL_TEMPLATE.format(event_id=event.id)
    dashboard_link = f"[Ver en dashboard]({dashboard_url})"

    # Assemble
    parts = [
        header,
        "",
        f"\U0001f4cd Ubicacion: {location_str}",
        f"\U0001f5fa Mapa: {maps_url}",
        "",
        f"\U0001f525 {severity_detail}",
        "",
    ]

    if intent_section:
        parts.append(intent_section)
        parts.append("")

    parts.extend([
        source_line,
        "",
        disclaimer,
        "",
        dashboard_link,
    ])

    return "\n".join(parts)


def format_whatsapp_alert(event: FireEvent) -> str:
    """Format a WhatsApp alert message in plain text (no Markdown).

    Same content as Telegram but without Markdown links or formatting.
    WhatsApp doesn't support Markdown, so URLs are shown as full text.
    """
    sev = event.severity
    emoji = severity_emoji(sev)
    label_es = severity_label(sev)

    # Header
    header = f"{emoji} ALERTA {label_es} - Incendio detectado"

    # Location
    lat = event.center_lat
    lon = event.center_lon
    location_str = f"{lat}, {lon}"
    if event.nearest_town:
        town_province = event.nearest_town
        if event.province:
            town_province = f"{event.nearest_town}, {event.province}"
        location_str = f"{lat}, {lon} ({town_province})"

    maps_url = f"https://www.google.com/maps?q={lat},{lon}"

    # Severity detail
    n_hotspots = len(event.hotspots)
    severity_detail = (
        f"Severidad: {label_es} "
        f"({n_hotspots} detecciones, FRP max: {event.max_frp} MW)"
    )

    # Intentionality
    intent_section = ""
    if event.intent is not None:
        score = event.intent.total
        intent_lbl = intent_label(event.intent.label)
        intent_section = f"Intencionalidad: {score}/100 - {intent_lbl}"

        signals = format_signal_description(event.intent, event)
        if signals:
            signal_lines = "\n".join(f"- {s}" for s in signals)
            intent_section += f"\nSenales:\n{signal_lines}"

        intent_section += (
            f"\nBasado en {event.intent.active_signals}"
            f"/{event.intent.total_signals} senales"
        )

    # Satellite source and detection time
    satellite = _get_satellite_source(event)
    detected_str = event.first_detected.strftime("%Y-%m-%d %H:%M UTC")
    source_line = f"Fuente: {satellite} | Detectado: {detected_str}"

    # Disclaimer
    disclaimer = (
        "Modelo basado en patrones 2025-2026. "
        "No reemplaza investigacion oficial."
    )

    # Dashboard link (plain URL for WhatsApp)
    dashboard_url = _DASHBOARD_URL_TEMPLATE.format(event_id=event.id)

    # Assemble
    parts = [
        header,
        f"Ubicacion: {location_str}",
        f"Mapa: {maps_url}",
        severity_detail,
    ]

    if intent_section:
        parts.append(intent_section)

    parts.extend([
        source_line,
        disclaimer,
        f"Dashboard: {dashboard_url}",
    ])

    return "\n".join(parts)


def format_escalation_alert(
    event: FireEvent,
    previous_severity: str,
    previous_intent_score: int,
) -> str:
    """Format an escalation alert showing what changed.

    Uses "ACTUALIZACION" header instead of "ALERTA" and highlights
    the severity and/or intent score delta before the full alert info.
    """
    sev = event.severity
    emoji = severity_emoji(sev)
    label_es = severity_label(sev)

    header = f"{emoji} ACTUALIZACION - Incendio en seguimiento"

    # Build change summary lines
    changes: list[str] = []

    prev_sev_label = _severity_label_from_value(previous_severity)
    if prev_sev_label != label_es:
        changes.append(f"Severidad: {prev_sev_label} \u2192 {label_es}")

    if event.intent is not None:
        current_score = event.intent.total
        if current_score != previous_intent_score:
            changes.append(
                f"Intencionalidad: {previous_intent_score} \u2192 {current_score}"
            )

    change_section = ""
    if changes:
        change_lines = "\n".join(f"\u2022 {c}" for c in changes)
        change_section = f"Cambios detectados:\n{change_lines}"

    # Full current state (reuse Telegram formatting)
    full_alert = format_telegram_alert(event)

    parts = [header, ""]
    if change_section:
        parts.append(change_section)
        parts.append("")
    parts.append(full_alert)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _format_local_time(event: FireEvent) -> str:
    """Convert event detection time to Argentina local time string.

    Argentina is UTC-3 year-round (no daylight saving). Returns a formatted
    string like "23:45 hora local".
    """
    utc_dt = event.first_detected
    # Apply UTC-3 offset
    local_hour = (utc_dt.hour + _ARGENTINA_UTC_OFFSET_HOURS) % 24
    local_minute = utc_dt.minute
    return f"{local_hour:02d}:{local_minute:02d} hora local"


def _get_satellite_source(event: FireEvent) -> str:
    """Extract a human-readable satellite source from the event's hotspots."""
    if not event.hotspots:
        return "Desconocido"

    # Use the first hotspot's source as representative
    source = event.hotspots[0].hotspot.source
    source_map = {
        "VIIRS_SNPP_NRT": "VIIRS (Suomi NPP)",
        "VIIRS_NOAA20_NRT": "VIIRS (NOAA-20)",
        "VIIRS_NOAA21_NRT": "VIIRS (NOAA-21)",
        "MODIS_NRT": "MODIS (Terra/Aqua)",
    }
    return source_map.get(source.value, source.value)


def _severity_label_from_value(value: str) -> str:
    """Convert a Severity enum value string to its Spanish label.

    Accepts both enum values (e.g. "low") and already-translated labels
    (e.g. "BAJA") for robustness.
    """
    # Try matching against enum values first
    for sev in Severity:
        if sev.value == value:
            return severity_label(sev)

    # If already a Spanish label, return as-is
    known_labels = set(_SEVERITY_LABELS.values())
    if value.upper() in known_labels:
        return value.upper()

    return value
