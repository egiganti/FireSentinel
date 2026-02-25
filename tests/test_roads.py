"""Tests for the OpenStreetMap Overpass API road proximity client.

Validates road context retrieval, caching behavior, distance calculations,
and error handling using mocked HTTP responses via respx.
"""

from __future__ import annotations

from urllib.parse import unquote_plus

import httpx
import respx

from firesentinel.ingestion.roads import (
    _OVERPASS_URL,
    RoadsClient,
    haversine_distance,
    min_distance_to_way,
    point_to_segment_distance,
)

# ---------------------------------------------------------------------------
# Mock Overpass response data
# ---------------------------------------------------------------------------

_MOCK_OVERPASS_RESPONSE: dict = {
    "elements": [
        {
            "type": "way",
            "id": 123,
            "tags": {"highway": "track"},
            "geometry": [
                {"lat": -42.220, "lon": -71.430},
                {"lat": -42.221, "lon": -71.431},
            ],
        },
        {
            "type": "way",
            "id": 456,
            "tags": {"highway": "secondary", "ref": "RP71"},
            "geometry": [
                {"lat": -42.215, "lon": -71.425},
                {"lat": -42.216, "lon": -71.426},
            ],
        },
    ]
}

_MOCK_EMPTY_RESPONSE: dict = {"elements": []}


# ---------------------------------------------------------------------------
# Road context retrieval tests
# ---------------------------------------------------------------------------


class TestGetRoadContext:
    """Test road context retrieval from Overpass API."""

    @respx.mock
    async def test_get_road_nearby(self) -> None:
        """Hotspot near a track road returns correct distance and type."""
        respx.post(_OVERPASS_URL).mock(
            return_value=httpx.Response(200, json=_MOCK_OVERPASS_RESPONSE)
        )

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            # Point very close to the track road (way 123)
            result = await client.get_road_context(-42.220, -71.430)

        assert result is not None
        assert result.nearest_distance_m < 500
        assert result.nearest_road_type in ("track", "secondary")

    @respx.mock
    async def test_road_with_ref(self) -> None:
        """Road with a ref tag returns the ref value."""
        # Use only the secondary road with ref
        response_with_ref: dict = {
            "elements": [
                {
                    "type": "way",
                    "id": 456,
                    "tags": {"highway": "secondary", "ref": "RP71"},
                    "geometry": [
                        {"lat": -42.215, "lon": -71.425},
                        {"lat": -42.216, "lon": -71.426},
                    ],
                }
            ]
        }
        respx.post(_OVERPASS_URL).mock(return_value=httpx.Response(200, json=response_with_ref))

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            result = await client.get_road_context(-42.215, -71.425)

        assert result is not None
        assert result.nearest_road_ref == "RP71"
        assert result.nearest_road_type == "secondary"

    @respx.mock
    async def test_road_no_ref(self) -> None:
        """Road without a ref tag returns None for ref."""
        # Use only the track road without ref
        response_no_ref: dict = {
            "elements": [
                {
                    "type": "way",
                    "id": 123,
                    "tags": {"highway": "track"},
                    "geometry": [
                        {"lat": -42.220, "lon": -71.430},
                        {"lat": -42.221, "lon": -71.431},
                    ],
                }
            ]
        }
        respx.post(_OVERPASS_URL).mock(return_value=httpx.Response(200, json=response_no_ref))

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            result = await client.get_road_context(-42.220, -71.430)

        assert result is not None
        assert result.nearest_road_ref is None
        assert result.nearest_road_type == "track"

    @respx.mock
    async def test_multiple_roads_nearest(self) -> None:
        """With two roads, the nearest road is returned."""
        respx.post(_OVERPASS_URL).mock(
            return_value=httpx.Response(200, json=_MOCK_OVERPASS_RESPONSE)
        )

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            # Point exactly on the track road geometry
            result = await client.get_road_context(-42.220, -71.430)

        assert result is not None
        # Track road is at this exact point, so distance should be ~0
        assert result.nearest_distance_m < 10
        assert result.nearest_road_type == "track"

    @respx.mock
    async def test_no_roads(self) -> None:
        """Empty Overpass response returns default no-road context."""
        respx.post(_OVERPASS_URL).mock(return_value=httpx.Response(200, json=_MOCK_EMPTY_RESPONSE))

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            result = await client.get_road_context(-42.5, -71.5)

        assert result is not None
        assert result.nearest_distance_m == 10_000.0
        assert result.nearest_road_type == "none"
        assert result.nearest_road_ref is None


# ---------------------------------------------------------------------------
# Caching tests
# ---------------------------------------------------------------------------


class TestCaching:
    """Test grid-cell caching behavior."""

    @respx.mock
    async def test_cache_hit(self) -> None:
        """Same grid cell twice results in only one API call."""
        route = respx.post(_OVERPASS_URL).mock(
            return_value=httpx.Response(200, json=_MOCK_OVERPASS_RESPONSE)
        )

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)

            # Both points round to the same grid cell (-42.2, -71.4)
            result1 = await client.get_road_context(-42.220, -71.430)
            result2 = await client.get_road_context(-42.221, -71.431)

        assert result1 is not None
        assert result2 is not None
        assert route.call_count == 1

    @respx.mock
    async def test_cache_recalculates_distance(self) -> None:
        """Two points in same cell get different distances from cached data."""
        respx.post(_OVERPASS_URL).mock(
            return_value=httpx.Response(200, json=_MOCK_OVERPASS_RESPONSE)
        )

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)

            # Point very close to the track (on the geometry node)
            result1 = await client.get_road_context(-42.220, -71.430)
            # Point further away (but same grid cell)
            result2 = await client.get_road_context(-42.225, -71.435)

        assert result1 is not None
        assert result2 is not None
        # The second point is further from any road in the mock data
        assert result1.nearest_distance_m != result2.nearest_distance_m


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    """Test error handling and graceful degradation."""

    @respx.mock
    async def test_api_error_returns_none(self) -> None:
        """HTTP 500 error returns None."""
        respx.post(_OVERPASS_URL).mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            result = await client.get_road_context(-42.22, -71.43)

        assert result is None

    @respx.mock
    async def test_api_rate_limit_429(self) -> None:
        """HTTP 429 rate limit returns None and logs warning."""
        respx.post(_OVERPASS_URL).mock(return_value=httpx.Response(429, text="Too Many Requests"))

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            result = await client.get_road_context(-42.22, -71.43)

        assert result is None


# ---------------------------------------------------------------------------
# Distance calculation tests
# ---------------------------------------------------------------------------


class TestHaversine:
    """Test haversine distance calculation accuracy."""

    def test_haversine_known_distance(self) -> None:
        """Known distance between two points, accurate within 1%."""
        # Buenos Aires to Cordoba: approximately 646 km by great circle
        # Buenos Aires: -34.6037, -58.3816
        # Cordoba: -31.4201, -64.1888
        distance_m = haversine_distance(-34.6037, -58.3816, -31.4201, -64.1888)
        expected_km = 646.0
        actual_km = distance_m / 1000.0
        # Within 1% accuracy
        assert abs(actual_km - expected_km) / expected_km < 0.01

    def test_haversine_zero_distance(self) -> None:
        """Same point returns zero distance."""
        distance = haversine_distance(-42.22, -71.43, -42.22, -71.43)
        assert distance == 0.0

    def test_haversine_short_distance(self) -> None:
        """Short distance (roughly 111m for 0.001 degree latitude) is reasonable."""
        # 0.001 degrees latitude is approximately 111 meters
        distance = haversine_distance(-42.220, -71.430, -42.221, -71.430)
        assert 100 < distance < 120


class TestPointToSegment:
    """Test point-to-segment distance projection."""

    def test_point_to_segment_perpendicular(self) -> None:
        """Point projects perpendicularly onto the middle of a segment."""
        # Segment runs north-south: (-42.220, -71.430) to (-42.222, -71.430)
        # Point is offset east: (-42.221, -71.429) -- roughly perpendicular
        distance = point_to_segment_distance(
            -42.221,
            -71.429,  # point
            -42.220,
            -71.430,  # segment start
            -42.222,
            -71.430,  # segment end
        )
        # Should be roughly the east-west offset distance (~82m at this latitude)
        # It must be less than the distance to either endpoint
        d_to_start = haversine_distance(-42.221, -71.429, -42.220, -71.430)
        d_to_end = haversine_distance(-42.221, -71.429, -42.222, -71.430)
        assert distance < d_to_start
        assert distance < d_to_end
        # Should be roughly 82 meters (1/1000 degree of longitude at -42 lat)
        assert 50 < distance < 120

    def test_point_to_segment_endpoint(self) -> None:
        """Point beyond segment end returns distance to nearest endpoint."""
        # Segment: (-42.220, -71.430) to (-42.221, -71.430)
        # Point: (-42.225, -71.430) -- well beyond the southern end
        distance = point_to_segment_distance(
            -42.225,
            -71.430,  # point
            -42.220,
            -71.430,  # segment start
            -42.221,
            -71.430,  # segment end
        )
        # Distance should equal haversine to the nearest endpoint (-42.221)
        expected = haversine_distance(-42.225, -71.430, -42.221, -71.430)
        assert abs(distance - expected) < 1.0  # Within 1 meter


class TestMinDistanceToWay:
    """Test minimum distance calculation to multi-segment ways."""

    def test_min_distance_basic(self) -> None:
        """Point near a way returns a reasonable distance."""
        geometry = [
            {"lat": -42.220, "lon": -71.430},
            {"lat": -42.221, "lon": -71.431},
        ]
        distance = min_distance_to_way(-42.220, -71.430, geometry)
        assert distance < 10  # Point is on the first node

    def test_min_distance_empty_geometry(self) -> None:
        """Empty geometry returns default distance."""
        distance = min_distance_to_way(-42.220, -71.430, [])
        assert distance == 10_000.0


# ---------------------------------------------------------------------------
# Query format test
# ---------------------------------------------------------------------------


class TestQueryFormat:
    """Test Overpass query construction."""

    @respx.mock
    async def test_overpass_query_format(self) -> None:
        """Verify the Overpass query contains correct lat/lon and highway regex."""
        route = respx.post(_OVERPASS_URL).mock(
            return_value=httpx.Response(200, json=_MOCK_EMPTY_RESPONSE)
        )

        async with httpx.AsyncClient() as http_client:
            client = RoadsClient(client=http_client)
            await client.get_road_context(-42.2, -71.4)

        assert route.call_count == 1
        request = route.calls[0].request

        # Decode the URL-encoded POST body
        body = unquote_plus(request.content.decode("utf-8"))

        # The query uses grid cell center, which for (-42.2, -71.4) is (-42.2, -71.4)
        assert "-42.2" in body
        assert "-71.4" in body
        assert "highway" in body
        assert "track" in body
        assert "path" in body
        assert "tertiary" in body
        assert "unclassified" in body
        assert "secondary" in body
        assert "primary" in body
        assert "trunk" in body
        assert "motorway" in body
        assert "out geom" in body
