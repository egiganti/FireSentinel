"""Tests for Telegram client, WhatsApp client, and alert dispatcher.

Uses ``respx`` to mock HTTP calls to Telegram Bot API and Twilio REST API.
Dispatcher tests use a temporary database via the ``db_factory`` fixture.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from firesentinel.alerts.dispatcher import (
    AlertDispatcher,
    severity_gt,
    severity_gte,
)
from firesentinel.alerts.telegram import TelegramAlertClient
from firesentinel.alerts.whatsapp import WhatsAppAlertClient
from firesentinel.core.types import (
    AlertChannel,
    Confidence,
    DayNight,
    EnrichedHotspot,
    FireEvent,
    IntentBreakdown,
    RawHotspot,
    RoadContext,
    Severity,
    Source,
    WeatherContext,
)
from firesentinel.db.engine import get_engine, get_session_factory, init_db
from firesentinel.db.models import AlertSent, AlertSubscription

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_factory(tmp_path: Path) -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Create a temporary DB and yield a session factory (not a single session)."""
    db_path = tmp_path / "test_alerts.db"
    engine = get_engine(str(db_path))
    await init_db(engine)
    factory = get_session_factory(engine)
    yield factory
    await engine.dispose()


def _make_fire_event(
    *,
    event_id: str | None = None,
    lat: float = -42.22,
    lon: float = -71.43,
    severity: Severity = Severity.MEDIUM,
    intent_total: int = 70,
) -> FireEvent:
    """Create a FireEvent with controllable parameters for testing."""
    hotspot = RawHotspot(
        source=Source.VIIRS_SNPP_NRT,
        latitude=lat,
        longitude=lon,
        brightness=345.6,
        brightness_2=298.1,
        frp=28.5,
        confidence=Confidence.HIGH.value,
        acq_date=date(2026, 2, 15),
        acq_time=time(3, 30),
        satellite="N",
        daynight=DayNight.NIGHT,
    )
    weather = WeatherContext(
        cape=150.0,
        convective_inhibition=25.0,
        weather_code=0,
        temperature_c=28.5,
        wind_speed_kmh=15.0,
        humidity_pct=22.0,
        precipitation_mm_6h=0.0,
        precipitation_mm_72h=0.0,
        has_thunderstorm=False,
    )
    road = RoadContext(
        nearest_distance_m=500.0,
        nearest_road_type="track",
        nearest_road_ref=None,
    )
    enriched = EnrichedHotspot(hotspot=hotspot, weather=weather, road=road)

    # Distribute the intent_total across signals
    intent = IntentBreakdown(
        lightning_score=min(intent_total, 25),
        road_score=min(max(intent_total - 25, 0), 20),
        night_score=min(max(intent_total - 45, 0), 20),
        history_score=min(max(intent_total - 65, 0), 15),
        multi_point_score=0,
        dry_conditions_score=0,
        active_signals=6,
        total_signals=6,
    )

    return FireEvent(
        id=event_id or str(uuid.uuid4()),
        center_lat=lat,
        center_lon=lon,
        hotspots=[enriched],
        severity=severity,
        max_frp=28.5,
        first_detected=datetime(2026, 2, 15, 3, 30),
        last_updated=datetime(2026, 2, 15, 3, 45),
        province="Chubut",
        nearest_town="Epuyen",
        nearest_road_m=500.0,
        nearest_road_type="track",
        nearest_road_ref=None,
        intent=intent,
        is_active=True,
    )


async def _create_subscription(
    session: AsyncSession,
    *,
    channel: str = "telegram",
    channel_id: str = "12345",
    zone: str = "epuyen",
    min_severity: str = "medium",
    custom_lat: float | None = None,
    custom_lon: float | None = None,
    custom_radius_km: float | None = None,
) -> AlertSubscription:
    """Insert and return an active subscription."""
    sub = AlertSubscription(
        id=str(uuid.uuid4()),
        channel=channel,
        channel_id=channel_id,
        zone=zone,
        custom_lat=custom_lat,
        custom_lon=custom_lon,
        custom_radius_km=custom_radius_km,
        min_severity=min_severity,
        is_active=True,
        created_at=datetime.utcnow(),
        language="es",
    )
    session.add(sub)
    await session.commit()
    return sub


async def _insert_past_alerts(
    session: AsyncSession,
    event_id: str,
    subscription_id: str,
    count: int,
) -> None:
    """Insert *count* past AlertSent rows for rate-limit testing."""
    for i in range(count):
        alert = AlertSent(
            id=str(uuid.uuid4()),
            fire_event_id=event_id,
            subscription_id=subscription_id,
            channel="telegram",
            message_content=f"test alert {i}",
            sent_at=datetime.utcnow() - timedelta(minutes=10 * i),
            delivered=True,
            is_escalation=False,
        )
        session.add(alert)
    await session.commit()


# ===========================================================================
# Telegram tests
# ===========================================================================


class TestTelegramClient:
    """Tests for TelegramAlertClient using respx-mocked HTTP."""

    @respx.mock
    async def test_telegram_send_success(self) -> None:
        """A 200 response from Telegram returns True."""
        route = respx.post("https://api.telegram.org/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        client = TelegramAlertClient("TEST_TOKEN")
        try:
            result = await client.send_message("12345", "Hola mundo")
        finally:
            await client.close()

        assert result is True
        assert route.called

    @respx.mock
    async def test_telegram_send_chat_not_found(self) -> None:
        """A 400 response (chat not found) returns False."""
        respx.post("https://api.telegram.org/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(
                400,
                json={"ok": False, "description": "Bad Request: chat not found"},
            )
        )

        client = TelegramAlertClient("TEST_TOKEN")
        try:
            result = await client.send_message("99999", "test")
        finally:
            await client.close()

        assert result is False

    @respx.mock
    async def test_telegram_send_bot_blocked(self) -> None:
        """A 403 response (bot blocked) returns False."""
        respx.post("https://api.telegram.org/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(
                403,
                json={
                    "ok": False,
                    "description": "Forbidden: bot was blocked by the user",
                },
            )
        )

        client = TelegramAlertClient("TEST_TOKEN")
        try:
            result = await client.send_message("12345", "test")
        finally:
            await client.close()

        assert result is False

    @respx.mock
    async def test_telegram_rate_limited(self) -> None:
        """A 429 response triggers a retry after the indicated delay."""
        call_count = 0

        def _side_effect(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    429,
                    json={
                        "ok": False,
                        "parameters": {"retry_after": 0},
                    },
                )
            return httpx.Response(200, json={"ok": True})

        respx.post("https://api.telegram.org/botTEST_TOKEN/sendMessage").mock(
            side_effect=_side_effect
        )

        client = TelegramAlertClient("TEST_TOKEN")
        try:
            result = await client.send_message("12345", "test")
        finally:
            await client.close()

        assert result is True
        assert call_count == 2


# ===========================================================================
# WhatsApp tests
# ===========================================================================


class TestWhatsAppClient:
    """Tests for WhatsAppAlertClient using respx-mocked HTTP."""

    @respx.mock
    async def test_whatsapp_send_success(self) -> None:
        """A 201 response from Twilio returns True."""
        route = respx.post(
            "https://api.twilio.com/2010-04-01/Accounts/AC_TEST/Messages.json"
        ).mock(
            return_value=httpx.Response(
                201,
                json={"sid": "SM123", "status": "queued"},
            )
        )

        client = WhatsAppAlertClient("AC_TEST", "AUTH_TOKEN", "+14155238886")
        try:
            result = await client.send_message("+5491155551234", "Hola")
        finally:
            await client.close()

        assert result is True
        assert route.called

    @respx.mock
    async def test_whatsapp_send_failure(self) -> None:
        """A 400 response from Twilio returns False."""
        respx.post("https://api.twilio.com/2010-04-01/Accounts/AC_TEST/Messages.json").mock(
            return_value=httpx.Response(
                400,
                json={"code": 21211, "message": "Invalid 'To' Phone Number"},
            )
        )

        client = WhatsAppAlertClient("AC_TEST", "AUTH_TOKEN", "+14155238886")
        try:
            result = await client.send_message("+invalid", "test")
        finally:
            await client.close()

        assert result is False

    @respx.mock
    async def test_whatsapp_auth_header(self) -> None:
        """Verify Basic auth header is set on the request."""
        captured_request: httpx.Request | None = None

        def _capture(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(201, json={"sid": "SM456", "status": "queued"})

        respx.post("https://api.twilio.com/2010-04-01/Accounts/AC_TEST/Messages.json").mock(
            side_effect=_capture
        )

        client = WhatsAppAlertClient("AC_TEST", "AUTH_TOKEN", "+14155238886")
        try:
            await client.send_message("+5491155551234", "test")
        finally:
            await client.close()

        assert captured_request is not None
        auth_header = captured_request.headers.get("authorization", "")
        assert auth_header.startswith("Basic ")


# ===========================================================================
# Dispatcher tests
# ===========================================================================


class TestAlertDispatcher:
    """Tests for the AlertDispatcher routing engine."""

    @respx.mock
    async def test_dispatch_matches_zone(
        self, db_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """An event inside a subscription's zone triggers an alert."""
        # Mock Telegram API
        respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        # Create subscription for epuyen zone (center -42.22, -71.43, radius 15km)
        async with db_factory() as session:
            await _create_subscription(session, zone="epuyen")

        telegram = TelegramAlertClient("TOKEN")
        dispatcher = AlertDispatcher(telegram, None, db_factory)

        try:
            # Event at epuyen center -- clearly within zone
            event = _make_fire_event(lat=-42.22, lon=-71.43)
            records = await dispatcher.dispatch_alerts([event])
        finally:
            await telegram.close()

        assert len(records) == 1
        assert records[0].delivered is True

    @respx.mock
    async def test_dispatch_ignores_out_of_zone(
        self, db_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """An event outside a subscription's zone does not trigger an alert."""
        respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        async with db_factory() as session:
            await _create_subscription(session, zone="epuyen")

        telegram = TelegramAlertClient("TOKEN")
        dispatcher = AlertDispatcher(telegram, None, db_factory)

        try:
            # Event far away from epuyen (Buenos Aires)
            event = _make_fire_event(lat=-34.60, lon=-58.38)
            records = await dispatcher.dispatch_alerts([event])
        finally:
            await telegram.close()

        assert len(records) == 0

    @respx.mock
    async def test_dispatch_respects_min_severity(
        self, db_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """An event below the subscription's min severity is not sent."""
        respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        async with db_factory() as session:
            # min_severity = high, but event severity = medium
            await _create_subscription(session, zone="epuyen", min_severity="high")

        telegram = TelegramAlertClient("TOKEN")
        dispatcher = AlertDispatcher(telegram, None, db_factory)

        try:
            event = _make_fire_event(severity=Severity.MEDIUM)
            records = await dispatcher.dispatch_alerts([event])
        finally:
            await telegram.close()

        assert len(records) == 0

    @respx.mock
    async def test_dispatch_rate_limit(self, db_factory: async_sessionmaker[AsyncSession]) -> None:
        """After 3 alerts for the same event/subscription, the 4th is blocked."""
        respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        event_id = str(uuid.uuid4())

        async with db_factory() as session:
            sub = await _create_subscription(session, zone="epuyen")
            # Pre-insert 3 past alerts
            await _insert_past_alerts(session, event_id, sub.id, 3)

        telegram = TelegramAlertClient("TOKEN")
        dispatcher = AlertDispatcher(telegram, None, db_factory)

        try:
            event = _make_fire_event(event_id=event_id)
            records = await dispatcher.dispatch_alerts([event])
        finally:
            await telegram.close()

        # 4th alert should be blocked by rate limit
        assert len(records) == 0

    @respx.mock
    async def test_dispatch_escalation(self, db_factory: async_sessionmaker[AsyncSession]) -> None:
        """An escalation alert is sent when severity increases."""
        respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        event_id = str(uuid.uuid4())

        async with db_factory() as session:
            sub = await _create_subscription(session, zone="epuyen")

            # Insert a previous alert for this event
            prev_alert = AlertSent(
                id=str(uuid.uuid4()),
                fire_event_id=event_id,
                subscription_id=sub.id,
                channel="telegram",
                message_content="previous alert",
                sent_at=datetime.utcnow() - timedelta(minutes=30),
                delivered=True,
                is_escalation=False,
            )
            session.add(prev_alert)

            # Insert the fire event row with previous severity = medium
            from firesentinel.db.models import FireEvent as FireEventModel

            fe_row = FireEventModel(
                id=event_id,
                center_lat=-42.22,
                center_lon=-71.43,
                severity="medium",
                hotspot_count=1,
                max_frp=28.5,
                first_detected_at=datetime(2026, 2, 15, 3, 30),
                last_updated_at=datetime(2026, 2, 15, 3, 45),
                intent_score=40,
                intent_label="uncertain",
                is_active=True,
            )
            session.add(fe_row)
            await session.commit()

        telegram = TelegramAlertClient("TOKEN")
        dispatcher = AlertDispatcher(telegram, None, db_factory)

        try:
            # Now the event escalated to HIGH severity
            event = _make_fire_event(
                event_id=event_id,
                severity=Severity.HIGH,
                intent_total=70,
            )
            records = await dispatcher.dispatch_alerts([event])
        finally:
            await telegram.close()

        assert len(records) == 1
        assert records[0].is_escalation is True
        assert "ACTUALIZACION" in records[0].message_content

    @respx.mock
    async def test_dispatch_no_telegram_client(
        self, db_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """WhatsApp subscriptions still work when Telegram client is None."""
        respx.post("https://api.twilio.com/2010-04-01/Accounts/AC_TEST/Messages.json").mock(
            return_value=httpx.Response(201, json={"sid": "SM1", "status": "queued"})
        )

        async with db_factory() as session:
            await _create_subscription(
                session,
                channel="whatsapp",
                channel_id="+5491155551234",
                zone="epuyen",
            )

        whatsapp = WhatsAppAlertClient("AC_TEST", "AUTH_TOKEN", "+14155238886")
        # Telegram is None -- should not crash
        dispatcher = AlertDispatcher(None, whatsapp, db_factory)

        try:
            event = _make_fire_event()
            records = await dispatcher.dispatch_alerts([event])
        finally:
            await whatsapp.close()

        assert len(records) == 1
        assert records[0].channel == AlertChannel.WHATSAPP
        assert records[0].delivered is True

    @respx.mock
    async def test_dispatch_records_alert_sent(
        self, db_factory: async_sessionmaker[AsyncSession]
    ) -> None:
        """Dispatched alerts are persisted in the alerts_sent table."""
        respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True})
        )

        async with db_factory() as session:
            await _create_subscription(session, zone="epuyen")

        telegram = TelegramAlertClient("TOKEN")
        dispatcher = AlertDispatcher(telegram, None, db_factory)

        try:
            event = _make_fire_event()
            records = await dispatcher.dispatch_alerts([event])
        finally:
            await telegram.close()

        assert len(records) == 1

        # Verify the record exists in the DB
        from sqlalchemy import select

        async with db_factory() as session:
            stmt = select(AlertSent).where(AlertSent.id == records[0].id)
            result = await session.execute(stmt)
            db_row = result.scalar_one_or_none()

        assert db_row is not None
        assert db_row.fire_event_id == event.id
        assert db_row.delivered is True


class TestSeverityOrdering:
    """Test severity comparison helpers."""

    def test_severity_ordering(self) -> None:
        """Verify low < medium < high < critical."""
        assert severity_gte("medium", "low") is True
        assert severity_gte("high", "medium") is True
        assert severity_gte("critical", "high") is True
        assert severity_gte("low", "medium") is False
        assert severity_gte("medium", "medium") is True

        assert severity_gt("high", "medium") is True
        assert severity_gt("medium", "medium") is False
        assert severity_gt("low", "critical") is False
        assert severity_gt("critical", "low") is True
