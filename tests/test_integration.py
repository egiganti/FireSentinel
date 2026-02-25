"""Integration tests that verify the full pipeline with real DB but mocked HTTP.

These tests use real SQLite databases, real module interactions (dedup,
clustering, classifier), but mock all external HTTP APIs (FIRMS, Open-Meteo,
Overpass) via respx. This validates end-to-end data flow without hitting
any external services.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

import httpx
import pytest
import respx

from firesentinel.alerts.templates import (
    format_telegram_alert,
    format_whatsapp_alert,
)
from firesentinel.config import YAMLConfig, get_yaml_config, reset_config
from firesentinel.core.pipeline import Pipeline
from firesentinel.core.types import (
    DayNight,
    EnrichedHotspot,
    FireEvent,
    PipelineStatus,
    RawHotspot,
    RoadContext,
    Severity,
    Source,
    WeatherContext,
)
from firesentinel.db.engine import get_engine, get_session_factory, init_db
from firesentinel.db.models import FireEvent as FireEventModel
from firesentinel.db.models import Hotspot, PipelineRun
from firesentinel.ingestion.firms import FIRMSClient
from firesentinel.ingestion.roads import RoadsClient
from firesentinel.ingestion.weather import WeatherClient
from firesentinel.processing.classifier import IntentClassifier

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Epuyen area coordinates for realistic Patagonian test data
_EPUYEN_LAT = -42.22
_EPUYEN_LON = -71.43

# FIRMS CSV header for VIIRS data
_VIIRS_CSV_HEADER = (
    "latitude,longitude,bright_ti4,bright_ti5,acq_date,acq_time,"
    "satellite,confidence,frp,daynight,scan,track,version\n"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def yaml_config() -> YAMLConfig:
    """Load the real YAML config from config/monitoring.yml."""
    reset_config()
    return get_yaml_config()


@pytest.fixture
async def integration_db(tmp_path: Path) -> dict[str, Any]:
    """Create a temporary SQLite database with all tables.

    Returns a dict with engine, session_factory, and db_path
    for use across multiple operations in a test.
    """
    db_path = tmp_path / "integration_test.db"
    engine = get_engine(str(db_path))
    await init_db(engine)
    session_factory = get_session_factory(engine)

    yield {  # type: ignore[misc]
        "engine": engine,
        "session_factory": session_factory,
        "db_path": str(db_path),
    }

    await engine.dispose()


# ---------------------------------------------------------------------------
# Mock data builders
# ---------------------------------------------------------------------------


def _build_viirs_csv(hotspots: list[dict[str, str]]) -> str:
    """Build a FIRMS VIIRS CSV response from hotspot dictionaries."""
    lines = [_VIIRS_CSV_HEADER.strip()]
    for hs in hotspots:
        lines.append(
            f"{hs['lat']},{hs['lon']},{hs['bright_ti4']},{hs['bright_ti5']},"
            f"{hs['acq_date']},{hs['acq_time']},{hs['satellite']},"
            f"{hs['confidence']},{hs['frp']},{hs['daynight']},"
            f"{hs.get('scan', '0.39')},{hs.get('track', '0.36')},"
            f"{hs.get('version', '2.0NRT')}"
        )
    return "\n".join(lines)


def _make_five_patagonian_hotspots() -> list[dict[str, str]]:
    """Create 5 realistic Patagonian hotspot rows for VIIRS CSV."""
    base_lat = _EPUYEN_LAT
    base_lon = _EPUYEN_LON
    return [
        {
            "lat": str(base_lat),
            "lon": str(base_lon),
            "bright_ti4": "345.6",
            "bright_ti5": "298.1",
            "acq_date": "2026-02-15",
            "acq_time": "0330",
            "satellite": "N",
            "confidence": "high",
            "frp": "28.5",
            "daynight": "N",
        },
        {
            "lat": str(base_lat + 0.005),
            "lon": str(base_lon + 0.003),
            "bright_ti4": "340.2",
            "bright_ti5": "295.0",
            "acq_date": "2026-02-15",
            "acq_time": "0330",
            "satellite": "N",
            "confidence": "nominal",
            "frp": "22.3",
            "daynight": "N",
        },
        {
            "lat": str(base_lat - 0.003),
            "lon": str(base_lon + 0.002),
            "bright_ti4": "355.8",
            "bright_ti5": "302.5",
            "acq_date": "2026-02-15",
            "acq_time": "0330",
            "satellite": "N",
            "confidence": "high",
            "frp": "45.1",
            "daynight": "N",
        },
        {
            "lat": str(base_lat + 0.001),
            "lon": str(base_lon - 0.004),
            "bright_ti4": "332.1",
            "bright_ti5": "290.8",
            "acq_date": "2026-02-15",
            "acq_time": "0331",
            "satellite": "N",
            "confidence": "nominal",
            "frp": "18.7",
            "daynight": "N",
        },
        {
            "lat": str(base_lat - 0.002),
            "lon": str(base_lon - 0.001),
            "bright_ti4": "348.9",
            "bright_ti5": "300.3",
            "acq_date": "2026-02-15",
            "acq_time": "0331",
            "satellite": "N",
            "confidence": "high",
            "frp": "33.2",
            "daynight": "N",
        },
    ]


def _build_open_meteo_response(
    weather_code: int = 0,
    cape: float = 100.0,
    humidity: float = 20.0,
    precipitation: float = 0.0,
    has_thunderstorm: bool = False,
) -> dict[str, Any]:
    """Build a realistic Open-Meteo API JSON response."""
    # Generate 7 hourly time slots around 03:30 UTC
    times = [f"2026-02-15T{h:02d}:00" for h in range(0, 7)]
    n = len(times)

    weather_codes = [weather_code] * n
    if has_thunderstorm:
        weather_codes[3] = 95  # Thunderstorm at 03:00

    return {
        "hourly": {
            "time": times,
            "cape": [cape] * n,
            "convective_inhibition": [10.0] * n,
            "weather_code": weather_codes,
            "temperature_2m": [25.0] * n,
            "wind_speed_10m": [12.0] * n,
            "relative_humidity_2m": [humidity] * n,
            "precipitation": [precipitation] * n,
        }
    }


def _build_overpass_response(
    distance_offset_lat: float = 0.003,
    road_type: str = "track",
    road_ref: str | None = None,
) -> dict[str, Any]:
    """Build a realistic Overpass API JSON response with a road near Epuyen."""
    tags: dict[str, str] = {"highway": road_type}
    if road_ref is not None:
        tags["ref"] = road_ref

    return {
        "elements": [
            {
                "type": "way",
                "id": 123456789,
                "tags": tags,
                "geometry": [
                    {"lat": _EPUYEN_LAT + distance_offset_lat, "lon": _EPUYEN_LON - 0.001},
                    {"lat": _EPUYEN_LAT + distance_offset_lat, "lon": _EPUYEN_LON + 0.010},
                ],
            }
        ]
    }


# ---------------------------------------------------------------------------
# Helper to register FIRMS mocks for all 4 sources
# ---------------------------------------------------------------------------


def _register_firms_mocks(
    router: respx.Router,
    csv_content: str,
) -> None:
    """Register mocked FIRMS responses for all 4 sources on the router."""
    sources = ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT", "MODIS_NRT"]
    for source in sources:
        url_pattern = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/{source}/"
        router.get(url__startswith=url_pattern).respond(
            200, text=csv_content
        )


def _register_weather_mock(
    router: respx.Router,
    response: dict[str, Any] | None = None,
    status_code: int = 200,
) -> None:
    """Register mocked Open-Meteo response."""
    if response is None:
        response = _build_open_meteo_response()
    router.get(url__startswith="https://api.open-meteo.com/v1/forecast").respond(
        status_code, json=response
    )
    router.get(url__startswith="https://archive-api.open-meteo.com/v1/archive").respond(
        status_code, json=response
    )


def _register_overpass_mock(
    router: respx.Router,
    response: dict[str, Any] | None = None,
    status_code: int = 200,
) -> None:
    """Register mocked Overpass API response."""
    if response is None:
        response = _build_overpass_response()
    router.post("https://overpass-api.de/api/interpreter").respond(
        status_code, json=response
    )


# ---------------------------------------------------------------------------
# Pipeline factory for integration tests
# ---------------------------------------------------------------------------


def _create_integration_pipeline(
    session_factory: Any,
    yaml_config: YAMLConfig,
    http_client: httpx.AsyncClient,
    dispatcher: Any = None,
) -> Pipeline:
    """Create a fully wired Pipeline using real modules and a shared HTTP client."""
    firms_client = FIRMSClient(map_key="test_key", client=http_client)
    weather_client = WeatherClient(client=http_client)
    roads_client = RoadsClient(client=http_client)
    classifier = IntentClassifier(config=yaml_config.intent_scoring)

    return Pipeline(
        firms_client=firms_client,
        weather_client=weather_client,
        roads_client=roads_client,
        classifier=classifier,
        dispatcher=dispatcher,
        session_factory=session_factory,
        yaml_config=yaml_config,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_cycle(
    integration_db: dict[str, Any],
    yaml_config: YAMLConfig,
) -> None:
    """Full pipeline cycle with real DB, mocked HTTP: ingest, dedup, enrich, cluster, classify."""
    hotspot_rows = _make_five_patagonian_hotspots()
    viirs_csv = _build_viirs_csv(hotspot_rows)
    # MODIS uses different column names -- return empty for simplicity
    modis_csv = (
        "latitude,longitude,brightness,bright_t31,"
        "acq_date,acq_time,satellite,confidence,frp,daynight\n"
    )
    weather_response = _build_open_meteo_response(
        weather_code=0, cape=100.0, humidity=20.0, precipitation=0.0
    )
    overpass_response = _build_overpass_response(distance_offset_lat=0.003)

    session_factory = integration_db["session_factory"]

    with respx.mock(assert_all_called=False) as router:
        # VIIRS sources return our 5 hotspots; MODIS returns empty
        for source in ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]:
            router.get(
                url__startswith=f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/{source}/"
            ).respond(200, text=viirs_csv)
        router.get(
            url__startswith="https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/MODIS_NRT/"
        ).respond(200, text=modis_csv)

        _register_weather_mock(router, weather_response)
        _register_overpass_mock(router, overpass_response)

        async with httpx.AsyncClient() as http_client:
            pipeline = _create_integration_pipeline(
                session_factory=session_factory,
                yaml_config=yaml_config,
                http_client=http_client,
                dispatcher=None,
            )
            record = await pipeline.run_cycle()

    # Verify pipeline run record
    assert record.status == PipelineStatus.SUCCESS
    assert record.hotspots_fetched == 15  # 5 per VIIRS source * 3 sources
    assert record.new_hotspots == 15

    # Verify fire events were created in DB with intent scores
    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(FireEventModel))
        db_events = result.scalars().all()
        assert len(db_events) >= 1

        # All events should have intent scores
        for ev in db_events:
            assert ev.intent_score is not None
            assert ev.intent_score >= 0
            assert ev.intent_label is not None

    # Verify hotspots stored in DB
    async with session_factory() as session:
        result = await session.execute(select(Hotspot))
        db_hotspots = result.scalars().all()
        assert len(db_hotspots) == 15

    # Verify pipeline run was recorded
    async with session_factory() as session:
        result = await session.execute(select(PipelineRun))
        runs = result.scalars().all()
        assert len(runs) == 1
        assert runs[0].status == "success"


@pytest.mark.asyncio
async def test_full_pipeline_second_cycle_dedup(
    integration_db: dict[str, Any],
    yaml_config: YAMLConfig,
) -> None:
    """Two cycles with same data: second cycle should deduplicate all hotspots."""
    hotspot_rows = _make_five_patagonian_hotspots()
    viirs_csv = _build_viirs_csv(hotspot_rows)
    modis_csv = (
        "latitude,longitude,brightness,bright_t31,"
        "acq_date,acq_time,satellite,confidence,frp,daynight\n"
    )
    weather_response = _build_open_meteo_response()
    overpass_response = _build_overpass_response()

    session_factory = integration_db["session_factory"]

    # First cycle -- should ingest all hotspots
    with respx.mock(assert_all_called=False) as router:
        for source in ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]:
            router.get(
                url__startswith=f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/{source}/"
            ).respond(200, text=viirs_csv)
        router.get(
            url__startswith="https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/MODIS_NRT/"
        ).respond(200, text=modis_csv)
        _register_weather_mock(router, weather_response)
        _register_overpass_mock(router, overpass_response)

        async with httpx.AsyncClient() as http_client:
            pipeline = _create_integration_pipeline(
                session_factory=session_factory,
                yaml_config=yaml_config,
                http_client=http_client,
            )
            record1 = await pipeline.run_cycle()

    assert record1.status == PipelineStatus.SUCCESS
    assert record1.new_hotspots == 15

    # Second cycle -- same data, all should be deduplicated
    with respx.mock(assert_all_called=False) as router:
        for source in ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]:
            router.get(
                url__startswith=f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/{source}/"
            ).respond(200, text=viirs_csv)
        router.get(
            url__startswith="https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/MODIS_NRT/"
        ).respond(200, text=modis_csv)
        _register_weather_mock(router, weather_response)
        _register_overpass_mock(router, overpass_response)

        async with httpx.AsyncClient() as http_client:
            pipeline2 = _create_integration_pipeline(
                session_factory=session_factory,
                yaml_config=yaml_config,
                http_client=http_client,
            )
            record2 = await pipeline2.run_cycle()

    assert record2.status == PipelineStatus.SUCCESS
    assert record2.hotspots_fetched == 15
    assert record2.new_hotspots == 0


@pytest.mark.asyncio
async def test_intent_scoring_realistic_intentional(yaml_config: YAMLConfig) -> None:
    """Intentional fire scenario: suspicious or likely_intentional (>= 70)."""
    classifier = IntentClassifier(config=yaml_config.intent_scoring)

    # Nighttime detection: 02:00 UTC = 23:00 local Argentina (UTC-3) -> peak night
    hotspot = RawHotspot(
        source=Source.VIIRS_SNPP_NRT,
        latitude=_EPUYEN_LAT,
        longitude=_EPUYEN_LON,
        brightness=355.0,
        brightness_2=302.0,
        frp=45.0,
        confidence="high",
        acq_date=date(2026, 2, 15),
        acq_time=time(2, 0),  # 02:00 UTC = 23:00 local
        satellite="N",
        daynight=DayNight.NIGHT,
    )

    # No thunderstorm, low CAPE -- strong lightning absence signal
    weather = WeatherContext(
        cape=50.0,
        convective_inhibition=10.0,
        weather_code=0,
        temperature_c=30.0,
        wind_speed_kmh=10.0,
        humidity_pct=20.0,
        precipitation_mm_6h=0.0,
        precipitation_mm_72h=0.0,
        has_thunderstorm=False,
    )

    # Road 150m away -- very close track road
    road = RoadContext(
        nearest_distance_m=150.0,
        nearest_road_type="track",
        nearest_road_ref=None,
    )

    enriched = EnrichedHotspot(hotspot=hotspot, weather=weather, road=road)

    event = FireEvent(
        id=str(uuid.uuid4()),
        center_lat=_EPUYEN_LAT,
        center_lon=_EPUYEN_LON,
        hotspots=[enriched],
        severity=Severity.MEDIUM,
        max_frp=45.0,
        first_detected=datetime(2026, 2, 15, 2, 0),
        last_updated=datetime(2026, 2, 15, 2, 0),
        is_active=True,
    )

    breakdown = classifier.classify(event)

    # With no lightning (25), close road (20), night (20), dry conditions (10) = 75
    # Expect >= 70 for suspicious or likely_intentional
    assert breakdown.total >= 70, f"Expected >= 70, got {breakdown.total}"
    assert breakdown.label.value in ("suspicious", "likely_intentional")
    assert breakdown.lightning_score > 0
    assert breakdown.road_score > 0
    assert breakdown.night_score > 0
    assert breakdown.dry_conditions_score > 0


@pytest.mark.asyncio
async def test_intent_scoring_realistic_natural(yaml_config: YAMLConfig) -> None:
    """Natural fire scenario should produce a low score (<= 25)."""
    classifier = IntentClassifier(config=yaml_config.intent_scoring)

    # Daytime detection: 18:00 UTC = 15:00 local Argentina (UTC-3)
    hotspot = RawHotspot(
        source=Source.VIIRS_SNPP_NRT,
        latitude=_EPUYEN_LAT,
        longitude=_EPUYEN_LON,
        brightness=340.0,
        brightness_2=295.0,
        frp=30.0,
        confidence="high",
        acq_date=date(2026, 2, 15),
        acq_time=time(18, 0),  # 18:00 UTC = 15:00 local
        satellite="N",
        daynight=DayNight.DAY,
    )

    # Thunderstorm detected, high CAPE -- natural ignition likely
    weather = WeatherContext(
        cape=1500.0,
        convective_inhibition=5.0,
        weather_code=95,
        temperature_c=22.0,
        wind_speed_kmh=25.0,
        humidity_pct=55.0,
        precipitation_mm_6h=5.0,
        precipitation_mm_72h=15.0,
        has_thunderstorm=True,
    )

    # Road 5km away -- far from access
    road = RoadContext(
        nearest_distance_m=5000.0,
        nearest_road_type="path",
        nearest_road_ref=None,
    )

    enriched = EnrichedHotspot(hotspot=hotspot, weather=weather, road=road)

    event = FireEvent(
        id=str(uuid.uuid4()),
        center_lat=_EPUYEN_LAT,
        center_lon=_EPUYEN_LON,
        hotspots=[enriched],
        severity=Severity.LOW,
        max_frp=30.0,
        first_detected=datetime(2026, 2, 15, 18, 0),
        last_updated=datetime(2026, 2, 15, 18, 0),
        is_active=True,
    )

    breakdown = classifier.classify(event)

    # With thunderstorm (0), far road (0), daytime (0), wet (0) = 0
    assert breakdown.total <= 25, f"Expected <= 25, got {breakdown.total}"
    assert breakdown.label.value == "natural"
    assert breakdown.lightning_score == 0
    assert breakdown.road_score == 0
    assert breakdown.night_score == 0


@pytest.mark.asyncio
async def test_pipeline_graceful_degradation(
    integration_db: dict[str, Any],
    yaml_config: YAMLConfig,
) -> None:
    """Weather API returning 500 should not crash the pipeline."""
    hotspot_rows = _make_five_patagonian_hotspots()
    viirs_csv = _build_viirs_csv(hotspot_rows)
    modis_csv = (
        "latitude,longitude,brightness,bright_t31,"
        "acq_date,acq_time,satellite,confidence,frp,daynight\n"
    )
    overpass_response = _build_overpass_response(distance_offset_lat=0.003)

    session_factory = integration_db["session_factory"]

    with respx.mock(assert_all_called=False) as router:
        for source in ["VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT"]:
            router.get(
                url__startswith=f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/{source}/"
            ).respond(200, text=viirs_csv)
        router.get(
            url__startswith="https://firms.modaps.eosdis.nasa.gov/api/area/csv/test_key/MODIS_NRT/"
        ).respond(200, text=modis_csv)

        # Weather API returns 500 -- should degrade gracefully
        _register_weather_mock(
            router, status_code=500, response={"error": "Server Error"}
        )
        _register_overpass_mock(router, overpass_response)

        async with httpx.AsyncClient() as http_client:
            pipeline = _create_integration_pipeline(
                session_factory=session_factory,
                yaml_config=yaml_config,
                http_client=http_client,
            )
            record = await pipeline.run_cycle()

    # Pipeline should not fail -- graceful degradation
    assert record.status in (PipelineStatus.SUCCESS, PipelineStatus.PARTIAL)
    assert record.hotspots_fetched == 15
    assert record.new_hotspots == 15

    # Fire events should still be created
    async with session_factory() as session:
        from sqlalchemy import select

        result = await session.execute(select(FireEventModel))
        db_events = result.scalars().all()
        assert len(db_events) >= 1

        # Intent scores should exist (renormalized without weather signals)
        for ev in db_events:
            assert ev.intent_score is not None


@pytest.mark.asyncio
async def test_alert_template_with_real_scored_event(yaml_config: YAMLConfig) -> None:
    """Run classifier on a realistic event, then format through alert templates."""
    classifier = IntentClassifier(config=yaml_config.intent_scoring)

    hotspot = RawHotspot(
        source=Source.VIIRS_SNPP_NRT,
        latitude=_EPUYEN_LAT,
        longitude=_EPUYEN_LON,
        brightness=350.0,
        brightness_2=300.0,
        frp=35.0,
        confidence="high",
        acq_date=date(2026, 2, 15),
        acq_time=time(2, 30),  # Nighttime UTC
        satellite="N",
        daynight=DayNight.NIGHT,
    )

    weather = WeatherContext(
        cape=80.0,
        convective_inhibition=15.0,
        weather_code=0,
        temperature_c=28.0,
        wind_speed_kmh=12.0,
        humidity_pct=18.0,
        precipitation_mm_6h=0.0,
        precipitation_mm_72h=0.0,
        has_thunderstorm=False,
    )

    road = RoadContext(
        nearest_distance_m=180.0,
        nearest_road_type="track",
        nearest_road_ref="RP71",
    )

    enriched = EnrichedHotspot(hotspot=hotspot, weather=weather, road=road)

    event = FireEvent(
        id=str(uuid.uuid4()),
        center_lat=_EPUYEN_LAT,
        center_lon=_EPUYEN_LON,
        hotspots=[enriched],
        severity=Severity.MEDIUM,
        max_frp=35.0,
        first_detected=datetime(2026, 2, 15, 2, 30),
        last_updated=datetime(2026, 2, 15, 2, 30),
        province="Chubut",
        nearest_town="Epuyen",
        nearest_road_m=180.0,
        nearest_road_type="track",
        nearest_road_ref="RP71",
        weather_data={"humidity_pct": 18.0},
        is_active=True,
    )

    # Classify
    breakdown = classifier.classify(event)
    event.intent = breakdown

    # Format Telegram alert
    telegram_msg = format_telegram_alert(event)

    # Verify the intent score is present in the message
    assert f"{breakdown.total}/100" in telegram_msg

    # Verify Spanish labels
    label_text = breakdown.label.value
    if label_text == "natural":
        assert "NATURAL" in telegram_msg
    elif label_text == "uncertain":
        assert "INCIERTO" in telegram_msg
    elif label_text == "suspicious":
        assert "SOSPECHOSO" in telegram_msg
    elif label_text == "likely_intentional":
        assert "PROBABLE INTENCIONAL" in telegram_msg

    # Verify Google Maps link is present
    assert f"https://www.google.com/maps?q={_EPUYEN_LAT},{_EPUYEN_LON}" in telegram_msg

    # Verify location information
    assert "Epuyen" in telegram_msg
    assert "Chubut" in telegram_msg

    # Verify signal descriptions are present
    assert "senales" in telegram_msg.lower() or "Senales" in telegram_msg

    # Format WhatsApp alert and verify similar content
    whatsapp_msg = format_whatsapp_alert(event)
    assert f"{breakdown.total}/100" in whatsapp_msg
    assert f"https://www.google.com/maps?q={_EPUYEN_LAT},{_EPUYEN_LON}" in whatsapp_msg
