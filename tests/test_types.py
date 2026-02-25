"""Tests for shared dataclass types and enums.

Validates that all contracts can be instantiated with valid data,
frozen types are immutable, enums have expected values, and
IntentBreakdown arithmetic is correct.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from firesentinel.core.types import (
    AlertChannel,
    Confidence,
    DayNight,
    EnrichedHotspot,
    FireEvent,
    IntentBreakdown,
    IntentLabel,
    PipelineRunRecord,
    PipelineStatus,
    RawHotspot,
    RoadContext,
    Severity,
    Source,
    WeatherContext,
)


# ---------------------------------------------------------------------------
# Enum value tests
# ---------------------------------------------------------------------------


class TestEnums:
    """Verify all enums have the expected values."""

    def test_source_values(self) -> None:
        assert Source.VIIRS_SNPP_NRT.value == "VIIRS_SNPP_NRT"
        assert Source.VIIRS_NOAA20_NRT.value == "VIIRS_NOAA20_NRT"
        assert Source.VIIRS_NOAA21_NRT.value == "VIIRS_NOAA21_NRT"
        assert Source.MODIS_NRT.value == "MODIS_NRT"
        assert len(Source) == 4

    def test_confidence_values(self) -> None:
        assert Confidence.LOW.value == "low"
        assert Confidence.NOMINAL.value == "nominal"
        assert Confidence.HIGH.value == "high"
        assert len(Confidence) == 3

    def test_daynight_values(self) -> None:
        assert DayNight.DAY.value == "D"
        assert DayNight.NIGHT.value == "N"
        assert len(DayNight) == 2

    def test_severity_values(self) -> None:
        assert Severity.LOW.value == "low"
        assert Severity.MEDIUM.value == "medium"
        assert Severity.HIGH.value == "high"
        assert Severity.CRITICAL.value == "critical"
        assert len(Severity) == 4

    def test_intent_label_values(self) -> None:
        assert IntentLabel.NATURAL.value == "natural"
        assert IntentLabel.UNCERTAIN.value == "uncertain"
        assert IntentLabel.SUSPICIOUS.value == "suspicious"
        assert IntentLabel.LIKELY_INTENTIONAL.value == "likely_intentional"
        assert len(IntentLabel) == 4

    def test_alert_channel_values(self) -> None:
        assert AlertChannel.TELEGRAM.value == "telegram"
        assert AlertChannel.WHATSAPP.value == "whatsapp"
        assert AlertChannel.EMAIL.value == "email"
        assert len(AlertChannel) == 3

    def test_pipeline_status_values(self) -> None:
        assert PipelineStatus.SUCCESS.value == "success"
        assert PipelineStatus.PARTIAL.value == "partial"
        assert PipelineStatus.FAILED.value == "failed"
        assert len(PipelineStatus) == 3


# ---------------------------------------------------------------------------
# Frozen dataclass immutability tests
# ---------------------------------------------------------------------------


class TestFrozenDataclasses:
    """Verify that frozen dataclasses cannot be mutated."""

    def test_raw_hotspot_is_frozen(self, sample_raw_hotspot: RawHotspot) -> None:
        with pytest.raises(AttributeError):
            sample_raw_hotspot.latitude = 0.0  # type: ignore[misc]

    def test_weather_context_is_frozen(
        self, sample_weather_context: WeatherContext
    ) -> None:
        with pytest.raises(AttributeError):
            sample_weather_context.cape = 0.0  # type: ignore[misc]

    def test_road_context_is_frozen(self, sample_road_context: RoadContext) -> None:
        with pytest.raises(AttributeError):
            sample_road_context.nearest_distance_m = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Dataclass instantiation tests
# ---------------------------------------------------------------------------


class TestDataclassInstantiation:
    """Verify all types can be created with valid data."""

    def test_raw_hotspot(self, sample_raw_hotspot: RawHotspot) -> None:
        assert sample_raw_hotspot.source == Source.VIIRS_SNPP_NRT
        assert sample_raw_hotspot.latitude == -42.22
        assert sample_raw_hotspot.longitude == -71.43
        assert sample_raw_hotspot.brightness == 345.6
        assert sample_raw_hotspot.daynight == DayNight.NIGHT
        assert sample_raw_hotspot.acq_date == date(2026, 2, 15)
        assert sample_raw_hotspot.acq_time == time(3, 30)

    def test_weather_context(self, sample_weather_context: WeatherContext) -> None:
        assert sample_weather_context.cape == 150.0
        assert sample_weather_context.has_thunderstorm is False
        assert sample_weather_context.humidity_pct == 22.0

    def test_road_context(self, sample_road_context: RoadContext) -> None:
        assert sample_road_context.nearest_distance_m == 500.0
        assert sample_road_context.nearest_road_type == "track"
        assert sample_road_context.nearest_road_ref is None

    def test_enriched_hotspot(
        self,
        sample_raw_hotspot: RawHotspot,
        sample_weather_context: WeatherContext,
        sample_road_context: RoadContext,
    ) -> None:
        enriched = EnrichedHotspot(
            hotspot=sample_raw_hotspot,
            weather=sample_weather_context,
            road=sample_road_context,
        )
        assert enriched.hotspot is sample_raw_hotspot
        assert enriched.weather is sample_weather_context
        assert enriched.road is sample_road_context

    def test_enriched_hotspot_with_none_context(
        self, sample_raw_hotspot: RawHotspot
    ) -> None:
        enriched = EnrichedHotspot(hotspot=sample_raw_hotspot)
        assert enriched.weather is None
        assert enriched.road is None

    def test_fire_event(self, sample_fire_event: FireEvent) -> None:
        assert sample_fire_event.center_lat == -42.22
        assert sample_fire_event.severity == Severity.MEDIUM
        assert len(sample_fire_event.hotspots) == 1
        assert sample_fire_event.intent is not None
        assert sample_fire_event.is_active is True

    def test_pipeline_run_record(self) -> None:
        record = PipelineRunRecord(
            id="test-run-001",
            started_at=datetime(2026, 2, 15, 3, 0),
            completed_at=datetime(2026, 2, 15, 3, 2),
            status=PipelineStatus.SUCCESS,
            hotspots_fetched=42,
            new_hotspots=5,
            events_created=2,
            events_updated=1,
            alerts_sent=3,
            duration_ms=120000,
        )
        assert record.hotspots_fetched == 42
        assert record.status == PipelineStatus.SUCCESS


# ---------------------------------------------------------------------------
# IntentBreakdown arithmetic tests
# ---------------------------------------------------------------------------


class TestIntentBreakdown:
    """Verify IntentBreakdown scoring logic."""

    def test_total_equals_sum_of_scores(self) -> None:
        breakdown = IntentBreakdown(
            lightning_score=25,
            road_score=15,
            night_score=20,
            history_score=10,
            multi_point_score=5,
            dry_conditions_score=10,
            active_signals=6,
            total_signals=6,
        )
        expected = 25 + 15 + 20 + 10 + 5 + 10
        assert breakdown.total == expected

    def test_total_with_zeros(self) -> None:
        breakdown = IntentBreakdown(
            lightning_score=0,
            road_score=0,
            night_score=0,
            history_score=0,
            multi_point_score=0,
            dry_conditions_score=0,
            active_signals=6,
            total_signals=6,
        )
        assert breakdown.total == 0

    def test_label_natural(self) -> None:
        breakdown = IntentBreakdown(
            lightning_score=0,
            road_score=10,
            night_score=0,
            history_score=5,
            multi_point_score=0,
            dry_conditions_score=5,
            active_signals=6,
            total_signals=6,
        )
        assert breakdown.label == IntentLabel.NATURAL

    def test_label_uncertain(self) -> None:
        breakdown = IntentBreakdown(
            lightning_score=15,
            road_score=10,
            night_score=10,
            history_score=0,
            multi_point_score=0,
            dry_conditions_score=5,
            active_signals=6,
            total_signals=6,
        )
        assert breakdown.label == IntentLabel.UNCERTAIN

    def test_label_suspicious(self) -> None:
        breakdown = IntentBreakdown(
            lightning_score=25,
            road_score=15,
            night_score=10,
            history_score=5,
            multi_point_score=0,
            dry_conditions_score=5,
            active_signals=6,
            total_signals=6,
        )
        assert breakdown.label == IntentLabel.SUSPICIOUS

    def test_label_likely_intentional(self) -> None:
        breakdown = IntentBreakdown(
            lightning_score=25,
            road_score=20,
            night_score=20,
            history_score=10,
            multi_point_score=5,
            dry_conditions_score=10,
            active_signals=6,
            total_signals=6,
        )
        assert breakdown.label == IntentLabel.LIKELY_INTENTIONAL

    def test_boundary_natural_25(self) -> None:
        """Score of exactly 25 should be natural."""
        breakdown = IntentBreakdown(
            lightning_score=25,
            road_score=0,
            night_score=0,
            history_score=0,
            multi_point_score=0,
            dry_conditions_score=0,
            active_signals=6,
            total_signals=6,
        )
        assert breakdown.total == 25
        assert breakdown.label == IntentLabel.NATURAL

    def test_boundary_uncertain_26(self) -> None:
        """Score of exactly 26 should be uncertain."""
        breakdown = IntentBreakdown(
            lightning_score=25,
            road_score=1,
            night_score=0,
            history_score=0,
            multi_point_score=0,
            dry_conditions_score=0,
            active_signals=6,
            total_signals=6,
        )
        assert breakdown.total == 26
        assert breakdown.label == IntentLabel.UNCERTAIN

    def test_to_dict(self) -> None:
        breakdown = IntentBreakdown(
            lightning_score=25,
            road_score=15,
            night_score=20,
            history_score=0,
            multi_point_score=0,
            dry_conditions_score=10,
            active_signals=5,
            total_signals=6,
        )
        d = breakdown.to_dict()
        assert d["lightning"] == 25
        assert d["road"] == 15
        assert d["night"] == 20
        assert d["history"] == 0
        assert d["multi_point"] == 0
        assert d["dry_conditions"] == 10
        assert d["active_signals"] == 5
        assert d["total_signals"] == 6
        assert d["total"] == 70
        assert d["label"] == "suspicious"

    def test_partial_signals_tracked(self) -> None:
        """When some signals are unavailable, active_signals < total_signals."""
        breakdown = IntentBreakdown(
            lightning_score=25,
            road_score=0,
            night_score=20,
            history_score=0,
            multi_point_score=0,
            dry_conditions_score=0,
            active_signals=3,
            total_signals=6,
        )
        assert breakdown.active_signals == 3
        assert breakdown.total_signals == 6
