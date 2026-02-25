"""Pytest fixtures for FireSentinel test suite.

Provides temporary database sessions, sample data objects, and shared
test utilities.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

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
from firesentinel.db.engine import get_engine, get_session_factory, init_db


@pytest.fixture
async def tmp_db(tmp_path: object) -> AsyncGenerator[AsyncSession, None]:
    """Create a temporary SQLite database with all tables.

    Yields an async session for test use, then cleans up the engine.
    """
    from pathlib import Path

    db_path = Path(str(tmp_path)) / "test.db"
    engine = get_engine(str(db_path))
    await init_db(engine)

    session_factory = get_session_factory(engine)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
def sample_raw_hotspot() -> RawHotspot:
    """Return a RawHotspot with realistic Patagonia data (Epuyen area)."""
    return RawHotspot(
        source=Source.VIIRS_SNPP_NRT,
        latitude=-42.22,
        longitude=-71.43,
        brightness=345.6,
        brightness_2=298.1,
        frp=28.5,
        confidence=Confidence.HIGH.value,
        acq_date=date(2026, 2, 15),
        acq_time=time(3, 30),
        satellite="N",
        daynight=DayNight.NIGHT,
        raw_data={"scan": "0.39", "track": "0.36", "version": "2.0NRT"},
    )


@pytest.fixture
def sample_weather_context() -> WeatherContext:
    """Return a WeatherContext with no thunderstorm activity.

    Represents dry, calm conditions typical of Patagonian arson scenarios.
    """
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


@pytest.fixture
def sample_road_context() -> RoadContext:
    """Return a RoadContext showing proximity to a dirt track road (500m)."""
    return RoadContext(
        nearest_distance_m=500.0,
        nearest_road_type="track",
        nearest_road_ref=None,
    )


@pytest.fixture
def sample_fire_event(
    sample_raw_hotspot: RawHotspot,
    sample_weather_context: WeatherContext,
    sample_road_context: RoadContext,
) -> FireEvent:
    """Return a complete FireEvent with enriched hotspots and intent scoring."""
    enriched = EnrichedHotspot(
        hotspot=sample_raw_hotspot,
        weather=sample_weather_context,
        road=sample_road_context,
    )

    intent = IntentBreakdown(
        lightning_score=25,
        road_score=15,
        night_score=20,
        history_score=0,
        multi_point_score=0,
        dry_conditions_score=10,
        active_signals=6,
        total_signals=6,
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
        nearest_road_m=500.0,
        nearest_road_type="track",
        nearest_road_ref=None,
        intent=intent,
        is_active=True,
    )
