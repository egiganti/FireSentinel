"""Shared dataclass contracts between all FireSentinel modules.

These types define the boundaries between pipeline stages. All modules import
from here -- no module imports from a peer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Source(str, Enum):
    """Satellite data source identifiers matching FIRMS API."""

    VIIRS_SNPP_NRT = "VIIRS_SNPP_NRT"
    VIIRS_NOAA20_NRT = "VIIRS_NOAA20_NRT"
    VIIRS_NOAA21_NRT = "VIIRS_NOAA21_NRT"
    MODIS_NRT = "MODIS_NRT"


class Confidence(str, Enum):
    """VIIRS confidence levels."""

    LOW = "low"
    NOMINAL = "nominal"
    HIGH = "high"


class DayNight(str, Enum):
    """Day/night flag from satellite acquisition."""

    DAY = "D"
    NIGHT = "N"


class Severity(str, Enum):
    """Fire event severity based on hotspot count and FRP."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IntentLabel(str, Enum):
    """Intentionality classification thresholds."""

    NATURAL = "natural"
    UNCERTAIN = "uncertain"
    SUSPICIOUS = "suspicious"
    LIKELY_INTENTIONAL = "likely_intentional"


class AlertChannel(str, Enum):
    """Supported alert delivery channels."""

    TELEGRAM = "telegram"
    WHATSAPP = "whatsapp"
    EMAIL = "email"


class Language(str, Enum):
    """Supported languages for alert messages."""

    ES = "es"
    EN = "en"


class PipelineStatus(str, Enum):
    """Pipeline run completion status."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Data contracts (frozen where appropriate)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RawHotspot:
    """Direct parse from FIRMS CSV response. Immutable."""

    source: Source
    latitude: float
    longitude: float
    brightness: float
    brightness_2: float
    frp: float
    confidence: str
    acq_date: date
    acq_time: time
    satellite: str
    daynight: DayNight
    raw_data: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WeatherContext:
    """Weather conditions at a hotspot location. Immutable."""

    cape: float
    convective_inhibition: float
    weather_code: int
    temperature_c: float
    wind_speed_kmh: float
    humidity_pct: float
    precipitation_mm_6h: float
    precipitation_mm_72h: float
    has_thunderstorm: bool


@dataclass(frozen=True)
class RoadContext:
    """Nearest road information for a hotspot. Immutable."""

    nearest_distance_m: float
    nearest_road_type: str
    nearest_road_ref: str | None


@dataclass
class EnrichedHotspot:
    """A hotspot with weather and road context attached.

    Weather or road may be None if the respective API call failed.
    The pipeline continues with available data (graceful degradation).
    """

    hotspot: RawHotspot
    weather: WeatherContext | None = None
    road: RoadContext | None = None


@dataclass
class IntentBreakdown:
    """Detailed intentionality score breakdown per signal.

    All individual scores are bounded by their respective max weights
    from config/monitoring.yml. The total is the sum of individual scores.
    """

    lightning_score: int
    road_score: int
    night_score: int
    history_score: int
    multi_point_score: int
    dry_conditions_score: int
    active_signals: int
    total_signals: int

    @property
    def total(self) -> int:
        """Sum of all individual signal scores."""
        return (
            self.lightning_score
            + self.road_score
            + self.night_score
            + self.history_score
            + self.multi_point_score
            + self.dry_conditions_score
        )

    @property
    def label(self) -> IntentLabel:
        """Classification label derived from total score."""
        score = self.total
        if score <= 25:
            return IntentLabel.NATURAL
        if score <= 50:
            return IntentLabel.UNCERTAIN
        if score <= 75:
            return IntentLabel.SUSPICIOUS
        return IntentLabel.LIKELY_INTENTIONAL

    def to_dict(self) -> dict[str, int | str]:
        """Serialize breakdown to a flat dictionary for JSON storage."""
        return {
            "lightning": self.lightning_score,
            "road": self.road_score,
            "night": self.night_score,
            "history": self.history_score,
            "multi_point": self.multi_point_score,
            "dry_conditions": self.dry_conditions_score,
            "active_signals": self.active_signals,
            "total_signals": self.total_signals,
            "total": self.total,
            "label": self.label.value,
        }


@dataclass
class FireEvent:
    """A grouped fire event with enriched hotspots and intent classification."""

    id: str
    center_lat: float
    center_lon: float
    hotspots: list[EnrichedHotspot]
    severity: Severity
    max_frp: float
    first_detected: datetime
    last_updated: datetime
    province: str | None = None
    nearest_town: str | None = None
    nearest_road_m: float | None = None
    nearest_road_type: str | None = None
    nearest_road_ref: str | None = None
    weather_data: dict[str, float | int | bool] | None = None
    intent: IntentBreakdown | None = None
    is_active: bool = True


@dataclass
class AlertRecord:
    """Record of an alert dispatched to a subscriber."""

    id: str
    fire_event_id: str
    subscription_id: str
    channel: AlertChannel
    message_content: str
    sent_at: datetime
    delivered: bool = False
    is_escalation: bool = False
    error: str | None = None


@dataclass
class PipelineRunRecord:
    """Metrics for a single pipeline execution cycle."""

    id: str
    started_at: datetime
    completed_at: datetime | None = None
    status: PipelineStatus = PipelineStatus.SUCCESS
    hotspots_fetched: int = 0
    new_hotspots: int = 0
    events_created: int = 0
    events_updated: int = 0
    alerts_sent: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: int | None = None
