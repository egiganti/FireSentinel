"""Tests for the clustering module.

Validates hotspot clustering into fire events, severity calculation,
centroid computation, and merging with existing database events.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from firesentinel.core.types import (
    Confidence,
    DayNight,
    EnrichedHotspot,
    RawHotspot,
    Severity,
    Source,
)
from firesentinel.db.models import FireEvent as FireEventModel
from firesentinel.processing.clustering import (
    calculate_centroid,
    calculate_severity,
    cluster_hotspots,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _make_enriched(
    lat: float = -42.22,
    lon: float = -71.43,
    acq_date: date = date(2026, 2, 15),
    acq_time: time = time(3, 30),
    frp: float = 28.5,
    source: Source = Source.VIIRS_SNPP_NRT,
) -> EnrichedHotspot:
    """Create an EnrichedHotspot with sensible defaults."""
    raw = RawHotspot(
        source=source,
        latitude=lat,
        longitude=lon,
        brightness=345.6,
        brightness_2=298.1,
        frp=frp,
        confidence=Confidence.HIGH.value,
        acq_date=acq_date,
        acq_time=acq_time,
        satellite="N",
        daynight=DayNight.NIGHT,
        raw_data={"scan": "0.39"},
    )
    return EnrichedHotspot(hotspot=raw)


async def _insert_active_event(
    session: AsyncSession,
    lat: float = -42.22,
    lon: float = -71.43,
    hotspot_count: int = 1,
    max_frp: float = 28.5,
    severity: str = "low",
) -> str:
    """Insert an active fire event into the database. Returns the event ID."""
    event_id = str(uuid.uuid4())
    record = FireEventModel(
        id=event_id,
        center_lat=lat,
        center_lon=lon,
        severity=severity,
        hotspot_count=hotspot_count,
        max_frp=max_frp,
        first_detected_at=datetime(2026, 2, 15, 3, 0),
        last_updated_at=datetime(2026, 2, 15, 3, 0),
        is_active=True,
    )
    session.add(record)
    await session.flush()
    return event_id


# ---------------------------------------------------------------------------
# Clustering tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cluster_single_hotspot(tmp_db: AsyncSession) -> None:
    """A single hotspot creates one fire event with severity 'low'."""
    hotspots = [_make_enriched()]

    events = await cluster_hotspots(hotspots, tmp_db)

    assert len(events) == 1
    assert events[0].severity == Severity.LOW
    assert len(events[0].hotspots) == 1


@pytest.mark.asyncio
async def test_cluster_nearby_hotspots(tmp_db: AsyncSession) -> None:
    """3 hotspots within 500m create 1 event with severity 'medium'."""
    # Spread hotspots ~200m apart (approx 0.002 degrees latitude)
    hotspots = [
        _make_enriched(lat=-42.2200, lon=-71.4300, acq_time=time(3, 30)),
        _make_enriched(lat=-42.2218, lon=-71.4300, acq_time=time(3, 31)),
        _make_enriched(lat=-42.2210, lon=-71.4305, acq_time=time(3, 32)),
    ]

    events = await cluster_hotspots(hotspots, tmp_db)

    assert len(events) == 1
    assert events[0].severity == Severity.MEDIUM
    assert len(events[0].hotspots) == 3


@pytest.mark.asyncio
async def test_cluster_distant_hotspots(tmp_db: AsyncSession) -> None:
    """2 hotspots 5km apart create 2 separate events."""
    hotspots = [
        _make_enriched(lat=-42.2200, lon=-71.4300),
        # ~5km away (approx 0.045 degrees latitude)
        _make_enriched(lat=-42.2650, lon=-71.4300),
    ]

    events = await cluster_hotspots(hotspots, tmp_db)

    assert len(events) == 2
    assert all(e.severity == Severity.LOW for e in events)


@pytest.mark.asyncio
async def test_cluster_temporal_split(tmp_db: AsyncSession) -> None:
    """2 hotspots at the same location but 4h apart create 2 events."""
    hotspots = [
        _make_enriched(
            lat=-42.2200,
            lon=-71.4300,
            acq_time=time(2, 0),
        ),
        _make_enriched(
            lat=-42.2200,
            lon=-71.4300,
            acq_time=time(6, 0),  # 4 hours later
        ),
    ]

    events = await cluster_hotspots(hotspots, tmp_db)

    assert len(events) == 2


# ---------------------------------------------------------------------------
# Severity tests
# ---------------------------------------------------------------------------


def test_severity_low() -> None:
    """1-2 hotspots with normal FRP = low severity."""
    assert calculate_severity(1, 20.0) == Severity.LOW
    assert calculate_severity(2, 50.0) == Severity.LOW


def test_severity_medium() -> None:
    """3-5 hotspots = medium severity."""
    assert calculate_severity(3, 20.0) == Severity.MEDIUM
    assert calculate_severity(5, 50.0) == Severity.MEDIUM


def test_severity_high() -> None:
    """6-9 hotspots = high severity."""
    assert calculate_severity(6, 20.0) == Severity.HIGH
    assert calculate_severity(9, 80.0) == Severity.HIGH


def test_severity_critical_count() -> None:
    """10+ hotspots = critical severity regardless of FRP."""
    assert calculate_severity(10, 20.0) == Severity.CRITICAL
    assert calculate_severity(15, 50.0) == Severity.CRITICAL


def test_severity_critical_frp() -> None:
    """FRP > 100 MW triggers critical even with few hotspots."""
    assert calculate_severity(3, 150.0) == Severity.CRITICAL
    assert calculate_severity(1, 101.0) == Severity.CRITICAL


# ---------------------------------------------------------------------------
# Centroid test
# ---------------------------------------------------------------------------


def test_centroid_calculation() -> None:
    """Centroid should be the average of all hotspot positions."""
    hotspots = [
        _make_enriched(lat=-42.0, lon=-71.0),
        _make_enriched(lat=-42.2, lon=-71.4),
        _make_enriched(lat=-42.4, lon=-71.8),
    ]

    lat, lon = calculate_centroid(hotspots)

    assert abs(lat - (-42.2)) < 0.001
    assert abs(lon - (-71.4)) < 0.001


# ---------------------------------------------------------------------------
# DB integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_with_existing_event(tmp_db: AsyncSession) -> None:
    """New hotspot near an active DB event should merge into that event."""
    event_id = await _insert_active_event(
        tmp_db,
        lat=-42.2200,
        lon=-71.4300,
        hotspot_count=2,
        max_frp=30.0,
        severity="low",
    )

    # New hotspot ~200m from the existing event
    hotspots = [
        _make_enriched(
            lat=-42.2218,
            lon=-71.4300,
            frp=45.0,
        )
    ]

    events = await cluster_hotspots(hotspots, tmp_db)

    assert len(events) == 1
    assert events[0].id == event_id
    # Hotspot count should be updated: 2 existing + 1 new = 3
    assert len(events[0].hotspots) == 1  # Only the new hotspot in memory

    # Verify DB was updated
    result = await tmp_db.execute(select(FireEventModel).where(FireEventModel.id == event_id))
    db_event = result.scalar_one()
    assert db_event.hotspot_count == 3
    assert db_event.max_frp == 45.0  # New hotspot has higher FRP


@pytest.mark.asyncio
async def test_cluster_updates_event_metadata(tmp_db: AsyncSession) -> None:
    """Adding hotspots to a cluster should update count, severity, last_updated."""
    event_id = await _insert_active_event(
        tmp_db,
        lat=-42.2200,
        lon=-71.4300,
        hotspot_count=4,
        max_frp=30.0,
        severity="medium",
    )

    # Add 2 more hotspots near the existing event (total will be 6 -> high)
    hotspots = [
        _make_enriched(lat=-42.2210, lon=-71.4305, acq_time=time(4, 0), frp=50.0),
        _make_enriched(lat=-42.2215, lon=-71.4310, acq_time=time(4, 5), frp=35.0),
    ]

    events = await cluster_hotspots(hotspots, tmp_db)

    assert len(events) == 1
    event = events[0]
    assert event.id == event_id

    # Verify updated metadata in DB
    result = await tmp_db.execute(select(FireEventModel).where(FireEventModel.id == event_id))
    db_event = result.scalar_one()
    assert db_event.hotspot_count == 6  # 4 + 2
    assert db_event.severity == Severity.HIGH.value
    assert db_event.max_frp == 50.0  # Highest FRP
    # last_updated should be the latest hotspot time
    assert db_event.last_updated_at >= datetime(2026, 2, 15, 4, 5)
