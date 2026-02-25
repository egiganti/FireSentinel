"""FireSentinel Patagonia entry point.

Loads configuration, initializes the database and all API clients, creates
the pipeline orchestrator, and either runs a single cycle (--once) or starts
the APScheduler for continuous monitoring.

Usage:
    python -m firesentinel.main           # Start scheduler (runs forever)
    python -m firesentinel.main --once    # Run one cycle and exit
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from typing import Any

import httpx

from firesentinel import __version__
from firesentinel.config import get_settings, get_yaml_config
from firesentinel.core.pipeline import Pipeline
from firesentinel.core.scheduler import create_scheduler, run_once
from firesentinel.db.engine import get_engine, get_session_factory, init_db
from firesentinel.ingestion.firms import FIRMSClient
from firesentinel.ingestion.roads import RoadsClient
from firesentinel.ingestion.weather import WeatherClient
from firesentinel.processing.classifier import IntentClassifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Arguments to parse. Uses sys.argv if None.

    Returns:
        Parsed namespace with the ``once`` flag.
    """
    parser = argparse.ArgumentParser(
        prog="firesentinel",
        description="FireSentinel Patagonia wildfire detection pipeline",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single pipeline cycle and exit",
    )
    return parser.parse_args(argv)


async def async_main(once: bool = False) -> None:
    """Async initialization and startup sequence.

    Args:
        once: If True, run a single pipeline cycle and exit.
    """
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
    session_factory = get_session_factory(engine)
    logger.info("Database initialized")

    # Create shared HTTP client
    http_client = httpx.AsyncClient(timeout=30.0)

    # Create API clients
    firms_client = FIRMSClient(
        map_key=settings.firms_map_key,
        client=http_client,
    )
    weather_client = WeatherClient(client=http_client)
    roads_client = RoadsClient(client=http_client)

    # Create intent classifier
    classifier = IntentClassifier(config=yaml_config.intent_scoring)

    # Create alert dispatcher (None if no alert channels configured)
    dispatcher = _create_dispatcher(settings)

    # Create pipeline orchestrator
    pipeline = Pipeline(
        firms_client=firms_client,
        weather_client=weather_client,
        roads_client=roads_client,
        classifier=classifier,
        dispatcher=dispatcher,
        session_factory=session_factory,
        yaml_config=yaml_config,
    )

    if once:
        # Run a single cycle and exit
        record = await run_once(pipeline)
        logger.info(
            "Single cycle complete: status=%s duration=%dms",
            record.status.value,
            record.duration_ms or 0,
        )
    else:
        # Start the scheduler and run forever
        interval = yaml_config.monitoring.poll_interval_minutes
        scheduler = create_scheduler(pipeline, interval)

        # Set up graceful shutdown on SIGINT/SIGTERM
        shutdown_event = asyncio.Event()

        def _signal_handler(sig: int, frame: Any) -> None:
            logger.info("Received signal %s, initiating shutdown...", sig)
            shutdown_event.set()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        scheduler.start()
        logger.info("Scheduler started, running pipeline every %d minutes", interval)

        # Run first cycle immediately
        logger.info("Running initial pipeline cycle...")
        await pipeline.run_cycle()

        # Wait for shutdown signal
        await shutdown_event.wait()

        logger.info("Shutting down scheduler...")
        scheduler.shutdown(wait=True)

    # Cleanup
    await http_client.aclose()
    await engine.dispose()
    logger.info("Shutdown complete")


def _create_dispatcher(settings: Any) -> Any:
    """Create an alert dispatcher if any alert channels are configured.

    Returns None if no Telegram token or Twilio credentials are configured.

    Args:
        settings: Application settings with alert channel credentials.

    Returns:
        An AlertDispatcher instance, or None if no channels are configured.
    """
    has_telegram = bool(settings.telegram_bot_token)
    has_whatsapp = bool(
        settings.twilio_account_sid
        and settings.twilio_auth_token
        and settings.twilio_whatsapp_from
    )

    if not has_telegram and not has_whatsapp:
        logger.info("No alert channels configured, dispatcher disabled")
        return None

    # Alert dispatcher will be implemented in a future phase
    # For now, return None to skip the alert stage
    logger.info(
        "Alert channels detected (telegram=%s, whatsapp=%s) but dispatcher not yet implemented",
        has_telegram,
        has_whatsapp,
    )
    return None


def main() -> None:
    """Synchronous entry point."""
    args = parse_args()
    asyncio.run(async_main(once=args.once))


if __name__ == "__main__":
    main()
