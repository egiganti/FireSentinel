"""Tests for the deduplication module.

Validates hotspot deduplication logic including spatial tolerance,
temporal tolerance, batch efficiency, and database storage.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from sqlalchemy import select

from firesentinel.core.types import Confidence, DayNight, RawHotspot, Source
from firesentinel.db.models import Hotspot
from firesentinel.processing.dedup import deduplicate, store_hotspots

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _make_hotspot(
    lat: float = -42.22,
    lon: float = -71.43,
    acq_date: date = date(2026, 2, 15),
    acq_time: time = time(3, 30),
    source: Source = Source.VIIRS_SNPP_NRT,
    frp: float = 28.5,
) -> RawHotspot:
    """Helper to create a RawHotspot with sensible defaults."""
    return RawHotspot(
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
        raw_data={"scan": "0.39", "track": "0.36"},
    )


async def _insert_existing_hotspot(
    session: AsyncSession,
    lat: float = -42.22,
    lon: float = -71.43,
    acq_date: date = date(2026, 2, 15),
    acq_time: time = time(3, 30),
    source: str = "VIIRS_SNPP_NRT",
) -> None:
    """Insert a hotspot record directly into the database."""
    record = Hotspot(
        id=str(uuid.uuid4()),
        source=source,
        latitude=lat,
        longitude=lon,
        brightness=345.6,
        brightness_2=298.1,
        frp=28.5,
        confidence="high",
        acq_date=acq_date,
        acq_time=acq_time,
        daynight="N",
        satellite="N",
        ingested_at=datetime.utcnow(),
        raw_data={"scan": "0.39"},
    )
    session.add(record)
    await session.flush()


@pytest.mark.asyncio
async def test_deduplicate_new_hotspots(tmp_db: AsyncSession) -> None:
    """Empty database means all hotspots are new."""
    hotspots = [
        _make_hotspot(lat=-42.22, lon=-71.43),
        _make_hotspot(lat=-42.30, lon=-71.50),
    ]

    result = await deduplicate(hotspots, tmp_db)

    assert len(result) == 2
    assert result == hotspots


@pytest.mark.asyncio
async def test_deduplicate_exact_duplicate(tmp_db: AsyncSession) -> None:
    """Exact same source/location/time in DB should be filtered out."""
    await _insert_existing_hotspot(
        tmp_db,
        lat=-42.22,
        lon=-71.43,
        acq_date=date(2026, 2, 15),
        acq_time=time(3, 30),
    )

    hotspots = [_make_hotspot(lat=-42.22, lon=-71.43)]

    result = await deduplicate(hotspots, tmp_db)

    assert len(result) == 0


@pytest.mark.asyncio
async def test_deduplicate_nearby_duplicate(tmp_db: AsyncSession) -> None:
    """Hotspot within 750m and 30min of existing should be filtered as dupe."""
    # Insert existing at -42.2200, -71.4300
    await _insert_existing_hotspot(
        tmp_db,
        lat=-42.2200,
        lon=-71.4300,
        acq_date=date(2026, 2, 15),
        acq_time=time(3, 30),
    )

    # Create a hotspot ~500m away (approx 0.004 degrees latitude ~ 440m)
    hotspots = [
        _make_hotspot(
            lat=-42.2240,
            lon=-71.4300,
            acq_time=time(3, 40),  # 10 min later
        )
    ]

    result = await deduplicate(hotspots, tmp_db)

    assert len(result) == 0


@pytest.mark.asyncio
async def test_deduplicate_far_enough(tmp_db: AsyncSession) -> None:
    """Hotspot > 750m away should NOT be filtered (it's genuinely new)."""
    await _insert_existing_hotspot(
        tmp_db,
        lat=-42.2200,
        lon=-71.4300,
        acq_date=date(2026, 2, 15),
        acq_time=time(3, 30),
    )

    # ~2km away (0.018 degrees latitude ~ 2000m)
    hotspots = [
        _make_hotspot(
            lat=-42.2380,
            lon=-71.4300,
            acq_time=time(3, 30),
        )
    ]

    result = await deduplicate(hotspots, tmp_db)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_deduplicate_different_time(tmp_db: AsyncSession) -> None:
    """Same location but > 30min apart means both are kept (different pass)."""
    await _insert_existing_hotspot(
        tmp_db,
        lat=-42.2200,
        lon=-71.4300,
        acq_date=date(2026, 2, 15),
        acq_time=time(3, 0),
    )

    # Same location, but 45 min later (exceeds 30 min tolerance)
    hotspots = [
        _make_hotspot(
            lat=-42.2200,
            lon=-71.4300,
            acq_time=time(3, 45),
        )
    ]

    result = await deduplicate(hotspots, tmp_db)

    assert len(result) == 1


@pytest.mark.asyncio
async def test_store_hotspots(tmp_db: AsyncSession) -> None:
    """Verify hotspots are saved to DB with UUIDs and correct data."""
    hotspots = [
        _make_hotspot(lat=-42.22, lon=-71.43),
        _make_hotspot(lat=-42.30, lon=-71.50, acq_time=time(4, 0)),
    ]

    ids = await store_hotspots(hotspots, tmp_db)

    assert len(ids) == 2

    # Verify all IDs are valid UUIDs
    for hotspot_id in ids:
        uuid.UUID(hotspot_id)  # Raises if invalid

    # Verify records exist in DB
    result = await tmp_db.execute(select(Hotspot))
    records = result.scalars().all()
    assert len(records) == 2

    # Check data integrity
    record = records[0]
    assert record.source == Source.VIIRS_SNPP_NRT.value
    assert record.latitude == -42.22
    assert record.raw_data is not None


@pytest.mark.asyncio
async def test_dedup_batch_efficiency(tmp_db: AsyncSession) -> None:
    """100 hotspots should be deduplicated with batch queries, not N+1.

    We verify this by checking that the DB is queried a minimal number of
    times rather than once per hotspot.
    """
    # Insert 10 existing hotspots spread across 2 dates
    for i in range(5):
        await _insert_existing_hotspot(
            tmp_db,
            lat=-42.22 + i * 0.001,
            lon=-71.43,
            acq_date=date(2026, 2, 15),
            acq_time=time(3, 30),
        )
    for i in range(5):
        await _insert_existing_hotspot(
            tmp_db,
            lat=-42.22 + i * 0.001,
            lon=-71.43,
            acq_date=date(2026, 2, 16),
            acq_time=time(3, 30),
        )

    # Create 100 hotspots: some duplicates, mostly new
    hotspots: list[RawHotspot] = []
    for i in range(50):
        hotspots.append(
            _make_hotspot(
                lat=-42.22 + i * 0.01,  # Spread out so mostly unique
                lon=-71.43,
                acq_date=date(2026, 2, 15),
                acq_time=time(3, 30),
            )
        )
    for i in range(50):
        hotspots.append(
            _make_hotspot(
                lat=-42.22 + i * 0.01,
                lon=-71.43,
                acq_date=date(2026, 2, 16),
                acq_time=time(3, 30),
            )
        )

    # Track the number of execute calls on the session
    original_execute = tmp_db.execute
    execute_count = 0

    async def counting_execute(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal execute_count
        execute_count += 1
        return await original_execute(*args, **kwargs)

    with patch.object(tmp_db, "execute", side_effect=counting_execute):
        result = await deduplicate(hotspots, tmp_db)

    # Should have far fewer queries than 100 (batch approach)
    # We expect exactly 1 batch query for the bounding box
    assert execute_count <= 5, (
        f"Expected batch queries, got {execute_count} queries for 100 hotspots"
    )

    # Some should be filtered (the first few are near existing), most should be new
    assert len(result) > 80  # Most of the 100 should be new
    assert len(result) < 100  # But some duplicates should be caught
