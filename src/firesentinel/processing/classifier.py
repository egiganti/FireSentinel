"""Intentionality scoring engine for fire events.

Scores each fire event 0-100 for probability of intentional origin by
combining six independent evidence signals. All weights and thresholds
are loaded from config/monitoring.yml -- no hardcoded magic numbers.

When a data source is unavailable (weather API down, road API timeout),
the affected signal is excluded and remaining weights are renormalized
to sum to 100. A fire scored on 4/6 signals with renormalized weights
is more useful than one artificially deflated because an API was down.
"""

from __future__ import annotations

import logging
from datetime import time, timezone, timedelta

from firesentinel.config import IntentScoringConfig
from firesentinel.core.types import (
    EnrichedHotspot,
    FireEvent,
    IntentBreakdown,
    RoadContext,
    WeatherContext,
)

logger = logging.getLogger(__name__)

# Argentina Standard Time is UTC-3
_ART_OFFSET = timezone(timedelta(hours=-3))


class IntentClassifier:
    """Rule-based intentionality classifier for fire events.

    Combines six evidence signals, each with configurable weights,
    to produce a 0-100 intentionality score. Handles graceful degradation
    when data sources are unavailable.

    All weights, thresholds, and distance tiers come from the
    IntentScoringConfig (loaded from config/monitoring.yml).
    """

    def __init__(self, config: IntentScoringConfig) -> None:
        self._weights = config.weights
        self._thresholds = config.thresholds
        self._road_distance = config.road_distance_m
        self._night_hours = config.night_hours_local

    def classify(
        self,
        event: FireEvent,
        history_count: int = 0,
        months_since_last: int | None = None,
        nearby_event_count: int = 0,
    ) -> IntentBreakdown:
        """Score a fire event for intentionality across all six signals.

        Args:
            event: The fire event to classify (must have hotspots).
            history_count: Number of historical fires in the same 1km grid cell.
            months_since_last: Months since the most recent prior fire, or None.
            nearby_event_count: Number of other fire events within proximity
                thresholds (5km/2h or 10km/2h).

        Returns:
            IntentBreakdown with individual signal scores, total, label,
            and active signal count.
        """
        # Extract weather and road context from the first enriched hotspot
        weather = self._get_weather(event)
        road = self._get_road(event)

        # Score each signal independently
        lightning_score, lightning_avail = self._score_lightning(weather)
        road_score, road_avail = self._score_road_proximity(road)
        night_score, night_avail = self._score_nighttime(event)
        history_score, history_avail = self._score_historical(
            event, history_count, months_since_last
        )
        multi_score, multi_avail = self._score_multi_point(nearby_event_count)
        dry_score, dry_avail = self._score_dry_conditions(weather)

        # Build signal tuples: (raw_score, max_weight, is_available)
        signals = [
            (lightning_score, self._weights.lightning_absence, lightning_avail),
            (road_score, self._weights.road_proximity, road_avail),
            (night_score, self._weights.nighttime_ignition, night_avail),
            (history_score, self._weights.historical_repeat, history_avail),
            (multi_score, self._weights.multi_point_ignition, multi_avail),
            (dry_score, self._weights.dry_conditions, dry_avail),
        ]

        breakdown = self._renormalize(signals)

        logger.info(
            "Intent classification: event=%s total=%d label=%s active=%d/%d",
            event.id,
            breakdown.total,
            breakdown.label.value,
            breakdown.active_signals,
            breakdown.total_signals,
        )

        return breakdown

    # ------------------------------------------------------------------
    # Signal scorers
    # ------------------------------------------------------------------

    def _score_lightning(self, weather: WeatherContext | None) -> tuple[int, bool]:
        """Score based on absence of natural lightning/thunderstorm activity.

        Args:
            weather: Weather context, or None if unavailable.

        Returns:
            Tuple of (score, is_available). Score is 0 when natural ignition
            is likely (thunderstorm/high CAPE), full weight when absent.
        """
        if weather is None:
            return (0, False)

        weight = self._weights.lightning_absence

        if weather.has_thunderstorm:
            return (0, True)

        if weather.cape >= 1000:
            return (0, True)

        if weather.cape >= 500:
            return (round(weight * 0.6), True)

        return (weight, True)

    def _score_road_proximity(self, road: RoadContext | None) -> tuple[int, bool]:
        """Score based on proximity to the nearest road.

        Closer roads indicate easier human access, increasing suspicion.

        Args:
            road: Road context, or None if unavailable.

        Returns:
            Tuple of (score, is_available).
        """
        if road is None:
            return (0, False)

        weight = self._weights.road_proximity
        dist = road.nearest_distance_m

        if dist < self._road_distance.very_close:
            return (weight, True)

        if dist < self._road_distance.close:
            return (round(weight * 0.75), True)

        if dist < self._road_distance.near:
            return (round(weight * 0.50), True)

        if dist < self._road_distance.moderate:
            return (round(weight * 0.25), True)

        return (0, True)

    def _score_nighttime(self, event: FireEvent) -> tuple[int, bool]:
        """Score based on whether the fire was detected during nighttime hours.

        Nighttime ignitions are more suspicious because legitimate agricultural
        burns do not occur at night.

        Args:
            event: Fire event with at least one enriched hotspot.

        Returns:
            Tuple of (score, is_available). Always available since time
            comes from satellite data.
        """
        weight = self._weights.nighttime_ignition

        # Use the earliest hotspot's acquisition time
        local_hour = self._get_local_hour(event)

        peak_start = self._night_hours.peak[0]
        peak_end = self._night_hours.peak[1]
        shoulder_start = self._night_hours.shoulder[0]
        shoulder_end = self._night_hours.shoulder[1]
        shoulder_eve_start = self._night_hours.shoulder_evening[0]
        shoulder_eve_end = self._night_hours.shoulder_evening[1]

        # Peak: 22:00-05:00 (wraps midnight)
        if self._in_hour_range(local_hour, peak_start, peak_end):
            return (weight, True)

        # Shoulder morning: 05:00-07:00
        if self._in_hour_range(local_hour, shoulder_start, shoulder_end):
            return (round(weight * 0.5), True)

        # Shoulder evening: 20:00-22:00
        if self._in_hour_range(local_hour, shoulder_eve_start, shoulder_eve_end):
            return (round(weight * 0.5), True)

        # Daytime
        return (0, True)

    def _score_historical(
        self,
        event: FireEvent,
        history_count: int,
        months_since_last: int | None,
    ) -> tuple[int, bool]:
        """Score based on historical fire recurrence at the same location.

        Repeated fires in the same area can indicate land-clearing operations,
        disputes, or revenge arson.

        Args:
            event: Fire event (used for logging context).
            history_count: Number of prior fires in the same 1km grid cell.
            months_since_last: Months since the most recent prior fire,
                or None if no history.

        Returns:
            Tuple of (score, is_available). Always available.
        """
        weight = self._weights.historical_repeat

        if history_count == 0 or months_since_last is None:
            return (0, True)

        if months_since_last < 12:
            return (weight, True)

        if months_since_last < 24:
            return (round(weight * 0.67), True)

        if months_since_last < 36:
            return (round(weight * 0.33), True)

        return (0, True)

    def _score_multi_point(self, nearby_event_count: int) -> tuple[int, bool]:
        """Score based on multiple simultaneous fire ignition points.

        Multiple fires starting close together in time and space is a strong
        arson indicator (e.g., the El Bolson fire had 3 separate ignitions).

        Args:
            nearby_event_count: Number of other fire events within proximity
                thresholds. 2+ within 5km/2h = full score, 1 within 10km/2h
                = half score.

        Returns:
            Tuple of (score, is_available). Always available.
        """
        weight = self._weights.multi_point_ignition

        if nearby_event_count >= 2:
            return (weight, True)

        if nearby_event_count == 1:
            return (round(weight * 0.5), True)

        return (0, True)

    def _score_dry_conditions(self, weather: WeatherContext | None) -> tuple[int, bool]:
        """Score based on extreme dryness and absence of precipitation.

        Arsonists deliberately choose dry conditions for maximum fire spread.
        This is a force-multiplier signal -- it does not indicate intent alone.

        Args:
            weather: Weather context, or None if unavailable.

        Returns:
            Tuple of (score, is_available).
        """
        if weather is None:
            return (0, False)

        weight = self._weights.dry_conditions

        if weather.humidity_pct < 25 and weather.precipitation_mm_72h == 0:
            return (weight, True)

        if weather.humidity_pct < 35 and weather.precipitation_mm_72h < 2:
            return (round(weight * 0.5), True)

        return (0, True)

    # ------------------------------------------------------------------
    # Renormalization
    # ------------------------------------------------------------------

    def _renormalize(
        self,
        signals: list[tuple[int, int, bool]],
    ) -> IntentBreakdown:
        """Compute the final IntentBreakdown with weight renormalization.

        When some signals are unavailable, the remaining signals are
        renormalized so their max possible sum is 100. This prevents
        artificial score deflation when a data source is down.

        Args:
            signals: List of (raw_score, max_weight, is_available) tuples
                in order: lightning, road, night, history, multi_point, dry.

        Returns:
            IntentBreakdown with individual and total scores.
        """
        total_signals = len(signals)
        active_signals = sum(1 for _, _, avail in signals if avail)

        # Extract raw scores (keep 0 for unavailable signals in breakdown)
        raw_scores = [score for score, _, _ in signals]

        # Calculate available weight sum for renormalization
        available_max = sum(
            max_w for _, max_w, avail in signals if avail
        )

        if available_max == 0:
            # All signals unavailable -- return zero scores
            logger.warning("All intent signals unavailable, returning score 0")
            return IntentBreakdown(
                lightning_score=0,
                road_score=0,
                night_score=0,
                history_score=0,
                multi_point_score=0,
                dry_conditions_score=0,
                active_signals=0,
                total_signals=total_signals,
            )

        # Sum of raw scores from available signals only
        available_raw_sum = sum(
            score for score, _, avail in signals if avail
        )

        if available_max == 100:
            # All signals available -- no renormalization needed
            return IntentBreakdown(
                lightning_score=raw_scores[0],
                road_score=raw_scores[1],
                night_score=raw_scores[2],
                history_score=raw_scores[3],
                multi_point_score=raw_scores[4],
                dry_conditions_score=raw_scores[5],
                active_signals=active_signals,
                total_signals=total_signals,
            )

        # Renormalize: scale each available signal's score proportionally
        # so that available weights map to 100
        scale = 100.0 / available_max
        renormalized = []
        for score, max_w, avail in signals:
            if avail:
                renormalized.append(round(score * scale))
            else:
                renormalized.append(0)

        # Cap total at 100 (rounding can push slightly over)
        total = sum(renormalized)
        if total > 100:
            # Find the largest score and reduce it by the excess
            max_idx = renormalized.index(max(renormalized))
            renormalized[max_idx] -= total - 100

        return IntentBreakdown(
            lightning_score=renormalized[0],
            road_score=renormalized[1],
            night_score=renormalized[2],
            history_score=renormalized[3],
            multi_point_score=renormalized[4],
            dry_conditions_score=renormalized[5],
            active_signals=active_signals,
            total_signals=total_signals,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_weather(event: FireEvent) -> WeatherContext | None:
        """Extract weather context from the first enriched hotspot.

        Args:
            event: Fire event with enriched hotspots.

        Returns:
            WeatherContext or None if no hotspots or weather unavailable.
        """
        if not event.hotspots:
            return None
        return event.hotspots[0].weather

    @staticmethod
    def _get_road(event: FireEvent) -> RoadContext | None:
        """Extract road context from the first enriched hotspot.

        Args:
            event: Fire event with enriched hotspots.

        Returns:
            RoadContext or None if no hotspots or road data unavailable.
        """
        if not event.hotspots:
            return None
        return event.hotspots[0].road

    @staticmethod
    def _get_local_hour(event: FireEvent) -> int:
        """Get the local Argentina hour (UTC-3) for the earliest hotspot.

        Args:
            event: Fire event with enriched hotspots.

        Returns:
            Hour (0-23) in Argentina local time.
        """
        if not event.hotspots:
            return 12  # Default to noon (daytime) if no hotspots

        # Find the earliest acquisition time across all hotspots
        earliest_time: time | None = None
        for enriched in event.hotspots:
            acq_time = enriched.hotspot.acq_time
            if earliest_time is None or acq_time < earliest_time:
                earliest_time = acq_time

        if earliest_time is None:
            return 12

        # acq_time from FIRMS is in UTC; convert to Argentina local (UTC-3)
        utc_hour = earliest_time.hour
        local_hour = (utc_hour - 3) % 24
        return local_hour

    @staticmethod
    def _in_hour_range(hour: int, start: int, end: int) -> bool:
        """Check if an hour falls within a range (handles midnight wrap).

        Args:
            hour: Hour to check (0-23).
            start: Range start hour (inclusive).
            end: Range end hour (exclusive).

        Returns:
            True if the hour is within the range.
        """
        if start <= end:
            # Simple range (e.g., 05-07)
            return start <= hour < end
        # Wraps midnight (e.g., 22-05 means 22,23,0,1,2,3,4)
        return hour >= start or hour < end
