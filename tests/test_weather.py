"""Tests for the Open-Meteo weather API client.

All tests use respx to mock HTTP responses -- no real API calls are made.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
import respx

from firesentinel.core.types import WeatherContext
from firesentinel.ingestion.weather import WeatherClient, _grid_key

# ---------------------------------------------------------------------------
# Helpers -- mock response builders
# ---------------------------------------------------------------------------

_BASE_URL = "https://api.open-meteo.com/v1/forecast"


def _make_hourly_response(
    *,
    hours: int = 7,
    start_hour: int = 18,
    cape_values: list[float] | None = None,
    cin_values: list[float] | None = None,
    weather_codes: list[int] | None = None,
    temperature_values: list[float] | None = None,
    wind_values: list[float] | None = None,
    humidity_values: list[float] | None = None,
    precipitation_values: list[float] | None = None,
) -> dict:
    """Build an Open-Meteo-shaped JSON response.

    Defaults produce 7 hourly slots starting at 2026-02-24T{start_hour}:00.
    """
    times = [f"2026-02-24T{start_hour + i:02d}:00" for i in range(hours)]

    def _pad(vals: list | None, default: float | int) -> list:
        if vals is None:
            return [default] * hours
        # Extend with default if shorter than *hours*
        return vals + [default] * max(0, hours - len(vals))

    return {
        "hourly": {
            "time": times,
            "cape": _pad(cape_values, 0.0),
            "convective_inhibition": _pad(cin_values, 0.0),
            "weather_code": _pad(weather_codes, 0),
            "temperature_2m": _pad(temperature_values, 18.5),
            "wind_speed_10m": _pad(wind_values, 12.0),
            "relative_humidity_2m": _pad(humidity_values, 35.0),
            "precipitation": _pad(precipitation_values, 0.0),
        }
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_get_weather_normal() -> None:
    """Verify all WeatherContext fields are populated from a normal response."""
    mock_response = _make_hourly_response(
        cape_values=[50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 350.0],
        cin_values=[10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0],
        weather_codes=[0, 1, 2, 3, 0, 1, 0],
        temperature_values=[18.0, 19.0, 20.0, 21.0, 22.0, 23.0, 24.0],
        wind_values=[10.0, 12.0, 14.0, 16.0, 18.0, 20.0, 22.0],
        humidity_values=[30.0, 32.0, 34.0, 36.0, 38.0, 40.0, 42.0],
        precipitation_values=[0.0, 0.5, 0.0, 1.0, 0.0, 0.0, 0.0],
    )

    respx.get(_BASE_URL).mock(return_value=httpx.Response(200, json=mock_response))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        detection = datetime(2026, 2, 24, 21, 0, tzinfo=UTC)
        result = await wc.get_weather_context(-42.22, -71.43, detection)

    assert result is not None
    assert isinstance(result, WeatherContext)
    # Closest slot to 21:00 is index 3 (T21:00)
    assert result.cape == 200.0
    assert result.convective_inhibition == 40.0
    assert result.weather_code == 3
    assert result.temperature_c == 21.0
    assert result.wind_speed_kmh == 16.0
    assert result.humidity_pct == 36.0
    assert result.precipitation_mm_6h == 1.5  # 0.0 + 0.5 + 0.0 + 1.0
    assert result.has_thunderstorm is False


@pytest.mark.asyncio
@respx.mock
async def test_thunderstorm_detected() -> None:
    """A weather_code 95 in the 6h window sets has_thunderstorm=True."""
    mock_response = _make_hourly_response(
        weather_codes=[0, 95, 0, 0, 0, 0, 0],
    )

    respx.get(_BASE_URL).mock(return_value=httpx.Response(200, json=mock_response))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        # Use hour 23 so the 6h window (17:00-23:00) covers index 1 (T19:00)
        detection = datetime(2026, 2, 24, 23, 0, tzinfo=UTC)
        result = await wc.get_weather_context(-42.25, -71.50, detection)

    assert result is not None
    assert result.has_thunderstorm is True


@pytest.mark.asyncio
@respx.mock
async def test_no_thunderstorm() -> None:
    """All benign weather codes produce has_thunderstorm=False."""
    mock_response = _make_hourly_response(
        weather_codes=[0, 1, 2, 3, 0, 1, 0],
    )

    respx.get(_BASE_URL).mock(return_value=httpx.Response(200, json=mock_response))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        detection = datetime(2026, 2, 24, 21, 0, tzinfo=UTC)
        result = await wc.get_weather_context(-42.25, -71.50, detection)

    assert result is not None
    assert result.has_thunderstorm is False


@pytest.mark.asyncio
@respx.mock
async def test_precipitation_sum_6h() -> None:
    """Verify 6h precipitation sums correctly across multiple rainy hours."""
    mock_response = _make_hourly_response(
        precipitation_values=[1.5, 2.0, 0.5, 3.0, 0.0, 1.0, 0.0],
    )

    respx.get(_BASE_URL).mock(return_value=httpx.Response(200, json=mock_response))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        # Detection at T23:00, 6h window covers T17:00-T23:00 (all 7 slots)
        detection = datetime(2026, 2, 24, 23, 0, tzinfo=UTC)
        result = await wc.get_weather_context(-42.25, -71.50, detection)

    assert result is not None
    # All 7 slots fall in window: 1.5+2.0+0.5+3.0+0.0+1.0+0.0 = 8.0
    assert result.precipitation_mm_6h == 8.0


@pytest.mark.asyncio
@respx.mock
async def test_cache_hit() -> None:
    """Same grid cell queried twice should only make 1 API call."""
    mock_response = _make_hourly_response()
    route = respx.get(_BASE_URL).mock(return_value=httpx.Response(200, json=mock_response))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        detection = datetime(2026, 2, 24, 21, 0, tzinfo=UTC)

        result1 = await wc.get_weather_context(-42.22, -71.43, detection)
        result2 = await wc.get_weather_context(-42.22, -71.43, detection)

    assert result1 is not None
    assert result2 is not None
    assert result1 == result2
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_cache_different_cells() -> None:
    """Different grid cells should each produce their own API call."""
    mock_response = _make_hourly_response()
    route = respx.get(_BASE_URL).mock(return_value=httpx.Response(200, json=mock_response))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        detection = datetime(2026, 2, 24, 21, 0, tzinfo=UTC)

        # These two are far enough apart to land in different grid cells
        await wc.get_weather_context(-42.00, -71.00, detection)
        await wc.get_weather_context(-43.00, -72.00, detection)

    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_api_error_returns_none() -> None:
    """HTTP 500 from Open-Meteo should return None (graceful degradation)."""
    respx.get(_BASE_URL).mock(return_value=httpx.Response(500))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        detection = datetime(2026, 2, 24, 21, 0, tzinfo=UTC)
        result = await wc.get_weather_context(-42.25, -71.50, detection)

    assert result is None


@pytest.mark.asyncio
@respx.mock
async def test_api_timeout_returns_none() -> None:
    """A timeout from Open-Meteo should return None (graceful degradation)."""
    respx.get(_BASE_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        detection = datetime(2026, 2, 24, 21, 0, tzinfo=UTC)
        result = await wc.get_weather_context(-42.25, -71.50, detection)

    assert result is None


@pytest.mark.asyncio
async def test_grid_cell_rounding() -> None:
    """Verify coordinates round correctly to 0.25-degree grid."""
    # -42.22 rounds to -42.25, -71.43 rounds to -71.50
    assert _grid_key(-42.22, -71.43) == (-42.25, -71.5)

    # -42.10 rounds to -42.0, -71.10 rounds to -71.0
    assert _grid_key(-42.10, -71.10) == (-42.0, -71.0)

    # -42.12 rounds to -42.25 (0.12/0.25=0.48 -> round to 0), actually:
    # -42.12 / 0.25 = -168.48, round(-168.48) = -168, * 0.25 = -42.0
    assert _grid_key(-42.12, -71.12) == (-42.0, -71.0)

    # Exact grid points stay put
    assert _grid_key(-42.25, -71.50) == (-42.25, -71.5)

    # Near midpoint: -42.375 -> round(-42.375/0.25) = round(-169.5) = -170 (banker's rounding)
    # -170 * 0.25 = -42.5
    # -71.625 -> round(-71.625/0.25) = round(-286.5) = -286 (banker's: .5 rounds to even)
    # -286 * 0.25 = -71.5
    assert _grid_key(-42.375, -71.625) == (-42.5, -71.5)

    # Non-midpoint value: -71.7 -> round(-71.7/0.25) = round(-286.8) = -287 -> -287*0.25 = -71.75
    assert _grid_key(-42.375, -71.7) == (-42.5, -71.75)


@pytest.mark.asyncio
@respx.mock
async def test_high_cape_values() -> None:
    """CAPE values > 1000 are passed through correctly."""
    mock_response = _make_hourly_response(
        cape_values=[1500.0, 2000.0, 2500.0, 3000.0, 1800.0, 1200.0, 900.0],
    )

    respx.get(_BASE_URL).mock(return_value=httpx.Response(200, json=mock_response))

    async with httpx.AsyncClient() as client:
        wc = WeatherClient(client=client)
        detection = datetime(2026, 2, 24, 21, 0, tzinfo=UTC)
        result = await wc.get_weather_context(-42.25, -71.50, detection)

    assert result is not None
    # Closest slot to T21:00 is index 3 (T21:00)
    assert result.cape == 3000.0
