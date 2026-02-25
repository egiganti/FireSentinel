"""Tests for the pipeline orchestrator and scheduler.

Uses mocks for all external dependencies to verify pipeline stage
orchestration, error handling, timing, and count recording.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from firesentinel.core.pipeline import Pipeline
from firesentinel.core.scheduler import run_once
from firesentinel.core.types import (
    DayNight,
    EnrichedHotspot,
    FireEvent,
    IntentBreakdown,
    PipelineRunRecord,
    PipelineStatus,
    RawHotspot,
    RoadContext,
    Severity,
    Source,
    WeatherContext,
)

# ---------------------------------------------------------------------------
# Test data factories
# ---------------------------------------------------------------------------


def _make_raw_hotspot(
    lat: float = -42.22,
    lon: float = -71.43,
    frp: float = 28.5,
) -> RawHotspot:
    """Create a sample RawHotspot for testing."""
    return RawHotspot(
        source=Source.VIIRS_SNPP_NRT,
        latitude=lat,
        longitude=lon,
        brightness=345.6,
        brightness_2=298.1,
        frp=frp,
        confidence="high",
        acq_date=date(2026, 2, 15),
        acq_time=time(3, 30),
        satellite="N",
        daynight=DayNight.NIGHT,
        raw_data={},
    )


def _make_weather_context() -> WeatherContext:
    """Create a sample WeatherContext."""
    return WeatherContext(
        cape=150.0,
        convective_inhibition=25.0,
        weather_code=0,
        temperature_c=28.5,
        wind_speed_kmh=15.0,
        humidity_pct=22.0,
        precipitation_mm_6h=0.0,
        precipitation_mm_72h=0.0,
        has_thunderstorm=False,
    )


def _make_road_context() -> RoadContext:
    """Create a sample RoadContext."""
    return RoadContext(
        nearest_distance_m=500.0,
        nearest_road_type="track",
        nearest_road_ref=None,
    )


def _make_fire_event(
    hotspots: list[EnrichedHotspot] | None = None,
) -> FireEvent:
    """Create a sample FireEvent for testing."""
    if hotspots is None:
        hs = _make_raw_hotspot()
        enriched = EnrichedHotspot(
            hotspot=hs,
            weather=_make_weather_context(),
            road=_make_road_context(),
        )
        hotspots = [enriched]

    return FireEvent(
        id=str(uuid.uuid4()),
        center_lat=-42.22,
        center_lon=-71.43,
        hotspots=hotspots,
        severity=Severity.MEDIUM,
        max_frp=28.5,
        first_detected=datetime(2026, 2, 15, 3, 30),
        last_updated=datetime(2026, 2, 15, 3, 45),
        is_active=True,
    )


def _make_intent_breakdown() -> IntentBreakdown:
    """Create a sample IntentBreakdown."""
    return IntentBreakdown(
        lightning_score=25,
        road_score=15,
        night_score=20,
        history_score=0,
        multi_point_score=0,
        dry_conditions_score=10,
        active_signals=6,
        total_signals=6,
    )


# ---------------------------------------------------------------------------
# Mock session factory
# ---------------------------------------------------------------------------


def _mock_session_factory() -> AsyncMock:
    """Create a mock async session factory that acts as a context manager.

    Returns an AsyncMock that, when called and used as ``async with``,
    yields a mock session with commit/flush/execute methods.
    """
    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()

    # Make session usable as async context manager
    context = AsyncMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)

    factory = MagicMock()
    factory.return_value = context

    return factory


# ---------------------------------------------------------------------------
# Mock YAML config
# ---------------------------------------------------------------------------


def _mock_yaml_config() -> MagicMock:
    """Create a mock YAMLConfig with essential attributes."""
    config = MagicMock()
    config.monitoring.poll_interval_minutes = 15
    config.monitoring.bbox.full_patagonia = [-74, -50, -65, -38]
    config.monitoring.sources = [
        "VIIRS_SNPP_NRT",
        "VIIRS_NOAA20_NRT",
        "VIIRS_NOAA21_NRT",
        "MODIS_NRT",
    ]
    config.intent_scoring.weights.lightning_absence = 25
    config.intent_scoring.weights.road_proximity = 20
    config.intent_scoring.weights.nighttime_ignition = 20
    config.intent_scoring.weights.historical_repeat = 15
    config.intent_scoring.weights.multi_point_ignition = 10
    config.intent_scoring.weights.dry_conditions = 10
    return config


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------


def _create_pipeline(
    firms_client: Any = None,
    weather_client: Any = None,
    roads_client: Any = None,
    classifier: Any = None,
    dispatcher: Any = None,
    session_factory: Any = None,
    yaml_config: Any = None,
) -> Pipeline:
    """Create a Pipeline with all dependencies mocked by default."""
    return Pipeline(
        firms_client=firms_client or AsyncMock(),
        weather_client=weather_client or AsyncMock(),
        roads_client=roads_client or AsyncMock(),
        classifier=classifier or MagicMock(),
        dispatcher=dispatcher,
        session_factory=session_factory or _mock_session_factory(),
        yaml_config=yaml_config or _mock_yaml_config(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cycle_full_success() -> None:
    """Full pipeline cycle: all stages succeed, status=success."""
    hotspots = [_make_raw_hotspot(), _make_raw_hotspot(lat=-42.23)]
    events = [_make_fire_event()]
    breakdown = _make_intent_breakdown()

    firms = AsyncMock()
    firms.fetch_all_sources = AsyncMock(return_value=hotspots)

    weather = AsyncMock()
    weather.get_weather_context = AsyncMock(return_value=_make_weather_context())

    roads = AsyncMock()
    roads.get_road_context = AsyncMock(return_value=_make_road_context())

    classifier = MagicMock()
    classifier.classify = MagicMock(return_value=breakdown)

    dispatcher = AsyncMock()
    dispatcher.dispatch = AsyncMock(return_value={"telegram": 2})

    session_factory = _mock_session_factory()

    pipeline = _create_pipeline(
        firms_client=firms,
        weather_client=weather,
        roads_client=roads,
        classifier=classifier,
        dispatcher=dispatcher,
        session_factory=session_factory,
    )

    with (
        patch(
            "firesentinel.core.pipeline.deduplicate",
            new_callable=AsyncMock,
            return_value=hotspots,
        ),
        patch(
            "firesentinel.core.pipeline.store_hotspots",
            new_callable=AsyncMock,
            return_value=["id1", "id2"],
        ),
        patch(
            "firesentinel.core.pipeline.cluster_hotspots",
            new_callable=AsyncMock,
            return_value=events,
        ),
    ):
        record = await pipeline.run_cycle()

    assert record.status == PipelineStatus.SUCCESS
    assert record.hotspots_fetched == 2
    assert record.new_hotspots == 2
    assert record.alerts_sent == 2
    assert not record.errors


@pytest.mark.asyncio
async def test_run_cycle_no_new_hotspots() -> None:
    """Pipeline returns early with success when dedup filters all hotspots."""
    hotspots = [_make_raw_hotspot()]

    firms = AsyncMock()
    firms.fetch_all_sources = AsyncMock(return_value=hotspots)

    session_factory = _mock_session_factory()

    pipeline = _create_pipeline(
        firms_client=firms,
        session_factory=session_factory,
    )

    with (
        patch(
            "firesentinel.core.pipeline.deduplicate",
            new_callable=AsyncMock,
            return_value=[],  # All filtered as duplicates
        ),
        patch(
            "firesentinel.core.pipeline.store_hotspots",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        record = await pipeline.run_cycle()

    assert record.status == PipelineStatus.SUCCESS
    assert record.hotspots_fetched == 1
    assert record.new_hotspots == 0
    assert record.events_created == 0
    assert record.alerts_sent == 0


@pytest.mark.asyncio
async def test_run_cycle_firms_failure() -> None:
    """FIRMS client failure results in status=failed."""
    firms = AsyncMock()
    firms.fetch_all_sources = AsyncMock(side_effect=RuntimeError("FIRMS API timeout"))

    session_factory = _mock_session_factory()

    pipeline = _create_pipeline(
        firms_client=firms,
        session_factory=session_factory,
    )

    record = await pipeline.run_cycle()

    assert record.status == PipelineStatus.FAILED
    assert record.hotspots_fetched == 0
    assert len(record.errors) >= 1
    assert "INGEST" in record.errors[0]


@pytest.mark.asyncio
async def test_run_cycle_enrichment_partial_failure() -> None:
    """Weather fails for some hotspots, pipeline continues with partial enrichment."""
    hotspots = [_make_raw_hotspot(), _make_raw_hotspot(lat=-42.23)]
    events = [_make_fire_event()]
    breakdown = _make_intent_breakdown()

    firms = AsyncMock()
    firms.fetch_all_sources = AsyncMock(return_value=hotspots)

    # Weather fails on second call
    weather = AsyncMock()
    weather.get_weather_context = AsyncMock(side_effect=[_make_weather_context(), None])

    roads = AsyncMock()
    roads.get_road_context = AsyncMock(return_value=_make_road_context())

    classifier = MagicMock()
    classifier.classify = MagicMock(return_value=breakdown)

    session_factory = _mock_session_factory()

    pipeline = _create_pipeline(
        firms_client=firms,
        weather_client=weather,
        roads_client=roads,
        classifier=classifier,
        session_factory=session_factory,
    )

    with (
        patch(
            "firesentinel.core.pipeline.deduplicate",
            new_callable=AsyncMock,
            return_value=hotspots,
        ),
        patch(
            "firesentinel.core.pipeline.store_hotspots",
            new_callable=AsyncMock,
            return_value=["id1", "id2"],
        ),
        patch(
            "firesentinel.core.pipeline.cluster_hotspots",
            new_callable=AsyncMock,
            return_value=events,
        ),
    ):
        record = await pipeline.run_cycle()

    # Pipeline should still complete -- partial enrichment is not a hard failure
    assert record.status in (PipelineStatus.SUCCESS, PipelineStatus.PARTIAL)
    assert record.new_hotspots == 2


@pytest.mark.asyncio
async def test_run_cycle_no_dispatcher() -> None:
    """Pipeline skips alert stage when dispatcher is None."""
    hotspots = [_make_raw_hotspot()]
    events = [_make_fire_event()]
    breakdown = _make_intent_breakdown()

    firms = AsyncMock()
    firms.fetch_all_sources = AsyncMock(return_value=hotspots)

    weather = AsyncMock()
    weather.get_weather_context = AsyncMock(return_value=_make_weather_context())

    roads = AsyncMock()
    roads.get_road_context = AsyncMock(return_value=_make_road_context())

    classifier = MagicMock()
    classifier.classify = MagicMock(return_value=breakdown)

    session_factory = _mock_session_factory()

    pipeline = _create_pipeline(
        firms_client=firms,
        weather_client=weather,
        roads_client=roads,
        classifier=classifier,
        dispatcher=None,  # No dispatcher
        session_factory=session_factory,
    )

    with (
        patch(
            "firesentinel.core.pipeline.deduplicate",
            new_callable=AsyncMock,
            return_value=hotspots,
        ),
        patch(
            "firesentinel.core.pipeline.store_hotspots",
            new_callable=AsyncMock,
            return_value=["id1"],
        ),
        patch(
            "firesentinel.core.pipeline.cluster_hotspots",
            new_callable=AsyncMock,
            return_value=events,
        ),
    ):
        record = await pipeline.run_cycle()

    assert record.status == PipelineStatus.SUCCESS
    assert record.alerts_sent == 0


@pytest.mark.asyncio
async def test_run_cycle_records_timing() -> None:
    """Verify that duration_ms is recorded and is a positive integer."""
    hotspots = [_make_raw_hotspot()]

    firms = AsyncMock()
    firms.fetch_all_sources = AsyncMock(return_value=hotspots)

    session_factory = _mock_session_factory()

    pipeline = _create_pipeline(
        firms_client=firms,
        session_factory=session_factory,
    )

    with (
        patch(
            "firesentinel.core.pipeline.deduplicate",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "firesentinel.core.pipeline.store_hotspots",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        record = await pipeline.run_cycle()

    assert record.duration_ms is not None
    assert record.duration_ms >= 0
    assert record.started_at is not None
    assert record.completed_at is not None
    assert record.completed_at >= record.started_at


@pytest.mark.asyncio
async def test_run_cycle_records_counts() -> None:
    """Verify hotspots_fetched, new_hotspots, events_created, alerts_sent."""
    hotspots = [
        _make_raw_hotspot(),
        _make_raw_hotspot(lat=-42.23),
        _make_raw_hotspot(lat=-42.24),
    ]
    new_hotspots = hotspots[:2]  # 1 duplicate
    events = [_make_fire_event(), _make_fire_event()]
    breakdown = _make_intent_breakdown()

    firms = AsyncMock()
    firms.fetch_all_sources = AsyncMock(return_value=hotspots)

    weather = AsyncMock()
    weather.get_weather_context = AsyncMock(return_value=_make_weather_context())

    roads = AsyncMock()
    roads.get_road_context = AsyncMock(return_value=_make_road_context())

    classifier = MagicMock()
    classifier.classify = MagicMock(return_value=breakdown)

    dispatcher = AsyncMock()
    dispatcher.dispatch = AsyncMock(return_value={"telegram": 3, "whatsapp": 1})

    session_factory = _mock_session_factory()

    pipeline = _create_pipeline(
        firms_client=firms,
        weather_client=weather,
        roads_client=roads,
        classifier=classifier,
        dispatcher=dispatcher,
        session_factory=session_factory,
    )

    with (
        patch(
            "firesentinel.core.pipeline.deduplicate",
            new_callable=AsyncMock,
            return_value=new_hotspots,
        ),
        patch(
            "firesentinel.core.pipeline.store_hotspots",
            new_callable=AsyncMock,
            return_value=["id1", "id2"],
        ),
        patch(
            "firesentinel.core.pipeline.cluster_hotspots",
            new_callable=AsyncMock,
            return_value=events,
        ),
    ):
        record = await pipeline.run_cycle()

    assert record.hotspots_fetched == 3
    assert record.new_hotspots == 2
    assert record.alerts_sent == 4  # 3 telegram + 1 whatsapp


@pytest.mark.asyncio
async def test_enrich_batch_concurrency() -> None:
    """Verify semaphore limits concurrent enrichment calls."""
    # Create 20 hotspots to exceed the default concurrency limit of 10
    hotspots = [_make_raw_hotspot(lat=-42.0 - i * 0.01) for i in range(20)]

    max_concurrent = 0
    current_concurrent = 0
    lock = asyncio.Lock()

    weather_context = _make_weather_context()
    road_context = _make_road_context()

    async def _tracking_weather(
        latitude: float, longitude: float, detection_time: Any
    ) -> WeatherContext:
        nonlocal max_concurrent, current_concurrent
        async with lock:
            current_concurrent += 1
            if current_concurrent > max_concurrent:
                max_concurrent = current_concurrent
        await asyncio.sleep(0.01)  # Simulate API latency
        async with lock:
            current_concurrent -= 1
        return weather_context

    async def _tracking_road(latitude: float, longitude: float) -> RoadContext:
        return road_context

    weather = AsyncMock()
    weather.get_weather_context = _tracking_weather

    roads = AsyncMock()
    roads.get_road_context = _tracking_road

    pipeline = _create_pipeline(
        weather_client=weather,
        roads_client=roads,
    )

    enriched = await pipeline._enrich_batch(hotspots)

    assert len(enriched) == 20
    # The semaphore should have limited concurrency to 10
    assert max_concurrent <= 10


@pytest.mark.asyncio
async def test_run_once() -> None:
    """Verify run_once calls pipeline.run_cycle and returns the record."""
    expected_record = PipelineRunRecord(
        id=str(uuid.uuid4()),
        started_at=datetime(2026, 2, 15, 3, 30),
        completed_at=datetime(2026, 2, 15, 3, 31),
        status=PipelineStatus.SUCCESS,
        duration_ms=60000,
    )

    pipeline = AsyncMock()
    pipeline.run_cycle = AsyncMock(return_value=expected_record)

    record = await run_once(pipeline)

    pipeline.run_cycle.assert_awaited_once()
    assert record == expected_record
    assert record.status == PipelineStatus.SUCCESS
