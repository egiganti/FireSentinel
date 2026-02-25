"""Deduplication module for satellite hotspot detections.

Filters out hotspots that already exist in the database (same source,
nearby location, similar acquisition time) and stores genuinely new
detections. Batches DB queries to avoid N+1 patterns.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from firesentinel.config import get_yaml_config
from firesentinel.db.models import Hotspot
from firesentinel.ingestion.roads import haversine_distance

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from firesentinel.core.types import RawHotspot

logger = logging.getLogger(__name__)


def _bbox_padding_degrees(tolerance_m: int) -> float:
    """Convert a meter tolerance to an approximate degree padding.

    Uses a conservative estimate (~111km per degree latitude) to ensure
    the bounding box captures all potentially-duplicate hotspots.

    Args:
        tolerance_m: Distance tolerance in meters.

    Returns:
        Padding in degrees, slightly overestimated for safety.
    """
    # 1 degree latitude ~ 111,000 m; add 50% safety margin
    return (tolerance_m / 111_000.0) * 1.5


def _time_to_minutes(t: datetime | None) -> int:
    """Convert a time-like value to minutes since midnight.

    Args:
        t: A datetime or time object.

    Returns:
        Minutes since midnight.
    """
    if t is None:
        return 0
    if isinstance(t, datetime):
        return t.hour * 60 + t.minute
    return t.hour * 60 + t.minute


async def deduplicate(
    hotspots: list[RawHotspot],
    session: AsyncSession,
) -> list[RawHotspot]:
    """Filter out hotspots that already exist in the database.

    For each incoming hotspot, checks if a record exists with the same source,
    location within the configured spatial tolerance, and acquisition time within
    the configured temporal tolerance. Uses batch DB queries with bounding box
    filters for efficiency.

    Args:
        hotspots: List of raw hotspot detections to deduplicate.
        session: Async database session.

    Returns:
        List of genuinely new hotspots (duplicates removed).
    """
    if not hotspots:
        logger.info("Dedup: 0 hotspots in, 0 new, 0 duplicates filtered")
        return []

    cfg = get_yaml_config().dedup
    spatial_tolerance_m = cfg.spatial_tolerance_m
    temporal_tolerance_min = cfg.temporal_tolerance_minutes

    # Collect unique dates for batch query
    acq_dates: set[date] = {h.acq_date for h in hotspots}

    # Compute bounding box across all incoming hotspots
    lats = [h.latitude for h in hotspots]
    lons = [h.longitude for h in hotspots]
    padding = _bbox_padding_degrees(spatial_tolerance_m)

    min_lat = min(lats) - padding
    max_lat = max(lats) + padding
    min_lon = min(lons) - padding
    max_lon = max(lons) + padding

    # Batch query: fetch all existing hotspots in the date range + bounding box
    stmt = select(Hotspot).where(
        and_(
            Hotspot.acq_date.in_(acq_dates),
            Hotspot.latitude >= min_lat,
            Hotspot.latitude <= max_lat,
            Hotspot.longitude >= min_lon,
            Hotspot.longitude <= max_lon,
        )
    )
    result = await session.execute(stmt)
    existing = result.scalars().all()

    # Build lookup structure: group existing hotspots by (source, acq_date)
    existing_by_key: dict[tuple[str, date], list[Hotspot]] = {}
    for ex in existing:
        key = (ex.source, ex.acq_date)
        existing_by_key.setdefault(key, []).append(ex)

    new_hotspots: list[RawHotspot] = []

    for hs in hotspots:
        key = (hs.source.value, hs.acq_date)
        candidates = existing_by_key.get(key, [])

        is_duplicate = False
        hs_minutes = hs.acq_time.hour * 60 + hs.acq_time.minute

        for ex in candidates:
            # Spatial check
            dist = haversine_distance(
                hs.latitude,
                hs.longitude,
                ex.latitude,
                ex.longitude,
            )
            if dist > spatial_tolerance_m:
                continue

            # Temporal check
            ex_minutes = _time_to_minutes(ex.acq_time)
            if isinstance(ex.acq_time, datetime):
                ex_minutes = ex.acq_time.hour * 60 + ex.acq_time.minute
            time_diff = abs(hs_minutes - ex_minutes)
            # Handle midnight wrap-around
            if time_diff > 720:
                time_diff = 1440 - time_diff

            if time_diff <= temporal_tolerance_min:
                is_duplicate = True
                break

        if not is_duplicate:
            new_hotspots.append(hs)

    dupes = len(hotspots) - len(new_hotspots)
    logger.info(
        "Dedup: %d hotspots in, %d new, %d duplicates filtered",
        len(hotspots),
        len(new_hotspots),
        dupes,
    )

    return new_hotspots


async def store_hotspots(
    hotspots: list[RawHotspot],
    session: AsyncSession,
) -> list[str]:
    """Insert new hotspots into the database.

    Converts each RawHotspot to an ORM Hotspot record, assigns a UUID,
    and stores the raw_data as JSON.

    Args:
        hotspots: List of raw hotspot detections to store.
        session: Async database session.

    Returns:
        List of generated UUID strings for the inserted records.
    """
    if not hotspots:
        return []

    ids: list[str] = []

    for hs in hotspots:
        hotspot_id = str(uuid.uuid4())
        ids.append(hotspot_id)

        record = Hotspot(
            id=hotspot_id,
            source=hs.source.value,
            latitude=hs.latitude,
            longitude=hs.longitude,
            brightness=hs.brightness,
            brightness_2=hs.brightness_2,
            frp=hs.frp,
            confidence=hs.confidence,
            acq_date=hs.acq_date,
            acq_time=hs.acq_time,
            daynight=hs.daynight.value,
            satellite=hs.satellite,
            ingested_at=datetime.utcnow(),
            raw_data=hs.raw_data if hs.raw_data else None,
        )
        session.add(record)

    await session.flush()

    logger.info("Stored %d hotspots in database", len(ids))
    return ids
