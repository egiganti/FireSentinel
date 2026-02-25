"""Open-Meteo weather API client for enriching hotspot detections.

Provides weather context (temperature, wind, humidity, precipitation,
thunderstorm activity, CAPE) for a given location and time. Uses grid-cell
caching (0.25 deg, 60-min TTL) to reduce duplicate API calls for nearby
hotspots in the same satellite pass.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import httpx

from firesentinel.core.types import WeatherContext

logger = logging.getLogger(__name__)

# API endpoints
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

# Hourly variables requested from Open-Meteo
_HOURLY_VARS = (
    "cape",
    "convective_inhibition",
    "weather_code",
    "temperature_2m",
    "wind_speed_10m",
    "relative_humidity_2m",
    "precipitation",
)

# Weather codes indicating thunderstorm activity (WMO code table 4677)
_THUNDERSTORM_CODES = frozenset({95, 96, 99})

# Grid-cell caching defaults (can be overridden from monitoring.yml)
_GRID_DEGREES = 0.25
_CACHE_TTL_MINUTES = 60

# HTTP request timeout in seconds
_REQUEST_TIMEOUT_S = 15.0

# Timezone for Buenos Aires / Patagonia
_TIMEZONE = "America/Argentina/Buenos_Aires"


def _snap_to_grid(value: float, step: float) -> float:
    """Round a coordinate to the nearest grid step."""
    return round(value / step) * step


def _grid_key(latitude: float, longitude: float) -> tuple[float, float]:
    """Compute grid-cell cache key for a coordinate pair."""
    return (
        _snap_to_grid(latitude, _GRID_DEGREES),
        _snap_to_grid(longitude, _GRID_DEGREES),
    )


def _find_closest_index(times: list[str], target: datetime) -> int:
    """Return the index of the time slot closest to *target*.

    Open-Meteo returns ISO-format timestamps without offset info (they are
    already in the requested timezone).  We parse them as naive datetimes and
    compare against a naive target.
    """
    target_naive = target.replace(tzinfo=None)
    best_idx = 0
    best_delta = abs((datetime.fromisoformat(times[0]) - target_naive).total_seconds())

    for idx in range(1, len(times)):
        dt = datetime.fromisoformat(times[idx])
        delta = abs((dt - target_naive).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best_idx = idx

    return best_idx


def _sum_precipitation(
    times: list[str],
    precipitation: list[float | None],
    end: datetime,
    hours: int,
) -> float:
    """Sum precipitation values in the *hours*-long window ending at *end*."""
    end_naive = end.replace(tzinfo=None)
    start_naive = end_naive - timedelta(hours=hours)
    total = 0.0
    for i, ts in enumerate(times):
        dt = datetime.fromisoformat(ts)
        if start_naive <= dt <= end_naive:
            val = precipitation[i]
            if val is not None:
                total += val
    return round(total, 2)


def _has_thunderstorm_in_window(
    times: list[str],
    weather_codes: list[int | None],
    end: datetime,
    hours: int,
) -> bool:
    """Return True if any thunderstorm weather code appears in the window."""
    end_naive = end.replace(tzinfo=None)
    start_naive = end_naive - timedelta(hours=hours)
    for i, ts in enumerate(times):
        dt = datetime.fromisoformat(ts)
        if start_naive <= dt <= end_naive:
            code = weather_codes[i]
            if code is not None and code in _THUNDERSTORM_CODES:
                return True
    return False


class WeatherClient:
    """Async Open-Meteo client with grid-cell caching.

    Parameters
    ----------
    client:
        Optional ``httpx.AsyncClient`` to reuse across requests. If not
        provided, a new client is created per request (less efficient but
        simpler for one-off calls).
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._external_client = client
        self._cache: dict[tuple[float, float], tuple[WeatherContext, datetime]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_weather_context(
        self,
        latitude: float,
        longitude: float,
        detection_time: datetime,
    ) -> WeatherContext | None:
        """Fetch weather conditions for a hotspot location and time.

        Returns ``None`` on any error (graceful degradation).
        """
        key = _grid_key(latitude, longitude)

        # Check cache
        cached = self._cache.get(key)
        if cached is not None:
            ctx, cached_at = cached
            if datetime.now(tz=UTC) - cached_at < timedelta(minutes=_CACHE_TTL_MINUTES):
                logger.debug("Cache hit for grid cell %s", key)
                return ctx
            # Expired -- remove
            del self._cache[key]

        try:
            result = await self._fetch_and_parse(key[0], key[1], detection_time)
        except Exception:
            logger.exception(
                "Weather API error for (%.4f, %.4f) at %s",
                latitude,
                longitude,
                detection_time.isoformat(),
            )
            return None

        if result is not None:
            self._cache[key] = (result, datetime.now(tz=UTC))

        return result

    def clear_expired(self) -> None:
        """Remove all expired entries from the cache."""
        now = datetime.now(tz=UTC)
        expired_keys = [
            k
            for k, (_, cached_at) in self._cache.items()
            if now - cached_at >= timedelta(minutes=_CACHE_TTL_MINUTES)
        ]
        for k in expired_keys:
            del self._cache[k]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_and_parse(
        self,
        grid_lat: float,
        grid_lon: float,
        detection_time: datetime,
    ) -> WeatherContext | None:
        """Query Open-Meteo and parse into a WeatherContext."""
        now_utc = datetime.now(tz=UTC)
        det_utc = (
            detection_time
            if detection_time.tzinfo is not None
            else detection_time.replace(tzinfo=UTC)
        )
        is_historical = (now_utc - det_utc).total_seconds() > 86400  # >24h ago

        params = self._build_params(grid_lat, grid_lon, detection_time, is_historical)
        url = _ARCHIVE_URL if is_historical else _FORECAST_URL

        data = await self._request(url, params)
        if data is None:
            return None

        hourly = data.get("hourly")
        if hourly is None:
            logger.warning("Open-Meteo response missing 'hourly' key")
            return None

        return self._parse_hourly(hourly, detection_time)

    def _build_params(
        self,
        grid_lat: float,
        grid_lon: float,
        detection_time: datetime,
        is_historical: bool,
    ) -> dict[str, str | float]:
        """Build query parameters for the Open-Meteo request."""
        hourly_str = ",".join(_HOURLY_VARS)

        if is_historical:
            # Archive API requires start_date / end_date
            end_date = detection_time.date()
            start_date = end_date - timedelta(days=3)
            return {
                "latitude": grid_lat,
                "longitude": grid_lon,
                "hourly": hourly_str,
                "timezone": _TIMEZONE,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            }

        return {
            "latitude": grid_lat,
            "longitude": grid_lon,
            "hourly": hourly_str,
            "timezone": _TIMEZONE,
            "past_hours": "6",
            "forecast_hours": "1",
        }

    async def _request(
        self,
        url: str,
        params: dict[str, str | float],
    ) -> dict | None:  # type: ignore[type-arg]
        """Perform the HTTP GET and return parsed JSON, or None on error."""
        if self._external_client is not None:
            response = await self._external_client.get(
                url, params=params, timeout=_REQUEST_TIMEOUT_S
            )
        else:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=_REQUEST_TIMEOUT_S)

        if response.status_code != 200:
            logger.warning(
                "Open-Meteo returned HTTP %d for %s",
                response.status_code,
                url,
            )
            return None

        return response.json()  # type: ignore[no-any-return]

    def _parse_hourly(
        self,
        hourly: dict[str, list[float | int | str | None]],
        detection_time: datetime,
    ) -> WeatherContext | None:
        """Extract fields from the hourly response block."""
        times: list[str] = hourly.get("time", [])  # type: ignore[assignment]
        if not times:
            logger.warning("Open-Meteo hourly data has no time entries")
            return None

        idx = _find_closest_index(times, detection_time)

        cape_vals: list[float | None] = hourly.get("cape", [])  # type: ignore[assignment]
        cin_vals: list[float | None] = hourly.get("convective_inhibition", [])  # type: ignore[assignment]
        wcode_vals: list[int | None] = hourly.get("weather_code", [])  # type: ignore[assignment]
        temp_vals: list[float | None] = hourly.get("temperature_2m", [])  # type: ignore[assignment]
        wind_vals: list[float | None] = hourly.get("wind_speed_10m", [])  # type: ignore[assignment]
        hum_vals: list[float | None] = hourly.get("relative_humidity_2m", [])  # type: ignore[assignment]
        precip_vals: list[float | None] = hourly.get("precipitation", [])  # type: ignore[assignment]

        cape = cape_vals[idx] if idx < len(cape_vals) else None
        cin = cin_vals[idx] if idx < len(cin_vals) else None
        wcode = wcode_vals[idx] if idx < len(wcode_vals) else None
        temp = temp_vals[idx] if idx < len(temp_vals) else None
        wind = wind_vals[idx] if idx < len(wind_vals) else None
        hum = hum_vals[idx] if idx < len(hum_vals) else None

        # Default any None values to 0
        cape = cape if cape is not None else 0.0
        cin = cin if cin is not None else 0.0
        wcode = wcode if wcode is not None else 0
        temp = temp if temp is not None else 0.0
        wind = wind if wind is not None else 0.0
        hum = hum if hum is not None else 0.0

        precip_6h = _sum_precipitation(times, precip_vals, detection_time, hours=6)

        # 72h precipitation: use available data (may be less than 72h for forecast queries)
        precip_72h = _sum_precipitation(times, precip_vals, detection_time, hours=72)

        has_storm = _has_thunderstorm_in_window(times, wcode_vals, detection_time, hours=6)

        return WeatherContext(
            cape=float(cape),
            convective_inhibition=float(cin),
            weather_code=int(wcode),
            temperature_c=float(temp),
            wind_speed_kmh=float(wind),
            humidity_pct=float(hum),
            precipitation_mm_6h=precip_6h,
            precipitation_mm_72h=precip_72h,
            has_thunderstorm=has_storm,
        )
