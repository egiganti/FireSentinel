"""Tests for alert message templates.

Validates that Telegram, WhatsApp, and escalation alert messages contain
the required content in Spanish, with correct formatting per channel.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

import pytest

from firesentinel.alerts.templates import (
    format_escalation_alert,
    format_signal_description,
    format_telegram_alert,
    format_whatsapp_alert,
    intent_label,
    road_type_spanish,
    severity_emoji,
    severity_label,
)
from firesentinel.core.types import (
    Confidence,
    DayNight,
    EnrichedHotspot,
    FireEvent,
    IntentBreakdown,
    IntentLabel,
    RawHotspot,
    RoadContext,
    Severity,
    Source,
    WeatherContext,
)


# ---------------------------------------------------------------------------
# Test helpers -- build FireEvent instances with controlled data
# ---------------------------------------------------------------------------


def _make_hotspot(
    *,
    lat: float = -42.22,
    lon: float = -71.43,
    frp: float = 28.5,
    acq_date: date | None = None,
    acq_time: time | None = None,
    daynight: DayNight = DayNight.NIGHT,
    source: Source = Source.VIIRS_SNPP_NRT,
) -> RawHotspot:
    """Build a RawHotspot with sensible defaults."""
    return RawHotspot(
        source=source,
        latitude=lat,
        longitude=lon,
        brightness=345.6,
        brightness_2=298.1,
        frp=frp,
        confidence=Confidence.HIGH.value,
        acq_date=acq_date or date(2026, 2, 15),
        acq_time=acq_time or time(3, 30),
        satellite="N",
        daynight=daynight,
    )


def _make_weather(
    *,
    humidity_pct: float = 22.0,
    has_thunderstorm: bool = False,
) -> WeatherContext:
    """Build a WeatherContext with sensible defaults."""
    return WeatherContext(
        cape=150.0,
        convective_inhibition=25.0,
        weather_code=0,
        temperature_c=28.5,
        wind_speed_kmh=15.0,
        humidity_pct=humidity_pct,
        precipitation_mm_6h=0.0,
        precipitation_mm_72h=0.0,
        has_thunderstorm=has_thunderstorm,
    )


def _make_road(
    *,
    distance_m: float = 500.0,
    road_type: str = "track",
    road_ref: str | None = None,
) -> RoadContext:
    """Build a RoadContext with sensible defaults."""
    return RoadContext(
        nearest_distance_m=distance_m,
        nearest_road_type=road_type,
        nearest_road_ref=road_ref,
    )


def _make_intent(
    *,
    lightning: int = 25,
    road: int = 15,
    night: int = 20,
    history: int = 0,
    multi_point: int = 0,
    dry: int = 10,
    active: int = 6,
    total: int = 6,
) -> IntentBreakdown:
    """Build an IntentBreakdown with sensible defaults."""
    return IntentBreakdown(
        lightning_score=lightning,
        road_score=road,
        night_score=night,
        history_score=history,
        multi_point_score=multi_point,
        dry_conditions_score=dry,
        active_signals=active,
        total_signals=total,
    )


def _make_fire_event(
    *,
    lat: float = -42.22,
    lon: float = -71.43,
    severity: Severity = Severity.MEDIUM,
    max_frp: float = 28.5,
    nearest_town: str | None = "Epuyen",
    province: str | None = "Chubut",
    nearest_road_m: float | None = 500.0,
    nearest_road_type: str | None = "track",
    nearest_road_ref: str | None = None,
    intent: IntentBreakdown | None = None,
    weather_data: dict[str, float | int | bool] | None = None,
    hotspots: list[EnrichedHotspot] | None = None,
    first_detected: datetime | None = None,
) -> FireEvent:
    """Build a full FireEvent with sensible defaults."""
    if intent is None:
        intent = _make_intent()

    if weather_data is None:
        weather_data = {"humidity_pct": 22.0}

    if hotspots is None:
        enriched = EnrichedHotspot(
            hotspot=_make_hotspot(),
            weather=_make_weather(),
            road=_make_road(),
        )
        hotspots = [enriched]

    return FireEvent(
        id=str(uuid.uuid4()),
        center_lat=lat,
        center_lon=lon,
        hotspots=hotspots,
        severity=severity,
        max_frp=max_frp,
        first_detected=first_detected or datetime(2026, 2, 15, 3, 30),
        last_updated=datetime(2026, 2, 15, 3, 45),
        province=province,
        nearest_town=nearest_town,
        nearest_road_m=nearest_road_m,
        nearest_road_type=nearest_road_type,
        nearest_road_ref=nearest_road_ref,
        weather_data=weather_data,
        intent=intent,
        is_active=True,
    )


# ---------------------------------------------------------------------------
# Telegram alert tests
# ---------------------------------------------------------------------------


class TestTelegramAlert:
    """Verify Telegram alert message formatting."""

    def test_telegram_alert_contains_location(self) -> None:
        """Verify lat/lon appear in the output."""
        event = _make_fire_event(lat=-42.22, lon=-71.43)
        msg = format_telegram_alert(event)
        assert "-42.22" in msg
        assert "-71.43" in msg

    def test_telegram_alert_contains_maps_link(self) -> None:
        """Verify Google Maps URL is present."""
        event = _make_fire_event(lat=-42.22, lon=-71.43)
        msg = format_telegram_alert(event)
        assert "https://www.google.com/maps?q=-42.22,-71.43" in msg

    def test_telegram_alert_contains_severity(self) -> None:
        """Verify Spanish severity label is present."""
        event = _make_fire_event(severity=Severity.HIGH)
        msg = format_telegram_alert(event)
        assert "ALTA" in msg

    def test_telegram_alert_contains_intent_score(self) -> None:
        """Verify 'Intencionalidad: XX/100' format is present."""
        intent = _make_intent(lightning=25, road=15, night=20, dry=10)
        event = _make_fire_event(intent=intent)
        total = intent.total
        assert f"Intencionalidad: {total}/100" in format_telegram_alert(event)

    def test_telegram_alert_contains_intent_label(self) -> None:
        """Verify Spanish intent label is present."""
        intent = _make_intent(
            lightning=25, road=20, night=20, history=10, multi_point=5, dry=10
        )
        event = _make_fire_event(intent=intent)
        msg = format_telegram_alert(event)
        # Total = 90, which is LIKELY_INTENTIONAL -> PROBABLE INTENCIONAL
        assert "PROBABLE INTENCIONAL" in msg

    def test_telegram_alert_contains_signals(self) -> None:
        """Verify signal descriptions appear in Spanish."""
        event = _make_fire_event()
        msg = format_telegram_alert(event)
        assert "Sin actividad de rayos" in msg
        assert "camino rural" in msg
        assert "noche" in msg

    def test_telegram_alert_contains_disclaimer(self) -> None:
        """Verify calibration disclaimer is present."""
        event = _make_fire_event()
        msg = format_telegram_alert(event)
        assert "Modelo basado en patrones 2025-2026" in msg
        assert "No reemplaza investigacion oficial" in msg

    def test_telegram_alert_contains_satellite_source(self) -> None:
        """Verify satellite info is present."""
        event = _make_fire_event()
        msg = format_telegram_alert(event)
        assert "VIIRS" in msg
        assert "Fuente:" in msg
        assert "Detectado:" in msg


# ---------------------------------------------------------------------------
# WhatsApp alert tests
# ---------------------------------------------------------------------------


class TestWhatsAppAlert:
    """Verify WhatsApp alert message formatting."""

    def test_whatsapp_alert_no_markdown(self) -> None:
        """Verify no Markdown syntax in WhatsApp output."""
        event = _make_fire_event()
        msg = format_whatsapp_alert(event)
        # Markdown links use [text](url) syntax
        assert "](" not in msg
        # Markdown bold uses ** or __
        assert "**" not in msg
        assert "__" not in msg

    def test_whatsapp_alert_contains_same_info(self) -> None:
        """Verify WhatsApp has same key info as Telegram."""
        event = _make_fire_event(lat=-42.22, lon=-71.43)
        wa_msg = format_whatsapp_alert(event)

        # All key elements should be present
        assert "-42.22" in wa_msg
        assert "-71.43" in wa_msg
        assert "https://www.google.com/maps?q=-42.22,-71.43" in wa_msg
        assert "MEDIA" in wa_msg
        assert "Intencionalidad:" in wa_msg
        assert "Modelo basado en patrones 2025-2026" in wa_msg
        assert "VIIRS" in wa_msg


# ---------------------------------------------------------------------------
# Escalation alert tests
# ---------------------------------------------------------------------------


class TestEscalationAlert:
    """Verify escalation alert formatting."""

    def test_escalation_shows_changes(self) -> None:
        """Verify 'ACTUALIZACION' header and severity/intent delta."""
        event = _make_fire_event(severity=Severity.HIGH)
        msg = format_escalation_alert(
            event, previous_severity="medium", previous_intent_score=45
        )
        assert "ACTUALIZACION" in msg
        assert "MEDIA" in msg
        assert "ALTA" in msg
        assert "\u2192" in msg  # arrow character
        assert "45" in msg


# ---------------------------------------------------------------------------
# Label and emoji helper tests
# ---------------------------------------------------------------------------


class TestSeverityLabels:
    """Verify all severity levels have Spanish labels."""

    def test_severity_labels_spanish(self) -> None:
        """All 4 severity levels return expected Spanish labels."""
        assert severity_label(Severity.LOW) == "BAJA"
        assert severity_label(Severity.MEDIUM) == "MEDIA"
        assert severity_label(Severity.HIGH) == "ALTA"
        assert severity_label(Severity.CRITICAL) == "CRITICA"


class TestIntentLabels:
    """Verify all intent labels have Spanish translations."""

    def test_intent_labels_spanish(self) -> None:
        """All 4 intent labels return expected Spanish labels."""
        assert intent_label(IntentLabel.NATURAL) == "NATURAL"
        assert intent_label(IntentLabel.UNCERTAIN) == "INCIERTO"
        assert intent_label(IntentLabel.SUSPICIOUS) == "SOSPECHOSO"
        assert intent_label(IntentLabel.LIKELY_INTENTIONAL) == "PROBABLE INTENCIONAL"


class TestRoadTypes:
    """Verify road type translations."""

    def test_road_types_spanish(self) -> None:
        """All road types translate correctly to Spanish."""
        assert road_type_spanish("track") == "camino rural"
        assert road_type_spanish("path") == "sendero"
        assert road_type_spanish("tertiary") == "camino terciario"
        assert road_type_spanish("unclassified") == "camino sin clasificar"
        assert road_type_spanish("secondary") == "ruta secundaria"
        assert road_type_spanish("primary") == "ruta principal"
        assert road_type_spanish("trunk") == "ruta troncal"
        assert road_type_spanish("motorway") == "autopista"
        assert road_type_spanish("none") == "sin camino cercano"


class TestSeverityEmojis:
    """Verify severity emoji mapping."""

    def test_severity_emojis(self) -> None:
        """Each severity has a distinct color emoji."""
        assert severity_emoji(Severity.LOW) == "\U0001f7e2"
        assert severity_emoji(Severity.MEDIUM) == "\U0001f7e1"
        assert severity_emoji(Severity.HIGH) == "\U0001f7e0"
        assert severity_emoji(Severity.CRITICAL) == "\U0001f534"


# ---------------------------------------------------------------------------
# Signal description tests
# ---------------------------------------------------------------------------


class TestSignalDescriptions:
    """Verify signal description generation."""

    def test_signal_descriptions_all_signals(self) -> None:
        """Verify descriptions for all 6 signals when all are non-zero."""
        intent = _make_intent(
            lightning=25,
            road=15,
            night=20,
            history=10,
            multi_point=5,
            dry=10,
        )
        event = _make_fire_event(intent=intent)
        descriptions = format_signal_description(intent, event)

        assert len(descriptions) == 6
        # Check each signal type is represented
        assert any("rayos" in d for d in descriptions)
        assert any("camino rural" in d or "ruta" in d for d in descriptions)
        assert any("noche" in d for d in descriptions)
        assert any("previo" in d for d in descriptions)
        assert any("focos simultaneos" in d for d in descriptions)
        assert any("secas" in d or "humedad" in d for d in descriptions)

    def test_signal_descriptions_no_zero_signals(self) -> None:
        """Zero-score signals are excluded from descriptions."""
        intent = _make_intent(
            lightning=25,
            road=0,
            night=0,
            history=0,
            multi_point=0,
            dry=0,
        )
        event = _make_fire_event(intent=intent)
        descriptions = format_signal_description(intent, event)

        assert len(descriptions) == 1
        assert "rayos" in descriptions[0]


# ---------------------------------------------------------------------------
# Edge case tests
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Verify graceful handling of missing or null data."""

    def test_format_with_missing_town(self) -> None:
        """No nearest_town -- output still valid."""
        event = _make_fire_event(nearest_town=None, province=None)
        msg = format_telegram_alert(event)

        # Should still contain coordinates
        assert "-42.22" in msg
        assert "-71.43" in msg
        # Should not contain "None" as text
        assert "None" not in msg
        # Maps link still works
        assert "https://www.google.com/maps?q=-42.22,-71.43" in msg

    def test_format_with_no_road_ref(self) -> None:
        """Road ref is None -- doesn't show 'None' in text."""
        event = _make_fire_event(nearest_road_ref=None)
        msg = format_telegram_alert(event)

        # The word "None" should not appear in user-facing text
        assert "None" not in msg
        # Road description should still be present
        assert "camino rural" in msg
