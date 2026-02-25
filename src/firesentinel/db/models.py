"""SQLAlchemy 2.0 ORM models matching the PRD section 7.3 schema.

All tables use UUID primary keys stored as String (SQLite compatibility).
JSON columns store raw API responses and scoring breakdowns.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, Time, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all FireSentinel models."""


class Hotspot(Base):
    """Raw satellite hotspot detection record.

    Stores the original FIRMS data with a unique constraint preventing
    duplicate ingestion of the same detection.
    """

    __tablename__ = "hotspots"
    __table_args__ = (
        UniqueConstraint("source", "latitude", "longitude", "acq_date", "acq_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source: Mapped[str] = mapped_column(String(30), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    brightness: Mapped[float] = mapped_column(Float, nullable=False)
    brightness_2: Mapped[float] = mapped_column(Float, nullable=False)
    frp: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    acq_date: Mapped[date] = mapped_column(Date, nullable=False)
    acq_time: Mapped[time] = mapped_column(Time, nullable=False)
    daynight: Mapped[str] = mapped_column(String(1), nullable=False)
    satellite: Mapped[str] = mapped_column(String(10), nullable=False)
    fire_event_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("fire_events.id"), nullable=True
    )
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    raw_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    fire_event: Mapped[FireEvent | None] = relationship(
        "FireEvent", back_populates="hotspots"
    )


class FireEvent(Base):
    """A grouped fire event composed of clustered hotspot detections.

    Contains intent scoring results, weather context, and road proximity
    data for the fire location.
    """

    __tablename__ = "fire_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)
    province: Mapped[str | None] = mapped_column(String(50), nullable=True)
    nearest_town: Mapped[str | None] = mapped_column(String(100), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    hotspot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_frp: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    first_detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    intent_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    intent_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    intent_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    weather_data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    nearest_road_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    nearest_road_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    nearest_road_ref: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    hotspots: Mapped[list[Hotspot]] = relationship(
        "Hotspot", back_populates="fire_event"
    )
    alerts_sent: Mapped[list[AlertSent]] = relationship(
        "AlertSent", back_populates="fire_event"
    )


class AlertSubscription(Base):
    """User subscription for fire alerts on a specific channel and zone."""

    __tablename__ = "alert_subscriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    channel_id: Mapped[str] = mapped_column(String(100), nullable=False)
    zone: Mapped[str] = mapped_column(String(50), nullable=False)
    custom_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    custom_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    custom_radius_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_severity: Mapped[str] = mapped_column(
        String(20), nullable=False, default="medium"
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    language: Mapped[str] = mapped_column(String(5), nullable=False, default="es")

    alerts_sent: Mapped[list[AlertSent]] = relationship(
        "AlertSent", back_populates="subscription"
    )


class AlertSent(Base):
    """Record of an individual alert dispatched to a subscriber."""

    __tablename__ = "alerts_sent"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    fire_event_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("fire_events.id"), nullable=False
    )
    subscription_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("alert_subscriptions.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    message_content: Mapped[str] = mapped_column(Text, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_escalation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    fire_event: Mapped[FireEvent] = relationship(
        "FireEvent", back_populates="alerts_sent"
    )
    subscription: Mapped[AlertSubscription] = relationship(
        "AlertSubscription", back_populates="alerts_sent"
    )


class ExclusionZone(Base):
    """Geographic zone excluded from fire detection (industrial, volcanic, etc.)."""

    __tablename__ = "exclusion_zones"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    radius_km: Mapped[float] = mapped_column(Float, nullable=False)
    reason: Mapped[str] = mapped_column(String(200), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PipelineRun(Base):
    """Tracking record for each pipeline execution cycle.

    Stores timing, counts, and error details for monitoring and debugging.
    """

    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="success"
    )
    hotspots_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new_hotspots: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    events_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    alerts_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    errors: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
