"""OpenStreetMap Overpass API client for road proximity context.

Queries the Overpass API for nearby roads and calculates haversine-based
distances using pure math (no geopandas/shapely). Results are cached per
grid cell (0.1 degree, 24h TTL) to minimize API calls.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from firesentinel.core.types import RoadContext

logger = logging.getLogger(__name__)

# Overpass API endpoint
_OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Search radius in meters for Overpass query
_SEARCH_RADIUS_M = 10_000

# Grid cell size in degrees for caching
_GRID_CELL_DEG = 0.1

# Cache TTL in seconds (24 hours)
_CACHE_TTL_S = 24 * 60 * 60

# HTTP timeout in seconds
_HTTP_TIMEOUT_S = 30.0

# Earth radius in meters for haversine
_EARTH_RADIUS_M = 6_371_000.0

# Default distance when no roads are found
_NO_ROAD_DISTANCE_M = 10_000.0

# Highway types to query (ordered roughly by importance)
_HIGHWAY_REGEX = r"^(track|path|tertiary|unclassified|secondary|primary|trunk|motorway)$"


# ---------------------------------------------------------------------------
# Overpass query template
# ---------------------------------------------------------------------------

_OVERPASS_QUERY_TEMPLATE = """\
[out:json][timeout:25];
(
  way["highway"~"{highway_regex}"](around:{radius},{lat},{lon});
);
out geom;"""


# ---------------------------------------------------------------------------
# Parsed way from Overpass response
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ParsedWay:
    """A road way parsed from Overpass JSON response."""

    way_id: int
    highway: str
    ref: str | None
    geometry: list[dict[str, float]]


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    """Cached Overpass response for a grid cell."""

    ways: list[_ParsedWay]
    timestamp: float


# ---------------------------------------------------------------------------
# Distance calculations (pure math, no geopandas/shapely)
# ---------------------------------------------------------------------------


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two WGS84 points.

    Args:
        lat1: Latitude of first point in degrees.
        lon1: Longitude of first point in degrees.
        lat2: Latitude of second point in degrees.
        lon2: Longitude of second point in degrees.

    Returns:
        Distance in meters.
    """
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return _EARTH_RADIUS_M * c


def point_to_segment_distance(
    plat: float,
    plon: float,
    slat1: float,
    slon1: float,
    slat2: float,
    slon2: float,
) -> float:
    """Calculate minimum distance from a point to a line segment.

    Projects the point onto the segment using a local Cartesian approximation,
    clamps to segment endpoints, then uses haversine for the final distance.

    Args:
        plat: Point latitude in degrees.
        plon: Point longitude in degrees.
        slat1: Segment start latitude in degrees.
        slon1: Segment start longitude in degrees.
        slat2: Segment end latitude in degrees.
        slon2: Segment end longitude in degrees.

    Returns:
        Distance in meters from the point to the nearest position on the segment.
    """
    # Convert to local Cartesian approximation (meters) centered on segment start
    mid_lat = math.radians((slat1 + slat2) / 2)
    cos_lat = math.cos(mid_lat)

    # Scale factors: degrees to meters
    m_per_deg_lat = _EARTH_RADIUS_M * math.radians(1.0)
    m_per_deg_lon = _EARTH_RADIUS_M * math.radians(1.0) * cos_lat

    # Segment vector in local meters
    dx = (slon2 - slon1) * m_per_deg_lon
    dy = (slat2 - slat1) * m_per_deg_lat

    # Point vector relative to segment start
    px = (plon - slon1) * m_per_deg_lon
    py = (plat - slat1) * m_per_deg_lat

    # Squared length of segment
    seg_len_sq = dx * dx + dy * dy

    if seg_len_sq < 1e-12:
        # Degenerate segment (zero length): distance to the single point
        return haversine_distance(plat, plon, slat1, slon1)

    # Projection parameter t, clamped to [0, 1]
    t = (px * dx + py * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))

    # Nearest point on segment in degrees
    nearest_lat = slat1 + t * (slat2 - slat1)
    nearest_lon = slon1 + t * (slon2 - slon1)

    return haversine_distance(plat, plon, nearest_lat, nearest_lon)


def min_distance_to_way(lat: float, lon: float, geometry: list[dict[str, float]]) -> float:
    """Calculate minimum distance from a point to a way's geometry.

    Iterates over all consecutive node pairs in the way geometry and
    returns the minimum point-to-segment distance.

    Args:
        lat: Point latitude in degrees.
        lon: Point longitude in degrees.
        geometry: List of node dicts with 'lat' and 'lon' keys.

    Returns:
        Minimum distance in meters to any segment of the way.
    """
    if len(geometry) < 2:
        if len(geometry) == 1:
            return haversine_distance(lat, lon, geometry[0]["lat"], geometry[0]["lon"])
        return _NO_ROAD_DISTANCE_M

    best = float("inf")
    for i in range(len(geometry) - 1):
        n1 = geometry[i]
        n2 = geometry[i + 1]
        d = point_to_segment_distance(lat, lon, n1["lat"], n1["lon"], n2["lat"], n2["lon"])
        best = min(best, d)

    return best


# ---------------------------------------------------------------------------
# Grid cell key
# ---------------------------------------------------------------------------


def _grid_key(latitude: float, longitude: float) -> tuple[float, float]:
    """Round coordinates to the nearest grid cell center.

    Args:
        latitude: Latitude in degrees.
        longitude: Longitude in degrees.

    Returns:
        Tuple of (rounded_lat, rounded_lon) at 0.1 degree resolution.
    """
    return (
        round(round(latitude / _GRID_CELL_DEG) * _GRID_CELL_DEG, 1),
        round(round(longitude / _GRID_CELL_DEG) * _GRID_CELL_DEG, 1),
    )


# ---------------------------------------------------------------------------
# Parse Overpass response
# ---------------------------------------------------------------------------


def _parse_overpass_response(data: dict[str, Any]) -> list[_ParsedWay]:
    """Extract road ways from Overpass JSON response.

    Args:
        data: Raw JSON response from Overpass API.

    Returns:
        List of parsed road ways.
    """
    ways: list[_ParsedWay] = []

    for element in data.get("elements", []):
        if element.get("type") != "way":
            continue

        tags = element.get("tags", {})
        highway = tags.get("highway")
        if not highway:
            continue

        geometry_raw = element.get("geometry", [])
        if not geometry_raw:
            continue

        geometry = [
            {"lat": float(node["lat"]), "lon": float(node["lon"])}
            for node in geometry_raw
            if "lat" in node and "lon" in node
        ]

        if len(geometry) < 2:
            continue

        ways.append(
            _ParsedWay(
                way_id=element.get("id", 0),
                highway=highway,
                ref=tags.get("ref"),
                geometry=geometry,
            )
        )

    return ways


# ---------------------------------------------------------------------------
# Build the RoadContext from parsed ways
# ---------------------------------------------------------------------------


def _build_road_context(latitude: float, longitude: float, ways: list[_ParsedWay]) -> RoadContext:
    """Find nearest road and build a RoadContext.

    Args:
        latitude: Hotspot latitude.
        longitude: Hotspot longitude.
        ways: Parsed road ways from Overpass.

    Returns:
        RoadContext with nearest road info, or default (10000m, 'none') if empty.
    """
    if not ways:
        return RoadContext(
            nearest_distance_m=_NO_ROAD_DISTANCE_M,
            nearest_road_type="none",
            nearest_road_ref=None,
        )

    best_distance = float("inf")
    best_way: _ParsedWay | None = None

    for way in ways:
        d = min_distance_to_way(latitude, longitude, way.geometry)
        if d < best_distance:
            best_distance = d
            best_way = way

    if best_way is None:
        return RoadContext(
            nearest_distance_m=_NO_ROAD_DISTANCE_M,
            nearest_road_type="none",
            nearest_road_ref=None,
        )

    return RoadContext(
        nearest_distance_m=best_distance,
        nearest_road_type=best_way.highway,
        nearest_road_ref=best_way.ref,
    )


# ---------------------------------------------------------------------------
# Roads client
# ---------------------------------------------------------------------------


class RoadsClient:
    """Async client for OpenStreetMap Overpass API road queries.

    Provides road proximity context for fire hotspots using cached
    grid-cell queries and haversine distance calculations.

    Args:
        client: Optional httpx.AsyncClient. A new one is created if not provided.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S)
        self._owns_client = client is None
        self._cache: dict[tuple[float, float], _CacheEntry] = {}

    async def close(self) -> None:
        """Close the HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def get_road_context(self, latitude: float, longitude: float) -> RoadContext | None:
        """Get road proximity context for a geographic point.

        Checks grid-cell cache first. On cache miss, queries the Overpass API.
        Returns None on any error (timeout, rate limit, network failure).

        Args:
            latitude: Point latitude in WGS84 degrees.
            longitude: Point longitude in WGS84 degrees.

        Returns:
            RoadContext with nearest road info, or None on error.
        """
        try:
            key = _grid_key(latitude, longitude)

            # Check cache (with TTL)
            cached = self._cache.get(key)
            if cached is not None:
                age = time.monotonic() - cached.timestamp
                if age < _CACHE_TTL_S:
                    logger.debug("Cache hit for grid cell (%.1f, %.1f)", key[0], key[1])
                    return _build_road_context(latitude, longitude, cached.ways)
                # Expired -- remove stale entry
                del self._cache[key]

            # Query Overpass API
            ways = await self._query_overpass(key[0], key[1])

            # Cache the result
            self._cache[key] = _CacheEntry(
                ways=ways,
                timestamp=time.monotonic(),
            )

            return _build_road_context(latitude, longitude, ways)

        except Exception:
            logger.exception("Failed to get road context for (%.4f, %.4f)", latitude, longitude)
            return None

    async def _query_overpass(self, lat: float, lon: float) -> list[_ParsedWay]:
        """Execute Overpass API query for roads near a point.

        Args:
            lat: Center latitude for the query.
            lon: Center longitude for the query.

        Returns:
            List of parsed road ways.

        Raises:
            httpx.HTTPStatusError: On non-2xx response.
            httpx.TimeoutException: On request timeout.
        """
        query = _OVERPASS_QUERY_TEMPLATE.format(
            highway_regex=_HIGHWAY_REGEX,
            radius=_SEARCH_RADIUS_M,
            lat=lat,
            lon=lon,
        )

        logger.debug("Querying Overpass API for roads near (%.4f, %.4f)", lat, lon)

        response = await self._client.post(
            _OVERPASS_URL,
            data={"data": query},
        )

        if response.status_code == 429:
            logger.warning("Overpass API rate limit (429) for (%.4f, %.4f)", lat, lon)
            raise httpx.HTTPStatusError(
                "Rate limited",
                request=response.request,
                response=response,
            )

        response.raise_for_status()

        data: dict[str, Any] = response.json()
        return _parse_overpass_response(data)
