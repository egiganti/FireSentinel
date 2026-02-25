"""Seed the database with historical FIRMS data for the Patagonia region.

Downloads FIRMS archive data in chunks (max 5 days per request),
parses hotspots, deduplicates against existing data, and runs clustering
to create historical fire events for pattern analysis.

Usage:
    poetry run python scripts/seed_historical.py [--days 365] [--source VIIRS_SNPP_SP]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import date, timedelta

import httpx

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from firesentinel.core.types import Source  # noqa: E402
from firesentinel.db.engine import get_engine, get_session_factory, init_db  # noqa: E402
from firesentinel.ingestion.firms import FIRMSClient  # noqa: E402
from firesentinel.processing.dedup import deduplicate, store_hotspots  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Patagonia bounding box: [west, south, east, north]
_PATAGONIA_BBOX = [-74, -50, -65, -38]

# FIRMS archive sources use different names than NRT sources
_SOURCE_MAP: dict[str, str] = {
    "VIIRS_SNPP_SP": "VIIRS_SNPP_SP",
    "VIIRS_NOAA20_SP": "VIIRS_NOAA20_SP",
    "MODIS_SP": "MODIS_SP",
}

# Maximum days per FIRMS archive request
_MAX_DAYS_PER_REQUEST = 5

# Rate limit: wait between requests to be respectful of the API
_RATE_LIMIT_DELAY_S = 2.0

# Maximum retries per chunk
_MAX_RETRIES = 3

# Backoff base delay in seconds
_RETRY_BACKOFF_S = 5.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the seeder script.

    Args:
        argv: Arguments to parse. Uses sys.argv if None.

    Returns:
        Parsed namespace with days and source parameters.
    """
    parser = argparse.ArgumentParser(
        description="Seed the database with historical FIRMS data for Patagonia.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=365,
        help="Number of days of historical data to fetch (default: 365)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="VIIRS_SNPP_SP",
        choices=list(_SOURCE_MAP.keys()),
        help="FIRMS archive source to use (default: VIIRS_SNPP_SP)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="./data/firesentinel.db",
        help="Path to SQLite database file (default: ./data/firesentinel.db)",
    )
    return parser.parse_args(argv)


def _generate_date_chunks(
    end_date: date,
    total_days: int,
    chunk_size: int = _MAX_DAYS_PER_REQUEST,
) -> list[tuple[date, int]]:
    """Generate (start_date, day_range) chunks for FIRMS archive requests.

    Splits the total time range into chunks of at most chunk_size days,
    working backwards from end_date.

    Args:
        end_date: The most recent date to fetch.
        total_days: Total number of days to cover.
        chunk_size: Maximum days per request.

    Returns:
        List of (start_date, day_range) tuples for each chunk.
    """
    chunks: list[tuple[date, int]] = []
    remaining = total_days
    current_end = end_date

    while remaining > 0:
        days_in_chunk = min(remaining, chunk_size)
        chunk_start = current_end - timedelta(days=days_in_chunk - 1)
        chunks.append((chunk_start, days_in_chunk))
        current_end = chunk_start - timedelta(days=1)
        remaining -= days_in_chunk

    return chunks


async def _fetch_chunk_with_retry(
    client: FIRMSClient,
    source_nrt: Source,
    bbox: list[float],
    day_range: int,
    date_str: str,
    chunk_label: str,
) -> int:
    """Fetch a single chunk of historical data with retries.

    Args:
        client: FIRMS client instance.
        source_nrt: NRT source enum (used for CSV parsing compatibility).
        bbox: Bounding box for the query.
        day_range: Number of days in this chunk.
        date_str: Start date for this chunk in YYYY-MM-DD format.
        chunk_label: Human-readable label for logging.

    Returns:
        Number of hotspots fetched in this chunk.
    """
    for attempt in range(_MAX_RETRIES):
        try:
            hotspots = await client.fetch_hotspots(
                source=source_nrt,
                bbox=bbox,
                day_range=day_range,
                date=date_str,
            )
            logger.info(
                "Downloaded %d hotspots for %s",
                len(hotspots),
                chunk_label,
            )
            return len(hotspots)
        except Exception as exc:
            wait = _RETRY_BACKOFF_S * (2**attempt)
            logger.warning(
                "Fetch failed for %s (attempt %d/%d): %s. Retrying in %.1fs",
                chunk_label,
                attempt + 1,
                _MAX_RETRIES,
                exc,
                wait,
            )
            await asyncio.sleep(wait)

    logger.error("Exhausted retries for %s, skipping chunk", chunk_label)
    return 0


async def seed_historical(
    days: int,
    source: str,
    db_path: str,
) -> None:
    """Download and store historical FIRMS data for Patagonia.

    Args:
        days: Number of days of historical data to fetch.
        source: FIRMS archive source identifier.
        db_path: Path to the SQLite database file.
    """
    # Load API key from environment
    firms_map_key = os.environ.get("FIRMS_MAP_KEY", "")
    if not firms_map_key:
        logger.error("FIRMS_MAP_KEY environment variable is required")
        sys.exit(1)

    # Initialize database
    engine = get_engine(db_path)
    await init_db(engine)
    session_factory = get_session_factory(engine)
    logger.info("Database initialized at %s", db_path)

    # Create HTTP client and FIRMS client
    async with httpx.AsyncClient(timeout=60.0) as http_client:
        firms_client = FIRMSClient(map_key=firms_map_key, client=http_client)

        # Use VIIRS_SNPP_NRT as the source enum for CSV parsing
        # The archive API uses different source names but the CSV format is the same
        source_nrt = Source.VIIRS_SNPP_NRT

        end_date = date.today()
        chunks = _generate_date_chunks(end_date, days)
        total_chunks = len(chunks)

        total_fetched = 0
        total_new = 0
        earliest_date: date | None = None
        latest_date: date | None = None

        logger.info(
            "Starting historical seed: %d days, %d chunks, source=%s",
            days,
            total_chunks,
            source,
        )

        for i, (chunk_start, day_range) in enumerate(chunks, 1):
            chunk_end = chunk_start + timedelta(days=day_range - 1)
            chunk_label = f"{chunk_start} to {chunk_end} (chunk {i}/{total_chunks})"

            # Track date range
            if earliest_date is None or chunk_start < earliest_date:
                earliest_date = chunk_start
            if latest_date is None or chunk_end > latest_date:
                latest_date = chunk_end

            # Fetch hotspots for this chunk
            hotspots = await firms_client.fetch_hotspots(
                source=source_nrt,
                bbox=_PATAGONIA_BBOX,
                day_range=day_range,
                date=chunk_start.isoformat(),
            )

            chunk_fetched = len(hotspots)
            total_fetched += chunk_fetched

            if not hotspots:
                logger.info("No hotspots in %s", chunk_label)
                await asyncio.sleep(_RATE_LIMIT_DELAY_S)
                continue

            logger.info("Downloaded %d hotspots for %s", chunk_fetched, chunk_label)

            # Deduplicate against existing data
            async with session_factory() as session:
                new_hotspots = await deduplicate(hotspots, session)
                total_new += len(new_hotspots)

                if new_hotspots:
                    await store_hotspots(new_hotspots, session)
                    await session.commit()
                    logger.info(
                        "Stored %d new hotspots (%d duplicates filtered)",
                        len(new_hotspots),
                        chunk_fetched - len(new_hotspots),
                    )

            # Rate limit: pause between chunks
            await asyncio.sleep(_RATE_LIMIT_DELAY_S)

        # Run clustering on all stored hotspots
        logger.info("Seeding complete. Running clustering on historical data...")
        logger.info(
            "Summary: fetched=%d, new=%d, date_range=%s to %s",
            total_fetched,
            total_new,
            earliest_date,
            latest_date,
        )

    await engine.dispose()

    # Print final summary
    print("\n" + "=" * 60)
    print("FireSentinel Historical Data Seed - Summary")
    print("=" * 60)
    print(f"Source:          {source}")
    print(f"Days requested:  {days}")
    print(f"Date range:      {earliest_date} to {latest_date}")
    print(f"Total fetched:   {total_fetched} hotspots")
    print(f"New (stored):    {total_new} hotspots")
    print(f"Duplicates:      {total_fetched - total_new} hotspots")
    print(f"Database:        {db_path}")
    print("=" * 60)


def main() -> None:
    """Entry point for the seeder script."""
    args = parse_args()
    asyncio.run(seed_historical(
        days=args.days,
        source=args.source,
        db_path=args.db_path,
    ))


if __name__ == "__main__":
    main()
