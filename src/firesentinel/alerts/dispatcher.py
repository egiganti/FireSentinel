"""Alert dispatcher: routes fire events to matching subscribers.

Queries active subscriptions, filters by zone and severity, enforces
rate limits, and dispatches formatted alerts via Telegram or WhatsApp.

Imports from ``core/types``, ``alerts/templates``, and ``config`` only.
Uses an inline haversine implementation to avoid importing from ``ingestion/``.
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from firesentinel.alerts.templates import (
    format_escalation_alert,
    format_telegram_alert,
    format_whatsapp_alert,
)
from firesentinel.config import get_yaml_config
from firesentinel.core.types import (
    AlertChannel,
    AlertRecord,
    FireEvent,
    Severity,
)
from firesentinel.db.models import AlertSent, AlertSubscription

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from firesentinel.alerts.telegram import TelegramAlertClient
    from firesentinel.alerts.whatsapp import WhatsAppAlertClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Severity ordering for comparisons
# ---------------------------------------------------------------------------

_SEVERITY_ORDER: dict[str, int] = {
    Severity.LOW.value: 0,
    Severity.MEDIUM.value: 1,
    Severity.HIGH.value: 2,
    Severity.CRITICAL.value: 3,
}


def severity_gte(a: str, b: str) -> bool:
    """Return ``True`` if severity *a* is >= severity *b*."""
    return _SEVERITY_ORDER.get(a, 0) >= _SEVERITY_ORDER.get(b, 0)


def severity_gt(a: str, b: str) -> bool:
    """Return ``True`` if severity *a* is strictly > severity *b*."""
    return _SEVERITY_ORDER.get(a, 0) > _SEVERITY_ORDER.get(b, 0)


# ---------------------------------------------------------------------------
# Haversine (inline to avoid importing from ingestion/)
# ---------------------------------------------------------------------------

_EARTH_RADIUS_M = 6_371_000.0


def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance in meters between two WGS84 points."""
    lat1_r = math.radians(lat1)
    lat2_r = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_M * c


# ---------------------------------------------------------------------------
# Intent threshold boundaries
# ---------------------------------------------------------------------------

_INTENT_THRESHOLDS = [25, 50, 75]


def _intent_boundary_crossed(old_score: int, new_score: int) -> bool:
    """Return ``True`` if the score crossed a classification boundary."""
    return any(old_score <= threshold < new_score for threshold in _INTENT_THRESHOLDS)


# ---------------------------------------------------------------------------
# AlertDispatcher
# ---------------------------------------------------------------------------


class AlertDispatcher:
    """Routes fire events to matching subscribers and dispatches alerts.

    Either or both channel clients may be ``None`` for graceful degradation.
    """

    def __init__(
        self,
        telegram: TelegramAlertClient | None,
        whatsapp: WhatsAppAlertClient | None,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._telegram = telegram
        self._whatsapp = whatsapp
        self._session_factory = session_factory

    # -- public API ----------------------------------------------------------

    async def dispatch_alerts(self, events: list[FireEvent]) -> list[AlertRecord]:
        """Dispatch alerts for a batch of fire events.

        For each event the method:
        1. Matches active subscriptions (zone + severity filter).
        2. Checks per-event rate limits.
        3. Checks for escalation (severity increase or intent boundary).
        4. Formats and sends the alert.
        5. Records the alert in the ``alerts_sent`` table.

        Returns:
            List of :class:`AlertRecord` instances for all dispatched alerts.
        """
        all_records: list[AlertRecord] = []

        for event in events:
            async with self._session_factory() as session:
                records = await self._dispatch_event(event, session)
                all_records.extend(records)

        return all_records

    # -- internals -----------------------------------------------------------

    async def _dispatch_event(self, event: FireEvent, session: AsyncSession) -> list[AlertRecord]:
        """Dispatch alerts for a single fire event."""
        records: list[AlertRecord] = []

        # Check for escalation context
        should_escalate, prev_severity, prev_intent = await self._should_escalate(event, session)

        subscriptions = await self._match_subscriptions(event, session)

        for sub in subscriptions:
            # Rate-limit check
            under_limit = await self._check_rate_limit(event.id, sub.id, session)
            if not under_limit:
                logger.info("Rate limit reached for event=%s sub=%s", event.id, sub.id)
                continue

            # Format message
            channel = AlertChannel(sub.channel)
            is_escalation = should_escalate

            if is_escalation and prev_severity is not None and prev_intent is not None:
                message = format_escalation_alert(event, prev_severity, prev_intent)
            elif channel == AlertChannel.WHATSAPP:
                message = format_whatsapp_alert(event)
            else:
                message = format_telegram_alert(event)

            # Send
            delivered = await self._send_alert(sub, message)

            # Record
            record = AlertRecord(
                id=str(uuid.uuid4()),
                fire_event_id=event.id,
                subscription_id=sub.id,
                channel=channel,
                message_content=message,
                sent_at=datetime.utcnow(),
                delivered=delivered,
                is_escalation=is_escalation,
                error=None if delivered else "send_failed",
            )
            records.append(record)

            # Persist to DB
            db_alert = AlertSent(
                id=record.id,
                fire_event_id=record.fire_event_id,
                subscription_id=record.subscription_id,
                channel=record.channel.value,
                message_content=record.message_content,
                sent_at=record.sent_at,
                delivered=record.delivered,
                is_escalation=record.is_escalation,
                error=record.error,
            )
            session.add(db_alert)

        await session.commit()
        return records

    async def _match_subscriptions(
        self, event: FireEvent, session: AsyncSession
    ) -> list[AlertSubscription]:
        """Find active subscriptions whose zone contains the event.

        Filters by:
        - Zone proximity (predefined zone center/radius or custom coords).
        - Minimum severity threshold.
        """
        stmt = select(AlertSubscription).where(AlertSubscription.is_active.is_(True))
        result = await session.execute(stmt)
        all_subs: list[AlertSubscription] = list(result.scalars().all())

        yaml_config = get_yaml_config()
        matched: list[AlertSubscription] = []

        for sub in all_subs:
            # -- zone check --
            if sub.zone == "custom":
                # Custom coordinates
                if (
                    sub.custom_lat is None
                    or sub.custom_lon is None
                    or sub.custom_radius_km is None
                ):
                    continue
                dist_m = _haversine_distance(
                    event.center_lat,
                    event.center_lon,
                    sub.custom_lat,
                    sub.custom_lon,
                )
                if dist_m > sub.custom_radius_km * 1000:
                    continue
            else:
                # Predefined zone from monitoring.yml
                zone_cfg = yaml_config.zones.get(sub.zone)
                if zone_cfg is None:
                    logger.warning("Unknown zone '%s' in subscription %s", sub.zone, sub.id)
                    continue
                zone_lat, zone_lon = zone_cfg.center
                dist_m = _haversine_distance(
                    event.center_lat,
                    event.center_lon,
                    zone_lat,
                    zone_lon,
                )
                if dist_m > zone_cfg.radius_km * 1000:
                    continue

            # -- severity check --
            if not severity_gte(event.severity.value, sub.min_severity):
                continue

            matched.append(sub)

        return matched

    async def _check_rate_limit(
        self,
        event_id: str,
        subscription_id: str,
        session: AsyncSession,
    ) -> bool:
        """Return ``True`` if the subscription is under the alert rate limit.

        Limit: max ``alerts.max_per_event_per_user`` alerts per event per
        subscription within ``alerts.cooldown_hours``.
        """
        yaml_config = get_yaml_config()
        max_alerts = yaml_config.alerts.max_per_event_per_user
        cooldown_hours = yaml_config.alerts.cooldown_hours

        cutoff = datetime.utcnow() - timedelta(hours=cooldown_hours)

        stmt = (
            select(func.count())
            .select_from(AlertSent)
            .where(
                AlertSent.fire_event_id == event_id,
                AlertSent.subscription_id == subscription_id,
                AlertSent.sent_at >= cutoff,
            )
        )
        result = await session.execute(stmt)
        count: int = result.scalar_one()

        return count < max_alerts

    async def _should_escalate(
        self, event: FireEvent, session: AsyncSession
    ) -> tuple[bool, str | None, int | None]:
        """Determine if an event warrants an escalation alert.

        Returns:
            A 3-tuple of (should_escalate, previous_severity_value,
            previous_intent_score). The latter two are ``None`` when
            there is no prior alert to compare against.
        """
        # Find the most recent alert for this event
        stmt = (
            select(AlertSent)
            .where(AlertSent.fire_event_id == event.id)
            .order_by(AlertSent.sent_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        prev_alert: AlertSent | None = result.scalar_one_or_none()

        if prev_alert is None:
            return (False, None, None)

        # Look up the fire event row for previous severity/intent
        from firesentinel.db.models import FireEvent as FireEventModel

        fe_stmt = select(FireEventModel).where(FireEventModel.id == event.id)
        fe_result = await session.execute(fe_stmt)
        fe_row: FireEventModel | None = fe_result.scalar_one_or_none()

        if fe_row is None:
            return (False, None, None)

        prev_severity = fe_row.severity
        prev_intent = fe_row.intent_score if fe_row.intent_score is not None else 0

        current_intent = event.intent.total if event.intent is not None else 0

        # Check severity increase
        sev_increased = severity_gt(event.severity.value, prev_severity)

        # Check intent boundary crossing
        intent_crossed = _intent_boundary_crossed(prev_intent, current_intent)

        should = sev_increased or intent_crossed
        return (should, prev_severity, prev_intent)

    async def _send_alert(self, subscription: AlertSubscription, message: str) -> bool:
        """Route the message to the correct channel client.

        Returns ``False`` if the required client is not configured.
        """
        channel = AlertChannel(subscription.channel)

        if channel == AlertChannel.TELEGRAM:
            if self._telegram is None:
                logger.warning(
                    "Telegram client not configured; skipping sub=%s",
                    subscription.id,
                )
                return False
            return await self._telegram.send_message(subscription.channel_id, message)

        if channel == AlertChannel.WHATSAPP:
            if self._whatsapp is None:
                logger.warning(
                    "WhatsApp client not configured; skipping sub=%s",
                    subscription.id,
                )
                return False
            return await self._whatsapp.send_message(subscription.channel_id, message)

        logger.error("Unsupported channel '%s' for sub=%s", channel, subscription.id)
        return False
