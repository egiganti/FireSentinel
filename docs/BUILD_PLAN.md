# FireSentinel Patagonia - Build Plan

**Purpose:** Define the coding agent structure and software architecture for building FSP with Claude Code.

---

## 1. Software Architecture

### 1.1 Design Principles

- **No AI agent frameworks** (no CrewAI, LangGraph, AutoGen). The runtime is a deterministic pipeline.
- **Modular monolith.** One deployable unit, but internally separated by clear boundaries.
- **Async-first.** All I/O (API calls, DB) uses async Python. Satellite polling hits 4 APIs per cycle -- async makes this fast.
- **Fail-safe over fail-fast.** If one data source fails, the pipeline continues with the others. A FIRMS outage shouldn't kill weather enrichment.
- **Config-driven scoring.** Intent weights, thresholds, monitoring zones, and exclusion zones are all in YAML config. No magic numbers in code.

### 1.2 Runtime Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    SCHEDULER (APScheduler)                │
│                   Triggers every 15 min                   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  PIPELINE ORCHESTRATOR                    │
│                  (pipeline.py)                            │
│                                                          │
│  Runs the full cycle. Handles errors per-stage.          │
│  Logs timing + metrics. Stores pipeline run records.     │
└──────┬───────────────────────────────────────────────────┘
       │
       │  Stage 1: INGEST
       │  ┌─────────────────────────────────────────────┐
       ├─▶│  FIRMS Client (async, parallel 4 sources)    │
       │  │  → VIIRS_SNPP + VIIRS_NOAA20 + VIIRS_NOAA21│
       │  │  → MODIS_NRT                                 │
       │  │  Returns: list[RawHotspot]                   │
       │  └─────────────────────────────────────────────┘
       │
       │  Stage 2: DEDUPLICATE
       │  ┌─────────────────────────────────────────────┐
       ├─▶│  Deduplicator                                │
       │  │  → Compare against DB (spatial + temporal)   │
       │  │  → Returns: list[NewHotspot]                 │
       │  └─────────────────────────────────────────────┘
       │
       │  Stage 3: ENRICH (parallel)
       │  ┌─────────────────────────────────────────────┐
       ├─▶│  Weather Enricher ──┐                        │
       │  │  (Open-Meteo)       │  Run in parallel       │
       │  │                     │  per hotspot            │
       │  │  Road Enricher ─────┘                        │
       │  │  (Overpass API)                              │
       │  │  Returns: list[EnrichedHotspot]              │
       │  └─────────────────────────────────────────────┘
       │
       │  Stage 4: CLUSTER
       │  ┌─────────────────────────────────────────────┐
       ├─▶│  Fire Event Clusterer                        │
       │  │  → Group hotspots within 1km + 2hr          │
       │  │  → Merge into existing events or create new │
       │  │  Returns: list[FireEvent]                    │
       │  └─────────────────────────────────────────────┘
       │
       │  Stage 5: CLASSIFY
       │  ┌─────────────────────────────────────────────┐
       ├─▶│  Intent Classifier                           │
       │  │  → Score 0-100 per fire event               │
       │  │  → 6 weighted signals                       │
       │  │  Returns: list[ScoredFireEvent]              │
       │  └─────────────────────────────────────────────┘
       │
       │  Stage 6: ALERT
       │  ┌─────────────────────────────────────────────┐
       └─▶│  Alert Dispatcher                            │
          │  → Match events to subscriptions             │
          │  → Rate limit (max 3/event/user/6hr)        │
          │  → Dispatch via Telegram / WhatsApp          │
          │  → Record delivery status                    │
          └─────────────────────────────────────────────┘

DASHBOARD (separate process)
┌─────────────────────────────────────────────────────────┐
│  Streamlit App                                           │
│  → Reads from same DB                                    │
│  → Public map + filters + detail views                   │
│  → Admin panel (auth-protected)                          │
│  → Auto-refreshes every 5 min                            │
└─────────────────────────────────────────────────────────┘
```

### 1.3 Key Data Flow Types

```python
# Stage boundaries are defined by these types:

RawHotspot        # Direct from FIRMS CSV parse
    ↓ deduplicate
NewHotspot        # Confirmed not in DB
    ↓ enrich
EnrichedHotspot   # Has weather + road data attached
    ↓ cluster
FireEvent         # Grouped hotspots = one fire
    ↓ classify
ScoredFireEvent   # Has intent score + breakdown
    ↓ alert
Alert             # Dispatched notification record
```

### 1.4 Module Dependency Graph

```
config.py ◄──────────────── everything depends on config
    │
    ▼
db/models.py ◄────────────── data layer, no business logic
db/engine.py
    │
    ▼
ingestion/firms.py            # no internal deps (only config + models)
ingestion/weather.py          # no internal deps
ingestion/roads.py            # no internal deps
    │
    ▼
processing/dedup.py           # depends on: db
processing/clustering.py      # depends on: db
processing/classifier.py      # depends on: config (weights), db (history)
    │
    ▼
alerts/telegram.py            # depends on: config
alerts/whatsapp.py            # depends on: config
alerts/templates.py           # depends on: nothing (pure formatting)
alerts/dispatcher.py          # depends on: telegram, whatsapp, templates, db
    │
    ▼
pipeline.py                   # depends on: everything above (orchestrator)
    │
    ▼
main.py                       # depends on: pipeline, scheduler
dashboard/app.py              # depends on: db, config (separate process)
```

This dependency graph is why we can build the bottom layers in parallel.

---

## 2. Coding Agent Structure

### 2.1 Philosophy

Each coding agent is a Claude Code Task subagent that owns a **vertical slice** of the system. Agents at the same level have no dependencies on each other and can run **in parallel using worktree isolation**. Agents at later levels depend on earlier ones and run **sequentially**.

### 2.2 Build Phases

```
PHASE 0 ─── Foundation (sequential, sets up everything)
  │
  └── Agent 0: Scaffolder
      Creates: pyproject.toml, package structure, config.py,
               db/models.py, db/engine.py, shared types,
               conftest.py, YAML config files
      Why first: Everything else imports from here.

PHASE 1 ─── Data Clients (parallel, 3 agents in worktrees)
  │
  ├── Agent 1A: FIRMS Client
  │   Creates: ingestion/firms.py, tests/test_firms.py
  │   Owns: API polling, CSV parsing, rate limit handling
  │   Tests: Mocked HTTP responses, parse validation
  │
  ├── Agent 1B: Weather Client
  │   Creates: ingestion/weather.py, tests/test_weather.py
  │   Owns: Open-Meteo queries, grid caching, lightning proxy logic
  │   Tests: Mocked responses, CAPE threshold logic
  │
  └── Agent 1C: Roads Client
      Creates: ingestion/roads.py, tests/test_roads.py
      Owns: Overpass queries, distance calculation, road caching
      Tests: Mocked responses, distance math

PHASE 2 ─── Processing (parallel after Phase 1 merge, 3 agents)
  │
  ├── Agent 2A: Deduplication + Clustering
  │   Creates: processing/dedup.py, processing/clustering.py,
  │            tests/test_dedup.py, tests/test_clustering.py
  │   Owns: Spatial dedup (750m), temporal dedup, DBSCAN-like clustering
  │   Tests: Duplicate detection, cluster formation edge cases
  │
  ├── Agent 2B: Intent Classifier
  │   Creates: processing/classifier.py, tests/test_classifier.py
  │   Owns: 6-signal scoring engine, config-driven weights, threshold labels
  │   Tests: Score calculation, edge cases, weight validation
  │
  └── Agent 2C: Alert Templates
      Creates: alerts/templates.py, tests/test_templates.py
      Owns: Spanish-language alert formatting, Telegram markdown,
            WhatsApp plain text, Google Maps links
      Tests: Template rendering with sample fire events

PHASE 3 ─── Integration (parallel, 2 agents)
  │
  ├── Agent 3A: Alert System
  │   Creates: alerts/telegram.py, alerts/whatsapp.py,
  │            alerts/dispatcher.py, tests/test_alerts.py
  │   Owns: Bot setup, subscription CRUD, rate limiting,
  │          escalation logic, delivery tracking
  │   Tests: Dispatch logic, rate limit enforcement
  │
  └── Agent 3B: Pipeline Orchestrator
      Creates: pipeline.py, main.py, tests/test_pipeline.py
      Owns: Full cycle orchestration, stage error handling,
            APScheduler setup, metrics logging
      Tests: Pipeline with mocked stages, failure scenarios

PHASE 4 ─── Dashboard (sequential, 1 agent)
  │
  └── Agent 4: Dashboard
      Creates: dashboard/app.py, dashboard/pages/map.py,
               dashboard/pages/detail.py, dashboard/pages/admin.py,
               dashboard/components/fire_map.py
      Owns: Streamlit app, Folium map, filters, admin panel
      Tests: Manual (Streamlit doesn't unit test well)

PHASE 5 ─── Polish (sequential, 1 agent)
  │
  └── Agent 5: Integration Testing + Deploy
      Creates: scripts/seed_historical.py, scripts/deploy.sh,
               tests/test_integration.py, README.md
      Owns: End-to-end tests, deploy automation, documentation
```

### 2.3 Agent Execution Plan

```
Time ──────────────────────────────────────────────────▶

Phase 0:  [===== Agent 0: Scaffold =====]
                                          │
Phase 1:                                  ├─[== Agent 1A: FIRMS ==]
                                          ├─[== Agent 1B: Weather ==]
                                          └─[== Agent 1C: Roads ==]
                                                                    │
Phase 2:                                                            ├─[== Agent 2A: Dedup+Cluster ==]
                                                                    ├─[== Agent 2B: Classifier ==]
                                                                    └─[== Agent 2C: Templates ==]
                                                                                                    │
Phase 3:                                                                                            ├─[== Agent 3A: Alerts ==]
                                                                                                    └─[== Agent 3B: Pipeline ==]
                                                                                                                                │
Phase 4:                                                                                                                        [== Agent 4: Dashboard ==]
                                                                                                                                                          │
Phase 5:                                                                                                                                                  [== Agent 5: Polish ==]
```

**Total: 11 agent invocations across 6 phases.**
**Phases 1, 2, and 3 run 3, 3, and 2 agents in parallel respectively.**

### 2.4 Agent Prompt Strategy

Each agent receives:
1. **Context:** Read the PRD, CLAUDE.md, and relevant config/models from Phase 0
2. **Scope:** Exact files to create, exact interfaces to implement
3. **Contracts:** Input/output types they must conform to (defined in Phase 0)
4. **Tests:** Must write and pass tests before completing
5. **Constraints:** No modifications to files outside their scope

### 2.5 Merge Strategy

After each parallel phase:
1. Worktree agents produce branches
2. Main context reviews + merges each branch
3. Run full test suite to catch integration issues
4. Fix any conflicts before starting next phase

---

## 3. Shared Contracts (Defined in Phase 0)

These types are the "API" between agents. Defined once, used everywhere.

```python
# src/firesentinel/types.py

@dataclass
class RawHotspot:
    source: str               # VIIRS_SNPP_NRT, MODIS_NRT, etc.
    latitude: float
    longitude: float
    brightness: float         # bright_ti4 (VIIRS) or brightness (MODIS)
    brightness_2: float       # bright_ti5 (VIIRS) or bright_t31 (MODIS)
    frp: float                # fire radiative power (MW)
    confidence: str           # low, nominal, high (VIIRS) or 0-100 (MODIS)
    acq_date: date
    acq_time: str             # HHMM
    satellite: str            # N, N20, N21, T, A
    daynight: str             # D or N

@dataclass
class WeatherContext:
    cape: float               # J/kg
    convective_inhibition: float
    weather_code: int
    temperature_c: float
    wind_speed_kmh: float
    humidity_pct: float
    precipitation_mm_6h: float
    has_thunderstorm: bool    # weather_code in (95, 96, 99)

@dataclass
class RoadContext:
    nearest_distance_m: float
    nearest_road_type: str    # highway tag value
    nearest_road_ref: str | None  # e.g., "RP71"

@dataclass
class EnrichedHotspot:
    hotspot: RawHotspot
    weather: WeatherContext | None   # None if API failed
    road: RoadContext | None         # None if API failed

@dataclass
class IntentBreakdown:
    lightning_score: int      # 0-25
    road_score: int           # 0-20
    night_score: int          # 0-20
    history_score: int        # 0-15
    multi_point_score: int    # 0-10
    dry_conditions_score: int # 0-10
    total: int                # 0-100
    label: str                # natural, uncertain, suspicious, likely_intentional

@dataclass
class FireEvent:
    id: str
    center_lat: float
    center_lon: float
    hotspots: list[EnrichedHotspot]
    severity: str             # low, medium, high, critical
    max_frp: float
    first_detected: datetime
    intent: IntentBreakdown | None  # None until classified
```

---

## 4. Config Structure

```yaml
# config/monitoring.yml

monitoring:
  poll_interval_minutes: 15
  sources:
    - VIIRS_SNPP_NRT
    - VIIRS_NOAA20_NRT
    - VIIRS_NOAA21_NRT
    - MODIS_NRT
  bbox:
    full_patagonia: [-74, -50, -65, -38]
    crisis_corridor: [-72.1, -43.0, -71.2, -41.8]

intent_scoring:
  weights:
    lightning_absence: 25
    road_proximity: 20
    nighttime_ignition: 20
    historical_repeat: 15
    multi_point_ignition: 10
    dry_conditions: 10
  thresholds:
    natural: [0, 25]
    uncertain: [26, 50]
    suspicious: [51, 75]
    likely_intentional: [76, 100]
  road_distance_m:
    very_close: 200    # full score
    close: 500         # 75% score
    near: 1000         # 50% score
    moderate: 2000     # 25% score
  night_hours_local:
    peak: [22, 5]      # full score (UTC-3)
    shoulder: [5, 7]   # half score
    shoulder_evening: [20, 22]

zones:
  epuyen:
    center: [-42.22, -71.43]
    radius_km: 15
  cholila:
    center: [-42.52, -71.45]
    radius_km: 15
  el_bolson:
    center: [-41.96, -71.53]
    radius_km: 15
  los_alerces:
    center: [-42.80, -71.89]
    radius_km: 30
  # ... etc

clustering:
  spatial_radius_m: 1000
  temporal_window_hours: 2
  severity:
    low: [1, 2]        # hotspot count
    medium: [3, 5]
    high: [6, 9]
    critical: [10, null]  # 10+ or FRP > 100 MW
  critical_frp_threshold_mw: 100

dedup:
  spatial_tolerance_m: 750
  temporal_tolerance_minutes: 30

alerts:
  max_per_event_per_user: 3
  cooldown_hours: 6
  min_severity_default: medium

caching:
  weather_grid_degrees: 0.25
  weather_ttl_minutes: 60
  roads_grid_degrees: 0.1
  roads_ttl_hours: 24
```
