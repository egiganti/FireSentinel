"""Tests for the intentionality scoring engine.

This is the product's core differentiator -- no other public system scores
fire intentionality. These tests verify every signal independently, the
renormalization logic for missing data, and full integration scenarios.

All expected scores are derived from the real config/monitoring.yml weights.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time

import pytest

from firesentinel.config import IntentScoringConfig, get_yaml_config
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
from firesentinel.processing.classifier import IntentClassifier


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> IntentScoringConfig:
    """Load real intent scoring config from monitoring.yml."""
    return get_yaml_config().intent_scoring


@pytest.fixture
def classifier(config: IntentScoringConfig) -> IntentClassifier:
    """Create an IntentClassifier using the real config."""
    return IntentClassifier(config)


def _make_hotspot(
    acq_time: time = time(3, 30),
    acq_date: date = date(2026, 2, 15),
) -> RawHotspot:
    """Build a RawHotspot with customizable acquisition time."""
    return RawHotspot(
        source=Source.VIIRS_SNPP_NRT,
        latitude=-42.22,
        longitude=-71.43,
        brightness=345.6,
        brightness_2=298.1,
        frp=28.5,
        confidence=Confidence.HIGH.value,
        acq_date=acq_date,
        acq_time=acq_time,
        satellite="N",
        daynight=DayNight.NIGHT,
    )


def _make_weather(
    cape: float = 150.0,
    has_thunderstorm: bool = False,
    humidity_pct: float = 22.0,
    precipitation_mm_72h: float = 0.0,
) -> WeatherContext:
    """Build a WeatherContext with customizable conditions."""
    return WeatherContext(
        cape=cape,
        convective_inhibition=25.0,
        weather_code=0,
        temperature_c=28.5,
        wind_speed_kmh=15.0,
        humidity_pct=humidity_pct,
        precipitation_mm_6h=0.0,
        precipitation_mm_72h=precipitation_mm_72h,
        has_thunderstorm=has_thunderstorm,
    )


def _make_road(distance_m: float = 100.0) -> RoadContext:
    """Build a RoadContext with customizable distance."""
    return RoadContext(
        nearest_distance_m=distance_m,
        nearest_road_type="track",
        nearest_road_ref=None,
    )


def _make_event(
    weather: WeatherContext | None = None,
    road: RoadContext | None = None,
    acq_time: time = time(3, 30),
) -> FireEvent:
    """Build a FireEvent with customizable weather, road, and time."""
    hotspot = _make_hotspot(acq_time=acq_time)
    enriched = EnrichedHotspot(
        hotspot=hotspot,
        weather=weather,
        road=road,
    )
    return FireEvent(
        id=str(uuid.uuid4()),
        center_lat=-42.22,
        center_lon=-71.43,
        hotspots=[enriched],
        severity=Severity.MEDIUM,
        max_frp=28.5,
        first_detected=datetime(2026, 2, 15, 3, 30),
        last_updated=datetime(2026, 2, 15, 3, 45),
        province="Chubut",
        nearest_town="Epuyen",
    )


# ---------------------------------------------------------------------------
# Lightning signal tests
# ---------------------------------------------------------------------------


class TestLightningScoring:
    """Tests for the lightning absence signal (weight: 25)."""

    def test_lightning_no_thunderstorm_low_cape(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """No thunderstorm and CAPE < 500: full weight (no natural ignition)."""
        weather = _make_weather(cape=150.0, has_thunderstorm=False)
        score, available = classifier._score_lightning(weather)
        assert available is True
        assert score == config.weights.lightning_absence

    def test_lightning_thunderstorm_detected(
        self, classifier: IntentClassifier
    ) -> None:
        """Thunderstorm detected: 0 score (natural ignition likely)."""
        weather = _make_weather(has_thunderstorm=True)
        score, available = classifier._score_lightning(weather)
        assert available is True
        assert score == 0

    def test_lightning_high_cape(self, classifier: IntentClassifier) -> None:
        """CAPE >= 1000: 0 score (high lightning risk)."""
        weather = _make_weather(cape=1200.0)
        score, available = classifier._score_lightning(weather)
        assert available is True
        assert score == 0

    def test_lightning_moderate_cape(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """CAPE 500-1000: partial score (moderate risk)."""
        weather = _make_weather(cape=750.0)
        score, available = classifier._score_lightning(weather)
        assert available is True
        expected = round(config.weights.lightning_absence * 0.6)
        assert score == expected

    def test_lightning_weather_unavailable(
        self, classifier: IntentClassifier
    ) -> None:
        """Weather unavailable: signal excluded, not zeroed."""
        score, available = classifier._score_lightning(None)
        assert available is False
        assert score == 0


# ---------------------------------------------------------------------------
# Road proximity signal tests
# ---------------------------------------------------------------------------


class TestRoadProximityScoring:
    """Tests for the road proximity signal (weight: 20)."""

    def test_road_very_close(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """< 200m from road: full score."""
        road = _make_road(distance_m=100.0)
        score, available = classifier._score_road_proximity(road)
        assert available is True
        assert score == config.weights.road_proximity

    def test_road_close(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """200-500m from road: 75% score."""
        road = _make_road(distance_m=350.0)
        score, available = classifier._score_road_proximity(road)
        assert available is True
        assert score == round(config.weights.road_proximity * 0.75)

    def test_road_near(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """500-1000m from road: 50% score."""
        road = _make_road(distance_m=750.0)
        score, available = classifier._score_road_proximity(road)
        assert available is True
        assert score == round(config.weights.road_proximity * 0.50)

    def test_road_moderate(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """1000-2000m from road: 25% score."""
        road = _make_road(distance_m=1500.0)
        score, available = classifier._score_road_proximity(road)
        assert available is True
        assert score == round(config.weights.road_proximity * 0.25)

    def test_road_far(self, classifier: IntentClassifier) -> None:
        """> 2000m from road: 0 score."""
        road = _make_road(distance_m=3000.0)
        score, available = classifier._score_road_proximity(road)
        assert available is True
        assert score == 0

    def test_road_unavailable(self, classifier: IntentClassifier) -> None:
        """Road data unavailable: signal excluded."""
        score, available = classifier._score_road_proximity(None)
        assert available is False
        assert score == 0


# ---------------------------------------------------------------------------
# Nighttime signal tests
# ---------------------------------------------------------------------------


class TestNighttimeScoring:
    """Tests for the nighttime ignition signal (weight: 20).

    FIRMS acq_time is in UTC. Argentina local time is UTC-3.
    """

    def test_night_peak_hours(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """23:00 local (02:00 UTC): full score (peak hours)."""
        # 23:00 ART = 02:00 UTC
        event = _make_event(acq_time=time(2, 0))
        score, available = classifier._score_nighttime(event)
        assert available is True
        assert score == config.weights.nighttime_ignition

    def test_night_shoulder_hours(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """06:00 local (09:00 UTC): half score (morning shoulder)."""
        # 06:00 ART = 09:00 UTC
        event = _make_event(acq_time=time(9, 0))
        score, available = classifier._score_nighttime(event)
        assert available is True
        assert score == round(config.weights.nighttime_ignition * 0.5)

    def test_daytime(self, classifier: IntentClassifier) -> None:
        """14:00 local (17:00 UTC): 0 score (daytime)."""
        # 14:00 ART = 17:00 UTC
        event = _make_event(acq_time=time(17, 0))
        score, available = classifier._score_nighttime(event)
        assert available is True
        assert score == 0


# ---------------------------------------------------------------------------
# Historical repeat signal tests
# ---------------------------------------------------------------------------


class TestHistoricalScoring:
    """Tests for the historical repeat signal (weight: 15)."""

    def test_history_recent(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """Fire 6 months ago: full score."""
        event = _make_event()
        score, available = classifier._score_historical(
            event, history_count=1, months_since_last=6
        )
        assert available is True
        assert score == config.weights.historical_repeat

    def test_history_old(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """Fire 30 months ago: partial score (24-36 months tier)."""
        event = _make_event()
        score, available = classifier._score_historical(
            event, history_count=1, months_since_last=30
        )
        assert available is True
        assert score == round(config.weights.historical_repeat * 0.33)

    def test_no_history(self, classifier: IntentClassifier) -> None:
        """No prior fires: 0 score."""
        event = _make_event()
        score, available = classifier._score_historical(
            event, history_count=0, months_since_last=None
        )
        assert available is True
        assert score == 0


# ---------------------------------------------------------------------------
# Multi-point ignition signal tests
# ---------------------------------------------------------------------------


class TestMultiPointScoring:
    """Tests for the multi-point ignition signal (weight: 10)."""

    def test_multi_point_two_nearby(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """2+ events within 5km/2h: full score."""
        score, available = classifier._score_multi_point(nearby_event_count=2)
        assert available is True
        assert score == config.weights.multi_point_ignition

    def test_multi_point_none(self, classifier: IntentClassifier) -> None:
        """No nearby events: 0 score."""
        score, available = classifier._score_multi_point(nearby_event_count=0)
        assert available is True
        assert score == 0


# ---------------------------------------------------------------------------
# Dry conditions signal tests
# ---------------------------------------------------------------------------


class TestDryConditionsScoring:
    """Tests for the dry conditions signal (weight: 10)."""

    def test_dry_and_no_rain(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """Humidity < 25% and no rain in 72h: full score."""
        weather = _make_weather(humidity_pct=20.0, precipitation_mm_72h=0.0)
        score, available = classifier._score_dry_conditions(weather)
        assert available is True
        assert score == config.weights.dry_conditions

    def test_moderate_dry(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """Humidity 25-35% and little rain: half score."""
        weather = _make_weather(humidity_pct=30.0, precipitation_mm_72h=1.0)
        score, available = classifier._score_dry_conditions(weather)
        assert available is True
        assert score == round(config.weights.dry_conditions * 0.5)

    def test_wet_conditions(self, classifier: IntentClassifier) -> None:
        """Wet conditions: 0 score."""
        weather = _make_weather(humidity_pct=60.0, precipitation_mm_72h=15.0)
        score, available = classifier._score_dry_conditions(weather)
        assert available is True
        assert score == 0


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestClassifyIntegration:
    """Full classification integration tests."""

    def test_classify_all_signals_suspicious(
        self, classifier: IntentClassifier
    ) -> None:
        """All signals at moderate levels produce a suspicious classification.

        Scenario: moderate CAPE, road at 350m, shoulder evening hours,
        fire 18 months ago, 1 nearby event, moderate dry.
        """
        weather = _make_weather(
            cape=750.0,  # moderate CAPE -> partial lightning score
            has_thunderstorm=False,
            humidity_pct=30.0,  # moderate dry
            precipitation_mm_72h=1.0,
        )
        road = _make_road(distance_m=350.0)  # 200-500m -> 75% road score
        # 21:00 ART = 00:00 UTC (next day) -> shoulder evening
        event = _make_event(
            weather=weather,
            road=road,
            acq_time=time(23, 0),  # 23:00 UTC = 20:00 ART -> shoulder evening
        )

        breakdown = classifier.classify(
            event,
            history_count=1,
            months_since_last=18,  # 12-24 months -> 67% history
            nearby_event_count=1,  # 1 nearby -> 50% multi
        )

        # All 6 signals should be active
        assert breakdown.active_signals == 6
        assert breakdown.total_signals == 6
        # Verify the total falls in suspicious range (51-75)
        assert 51 <= breakdown.total <= 75
        assert breakdown.label == IntentLabel.SUSPICIOUS

    def test_classify_likely_intentional(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """All signals at maximum produce likely_intentional (score = 100)."""
        weather = _make_weather(
            cape=100.0,
            has_thunderstorm=False,
            humidity_pct=20.0,
            precipitation_mm_72h=0.0,
        )
        road = _make_road(distance_m=50.0)
        # 01:00 ART = 04:00 UTC -> peak night
        event = _make_event(
            weather=weather,
            road=road,
            acq_time=time(4, 0),
        )

        breakdown = classifier.classify(
            event,
            history_count=3,
            months_since_last=6,
            nearby_event_count=3,
        )

        assert breakdown.active_signals == 6
        expected_total = (
            config.weights.lightning_absence
            + config.weights.road_proximity
            + config.weights.nighttime_ignition
            + config.weights.historical_repeat
            + config.weights.multi_point_ignition
            + config.weights.dry_conditions
        )
        assert breakdown.total == expected_total
        assert breakdown.total == 100
        assert breakdown.label == IntentLabel.LIKELY_INTENTIONAL

    def test_classify_natural(self, classifier: IntentClassifier) -> None:
        """Thunderstorm + far from road + daytime = low score, natural."""
        weather = _make_weather(
            cape=1500.0,
            has_thunderstorm=True,
            humidity_pct=60.0,
            precipitation_mm_72h=20.0,
        )
        road = _make_road(distance_m=5000.0)
        # 14:00 ART = 17:00 UTC -> daytime
        event = _make_event(
            weather=weather,
            road=road,
            acq_time=time(17, 0),
        )

        breakdown = classifier.classify(
            event,
            history_count=0,
            months_since_last=None,
            nearby_event_count=0,
        )

        assert breakdown.active_signals == 6
        assert breakdown.total <= 25
        assert breakdown.label == IntentLabel.NATURAL
        # All individual scores should be 0 in this scenario
        assert breakdown.lightning_score == 0
        assert breakdown.road_score == 0
        assert breakdown.night_score == 0
        assert breakdown.history_score == 0
        assert breakdown.multi_point_score == 0
        assert breakdown.dry_conditions_score == 0

    def test_renormalization_with_missing_signals(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """Weather unavailable removes 2 signals; verify renormalization.

        With weather=None, lightning (25) and dry_conditions (10) are excluded.
        Available max weight = 20 + 20 + 15 + 10 = 65.
        If all available signals score max, renormalized total should be 100.
        """
        road = _make_road(distance_m=50.0)  # full road score
        # 01:00 ART = 04:00 UTC -> peak night
        event = _make_event(
            weather=None,
            road=road,
            acq_time=time(4, 0),
        )

        breakdown = classifier.classify(
            event,
            history_count=2,
            months_since_last=6,  # full history score
            nearby_event_count=3,  # full multi-point score
        )

        assert breakdown.active_signals == 4
        assert breakdown.total_signals == 6
        # Lightning and dry conditions should be 0 (unavailable)
        assert breakdown.lightning_score == 0
        assert breakdown.dry_conditions_score == 0
        # Total should be renormalized to ~100 since all available signals are max
        # Available weights: road(20) + night(20) + history(15) + multi(10) = 65
        # Raw scores: 20 + 20 + 15 + 10 = 65
        # Renormalized: 65 * 100/65 = 100
        assert breakdown.total == 100

    def test_active_signals_count(
        self, classifier: IntentClassifier
    ) -> None:
        """Verify correct active signal count with partial data."""
        # No weather (excludes lightning + dry) but road available
        road = _make_road(distance_m=500.0)
        event = _make_event(weather=None, road=road)

        breakdown = classifier.classify(event)

        # 4 available: road, nighttime, history, multi_point
        # 2 unavailable: lightning, dry_conditions
        assert breakdown.active_signals == 4
        assert breakdown.total_signals == 6

    def test_all_signals_unavailable(
        self, classifier: IntentClassifier
    ) -> None:
        """No weather and no road: only time-based signals available.

        Lightning (weather), road (road), and dry_conditions (weather) are
        unavailable. Nighttime, history, and multi_point are always available.
        """
        event = _make_event(weather=None, road=None)

        breakdown = classifier.classify(event)

        # 3 always-available: nighttime, history, multi_point
        assert breakdown.active_signals == 3
        assert breakdown.total_signals == 6
        assert breakdown.lightning_score == 0
        assert breakdown.road_score == 0
        assert breakdown.dry_conditions_score == 0
        # Label determined by renormalized total from available signals
        assert isinstance(breakdown.label, IntentLabel)

    def test_completely_no_data_graceful(
        self, classifier: IntentClassifier
    ) -> None:
        """All available signals score 0: natural classification, score 0."""
        # Daytime, no weather, no road, no history, no nearby events
        # 14:00 ART = 17:00 UTC -> daytime (nighttime = 0)
        event = _make_event(
            weather=None,
            road=None,
            acq_time=time(17, 0),
        )

        breakdown = classifier.classify(
            event,
            history_count=0,
            months_since_last=None,
            nearby_event_count=0,
        )

        assert breakdown.total == 0
        assert breakdown.label == IntentLabel.NATURAL
        assert breakdown.active_signals == 3  # nighttime, history, multi always active


# ---------------------------------------------------------------------------
# Renormalization edge cases
# ---------------------------------------------------------------------------


class TestRenormalization:
    """Edge cases for the weight renormalization logic."""

    def test_partial_renormalization_proportional(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """Verify partial scores renormalize proportionally.

        With weather=None (removing lightning=25 and dry=10),
        if road scores 50% (10/20), the renormalized road score should be
        10 * (100/65) = ~15.
        """
        road = _make_road(distance_m=750.0)  # 500-1000m -> 50% of 20 = 10
        # 14:00 ART = 17:00 UTC -> daytime (nighttime = 0)
        event = _make_event(
            weather=None,
            road=road,
            acq_time=time(17, 0),
        )

        breakdown = classifier.classify(
            event,
            history_count=0,
            months_since_last=None,
            nearby_event_count=0,
        )

        # Available weights: road(20) + night(20) + history(15) + multi(10) = 65
        # Raw available score: road=10, night=0, history=0, multi=0 -> total raw=10
        # Renormalized road: round(10 * 100/65) = round(15.38) = 15
        # Renormalized total: 15
        assert breakdown.road_score == round(10 * 100 / 65)
        assert breakdown.total == round(10 * 100 / 65)

    def test_renormalization_cap_at_100(
        self, classifier: IntentClassifier
    ) -> None:
        """Renormalized total should never exceed 100 even with rounding."""
        # Use all max signals to verify capping
        weather = _make_weather(cape=100.0, humidity_pct=20.0, precipitation_mm_72h=0.0)
        road = _make_road(distance_m=50.0)
        event = _make_event(
            weather=weather,
            road=road,
            acq_time=time(4, 0),  # peak night
        )

        breakdown = classifier.classify(
            event,
            history_count=3,
            months_since_last=6,
            nearby_event_count=3,
        )

        assert breakdown.total <= 100


# ---------------------------------------------------------------------------
# Shoulder evening hour test
# ---------------------------------------------------------------------------


class TestShoulderEvening:
    """Additional nighttime boundary tests."""

    def test_shoulder_evening_hours(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """21:00 local (00:00 UTC next day) -> shoulder evening, half score."""
        # 21:00 ART = 00:00 UTC
        event = _make_event(acq_time=time(0, 0))
        score, available = classifier._score_nighttime(event)
        assert available is True
        assert score == round(config.weights.nighttime_ignition * 0.5)

    def test_boundary_peak_start(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """22:00 local (01:00 UTC): start of peak, full score."""
        # 22:00 ART = 01:00 UTC
        event = _make_event(acq_time=time(1, 0))
        score, available = classifier._score_nighttime(event)
        assert available is True
        assert score == config.weights.nighttime_ignition

    def test_boundary_peak_end(
        self, classifier: IntentClassifier, config: IntentScoringConfig
    ) -> None:
        """04:59 local (07:59 UTC): still peak hours, full score."""
        # 04:00 ART = 07:00 UTC
        event = _make_event(acq_time=time(7, 0))
        score, available = classifier._score_nighttime(event)
        assert available is True
        assert score == config.weights.nighttime_ignition
