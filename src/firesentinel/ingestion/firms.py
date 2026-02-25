"""NASA FIRMS API client for fetching satellite fire hotspot data.

Supports VIIRS (SNPP, NOAA-20, NOAA-21) and MODIS NRT sources.
Parses CSV responses into RawHotspot dataclass instances.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time as time_mod
from datetime import date, time

import httpx

from firesentinel.core.types import DayNight, RawHotspot, Source

logger = logging.getLogger(__name__)

# FIRMS API base URL
_BASE_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"

# Rate limit: 5,000 requests per 10-minute window
_RATE_LIMIT_WINDOW_SECONDS = 600
_RATE_LIMIT_MAX_REQUESTS = 5_000
_RATE_LIMIT_WARN_THRESHOLD = 4_500

# Brightness temperature minimum threshold (Kelvin)
_MIN_BRIGHTNESS_K = 300.0

# Exponential backoff settings for 429 responses
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_MAX_RETRIES = 3

# VIIRS sources share the same CSV column layout
_VIIRS_SOURCES = {Source.VIIRS_SNPP_NRT, Source.VIIRS_NOAA20_NRT, Source.VIIRS_NOAA21_NRT}

# Acceptable VIIRS confidence levels (case-insensitive)
_VIIRS_VALID_CONFIDENCE = {"nominal", "high"}

# Minimum MODIS confidence value (integer percentage)
_MODIS_MIN_CONFIDENCE = 30


class FIRMSClient:
    """Async client for the NASA FIRMS fire data API.

    Fetches hotspot CSV data, parses it into RawHotspot instances, and applies
    confidence and brightness filters.
    """

    def __init__(
        self,
        map_key: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._map_key = map_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=30.0)

        # Simple rate-limit tracking
        self._request_count = 0
        self._window_start = time_mod.monotonic()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_hotspots(
        self,
        source: Source,
        bbox: list[float],
        day_range: int = 1,
        date: str | None = None,
    ) -> list[RawHotspot]:
        """Fetch hotspots for a single source within a bounding box.

        Args:
            source: Satellite data source enum.
            bbox: Bounding box as [west, south, east, north].
            day_range: Number of days to look back (1-10).
            date: Optional specific date in YYYY-MM-DD format.

        Returns:
            Parsed and filtered list of RawHotspot instances.
            Returns empty list on any HTTP or parsing error.
        """
        bbox_str = ",".join(str(c) for c in bbox)
        url = f"{_BASE_URL}/{self._map_key}/{source.value}/{bbox_str}/{day_range}"
        if date is not None:
            url = f"{url}/{date}"

        csv_text = await self._request_with_backoff(url, source)
        if csv_text is None:
            return []

        return self._parse_csv(csv_text, source)

    async def fetch_all_sources(
        self,
        bbox: list[float],
        day_range: int = 1,
    ) -> list[RawHotspot]:
        """Fetch hotspots from all 4 satellite sources in parallel.

        If one source fails, the others still return data (graceful degradation).

        Args:
            bbox: Bounding box as [west, south, east, north].
            day_range: Number of days to look back.

        Returns:
            Combined list of hotspots from all successful source fetches.
        """
        sources = [
            Source.VIIRS_SNPP_NRT,
            Source.VIIRS_NOAA20_NRT,
            Source.VIIRS_NOAA21_NRT,
            Source.MODIS_NRT,
        ]

        results = await asyncio.gather(
            *(self.fetch_hotspots(src, bbox, day_range) for src in sources),
            return_exceptions=True,
        )

        combined: list[RawHotspot] = []
        for src, result in zip(sources, results, strict=True):
            if isinstance(result, BaseException):
                logger.error("Source %s failed: %s", src.value, result)
                continue
            logger.info("Source %s returned %d hotspots", src.value, len(result))
            combined.extend(result)

        logger.info("Total hotspots from all sources: %d", len(combined))
        return combined

    async def close(self) -> None:
        """Close the underlying HTTP client if we own it."""
        if self._owns_client:
            await self._client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_rate_limit(self) -> None:
        """Track request count and log warning when approaching the limit."""
        now = time_mod.monotonic()
        elapsed = now - self._window_start

        # Reset window if 10 minutes have passed
        if elapsed >= _RATE_LIMIT_WINDOW_SECONDS:
            self._request_count = 0
            self._window_start = now

        self._request_count += 1

        if self._request_count >= _RATE_LIMIT_WARN_THRESHOLD:
            logger.warning(
                "Approaching FIRMS rate limit: %d / %d requests in current window",
                self._request_count,
                _RATE_LIMIT_MAX_REQUESTS,
            )

    async def _request_with_backoff(
        self,
        url: str,
        source: Source,
    ) -> str | None:
        """Make an HTTP GET with exponential backoff on 429 responses.

        Returns the response text, or None on unrecoverable errors.
        """
        self._check_rate_limit()

        for attempt in range(_BACKOFF_MAX_RETRIES + 1):
            try:
                response = await self._client.get(url)

                if response.status_code == 429:
                    wait = _BACKOFF_BASE_SECONDS * (2**attempt)
                    logger.warning(
                        "Rate limited (429) on %s, retrying in %.1fs (attempt %d/%d)",
                        source.value,
                        wait,
                        attempt + 1,
                        _BACKOFF_MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue

                response.raise_for_status()
                return response.text

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "HTTP %d error fetching %s: %s",
                    exc.response.status_code,
                    source.value,
                    exc,
                )
                return None
            except httpx.HTTPError as exc:
                logger.error("HTTP error fetching %s: %s", source.value, exc)
                return None

        logger.error("Exhausted retries for %s after persistent 429 responses", source.value)
        return None

    def _parse_csv(self, csv_text: str, source: Source) -> list[RawHotspot]:
        """Parse FIRMS CSV text into filtered RawHotspot instances."""
        reader = csv.DictReader(io.StringIO(csv_text))
        is_viirs = source in _VIIRS_SOURCES
        hotspots: list[RawHotspot] = []

        for row in reader:
            try:
                hotspot = self._parse_row(row, source, is_viirs)
            except (ValueError, KeyError) as exc:
                logger.debug("Skipping malformed row: %s -- %s", row, exc)
                continue

            if hotspot is not None:
                hotspots.append(hotspot)

        return hotspots

    def _parse_row(
        self,
        row: dict[str, str],
        source: Source,
        is_viirs: bool,
    ) -> RawHotspot | None:
        """Parse and filter a single CSV row.

        Returns None if the row fails confidence or brightness filters.
        """
        # Extract confidence and apply filter
        confidence_raw = row["confidence"].strip()

        if is_viirs:
            if confidence_raw.lower() not in _VIIRS_VALID_CONFIDENCE:
                return None
        else:
            # MODIS confidence is an integer percentage
            try:
                conf_int = int(confidence_raw)
            except ValueError:
                return None
            if conf_int < _MODIS_MIN_CONFIDENCE:
                return None

        # Extract brightness and apply filter
        if is_viirs:
            brightness = float(row["bright_ti4"])
            brightness_2 = float(row["bright_ti5"])
        else:
            brightness = float(row["brightness"])
            brightness_2 = float(row["bright_t31"])

        if brightness <= _MIN_BRIGHTNESS_K:
            return None

        # Parse FRP -- handle missing/empty values
        frp_raw = row.get("frp", "").strip()
        frp = float(frp_raw) if frp_raw else 0.0

        # Parse acquisition date and time
        acq_date = _parse_date(row["acq_date"])
        acq_time = _parse_time(row["acq_time"])

        # Parse daynight flag
        daynight = DayNight(row["daynight"].strip())

        return RawHotspot(
            source=source,
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            brightness=brightness,
            brightness_2=brightness_2,
            frp=frp,
            confidence=confidence_raw,
            acq_date=acq_date,
            acq_time=acq_time,
            satellite=row["satellite"].strip(),
            daynight=daynight,
            raw_data=dict(row),
        )


def _parse_date(date_str: str) -> date:
    """Parse FIRMS date string (YYYY-MM-DD) into a date object."""
    parts = date_str.strip().split("-")
    return date(int(parts[0]), int(parts[1]), int(parts[2]))


def _parse_time(time_str: str) -> time:
    """Parse FIRMS time string (HHMM) into a time object."""
    raw = time_str.strip().zfill(4)
    return time(int(raw[:2]), int(raw[2:]))
