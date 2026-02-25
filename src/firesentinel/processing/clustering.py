"""Clustering module for grouping hotspots into fire events.

Uses simple agglomerative clustering: hotspots within the configured spatial
radius and temporal window are merged into a single fire event. New hotspots
near existing active events in the database are merged into those events.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import and_, select

from firesentinel.config import get_yaml_config
from firesentinel.core.types import EnrichedHotspot, FireEvent, Severity
from firesentinel.db.models import FireEvent as FireEventModel
from firesentinel.ingestion.roads import haversine_distance

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def calculate_severity(hotspot_count: int, max_frp: float) -> Severity:
    """Determine fire event severity from hotspot count and peak FRP.

    Uses thresholds from config/monitoring.yml. Critical is triggered by
    either high hotspot count OR high FRP.

    Args:
        hotspot_count: Number of hotspots in the fire event.
        max_frp: Maximum fire radiative power in MW across all hotspots.

    Returns:
        Severity enum value.
    """
    cfg = get_yaml_config().clustering
    critical_frp = cfg.critical_frp_threshold_mw

    if max_frp > critical_frp:
        return Severity.CRITICAL

    sev = cfg.severity
    # critical range: [10, null] means 10+
    if hotspot_count >= sev.critical[0]:
        return Severity.CRITICAL
    if sev.high[0] <= hotspot_count <= sev.high[1]:
        return Severity.HIGH
    if sev.medium[0] <= hotspot_count <= sev.medium[1]:
        return Severity.MEDIUM
    return Severity.LOW


def calculate_centroid(hotspots: list[EnrichedHotspot]) -> tuple[float, float]:
    """Calculate the geographic centroid of a list of enriched hotspots.

    Args:
        hotspots: List of enriched hotspot detections.

    Returns:
        Tuple of (latitude, longitude) as the average of all hotspot positions.
    """
    if not hotspots:
        return (0.0, 0.0)

    total_lat = sum(h.hotspot.latitude for h in hotspots)
    total_lon = sum(h.hotspot.longitude for h in hotspots)
    n = len(hotspots)
    return (total_lat / n, total_lon / n)


def _hotspot_datetime(hs: EnrichedHotspot) -> datetime:
    """Combine acq_date and acq_time into a single datetime.

    Args:
        hs: An enriched hotspot.

    Returns:
        Combined datetime.
    """
    return datetime.combine(hs.hotspot.acq_date, hs.hotspot.acq_time)


async def get_active_events(
    session: AsyncSession,
    bbox: list[float] | None = None,
) -> list[FireEvent]:
    """Query the database for active fire events.

    Args:
        session: Async database session.
        bbox: Optional bounding box [min_lon, min_lat, max_lon, max_lat].

    Returns:
        List of active FireEvent dataclass instances.
    """
    stmt = select(FireEventModel).where(FireEventModel.is_active.is_(True))

    if bbox is not None and len(bbox) == 4:
        min_lon, min_lat, max_lon, max_lat = bbox
        stmt = stmt.where(
            and_(
                FireEventModel.center_lat >= min_lat,
                FireEventModel.center_lat <= max_lat,
                FireEventModel.center_lon >= min_lon,
                FireEventModel.center_lon <= max_lon,
            )
        )

    result = await session.execute(stmt)
    db_events = result.scalars().all()

    events: list[FireEvent] = []
    for ev in db_events:
        events.append(
            FireEvent(
                id=ev.id,
                center_lat=ev.center_lat,
                center_lon=ev.center_lon,
                hotspots=[],
                severity=Severity(ev.severity),
                max_frp=ev.max_frp,
                first_detected=ev.first_detected_at,
                last_updated=ev.last_updated_at,
                is_active=ev.is_active,
            )
        )

    return events


async def cluster_hotspots(
    hotspots: list[EnrichedHotspot],
    session: AsyncSession,
) -> list[FireEvent]:
    """Cluster nearby hotspots into fire events.

    Algorithm (simple agglomerative clustering):
    1. Sort hotspots by acquisition datetime.
    2. For each hotspot, check existing active DB events first -- if within
       the spatial radius of an active event's centroid, merge into it.
    3. Then check in-memory clusters -- if within spatial radius AND temporal
       window of any cluster member, add to that cluster.
    4. Otherwise, create a new cluster.

    For each cluster, creates or updates a FireEvent with computed centroid,
    severity, FRP stats, and temporal bounds.

    Args:
        hotspots: List of enriched hotspot detections to cluster.
        session: Async database session.

    Returns:
        List of new or updated FireEvent instances.
    """
    if not hotspots:
        return []

    cfg = get_yaml_config().clustering
    spatial_radius_m = cfg.spatial_radius_m
    temporal_window_h = cfg.temporal_window_hours
    temporal_window = timedelta(hours=temporal_window_h)

    # Sort by acquisition datetime
    sorted_hotspots = sorted(hotspots, key=_hotspot_datetime)

    # Fetch existing active events from DB
    db_events = await _fetch_active_events_for_clustering(sorted_hotspots, session)

    # Clusters: list of (hotspot_list, existing_db_event_or_None)
    clusters: list[tuple[list[EnrichedHotspot], FireEventModel | None]] = []

    # Map from db event id -> cluster index for merging into existing events
    db_event_cluster_map: dict[str, int] = {}

    # Populate initial clusters from existing DB events
    for db_ev in db_events:
        cluster_idx = len(clusters)
        clusters.append(([], db_ev))
        db_event_cluster_map[db_ev.id] = cluster_idx

    for hs in sorted_hotspots:
        hs_dt = _hotspot_datetime(hs)
        assigned = False

        # Check existing DB events first
        for db_ev in db_events:
            dist = haversine_distance(
                hs.hotspot.latitude,
                hs.hotspot.longitude,
                db_ev.center_lat,
                db_ev.center_lon,
            )
            if dist <= spatial_radius_m:
                cluster_idx = db_event_cluster_map[db_ev.id]
                clusters[cluster_idx][0].append(hs)
                assigned = True
                break

        if assigned:
            continue

        # Check in-memory clusters (those without a DB event)
        for _i, (cluster_hs_list, _db_ev) in enumerate(clusters):
            if not cluster_hs_list:
                continue

            # Calculate current centroid of this cluster
            centroid_lat, centroid_lon = calculate_centroid(cluster_hs_list)

            dist = haversine_distance(
                hs.hotspot.latitude,
                hs.hotspot.longitude,
                centroid_lat,
                centroid_lon,
            )
            if dist > spatial_radius_m:
                continue

            # Check temporal window against any member
            time_ok = False
            for member in cluster_hs_list:
                member_dt = _hotspot_datetime(member)
                if abs(hs_dt - member_dt) <= temporal_window:
                    time_ok = True
                    break

            if time_ok:
                cluster_hs_list.append(hs)
                assigned = True
                break

        if not assigned:
            # Create new cluster
            clusters.append(([hs], None))

    # Build FireEvent objects from clusters
    result_events: list[FireEvent] = []

    for cluster_hs_list, db_event in clusters:
        if not cluster_hs_list:
            continue

        centroid_lat, centroid_lon = calculate_centroid(cluster_hs_list)
        max_frp = max(h.hotspot.frp for h in cluster_hs_list)

        datetimes = [_hotspot_datetime(h) for h in cluster_hs_list]
        first_detected = min(datetimes)
        last_updated = max(datetimes)

        if db_event is not None:
            # Merge into existing DB event
            total_count = db_event.hotspot_count + len(cluster_hs_list)
            combined_max_frp = max(max_frp, db_event.max_frp)

            # Recalculate centroid including existing event center
            # Weight existing center by existing hotspot count
            existing_count = db_event.hotspot_count
            new_count = len(cluster_hs_list)
            merged_lat = (
                db_event.center_lat * existing_count + centroid_lat * new_count
            ) / total_count
            merged_lon = (
                db_event.center_lon * existing_count + centroid_lon * new_count
            ) / total_count

            severity = calculate_severity(total_count, combined_max_frp)

            # Update DB record
            db_event.center_lat = merged_lat
            db_event.center_lon = merged_lon
            db_event.hotspot_count = total_count
            db_event.max_frp = combined_max_frp
            db_event.severity = severity.value
            db_event.last_updated_at = max(last_updated, db_event.last_updated_at)
            await session.flush()

            result_events.append(
                FireEvent(
                    id=db_event.id,
                    center_lat=merged_lat,
                    center_lon=merged_lon,
                    hotspots=cluster_hs_list,
                    severity=severity,
                    max_frp=combined_max_frp,
                    first_detected=db_event.first_detected_at,
                    last_updated=db_event.last_updated_at,
                    is_active=True,
                )
            )
        else:
            # Create new fire event
            hotspot_count = len(cluster_hs_list)
            severity = calculate_severity(hotspot_count, max_frp)
            event_id = str(uuid.uuid4())

            db_record = FireEventModel(
                id=event_id,
                center_lat=centroid_lat,
                center_lon=centroid_lon,
                severity=severity.value,
                hotspot_count=hotspot_count,
                max_frp=max_frp,
                first_detected_at=first_detected,
                last_updated_at=last_updated,
                is_active=True,
            )
            session.add(db_record)
            await session.flush()

            result_events.append(
                FireEvent(
                    id=event_id,
                    center_lat=centroid_lat,
                    center_lon=centroid_lon,
                    hotspots=cluster_hs_list,
                    severity=severity,
                    max_frp=max_frp,
                    first_detected=first_detected,
                    last_updated=last_updated,
                    is_active=True,
                )
            )

    logger.info(
        "Clustering: %d hotspots -> %d fire events",
        len(hotspots),
        len(result_events),
    )

    return result_events


async def _fetch_active_events_for_clustering(
    hotspots: list[EnrichedHotspot],
    session: AsyncSession,
) -> list[FireEventModel]:
    """Fetch active DB events near the hotspots being clustered.

    Computes a bounding box from the hotspots with padding based on the
    clustering spatial radius, then queries for active events in that area.

    Args:
        hotspots: Hotspots being clustered.
        session: Async database session.

    Returns:
        List of active FireEvent ORM model instances.
    """
    if not hotspots:
        return []

    cfg = get_yaml_config().clustering
    # Padding in degrees: spatial_radius_m / ~111km per degree, with safety margin
    padding = (cfg.spatial_radius_m / 111_000.0) * 1.5

    lats = [h.hotspot.latitude for h in hotspots]
    lons = [h.hotspot.longitude for h in hotspots]

    min_lat = min(lats) - padding
    max_lat = max(lats) + padding
    min_lon = min(lons) - padding
    max_lon = max(lons) + padding

    stmt = select(FireEventModel).where(
        and_(
            FireEventModel.is_active.is_(True),
            FireEventModel.center_lat >= min_lat,
            FireEventModel.center_lat <= max_lat,
            FireEventModel.center_lon >= min_lon,
            FireEventModel.center_lon <= max_lon,
        )
    )

    result = await session.execute(stmt)
    return list(result.scalars().all())
