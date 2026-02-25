"""Configuration management: environment variables (Pydantic Settings) + YAML config.

Env vars handle secrets and deployment-specific values.
monitoring.yml handles scoring weights, thresholds, and zones (version-controlled).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# YAML config models (nested, loaded from config/monitoring.yml)
# ---------------------------------------------------------------------------


class BBoxConfig(BaseModel):
    """Bounding box definitions for monitoring regions."""

    full_patagonia: list[float]
    crisis_corridor: list[float]


class MonitoringConfig(BaseModel):
    """Top-level monitoring configuration."""

    poll_interval_minutes: int
    day_range: int = 2
    sources: list[str]
    bbox: BBoxConfig


class IntentWeightsConfig(BaseModel):
    """Scoring signal weights (must sum to 100)."""

    lightning_absence: int
    road_proximity: int
    nighttime_ignition: int
    historical_repeat: int
    multi_point_ignition: int
    dry_conditions: int


class IntentThresholdsConfig(BaseModel):
    """Score range for each intent label."""

    natural: list[int]
    uncertain: list[int]
    suspicious: list[int]
    likely_intentional: list[int]


class RoadDistanceConfig(BaseModel):
    """Road proximity distance thresholds in meters."""

    very_close: int
    close: int
    near: int
    moderate: int


class NightHoursConfig(BaseModel):
    """Nighttime hour ranges for intent scoring (local time UTC-3)."""

    peak: list[int]
    shoulder: list[int]
    shoulder_evening: list[int]


class IntentScoringConfig(BaseModel):
    """Full intent scoring configuration."""

    weights: IntentWeightsConfig
    thresholds: IntentThresholdsConfig
    road_distance_m: RoadDistanceConfig
    night_hours_local: NightHoursConfig


class ZoneConfig(BaseModel):
    """A predefined monitoring zone."""

    center: list[float]
    radius_km: int | float


class SeverityRangeConfig(BaseModel):
    """Severity level hotspot count ranges."""

    low: list[int]
    medium: list[int]
    high: list[int]
    critical: list[int | None]


class ClusteringConfig(BaseModel):
    """Spatial and temporal clustering parameters."""

    spatial_radius_m: int
    temporal_window_hours: int
    severity: SeverityRangeConfig
    critical_frp_threshold_mw: int


class DedupConfig(BaseModel):
    """Deduplication tolerances."""

    spatial_tolerance_m: int
    temporal_tolerance_minutes: int


class AlertConfig(BaseModel):
    """Alert rate limiting and defaults."""

    max_per_event_per_user: int
    cooldown_hours: int
    min_severity_default: str


class CachingConfig(BaseModel):
    """API response caching parameters."""

    weather_grid_degrees: float
    weather_ttl_minutes: int
    roads_grid_degrees: float
    roads_ttl_hours: int


class YAMLConfig(BaseModel):
    """Complete parsed monitoring.yml structure."""

    monitoring: MonitoringConfig
    intent_scoring: IntentScoringConfig
    zones: dict[str, ZoneConfig]
    clustering: ClusteringConfig
    dedup: DedupConfig
    alerts: AlertConfig
    caching: CachingConfig


# ---------------------------------------------------------------------------
# Environment settings (Pydantic Settings)
# ---------------------------------------------------------------------------

# Default path to monitoring.yml relative to project root
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "monitoring.yml"


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Secrets and deployment-specific values come from env vars.
    Scoring weights, thresholds, and zones come from monitoring.yml.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Required API keys
    firms_map_key: str = ""

    # Telegram
    telegram_bot_token: str = ""

    # Twilio (WhatsApp)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""

    # Admin
    admin_password: str = ""

    # Deployment
    environment: str = "dev"
    db_path: str = "./data/firesentinel.db"

    # Path to monitoring.yml (not typically set via env, but useful for testing)
    config_path: str = str(_DEFAULT_CONFIG_PATH)

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        allowed = {"dev", "staging", "prod"}
        if v not in allowed:
            msg = f"ENVIRONMENT must be one of {allowed}, got '{v}'"
            raise ValueError(msg)
        return v

    def load_yaml_config(self) -> YAMLConfig:
        """Load and parse config/monitoring.yml into typed models."""
        config_file = Path(self.config_path)
        if not config_file.exists():
            msg = f"Config file not found: {config_file}"
            raise FileNotFoundError(msg)

        with open(config_file) as f:
            raw: dict[str, Any] = yaml.safe_load(f)

        return YAMLConfig.model_validate(raw)


# Module-level singleton for convenience
_settings: Settings | None = None
_yaml_config: YAMLConfig | None = None


def get_settings() -> Settings:
    """Get or create the global Settings instance."""
    global _settings  # noqa: PLW0603
    if _settings is None:
        _settings = Settings()
    return _settings


def get_yaml_config() -> YAMLConfig:
    """Get or create the global YAMLConfig instance."""
    global _yaml_config  # noqa: PLW0603
    if _yaml_config is None:
        _yaml_config = get_settings().load_yaml_config()
    return _yaml_config


def reset_config() -> None:
    """Reset cached config singletons. Useful for testing."""
    global _settings, _yaml_config  # noqa: PLW0603
    _settings = None
    _yaml_config = None
