"""APScheduler setup for running the pipeline on a configurable interval.

Provides factory functions for creating a scheduler and for running
a single pipeline cycle (useful for testing, manual runs, and the --once flag).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from apscheduler.schedulers.asyncio import AsyncIOScheduler

if TYPE_CHECKING:
    from firesentinel.core.pipeline import Pipeline
    from firesentinel.core.types import PipelineRunRecord

logger = logging.getLogger(__name__)


def create_scheduler(pipeline: Pipeline, interval_minutes: int) -> AsyncIOScheduler:
    """Create an APScheduler AsyncIOScheduler with the pipeline as an interval job.

    The scheduler is returned in a stopped state -- the caller must call
    ``scheduler.start()`` to begin execution.

    Args:
        pipeline: The pipeline orchestrator instance.
        interval_minutes: Polling interval in minutes (from monitoring.yml).

    Returns:
        Configured but not-yet-started AsyncIOScheduler.
    """
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        pipeline.run_cycle,
        trigger="interval",
        minutes=interval_minutes,
        id="firesentinel_pipeline",
        name="FireSentinel pipeline cycle",
        max_instances=1,
        coalesce=True,
    )

    logger.info(
        "Scheduler created: pipeline will run every %d minutes",
        interval_minutes,
    )

    return scheduler


async def run_once(pipeline: Pipeline) -> PipelineRunRecord:
    """Run a single pipeline cycle and return the result.

    Convenience wrapper for manual runs and the ``--once`` CLI flag.

    Args:
        pipeline: The pipeline orchestrator instance.

    Returns:
        PipelineRunRecord with timing, counts, and error details.
    """
    logger.info("Running single pipeline cycle")
    return await pipeline.run_cycle()
