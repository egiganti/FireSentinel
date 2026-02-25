"""Pipeline orchestrator for the FireSentinel detection cycle.

Runs the full detection cycle: ingest -> dedup -> enrich -> cluster ->
classify -> alert. Each stage is independently fault-tolerant. Individual
stage failures are logged and recorded but do not crash the pipeline.

This is the ONLY module that imports from all layers (ingestion, processing,
alerts). All other modules import exclusively from core/types.py.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import update

from firesentinel.core.types import (
    EnrichedHotspot,
    FireEvent,
    PipelineRunRecord,
    PipelineStatus,
    RawHotspot,
)
from firesentinel.db.models import FireEvent as FireEventModel
from firesentinel.db.models import PipelineRun
from firesentinel.processing.clustering import cluster_hotspots
from firesentinel.processing.dedup import deduplicate, store_hotspots

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from firesentinel.config import YAMLConfig
    from firesentinel.ingestion.firms import FIRMSClient
    from firesentinel.ingestion.roads import RoadsClient
    from firesentinel.ingestion.weather import WeatherClient
    from firesentinel.processing.classifier import IntentClassifier

logger = logging.getLogger(__name__)

# Maximum concurrent enrichment calls to avoid API flooding
_ENRICHMENT_CONCURRENCY = 10


class AlertDispatcher(Protocol):
    """Protocol for alert dispatching (duck-typed).

    Allows the pipeline to dispatch alerts without depending on a concrete
    implementation. The dispatcher is optional -- when None, the alert
    stage is skipped.
    """

    async def dispatch(self, events: list[FireEvent]) -> dict[str, int]:
        """Dispatch alerts for fire events.

        Args:
            events: Fire events to alert on.

        Returns:
            Dictionary with counts per channel (e.g. {"telegram": 3, "whatsapp": 1}).
        """
        ...  # pragma: no cover


class Pipeline:
    """Full detection cycle orchestrator.

    Coordinates all pipeline stages with dependency injection for testability.
    Each stage is wrapped in error handling so that individual failures are
    recorded but do not crash the system.
    """

    def __init__(
        self,
        firms_client: FIRMSClient,
        weather_client: WeatherClient,
        roads_client: RoadsClient,
        classifier: IntentClassifier,
        dispatcher: AlertDispatcher | None,
        session_factory: async_sessionmaker[AsyncSession],
        yaml_config: YAMLConfig,
    ) -> None:
        self._firms = firms_client
        self._weather = weather_client
        self._roads = roads_client
        self._classifier = classifier
        self._dispatcher = dispatcher
        self._session_factory = session_factory
        self._yaml_config = yaml_config

    async def run_cycle(self) -> PipelineRunRecord:
        """Run one complete detection cycle through all stages.

        Returns:
            PipelineRunRecord with timing, counts, and error details.
        """
        run_id = str(uuid.uuid4())
        start_time = datetime.utcnow()
        start_mono = time.monotonic()

        record = PipelineRunRecord(
            id=run_id,
            started_at=start_time,
        )

        errors: list[str] = []
        stage_failures: set[str] = set()

        # Track data flowing through the pipeline
        raw_hotspots: list[RawHotspot] = []
        new_hotspots: list[RawHotspot] = []
        enriched: list[EnrichedHotspot] = []
        events: list[FireEvent] = []

        # -----------------------------------------------------------------
        # Stage 1: INGEST
        # -----------------------------------------------------------------
        try:
            bbox = self._yaml_config.monitoring.bbox.full_patagonia
            raw_hotspots = await self._firms.fetch_all_sources(bbox=bbox)
            record.hotspots_fetched = len(raw_hotspots)
            logger.info(
                "Stage 1 INGEST: Fetched %d hotspots from FIRMS sources",
                len(raw_hotspots),
            )
        except Exception:
            error_msg = f"Stage 1 INGEST failed: {traceback.format_exc()}"
            logger.error(error_msg)
            errors.append(error_msg)
            stage_failures.add("ingest")

        # If ingest failed completely, record and return early
        if "ingest" in stage_failures:
            record.status = PipelineStatus.FAILED
            record.errors = errors
            record.completed_at = datetime.utcnow()
            record.duration_ms = int((time.monotonic() - start_mono) * 1000)
            await self._save_pipeline_run(record)
            return record

        # -----------------------------------------------------------------
        # Stage 2: DEDUPLICATE
        # -----------------------------------------------------------------
        try:
            async with self._session_factory() as session:
                new_hotspots = await deduplicate(raw_hotspots, session)
                record.new_hotspots = len(new_hotspots)

                dupes = len(raw_hotspots) - len(new_hotspots)
                logger.info(
                    "Stage 2 DEDUP: %d new hotspots, %d duplicates",
                    len(new_hotspots),
                    dupes,
                )

                if new_hotspots:
                    await store_hotspots(new_hotspots, session)
                    await session.commit()
        except Exception:
            error_msg = f"Stage 2 DEDUP failed: {traceback.format_exc()}"
            logger.error(error_msg)
            errors.append(error_msg)
            stage_failures.add("dedup")

        # If dedup failed or no new hotspots, finish up
        if "dedup" in stage_failures or len(new_hotspots) == 0:
            if len(new_hotspots) == 0 and "dedup" not in stage_failures:
                logger.info("No new hotspots after dedup, pipeline cycle complete")
                record.status = PipelineStatus.SUCCESS
            elif "dedup" in stage_failures:
                record.status = PipelineStatus.FAILED
            record.errors = errors if errors else []
            record.completed_at = datetime.utcnow()
            record.duration_ms = int((time.monotonic() - start_mono) * 1000)
            await self._save_pipeline_run(record)
            return record

        # -----------------------------------------------------------------
        # Stage 3: ENRICH (parallel per hotspot)
        # -----------------------------------------------------------------
        try:
            enriched = await self._enrich_batch(new_hotspots)

            weather_ok = sum(1 for e in enriched if e.weather is not None)
            road_ok = sum(1 for e in enriched if e.road is not None)
            logger.info(
                "Stage 3 ENRICH: Enriched %d hotspots (%d weather, %d road)",
                len(enriched),
                weather_ok,
                road_ok,
            )

            # Check if enrichment partially failed
            if weather_ok < len(enriched) or road_ok < len(enriched):
                partial_msg = (
                    f"Stage 3 ENRICH partial: {len(enriched) - weather_ok} weather "
                    f"failures, {len(enriched) - road_ok} road failures"
                )
                logger.warning(partial_msg)
                errors.append(partial_msg)
                stage_failures.add("enrich_partial")
        except Exception:
            error_msg = f"Stage 3 ENRICH failed: {traceback.format_exc()}"
            logger.error(error_msg)
            errors.append(error_msg)
            stage_failures.add("enrich")
            # Create unenriched hotspots so clustering can still proceed
            enriched = [EnrichedHotspot(hotspot=hs) for hs in new_hotspots]

        # -----------------------------------------------------------------
        # Stage 4: CLUSTER
        # -----------------------------------------------------------------
        try:
            async with self._session_factory() as session:
                events = await cluster_hotspots(enriched, session)
                await session.commit()

            # Count new vs updated events (events with hotspots from this cycle)
            new_events = sum(
                1
                for e in events
                if len(e.hotspots) == len([h for h in e.hotspots if h in enriched])
            )
            updated_events = len(events) - new_events

            record.events_created = new_events
            record.events_updated = updated_events
            logger.info(
                "Stage 4 CLUSTER: Clustered into %d fire events (%d new, %d updated)",
                len(events),
                new_events,
                updated_events,
            )
        except Exception:
            error_msg = f"Stage 4 CLUSTER failed: {traceback.format_exc()}"
            logger.error(error_msg)
            errors.append(error_msg)
            stage_failures.add("cluster")

        # -----------------------------------------------------------------
        # Stage 5: CLASSIFY
        # -----------------------------------------------------------------
        if events and "cluster" not in stage_failures:
            try:
                label_counts: dict[str, int] = {
                    "natural": 0,
                    "uncertain": 0,
                    "suspicious": 0,
                    "likely_intentional": 0,
                }

                for event in events:
                    breakdown = self._classifier.classify(event)
                    event.intent = breakdown
                    label_counts[breakdown.label.value] += 1

                # Persist intent scores to DB
                async with self._session_factory() as session:
                    for event in events:
                        if event.intent is not None:
                            stmt = (
                                update(FireEventModel)
                                .where(FireEventModel.id == event.id)
                                .values(
                                    intent_score=event.intent.total,
                                    intent_label=event.intent.label.value,
                                    intent_breakdown=event.intent.to_dict(),
                                )
                            )
                            await session.execute(stmt)
                    await session.commit()

                logger.info(
                    "Stage 5 CLASSIFY: Classified %d events: "
                    "%d natural, %d uncertain, %d suspicious, %d likely intentional",
                    len(events),
                    label_counts["natural"],
                    label_counts["uncertain"],
                    label_counts["suspicious"],
                    label_counts["likely_intentional"],
                )
            except Exception:
                error_msg = f"Stage 5 CLASSIFY failed: {traceback.format_exc()}"
                logger.error(error_msg)
                errors.append(error_msg)
                stage_failures.add("classify")

        # -----------------------------------------------------------------
        # Stage 6: ALERT
        # -----------------------------------------------------------------
        if self._dispatcher is not None and events and "cluster" not in stage_failures:
            try:
                channel_counts = await self._dispatcher.dispatch(events)
                total_alerts = sum(channel_counts.values())
                record.alerts_sent = total_alerts

                channel_summary = ", ".join(
                    f"{count} {channel}" for channel, count in channel_counts.items()
                )
                logger.info(
                    "Stage 6 ALERT: Dispatched %d alerts (%s)",
                    total_alerts,
                    channel_summary or "none",
                )
            except Exception:
                error_msg = f"Stage 6 ALERT failed: {traceback.format_exc()}"
                logger.error(error_msg)
                errors.append(error_msg)
                stage_failures.add("alert")
        elif self._dispatcher is None:
            logger.info("Stage 6 ALERT: Skipped (no dispatcher configured)")

        # -----------------------------------------------------------------
        # Finalize
        # -----------------------------------------------------------------
        record.errors = errors

        # Determine final status
        critical_stages = {"ingest", "dedup"}
        if stage_failures & critical_stages:
            record.status = PipelineStatus.FAILED
        elif stage_failures:
            record.status = PipelineStatus.PARTIAL
        else:
            record.status = PipelineStatus.SUCCESS

        record.completed_at = datetime.utcnow()
        record.duration_ms = int((time.monotonic() - start_mono) * 1000)

        await self._save_pipeline_run(record)

        logger.info(
            "Pipeline cycle %s completed: status=%s duration=%dms "
            "hotspots=%d new=%d events=%d alerts=%d",
            run_id,
            record.status.value,
            record.duration_ms,
            record.hotspots_fetched,
            record.new_hotspots,
            record.events_created + record.events_updated,
            record.alerts_sent,
        )

        return record

    async def _enrich_hotspot(self, hotspot: RawHotspot) -> EnrichedHotspot:
        """Enrich a single hotspot with weather and road context in parallel.

        Both enrichment calls are made concurrently. If either fails,
        the result is None for that context (graceful degradation).

        Args:
            hotspot: Raw hotspot detection to enrich.

        Returns:
            EnrichedHotspot with whatever data was successfully retrieved.
        """
        detection_time = datetime.combine(hotspot.acq_date, hotspot.acq_time)

        weather_result, road_result = await asyncio.gather(
            self._weather.get_weather_context(
                latitude=hotspot.latitude,
                longitude=hotspot.longitude,
                detection_time=detection_time,
            ),
            self._roads.get_road_context(
                latitude=hotspot.latitude,
                longitude=hotspot.longitude,
            ),
            return_exceptions=True,
        )

        weather = weather_result if not isinstance(weather_result, BaseException) else None
        road = road_result if not isinstance(road_result, BaseException) else None

        if isinstance(weather_result, BaseException):
            logger.warning(
                "Weather enrichment failed for (%.4f, %.4f): %s",
                hotspot.latitude,
                hotspot.longitude,
                weather_result,
            )
        if isinstance(road_result, BaseException):
            logger.warning(
                "Road enrichment failed for (%.4f, %.4f): %s",
                hotspot.latitude,
                hotspot.longitude,
                road_result,
            )

        return EnrichedHotspot(hotspot=hotspot, weather=weather, road=road)

    async def _enrich_batch(self, hotspots: list[RawHotspot]) -> list[EnrichedHotspot]:
        """Enrich all hotspots with concurrency limiting.

        Uses an asyncio.Semaphore to cap concurrent API calls at
        _ENRICHMENT_CONCURRENCY to avoid flooding external services.

        Args:
            hotspots: Raw hotspot detections to enrich.

        Returns:
            List of enriched hotspots in the same order as input.
        """
        semaphore = asyncio.Semaphore(_ENRICHMENT_CONCURRENCY)

        async def _limited_enrich(hs: RawHotspot) -> EnrichedHotspot:
            async with semaphore:
                return await self._enrich_hotspot(hs)

        return await asyncio.gather(*[_limited_enrich(hs) for hs in hotspots])

    async def _save_pipeline_run(self, record: PipelineRunRecord) -> None:
        """Persist a pipeline run record to the database.

        Args:
            record: The pipeline run record to store.
        """
        try:
            async with self._session_factory() as session:
                db_record = PipelineRun(
                    id=record.id,
                    started_at=record.started_at,
                    completed_at=record.completed_at,
                    status=record.status.value,
                    hotspots_fetched=record.hotspots_fetched,
                    new_hotspots=record.new_hotspots,
                    events_created=record.events_created,
                    events_updated=record.events_updated,
                    alerts_sent=record.alerts_sent,
                    errors=record.errors if record.errors else None,
                    duration_ms=record.duration_ms,
                )
                session.add(db_record)
                await session.commit()
        except Exception:
            logger.exception("Failed to save pipeline run record %s", record.id)
