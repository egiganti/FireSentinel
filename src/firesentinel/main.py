"""FireSentinel Patagonia entry point.

Initializes configuration, database, and starts the scheduler.
"""

from __future__ import annotations

import asyncio
import logging

from firesentinel import __version__
from firesentinel.config import get_settings, get_yaml_config
from firesentinel.db.engine import get_engine, init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def async_main() -> None:
    """Async initialization and startup sequence."""
    settings = get_settings()
    yaml_config = get_yaml_config()

    logger.info("FireSentinel Patagonia v%s initialized", __version__)
    logger.info("Environment: %s", settings.environment)
    logger.info("Database: %s", settings.db_path)
    logger.info(
        "Monitoring %d zones, polling every %d minutes",
        len(yaml_config.zones),
        yaml_config.monitoring.poll_interval_minutes,
    )

    # Initialize database
    engine = get_engine(settings.db_path)
    await init_db(engine)
    logger.info("Database tables created")

    # TODO: Set up APScheduler and start pipeline
    # scheduler = setup_scheduler(engine, settings, yaml_config)
    # scheduler.start()
    logger.info("Scheduler setup pending (Phase 3)")

    await engine.dispose()


def main() -> None:
    """Synchronous entry point."""
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
