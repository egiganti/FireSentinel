"""Tests for the NASA FIRMS API client.

All tests use respx to mock httpx responses -- no real API calls.
"""

from __future__ import annotations

import pytest
import respx
from httpx import AsyncClient, Response

from firesentinel.core.types import DayNight, RawHotspot, Source
from firesentinel.ingestion.firms import FIRMSClient

# ---------------------------------------------------------------------------
# Test fixtures and CSV helpers
# ---------------------------------------------------------------------------

_MAP_KEY = "TEST_MAP_KEY_123"
_BBOX = [-74.0, -50.0, -65.0, -38.0]
_BBOX_STR = "-74.0,-50.0,-65.0,-38.0"
_BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

VIIRS_HEADER = (
    "latitude,longitude,bright_ti4,scan,track,"
    "acq_date,acq_time,satellite,confidence,version,bright_ti5,frp,daynight"
)

MODIS_HEADER = (
    "latitude,longitude,brightness,scan,track,"
    "acq_date,acq_time,satellite,confidence,version,bright_t31,frp,type,daynight"
)


def _viirs_row(
    lat: float = -42.22,
    lon: float = -71.43,
    bright_ti4: float = 350.0,
    bright_ti5: float = 290.0,
    acq_date: str = "2025-01-15",
    acq_time: str = "0330",
    satellite: str = "N",
    confidence: str = "nominal",
    frp: str = "12.5",
    daynight: str = "N",
) -> str:
    return (
        f"{lat},{lon},{bright_ti4},0.39,0.36,"
        f"{acq_date},{acq_time},{satellite},{confidence},2.0,{bright_ti5},{frp},{daynight}"
    )


def _modis_row(
    lat: float = -42.22,
    lon: float = -71.43,
    brightness: float = 320.0,
    bright_t31: float = 285.0,
    acq_date: str = "2025-01-15",
    acq_time: str = "0330",
    satellite: str = "Terra",
    confidence: str = "80",
    frp: str = "25.0",
    daynight: str = "D",
) -> str:
    return (
        f"{lat},{lon},{brightness},1.0,1.0,"
        f"{acq_date},{acq_time},{satellite},{confidence},6.1,{bright_t31},{frp},0,{daynight}"
    )


def _build_viirs_csv(*rows: str) -> str:
    return "\n".join([VIIRS_HEADER, *rows])


def _build_modis_csv(*rows: str) -> str:
    return "\n".join([MODIS_HEADER, *rows])


@pytest.fixture
async def client() -> FIRMSClient:
    """Create a FIRMSClient with an httpx.AsyncClient managed by the test."""
    async with AsyncClient() as http_client:
        firms = FIRMSClient(map_key=_MAP_KEY, client=http_client)
        yield firms


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_fetch_viirs_hotspots(client: FIRMSClient) -> None:
    """Mock VIIRS CSV with 3 rows and verify parsing."""
    csv_body = _build_viirs_csv(
        _viirs_row(lat=-42.10, lon=-71.50, bright_ti4=350.0, confidence="nominal", frp="10.0"),
        _viirs_row(lat=-42.20, lon=-71.55, bright_ti4=380.0, confidence="high", frp="20.0"),
        _viirs_row(lat=-42.30, lon=-71.60, bright_ti4=310.0, confidence="nominal", frp="5.0"),
    )

    url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(200, text=csv_body))

    hotspots = await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX)

    assert len(hotspots) == 3
    assert all(isinstance(h, RawHotspot) for h in hotspots)
    assert hotspots[0].latitude == pytest.approx(-42.10)
    assert hotspots[0].longitude == pytest.approx(-71.50)
    assert hotspots[0].brightness == pytest.approx(350.0)
    assert hotspots[0].source == Source.VIIRS_SNPP_NRT
    assert hotspots[1].confidence == "high"
    assert hotspots[2].frp == pytest.approx(5.0)


@respx.mock
async def test_fetch_modis_hotspots(client: FIRMSClient) -> None:
    """Mock MODIS CSV and verify column mapping differences."""
    csv_body = _build_modis_csv(
        _modis_row(lat=-42.50, lon=-71.30, brightness=330.0, bright_t31=280.0, confidence="85"),
        _modis_row(lat=-42.55, lon=-71.35, brightness=340.0, bright_t31=290.0, confidence="90"),
    )

    url = f"{_BASE}/{_MAP_KEY}/MODIS_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(200, text=csv_body))

    hotspots = await client.fetch_hotspots(Source.MODIS_NRT, _BBOX)

    assert len(hotspots) == 2
    assert all(h.source == Source.MODIS_NRT for h in hotspots)
    # MODIS "brightness" maps to RawHotspot.brightness
    assert hotspots[0].brightness == pytest.approx(330.0)
    # MODIS "bright_t31" maps to RawHotspot.brightness_2
    assert hotspots[0].brightness_2 == pytest.approx(280.0)
    assert hotspots[0].satellite == "Terra"
    assert hotspots[0].daynight == DayNight.DAY


@respx.mock
async def test_fetch_filters_low_confidence(client: FIRMSClient) -> None:
    """Low-confidence VIIRS rows are filtered out."""
    csv_body = _build_viirs_csv(
        _viirs_row(confidence="low", bright_ti4=350.0),
        _viirs_row(confidence="nominal", bright_ti4=350.0),
        _viirs_row(confidence="high", bright_ti4=350.0),
    )

    url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(200, text=csv_body))

    hotspots = await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX)

    assert len(hotspots) == 2
    confidences = {h.confidence for h in hotspots}
    assert "low" not in confidences
    assert confidences == {"nominal", "high"}


@respx.mock
async def test_fetch_handles_single_letter_confidence_codes(client: FIRMSClient) -> None:
    """FIRMS CSV returns single-letter codes (l/n/h) which must be normalized."""
    csv_body = _build_viirs_csv(
        _viirs_row(confidence="l", bright_ti4=350.0),
        _viirs_row(confidence="n", bright_ti4=350.0),
        _viirs_row(confidence="h", bright_ti4=350.0),
    )

    url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(200, text=csv_body))

    hotspots = await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX)

    assert len(hotspots) == 2
    confidences = {h.confidence for h in hotspots}
    assert "l" not in confidences and "low" not in confidences
    assert confidences == {"nominal", "high"}


@respx.mock
async def test_fetch_filters_low_brightness(client: FIRMSClient) -> None:
    """Dim hotspots (brightness <= 300K) are filtered out."""
    csv_body = _build_viirs_csv(
        _viirs_row(bright_ti4=290.0, confidence="nominal"),
        _viirs_row(bright_ti4=300.0, confidence="nominal"),
        _viirs_row(bright_ti4=301.0, confidence="nominal"),
    )

    url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(200, text=csv_body))

    hotspots = await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX)

    # Only the 301.0K row passes (> 300, not >=)
    assert len(hotspots) == 1
    assert hotspots[0].brightness == pytest.approx(301.0)


@respx.mock
async def test_fetch_empty_response(client: FIRMSClient) -> None:
    """Headers-only CSV returns an empty list."""
    csv_body = VIIRS_HEADER

    url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(200, text=csv_body))

    hotspots = await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX)

    assert hotspots == []


@respx.mock
async def test_fetch_http_error(client: FIRMSClient) -> None:
    """HTTP 500 returns empty list (graceful degradation)."""
    url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(500))

    hotspots = await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX)

    assert hotspots == []


@respx.mock
async def test_fetch_all_sources_parallel(client: FIRMSClient) -> None:
    """All 4 sources fetched in parallel; results combined."""
    viirs_csv = _build_viirs_csv(
        _viirs_row(lat=-42.10, confidence="nominal", bright_ti4=350.0),
    )
    modis_csv = _build_modis_csv(
        _modis_row(lat=-42.50, confidence="80", brightness=320.0),
    )

    sources = [
        "VIIRS_SNPP_NRT",
        "VIIRS_NOAA20_NRT",
        "VIIRS_NOAA21_NRT",
        "MODIS_NRT",
    ]

    for src_name in sources:
        url = f"{_BASE}/{_MAP_KEY}/{src_name}/{_BBOX_STR}/1"
        csv_data = modis_csv if src_name == "MODIS_NRT" else viirs_csv
        respx.get(url).mock(return_value=Response(200, text=csv_data))

    hotspots = await client.fetch_all_sources(_BBOX)

    # 3 VIIRS sources x 1 row + 1 MODIS x 1 row = 4
    assert len(hotspots) == 4
    viirs_count = sum(1 for h in hotspots if h.source != Source.MODIS_NRT)
    modis_count = sum(1 for h in hotspots if h.source == Source.MODIS_NRT)
    assert viirs_count == 3
    assert modis_count == 1


@respx.mock
async def test_fetch_all_sources_partial_failure(client: FIRMSClient) -> None:
    """One source fails; the other 3 still return data."""
    viirs_csv = _build_viirs_csv(
        _viirs_row(lat=-42.10, confidence="nominal", bright_ti4=350.0),
    )
    modis_csv = _build_modis_csv(
        _modis_row(lat=-42.50, confidence="80", brightness=320.0),
    )

    # SNPP returns 500; others succeed
    respx.get(f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1").mock(return_value=Response(500))
    respx.get(f"{_BASE}/{_MAP_KEY}/VIIRS_NOAA20_NRT/{_BBOX_STR}/1").mock(
        return_value=Response(200, text=viirs_csv)
    )
    respx.get(f"{_BASE}/{_MAP_KEY}/VIIRS_NOAA21_NRT/{_BBOX_STR}/1").mock(
        return_value=Response(200, text=viirs_csv)
    )
    respx.get(f"{_BASE}/{_MAP_KEY}/MODIS_NRT/{_BBOX_STR}/1").mock(
        return_value=Response(200, text=modis_csv)
    )

    hotspots = await client.fetch_all_sources(_BBOX)

    # 0 from SNPP + 1 from NOAA20 + 1 from NOAA21 + 1 from MODIS = 3
    assert len(hotspots) == 3


@respx.mock
async def test_bbox_formatting(client: FIRMSClient) -> None:
    """Verify the URL contains correctly formatted bbox string."""
    csv_body = _build_viirs_csv()  # Headers only
    custom_bbox = [-72.1, -43.0, -71.2, -41.8]
    expected_bbox_str = "-72.1,-43.0,-71.2,-41.8"
    expected_url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{expected_bbox_str}/1"

    route = respx.get(expected_url).mock(return_value=Response(200, text=csv_body))

    await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, custom_bbox)

    assert route.called


@respx.mock
async def test_date_parameter(client: FIRMSClient) -> None:
    """Verify date is appended to the URL when provided."""
    csv_body = _build_viirs_csv()
    target_date = "2025-01-15"
    expected_url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1/{target_date}"

    route = respx.get(expected_url).mock(return_value=Response(200, text=csv_body))

    await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX, date=target_date)

    assert route.called


@respx.mock
async def test_missing_frp_handled(client: FIRMSClient) -> None:
    """Empty FRP field defaults to 0.0."""
    csv_body = _build_viirs_csv(
        _viirs_row(frp="", confidence="high", bright_ti4=350.0),
    )

    url = f"{_BASE}/{_MAP_KEY}/VIIRS_SNPP_NRT/{_BBOX_STR}/1"
    respx.get(url).mock(return_value=Response(200, text=csv_body))

    hotspots = await client.fetch_hotspots(Source.VIIRS_SNPP_NRT, _BBOX)

    assert len(hotspots) == 1
    assert hotspots[0].frp == pytest.approx(0.0)
