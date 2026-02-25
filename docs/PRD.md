# FireSentinel Patagonia - Product Requirements Document

**Version:** 1.0 (MVP)
**Date:** February 24, 2026
**Author:** Ezequiel Giganti
**Status:** Ready for Implementation

---

## 1. Executive Summary

FireSentinel Patagonia (FSP) is a remote, zero-hardware wildfire detection and intentionality analysis platform for the Argentine Patagonia crisis. It combines free satellite APIs (NASA FIRMS, Sentinel Hub), weather data (Open-Meteo), and geospatial intelligence (OpenStreetMap) to detect fires faster than official Argentine systems and classify the probability that a fire was intentionally set.

**Why now:** The 2025-2026 Patagonian fire season is the worst in 30+ years. Over 60,000 hectares of irreplaceable Andean-Patagonian forest have burned. Multiple fires have been confirmed intentional -- accelerants found at ignition sites, fires started from 3+ separate points simultaneously, and fires set at night near access roads. Argentina's fire management budget (SNMF) was cut 71% in real terms, and 25% of the remaining 2025 budget went unspent.

**What we build:** A Python backend that polls satellite data every 15-30 minutes, scores each hotspot for intentionality (0-100), and pushes alerts via Telegram AND WhatsApp to firefighters, park rangers, and citizens. A Streamlit dashboard provides real-time visualization.

**What we don't build:** No drones, no custom hardware, no mobile app (MVP). No ML model training (rule-based scoring first). No multi-agent AI framework (unnecessary complexity for MVP). No Sentinel-2 smoke analysis (Phase 2 -- revisit frequency too slow for real-time alerting).

**Target:** Operational prototype monitoring real fires within 4 weeks. First real users (Argentine brigadistas/ONGs) within 6 weeks.

**Positioning:** We are not competing on detection speed (Satellites On Fire claims 10 min; FIRMS has ~15-20 min inherent delay). We compete on *intelligence* -- we're the only public system that tells you not just WHERE a fire is, but WHETHER someone started it on purpose.

---

## 2. Problem Statement

### 2.1 The Crisis

| Metric | Value | Source |
|--------|-------|--------|
| Hectares burned (2025-2026 season) | 60,000+ | Buenos Aires Times |
| Worst season in | 30+ years | Bulletin of Atomic Scientists |
| Confirmed intentional fires | Multiple (Cholila, El Bolson 3-point ignition) | Rosario3, La Nacion |
| SNMF budget cut | 71% real terms vs prior year | Chequeado |
| Tourists evacuated | 3,000+ | Buenos Aires Herald |
| Residents displaced | 3,000+ | Al Jazeera |
| Ancient alerce trees threatened | 3,600+ year old specimens | IQAir |

### 2.2 Detection Gap

Argentina's official fire detection relies on CONAE (using MODIS at 1km resolution) and SNMF coordination. Current problems:

1. **Slow detection:** By the time official channels confirm a fire, it may have grown from controllable (<5 ha) to uncontrollable (>50 ha). The difference is often 30-90 minutes.
2. **No intentionality analysis:** Official systems detect heat, not intent. Intentional fires are investigated after the fact, not flagged in real-time.
3. **No citizen access:** Satellite data exists but isn't accessible to the people living near the fires -- brigadistas, park rangers, rural residents.
4. **Budget crisis:** With a 71% budget cut, the official system is understaffed and under-resourced.

### 2.3 What Exists Today

| System | Strengths | Gaps |
|--------|-----------|------|
| **NASA FIRMS** | Free, global, 375m VIIRS resolution | Raw data only. No alerting, no intent analysis, no localized context |
| **Satellites On Fire** (AR startup) | 30% more fires than FIRMS, 10-min delay, 35k users | Commercial/closed. No intent classification. No API for integrators |
| **CONAE Catalogo de Focos** | Official Argentine data since 2011 | MODIS only (1km), spreadsheet downloads, no real-time alerts |
| **Global Forest Watch** | Open data, email alerts, API | Slow (hours), no intent analysis, generic global tool |
| **INPE BDQueimadas** (Brazil) | 30 years of data, GOES geostationary | Brazil-focused, not optimized for Patagonia |
| **PyroNear** (open source) | Camera-based, deployed in France/Spain/Chile | Requires physical towers. Not satellite-based |

**Our gap:** No system combines satellite fire detection + intentionality scoring + localized alerts for Patagonia in a free, open, accessible way.

---

## 3. Vision

Be the definitive open-source wildfire intelligence platform for Patagonia -- the system that brigadistas, park rangers, and citizens trust to tell them not just *where* a fire is, but *whether someone started it on purpose*.

**Tagline:** "Ojos en el cielo. Alerta en tu celular."

### 3.1 Non-Goals (MVP)

- We are NOT building a mobile app
- We are NOT training custom ML models (rule-based intentionality scoring first)
- We are NOT replacing official fire services (we complement them)
- We are NOT using multi-agent AI frameworks (CrewAI, LangGraph, AutoGen) -- a well-structured Python application is simpler, faster to build, and easier to debug
- We are NOT processing Sentinel-2 imagery in MVP (Phase 2)
- We are NOT covering regions outside Patagonia in MVP

---

## 4. Users & Personas

### P1: Brigadista / Volunteer Firefighter (Primary)

- **Name archetype:** Juan, 34, volunteer brigadista in El Bolson
- **Context:** Gets called when a fire is reported. Often learns about fires from neighbors before official channels
- **Pain:** "Me entero cuando ya es tarde. Llego y ya son 20 hectareas."
- **Need:** Telegram alert with coordinates, fire intensity, and whether it looks intentional -- within 15 minutes of satellite detection
- **Access:** Smartphone with Telegram. No desktop during emergencies

### P2: Park Ranger / Defensa Civil Coordinator

- **Name archetype:** Maria, 42, ranger at Los Alerces National Park
- **Context:** Manages fire response for a 263,000 ha park with limited staff
- **Pain:** "Las herramientas oficiales son lentas y no me dicen si fue intencional."
- **Need:** Dashboard with map, filterable by severity and intentionality score. CSV/KML export for official reports
- **Access:** Desktop computer at ranger station, smartphone in the field

### P3: Citizen / Environmental Activist

- **Name archetype:** Lucia, 28, environmental activist in Buenos Aires
- **Context:** Follows Patagonian fires on social media, wants to amplify verified information
- **Pain:** "No se si lo que veo en Twitter es real o viejo."
- **Need:** Public dashboard showing current fires with verified satellite data. Shareable alert links
- **Access:** Desktop and mobile browser

### P4: Founder / System Administrator

- **Name archetype:** Ezequiel, operating remotely from Mexico
- **Context:** Builds and maintains the system solo. Needs to monitor system health 24/7
- **Need:** Admin panel with pipeline status, API health, error logs, and manual override controls
- **Access:** Desktop, SSH to server

---

## 5. Functional Requirements

### Epic 1: Data Ingestion Pipeline

The core pipeline that fetches, deduplicates, and stores satellite fire data.

**US-1.1: FIRMS Data Fetcher**
> As the system, I poll NASA FIRMS every 15 minutes for new hotspots in the Patagonia monitoring zone, so that new fires are detected as quickly as satellite data allows.

Acceptance Criteria:
- Queries all 3 VIIRS NRT sources: `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`
- Queries `MODIS_NRT` as supplementary source
- Uses bounding box `-74,-50,-65,-38` (full Patagonia) with focused monitoring on the crisis corridor `-72.1,-43.0,-71.2,-41.8`
- Parses CSV response into structured records: latitude, longitude, bright_ti4, bright_ti5, acq_date, acq_time, satellite, confidence, frp, daynight
- Deduplicates against existing records (same lat/lon within 750m + same acquisition time)
- Handles rate limits (5,000 transactions per 10-min window) with exponential backoff
- Stores raw data with ingestion timestamp
- Logs API response status, record count, and processing time

**US-1.2: Weather Data Enrichment**
> As the system, I fetch weather conditions for each new hotspot's location, so that the intentionality classifier can assess whether lightning was a plausible natural ignition source.

Acceptance Criteria:
- Queries Open-Meteo API for each hotspot location: CAPE, convective inhibition, weather code, temperature, wind speed, relative humidity, precipitation
- Queries both forecast and recent historical data (last 6 hours before fire detection)
- Flags thunderstorm activity using weather codes 95, 96, 99
- Uses CAPE > 1000 J/kg as secondary lightning risk indicator
- No authentication required (free tier: <10,000 calls/day)
- Caches weather data by grid cell (0.25 deg) to minimize API calls

**US-1.3: Road Proximity Enrichment**
> As the system, I calculate the distance from each hotspot to the nearest road/track, so that the intentionality classifier can assess human access.

Acceptance Criteria:
- Queries Overpass API for roads within 10km of each hotspot
- Includes highway types: track, path, tertiary, unclassified, secondary, primary
- Calculates minimum distance in meters from hotspot to nearest road segment
- Caches road network data by grid cell (0.1 deg) with 24-hour TTL
- Respects Overpass rate limits (timeout:25, queued requests)
- Returns distance + road type + road reference (e.g., "RP71", "RN40")

### Epic 2: Fire Detection & Classification

**US-2.1: Hotspot Filtering**
> As the system, I filter raw satellite detections to identify genuinely concerning fire events, reducing noise from false positives.

Acceptance Criteria:
- Filters by confidence: VIIRS `nominal` or `high` only (drops `low`)
- Filters by brightness: `bright_ti4 > 320 K` for high-confidence alerts, `> 300 K` for monitoring
- Flags known false-positive zones (industrial areas, volcanoes) using a configurable exclusion list
- Clusters nearby detections (within 1km radius, within 2 hours) into single fire events
- Assigns fire event severity: `low` (1-2 detections), `medium` (3-5), `high` (6+), `critical` (10+ or FRP > 100 MW)

**US-2.2: Historical Pattern Analysis**
> As the system, I compare new fire events against the historical record for that location, so that repeated-location fires are flagged.

Acceptance Criteria:
- Maintains rolling 3-year history of fire events per 1km grid cell
- Flags locations with 2+ fire events in the same grid cell within 12 months
- Calculates "repeat fire score" (0-100) based on frequency and recency
- Uses FIRMS historical data for initial seeding (available via archive API)

### Epic 3: Intentionality Classification

**US-3.1: Rule-Based Intent Scoring**
> As the system, I score each fire event 0-100 for probability of intentional origin, combining multiple evidence signals.

Scoring model (weights sum to 100):

| Signal | Weight | Score Logic |
|--------|--------|-------------|
| **No lightning activity** | 25 | 25 if no thunderstorm codes in 6h window AND CAPE < 500 J/kg; 15 if CAPE 500-1000; 0 if thunderstorm detected |
| **Road proximity** | 20 | 20 if < 200m from road; 15 if 200-500m; 10 if 500m-1km; 5 if 1-2km; 0 if > 2km |
| **Nighttime ignition** | 20 | 20 if detected between 22:00-05:00 local time; 10 if 05:00-07:00 or 20:00-22:00; 0 otherwise |
| **Historical repeat** | 15 | 15 if same 1km cell had fire in last 12 months; 10 if 12-24 months; 5 if 24-36 months; 0 otherwise |
| **Multiple ignition points** | 10 | 10 if 2+ separate fire clusters detected within 5km and 2 hours; 5 if within 10km |
| **Low humidity + no rain** | 10 | 10 if humidity < 25% AND no precipitation in 72h (conditions favor but don't cause fire); 5 if humidity 25-35% |

Classification thresholds:
- **0-25:** `natural` -- likely natural or accidental
- **26-50:** `uncertain` -- insufficient evidence
- **51-75:** `suspicious` -- multiple intentionality indicators
- **76-100:** `likely_intentional` -- strong intentionality pattern

**Graceful degradation:** When a data source fails (e.g., Open-Meteo down), the affected signal is excluded and remaining weights are renormalized to sum to 100. A fire scored on 4/6 signals with renormalized weights is more useful than a fire scored 0 because one API was down. The number of active signals is tracked in the breakdown (e.g., "scored on 5/6 signals").

**Calibration disclaimer:** All alerts and dashboard views include: "Modelo basado en patrones de incendios 2025-2026. No reemplaza investigacion forense." Initial weights are hypotheses calibrated against known intentional fires in the current season. They will be refined quarterly using user feedback (false positive/negative reports).

Acceptance Criteria:
- Every fire event gets a score within 2 minutes of detection
- Score breakdown is stored (individual signal scores + active signal count) for transparency
- Scores are recalculated when new data arrives (e.g., second satellite pass confirms multi-point ignition)
- When a signal's data source is unavailable, remaining signals are renormalized (not zeroed)
- Classification label, score, and signal count are included in all alerts and dashboard views

### Epic 4: Alerts & Notifications

**US-4.1: Telegram Alerts**
> As a brigadista, I receive a Telegram message when a new fire is detected in my area of interest, so that I can respond quickly.

Acceptance Criteria:
- Users subscribe via Telegram bot with `/start` command
- Users set area of interest: predefined zones (Los Alerces, Epuyen, Cholila, El Bolson, etc.) or custom coordinates + radius
- Users set minimum severity level for alerts (default: medium)
- Alert message includes:
  - Fire location (lat/lon + nearest town name)
  - Google Maps link to coordinates
  - Severity level and detection count
  - Intentionality score and label (e.g., "Intencionalidad: 72/100 - SOSPECHOSO")
  - Top contributing signals (e.g., "Sin rayos, cerca de ruta RP71, horario nocturno")
  - Satellite source and detection time
  - Dashboard link for full details
- Alerts sent within 5 minutes of fire event creation
- Rate-limited to max 1 alert per fire event per user (updates sent as edits, not new messages)

**US-4.2: WhatsApp Alerts (MVP)**
> As a brigadista, I receive WhatsApp alerts because that's what everyone in rural Patagonia uses.

Rationale: Argentine brigadistas use WhatsApp, not Telegram. This is not optional -- it's the primary channel for our primary users.

Acceptance Criteria:
- Uses Twilio WhatsApp Business API (free sandbox for testing, ~$0.005/msg in production)
- Same alert content as Telegram (plain text, no markdown)
- Opt-in via WhatsApp message to bot number
- MVP: Twilio sandbox (up to 5 numbers -- enough for initial validation with real brigadistas)
- Production (Week 5+): Apply for approved WhatsApp Business number

**US-4.3: Alert Escalation**
> As the system, I escalate alerts when fires grow or intentionality score increases.

Acceptance Criteria:
- Re-alerts if severity increases (e.g., medium -> high)
- Re-alerts if intentionality score crosses a threshold boundary (e.g., uncertain -> suspicious)
- Escalation alerts clearly marked as "ACTUALIZACION" with delta information
- Maximum 3 alerts per fire event per user in 6 hours

### Epic 5: Dashboard

**US-5.1: Public Fire Map**
> As a citizen, I see a real-time map of active fires in Patagonia with intentionality scores.

Acceptance Criteria:
- Streamlit web application, publicly accessible
- Interactive map (Folium/PyDeck) centered on Patagonia
- Fire markers color-coded: green (natural), yellow (uncertain), orange (suspicious), red (likely intentional)
- Marker popups show: location, time, severity, intentionality score + breakdown, satellite source
- Filters: date range, severity level, intentionality threshold, province
- Auto-refreshes every 5 minutes
- Mobile-responsive layout

**US-5.2: Fire Event Detail View**
> As a park ranger, I see full details of a fire event including all satellite detections, weather context, and scoring breakdown.

Acceptance Criteria:
- Clickable from map marker or event list
- Shows timeline of all satellite detections for this fire event
- Shows weather conditions at detection time
- Shows nearest roads with distances
- Shows intentionality score breakdown (table of signals + individual scores)
- Shows historical fire activity in same grid cell
- Export options: CSV (tabular data), KML (for Google Earth)

**US-5.3: Admin Panel**
> As the founder, I see system health, pipeline status, and can manually intervene.

Acceptance Criteria:
- Password-protected section of dashboard
- Shows: last successful API poll per source, record counts, error log (last 100)
- Shows: alert delivery stats (sent, delivered, failed per channel)
- Manual controls: force poll, pause alerts, add/remove exclusion zone
- Pipeline health: green/yellow/red status per component

---

## 6. Non-Functional Requirements

### 6.1 Performance

| Metric | Target | Rationale |
|--------|--------|-----------|
| Satellite poll interval | 15 min | FIRMS updates every few minutes for NRT data |
| End-to-end latency (satellite acquisition -> alert sent) | < 30 min | Satellite data has ~15-20 min inherent delay; our processing adds < 10 min |
| Intent scoring time | < 2 min per event | Weather + road API calls are the bottleneck |
| Dashboard load time | < 3 sec | Streamlit with cached data |

### 6.2 Reliability

| Metric | Target |
|--------|--------|
| Uptime | 99.5% (allows ~3.6 hours downtime/month) |
| Data loss | Zero -- all raw satellite data persisted before processing |
| Alert delivery rate | > 95% (Telegram) |
| Graceful degradation | If one data source fails, pipeline continues with remaining sources. Intent scoring renormalizes weights excluding failed signals. |

### 6.3 Cost

| Service | Cost | Tier |
|---------|------|------|
| NASA FIRMS | $0 | Free (5,000 req/10 min) |
| Open-Meteo | $0 | Free (<10,000 calls/day) |
| Overpass API | $0 | Free (public infrastructure) |
| Sentinel Hub | $0 | Free (10,000 PU/month) -- Phase 2 |
| Telegram Bot API | $0 | Free |
| Twilio WhatsApp | $0 - $15/mo | Sandbox free; production ~$0.005/msg |
| Hosting (Railway/Fly.io) | $5 - $15/mo | Hobby tier with persistent volume for SQLite |
| Domain + DNS | $10/year | Optional |
| **Total MVP** | **< $20/month** | |

### 6.4 Security

- All API keys stored in environment variables / secrets manager (never in code)
- Telegram bot token rotatable
- Admin panel behind authentication (password or OAuth)
- No PII collected (user = Telegram chat ID only)
- Rate limiting on dashboard endpoints
- HTTPS enforced on all public endpoints

### 6.5 Scalability Path

MVP is designed for Patagonia only. Architecture choices that enable future scaling:

- Bounding box is configurable (not hardcoded)
- Scoring weights are configurable (YAML/JSON, not hardcoded)
- Alert channels are pluggable (interface pattern)
- Database schema supports multiple monitoring regions
- All coordinates stored in standard WGS84

---

## 7. Technical Architecture

### 7.1 System Overview

```
                    ┌─────────────────────────────────────┐
                    │         External Data Sources         │
                    │                                       │
                    │  NASA FIRMS    Open-Meteo   Overpass  │
                    │  (satellite)   (weather)    (roads)   │
                    └───────┬───────────┬───────────┬───────┘
                            │           │           │
                    ┌───────▼───────────▼───────────▼───────┐
                    │          Ingestion Layer               │
                    │                                        │
                    │  FIRMS Fetcher  Weather    Road        │
                    │  (every 15min)  Enricher   Enricher    │
                    └───────────────────┬────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │          Processing Layer               │
                    │                                         │
                    │  Deduplicator → Clusterer → Classifier  │
                    │  (fire events)   (grouping) (intent)    │
                    └───────────────────┬─────────────────────┘
                                        │
                    ┌───────────────────▼────────────────────┐
                    │          Storage Layer                   │
                    │                                         │
                    │  PostgreSQL (Supabase) or SQLite        │
                    │  hotspots | fire_events | alerts | config│
                    └──────┬────────────────────────┬─────────┘
                           │                        │
              ┌────────────▼──────────┐  ┌──────────▼──────────┐
              │    Alert Dispatcher    │  │     Dashboard        │
              │                        │  │                      │
              │  Telegram Bot          │  │  Streamlit App       │
              │  WhatsApp (Twilio)     │  │  Public Map          │
              │  Email (future)        │  │  Admin Panel         │
              └────────────────────────┘  └──────────────────────┘
```

### 7.2 Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Language | Python 3.12 | Best ecosystem for geospatial + data + APIs |
| Project mgmt | Poetry | Dependency management + virtual envs |
| HTTP client | httpx | Async support, connection pooling, timeout handling |
| Geospatial | geopandas, shapely | Industry standard for geo operations |
| Database | SQLite (dev AND prod MVP) | Zero-config, handles millions of rows, no external dependency. Move to PostgreSQL only if concurrent access at scale demands it |
| ORM | SQLAlchemy 2.0 | Type-safe, async support |
| Scheduler | APScheduler | In-process scheduling, cron-like |
| Dashboard | Streamlit | Fastest path to interactive map dashboard |
| Maps | Folium (Leaflet.js) | Interactive maps with minimal code |
| Telegram | python-telegram-bot | Mature, async, well-documented |
| WhatsApp | twilio | Official SDK |
| Config | Pydantic Settings | Type-safe config from env vars |
| Testing | pytest + pytest-asyncio | Standard Python testing |
| Linting | ruff | Fast, replaces flake8/isort/black |
| Type checking | mypy | Strict type safety |
| Hosting | Railway or Fly.io | Cheap, easy deployment, always-on |

### 7.3 Database Schema (Core Tables)

```
hotspots
├── id (UUID, PK)
├── source (enum: VIIRS_SNPP, VIIRS_NOAA20, VIIRS_NOAA21, MODIS)
├── latitude (float)
├── longitude (float)
├── brightness (float) -- bright_ti4 for VIIRS, brightness for MODIS
├── brightness_2 (float) -- bright_ti5 for VIIRS, bright_t31 for MODIS
├── frp (float) -- fire radiative power in MW
├── confidence (string) -- low/nominal/high
├── acq_date (date)
├── acq_time (time) -- HHMM format from satellite
├── daynight (enum: D, N)
├── satellite (string) -- N, N20, N21, T, A
├── fire_event_id (FK -> fire_events, nullable)
├── ingested_at (timestamp)
├── raw_data (JSON) -- full original record
└── UNIQUE(source, latitude, longitude, acq_date, acq_time)

fire_events
├── id (UUID, PK)
├── center_lat (float) -- centroid of clustered hotspots
├── center_lon (float)
├── province (string) -- Chubut, Rio Negro, Neuquen, etc.
├── nearest_town (string)
├── severity (enum: low, medium, high, critical)
├── hotspot_count (int)
├── max_frp (float)
├── first_detected_at (timestamp)
├── last_updated_at (timestamp)
├── intent_score (int, 0-100)
├── intent_label (enum: natural, uncertain, suspicious, likely_intentional)
├── intent_breakdown (JSON) -- {lightning: 25, road: 15, night: 20, ...}
├── weather_data (JSON) -- cached weather at detection time
├── nearest_road_m (float) -- distance in meters
├── nearest_road_type (string) -- highway tag value
├── nearest_road_ref (string) -- e.g., "RP71"
├── is_active (boolean)
└── resolved_at (timestamp, nullable)

alert_subscriptions
├── id (UUID, PK)
├── channel (enum: telegram, whatsapp, email)
├── channel_id (string) -- telegram chat_id, phone number, email
├── zone (string) -- predefined zone name or "custom"
├── custom_lat (float, nullable)
├── custom_lon (float, nullable)
├── custom_radius_km (float, nullable)
├── min_severity (enum: low, medium, high, critical)
├── is_active (boolean)
├── created_at (timestamp)
└── language (enum: es, en) -- default: es

alerts_sent
├── id (UUID, PK)
├── fire_event_id (FK -> fire_events)
├── subscription_id (FK -> alert_subscriptions)
├── channel (enum: telegram, whatsapp, email)
├── message_content (text)
├── sent_at (timestamp)
├── delivered (boolean)
├── is_escalation (boolean)
└── error (text, nullable)

exclusion_zones
├── id (UUID, PK)
├── name (string) -- e.g., "Aluminium plant Madryn"
├── latitude (float)
├── longitude (float)
├── radius_km (float)
├── reason (string)
└── is_active (boolean)
```

### 7.4 Project Structure

```
FireSentinel/
├── docs/
│   ├── PRD.md
│   └── BUILD_PLAN.md
├── config/
│   └── monitoring.yml          -- scoring weights, zones, thresholds (version-controlled)
├── src/
│   └── firesentinel/
│       ├── __init__.py
│       ├── core/
│       │   ├── __init__.py
│       │   ├── types.py         -- shared dataclasses (contracts between modules)
│       │   ├── pipeline.py      -- orchestrator (runs the full cycle)
│       │   └── scheduler.py     -- APScheduler setup
│       ├── config.py            -- Pydantic settings from env vars + YAML
│       ├── main.py              -- entry point
│       ├── db/
│       │   ├── __init__.py
│       │   ├── models.py        -- SQLAlchemy models
│       │   └── engine.py        -- DB connection
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── firms.py         -- NASA FIRMS API client
│       │   ├── weather.py       -- Open-Meteo API client
│       │   └── roads.py         -- Overpass API client
│       ├── processing/
│       │   ├── __init__.py
│       │   ├── dedup.py         -- Hotspot deduplication
│       │   ├── clustering.py    -- Fire event clustering
│       │   └── classifier.py    -- Intentionality scoring
│       ├── alerts/
│       │   ├── __init__.py
│       │   ├── dispatcher.py    -- Alert routing + rate limiting
│       │   ├── telegram.py      -- Telegram bot
│       │   ├── whatsapp.py      -- Twilio WhatsApp
│       │   └── templates.py     -- Alert message formatting (Spanish)
│       └── dashboard/
│           ├── __init__.py
│           ├── app.py           -- Streamlit main app
│           ├── pages/
│           │   ├── map.py       -- Public fire map
│           │   ├── detail.py    -- Fire event detail
│           │   └── admin.py     -- Admin panel
│           └── components/
│               ├── fire_map.py  -- Folium map component
│               └── charts.py    -- Metrics visualizations
├── tests/
│   ├── conftest.py
│   ├── test_firms.py
│   ├── test_weather.py
│   ├── test_roads.py
│   ├── test_clustering.py
│   ├── test_classifier.py
│   └── test_alerts.py
├── scripts/
│   ├── seed_historical.py      -- Seed DB with FIRMS archive data
│   └── deploy.sh
├── pyproject.toml
├── CLAUDE.md
├── README.md
└── .env.example
```

Key structural decisions:
- **`core/types.py`**: Shared dataclasses imported by all modules. Defines the contracts between ingestion, processing, and alerts. No module imports from a peer -- they all speak through these types.
- **`config/monitoring.yml`**: Scoring weights, zone definitions, clustering thresholds -- all version-controlled YAML. No magic numbers in Python code.
- **`core/pipeline.py`**: The orchestrator. Calls ingestion → processing → alerts in sequence. Handles per-stage errors so one failure doesn't kill the cycle.
- **Dependency rule**: `ingestion/` never imports from `processing/`. `processing/` never imports from `alerts/`. All communicate through `core/types.py` and the database.

---

## 8. Intentionality Scoring Deep Dive

This is the core differentiator of FireSentinel. No other public system does this.

### 8.1 Evidence Signals

**Signal 1: Lightning Absence (weight: 25)**

Patagonian fires have two natural ignition sources: lightning and (rarely) volcanic activity. If neither is present, human origin is almost certain.

- Query Open-Meteo for the 6-hour window before detection
- Weather codes 95 (thunderstorm), 96 (thunderstorm + hail), 99 (severe thunderstorm) indicate lightning
- CAPE (Convective Available Potential Energy) > 1000 J/kg indicates thunderstorm risk even without confirmed strikes
- **Limitation:** Open-Meteo doesn't have direct lightning strike data for Patagonia. CAPE + weather codes are proxies. Phase 2: integrate Blitzortung.org or GOES-16 GLM for direct lightning data

**Signal 2: Road Proximity (weight: 20)**

Intentional fires require human access. A fire 50m from a dirt road is more suspicious than one 5km deep in roadless wilderness.

- Query Overpass API for `highway=track|path|tertiary|unclassified|secondary|primary` within 10km
- Argentina-specific: `track` roads (access to rural properties, usually dirt) are the most relevant -- these are the roads arsonists use
- Distance thresholds calibrated to Patagonian terrain (dense forest limits off-road access)

**Signal 3: Nighttime Ignition (weight: 20)**

Many intentional fires in Patagonia are set at night to avoid detection. The Cholila and El Bolson fires were detected in nighttime satellite passes.

- Uses `daynight` field from FIRMS data (D/N) + `acq_time` for precise timing
- Converts to local Argentina time (UTC-3)
- Peak suspicion: 22:00-05:00 local (when legitimate agricultural burns don't happen)

**Signal 4: Historical Repeat Location (weight: 15)**

Intentional fires sometimes target the same areas repeatedly -- land-clearing operations, disputes, or revenge arson.

- Check 3-year FIRMS archive for same 1km grid cell
- Recent repeats (< 12 months) score highest
- This signal is weak alone but powerful combined with others

**Signal 5: Multiple Simultaneous Ignition Points (weight: 10)**

The El Bolson fire (January 30, 2026) was confirmed to have started from 3 separate ignition points. Multiple simultaneous ignitions are a strong arson indicator.

- Detect 2+ new fire clusters within 5km and 2 hours of each other
- Must be genuinely separate clusters (not one fire's spread)

**Signal 6: Dry Conditions Without Precipitation (weight: 10)**

Extreme dryness doesn't cause fires, but intentional fires are more likely when conditions guarantee rapid spread.

- Humidity < 25% AND no precipitation in 72 hours
- This is a "force multiplier" signal -- it doesn't indicate intent alone, but arsonists choose these conditions deliberately

### 8.2 Scoring Calibration

Initial weights are based on the confirmed intentional fire patterns in the 2025-2026 season. As we accumulate data and receive feedback from users (false positive/negative reports), we will:

1. Track which signals contribute most to fires later confirmed as intentional
2. Adjust weights quarterly
3. Phase 2: replace rule-based scoring with a lightweight ML model (logistic regression or gradient boosted trees) trained on labeled data

### 8.3 Known Limitations

- **False positives on intent:** Agricultural burns near roads at night will score high. We mitigate with exclusion zones and user feedback
- **False negatives on intent:** A sophisticated arsonist who starts a fire far from roads during a thunderstorm won't be flagged
- **Lightning proxy:** Without direct lightning strike data, we may under-score intent when CAPE is high but no actual lightning occurred
- **No smoke analysis in MVP:** Sentinel-2 imagery could detect smoke before thermal hotspots appear, but this is Phase 2

---

## 9. Predefined Monitoring Zones

Based on the 2025-2026 crisis geography:

| Zone ID | Name | Center Lat | Center Lon | Radius (km) | Province |
|---------|------|-----------|-----------|-------------|----------|
| `epuyen` | Epuyen | -42.22 | -71.43 | 15 | Chubut |
| `cholila` | Cholila | -42.52 | -71.45 | 15 | Chubut |
| `el_hoyo` | El Hoyo | -42.07 | -71.52 | 10 | Chubut |
| `el_bolson` | El Bolson | -41.96 | -71.53 | 15 | Rio Negro |
| `los_alerces` | Los Alerces NP | -42.80 | -71.89 | 30 | Chubut |
| `lago_puelo` | Lago Puelo NP | -42.10 | -71.60 | 15 | Chubut |
| `bariloche` | San Carlos de Bariloche | -41.13 | -71.31 | 25 | Rio Negro |
| `esquel` | Esquel | -42.91 | -71.32 | 15 | Chubut |
| `all_patagonia` | Full Patagonia | -44.00 | -69.50 | 500 | All |

---

## 10. MVP Roadmap (4 Weeks)

### Week 1: Foundation + Data Pipeline

| Day | Task | Deliverable |
|-----|------|-------------|
| 1 | Project setup: Poetry, structure, CLAUDE.md, CI config | Buildable project skeleton |
| 1-2 | Database models + SQLite setup | Working schema with migrations |
| 2-3 | FIRMS API client + tests | Fetching real hotspot data |
| 3-4 | Deduplication + fire event clustering | Hotspots grouped into fire events |
| 5 | Historical data seeding script | 3-year FIRMS archive loaded |

**Week 1 milestone:** `python -m firesentinel.main` fetches real satellite data and stores it locally.

### Week 2: Enrichment + Intent Scoring

| Day | Task | Deliverable |
|-----|------|-------------|
| 1-2 | Open-Meteo weather client + tests | Weather data per hotspot |
| 2-3 | Overpass road proximity client + tests | Road distance per hotspot |
| 3-4 | Intentionality classifier + tests | Score 0-100 per fire event |
| 5 | Integration: pipeline runs end-to-end | Scored fire events in DB |

**Week 2 milestone:** Each fire event has an intentionality score with breakdown. Run against live data to validate.

### Week 3: Alerts + Dashboard

| Day | Task | Deliverable |
|-----|------|-------------|
| 1 | Alert templates (Spanish) + dispatcher (routing, rate limiting, escalation) | Alert pipeline core |
| 2 | Telegram bot (subscribe, alert delivery) | Working Telegram alerts |
| 2-3 | WhatsApp via Twilio sandbox (subscribe, alert delivery) | Working WhatsApp alerts (5 numbers) |
| 3-5 | Streamlit dashboard (map + filters + detail view) | Public dashboard live |

**Week 3 milestone:** Both Telegram AND WhatsApp alerts firing on real fire detections. Dashboard showing live map.

### Week 4: Polish + Deploy

| Day | Task | Deliverable |
|-----|------|-------------|
| 1-2 | Admin panel + system health monitoring | Founder can monitor/control system |
| 2-3 | Deploy to production (Railway/Fly.io) | System running 24/7 |
| 3-4 | End-to-end testing with real fire data | Validated against known fires |
| 4-5 | Documentation, README, onboarding flow | Ready for first external users |

**Week 4 milestone:** System running 24/7 in production, monitoring real Patagonian fires, sending real alerts.

---

## 11. Phase 2 Roadmap (Weeks 5-10)

| Feature | Description | Priority |
|---------|-------------|----------|
| User Feedback Loop | "Was this fire intentional?" button in dashboard + Telegram/WhatsApp. Critical for scoring calibration | High |
| Direct Lightning Data | Integrate Blitzortung.org or GOES-16 GLM -- eliminates the weakest signal in intent scoring | High |
| WhatsApp Production | Apply for approved WhatsApp Business number (removes 5-number sandbox limit) | High |
| Sentinel-2 Smoke Detection | NBR + SWIR analysis for post-fire burn severity. NOT real-time (2-3 day revisit) | Medium |
| ML Intent Classifier | Replace rules with trained model using labeled feedback from user feedback loop | Medium |
| Email Alerts + Weekly Digest | For park rangers and institutional users | Medium |
| REST API | Public API for third-party integrations | Low |
| Multi-region Support | Chile (Araucania), Bolivia, California | Low |

---

## 12. Success Metrics

### MVP (Week 4)

| Metric | Target | How Measured |
|--------|--------|-------------|
| Fire detection coverage | 100% of FIRMS-detected fires in monitoring zones | Compare our events vs raw FIRMS data |
| Intent scoring | Every fire event scored within 2 min | Pipeline logs |
| Alert delivery | < 30 min end-to-end latency | Timestamp delta: satellite acq_time -> alert sent_at |
| Uptime | 99%+ | Monitoring (UptimeRobot or similar) |
| False positive rate | < 20% of "suspicious+" alerts | Manual review of first 50 alerts |

### Month 2

| Metric | Target | How Measured |
|--------|--------|-------------|
| Active alert subscribers | 15-30 (brigadistas, rangers, activists) | DB count |
| Dashboard daily visitors | 50+ | Streamlit analytics |
| Real fires reported early | At least 1 fire where our alert preceded official channels | User feedback + timestamp comparison |
| Institutional interest | 1-2 conversations with Argentine fire/park authorities | Outreach tracking |

### Month 3-6

| Metric | Target | How Measured |
|--------|--------|-------------|
| Active subscribers | 100+ | DB count |
| Institutional partnerships | 1+ formal agreement with municipality/province/park | Signed document |
| Media coverage | 1+ article or social media amplification | Tracking |
| Hectares potentially saved | Qualitative estimate from early detections | User reports |

---

## 13. Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-------------|--------|------------|
| FIRMS API goes down or changes | Low | High | Cache last 24h of data. Abstract API client for easy swap. Monitor API health |
| High false positive rate in intent scoring | Medium | Medium | Conservative initial thresholds (alert only on "suspicious+"). User feedback loop. Exclusion zones |
| Low adoption by Argentine users | Medium | High | Partner with 1-2 known environmental orgs (Greenpeace AR, FARN). Spanish-first UX. WhatsApp support critical |
| Overpass API rate limited | Medium | Low | Aggressive caching (24h TTL for road data). Roads don't change frequently |
| Legal concerns from Argentine authorities | Low | Medium | Clear disclaimer: "Sistema ciudadano de informacion. No reemplaza canales oficiales." Only use public data |
| Server costs exceed budget | Low | Low | Start with SQLite + free tiers. Upgrade only when needed |
| Satellite data delayed during active fire | Medium | Medium | Use all 4 sources (3 VIIRS + MODIS). Monitor data availability endpoint |
| Founder burnout (solo developer) | Medium | High | Automate everything. MVP-only scope. No feature creep. Recruit 1-2 open source contributors post-launch |

---

## 14. Decisions Made

Resolved from original open questions:

1. **Telegram vs WhatsApp:** BOTH in MVP. WhatsApp is the primary channel (it's what brigadistas use). Telegram is secondary but free and easier to build first. Week 3 delivers both.
2. **Language:** Spanish-only for all user-facing content (alerts, dashboard, bot commands). English for code, comments, and developer docs. No bilingual complexity in MVP.
3. **Open source:** Public from day 1 on GitHub. Trust and transparency matter more than competitive moats for a citizen tool. MIT license.
4. **Monetization:** Not in MVP. Not in Phase 2. Focus entirely on impact and adoption first. Monetization discussion starts only after 100+ active users and at least 1 institutional partnership.
5. **Database:** SQLite for both dev and prod MVP. A single file on a persistent volume handles this workload. PostgreSQL migration only if concurrent dashboard access becomes a bottleneck.
6. **Scoring confidence:** All intent scores include signal count (e.g., "5/6 signals") and calibration disclaimer. We show breakdowns, not just labels, to avoid political controversy.

## 15. Remaining Open Questions

1. **Blitzortung.org data quality:** Is the community lightning network reliable enough for Patagonia, or do we need GOES-16 GLM? (Research in Phase 2, before integrating)
2. **Local champion:** Who is the first Argentine brigadista/ranger we can partner with for testing? (Outreach task, not engineering)
3. **Hosting region:** Railway US-West vs EU? Latency to Argentina matters for dashboard but not for alerts. Need to benchmark.

---

## 15. References

### Data Sources
- [NASA FIRMS API](https://firms.modaps.eosdis.nasa.gov/api/) - Free fire hotspot data
- [FIRMS Data Attributes (VIIRS)](https://www.earthdata.nasa.gov/data/tools/firms/active-fire-data-attributes-modis-viirs) - Field definitions
- [Open-Meteo API](https://open-meteo.com/en/docs) - Free weather data
- [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API) - OpenStreetMap road queries
- [Argentina Highways (OSM)](https://wiki.openstreetmap.org/wiki/Argentina/Highways) - Road classification
- [Copernicus/Sentinel Hub](https://dataspace.copernicus.eu/) - Satellite imagery (Phase 2)

### Crisis Context
- [Buenos Aires Times: 60,000 hectares destroyed](https://batimes.com.ar/news/argentina/firefighters-battle-blazes-in-patagonia-60000-hectares-destroyed.phtml)
- [Bulletin of Atomic Scientists: Why Patagonia Burns](https://thebulletin.org/2026/02/why-patagonia-burns-every-summer/)
- [Chequeado: 25% fire budget unspent](https://chequeado.com/el-explicador/incendios-en-la-patagonia/)
- [Rosario3: Confirmed intentional fires](https://www.rosario3.com/informaciongeneral/la-patagonia-arrasada-por-el-fuego/)
- [Greenpeace Argentina: Tragic summer](https://www.greenpeace.org/argentina/blog/problemas/bosques/otro-verano-tragico-en-patagonia/)

### Competitive Intelligence
- [Satellites On Fire](https://www.satellitesonfire.com/en) - Argentine commercial platform
- [CONAE Fire Catalog](https://catalogos5.conae.gov.ar/catalogoFocos/) - Official Argentine data
- [Global Forest Watch](https://www.globalforestwatch.org/) - WRI open platform
- [PyroNear](https://github.com/pyronear) - Open source camera-based detection
- [INPE BDQueimadas](http://queimadas.dgi.inpe.br/queimadas/bdqueimadas/) - Brazilian fire monitoring

### Technical References
- [FIRMS API Usage Guide](https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html)
- [FIRMS FAQ](https://www.earthdata.nasa.gov/data/tools/firms/faq)
- [Sentinel-2 NBR Script](https://custom-scripts.sentinel-hub.com/custom-scripts/sentinel-2/nbr/)
- [Overpass QL Documentation](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL)
