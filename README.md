# FireSentinel Patagonia

> Satellite-based wildfire detection and intentionality analysis for Argentine Patagonia.

FireSentinel is an open-source intelligence platform that combines NASA satellite data, weather analysis, and geospatial context to detect wildfires in Patagonia and assess the probability that each fire was intentionally set. It sends real-time alerts in Spanish via Telegram and WhatsApp to firefighters, park rangers, and citizens -- the people closest to the crisis who need information fastest.

## The Problem

The 2025-2026 Patagonian fire season is the worst in over 30 years. More than 60,000 hectares of irreplaceable Andean-Patagonian forest have burned, including ancient alerce trees over 3,600 years old. Over 3,000 residents have been displaced and 3,000 tourists evacuated. Multiple fires have been confirmed intentional -- investigators found accelerants at ignition sites, and the El Bolson fire started from three separate points simultaneously.

Argentina's national fire management agency (SNMF) saw its budget cut by 71% in real terms, with 25% of remaining funds going unspent. Official detection systems rely on MODIS at 1km resolution with no real-time alerting and no intentionality analysis. By the time official channels confirm a fire, it may have grown from a controllable 5 hectares to an uncontrollable 50+.

No existing public system combines satellite fire detection with intentionality scoring and localized alerts for Patagonia. NASA FIRMS provides the raw data but no analysis. Argentina's CONAE catalog is MODIS-only with spreadsheet downloads. The commercial platform Satellites On Fire (35,000 users) is closed-source with no intent classification. FireSentinel fills this gap as a free, open, citizen-built tool.

## What FireSentinel Does

- **Polls NASA FIRMS every 15 minutes** across 4 satellite sources (VIIRS SNPP, NOAA-20, NOAA-21, MODIS) at 375m resolution
- **Scores fire intentionality 0-100** using 6 independent evidence signals with configurable weights
- **Sends alerts via Telegram and WhatsApp** in Spanish to brigadistas and park rangers within minutes of detection
- **Deduplicates and clusters** satellite detections into unified fire events with severity levels
- **Enriches each detection** with weather data (Open-Meteo) and road proximity (OpenStreetMap Overpass API)
- **Degrades gracefully** when any data source is unavailable -- scores are renormalized, never zeroed
- **Interactive Streamlit dashboard** with Folium maps, filtering by severity and intentionality
- **All user-facing content in Spanish** -- alerts, labels, dashboard, bot commands

## Intentionality Scoring

Each fire event receives a score from 0 to 100 based on six evidence signals. When a data source is unavailable, remaining signals are renormalized so scores stay meaningful.

| Signal | Weight | What It Measures |
|--------|--------|------------------|
| Lightning Absence | 25 | No thunderstorm/CAPE activity in 6h window -- eliminates natural ignition |
| Road Proximity | 20 | Distance to nearest road/track -- closer means easier human access |
| Nighttime Ignition | 20 | Detection between 22:00-05:00 local -- legitimate burns don't happen at night |
| Historical Repeat | 15 | Prior fires in same 1km grid cell within 3 years -- land-clearing pattern |
| Multiple Ignition Points | 10 | 2+ separate fire clusters within 5km and 2 hours -- strong arson indicator |
| Dry Conditions | 10 | Low humidity (<25%) with no rain in 72h -- arsonists choose these conditions |

**Classification thresholds:**

| Score | Label | Meaning |
|-------|-------|---------|
| 0-25 | Natural | Likely natural or accidental origin |
| 26-50 | Uncertain | Insufficient evidence to determine |
| 51-75 | Suspicious | Multiple intentionality indicators present |
| 76-100 | Likely Intentional | Strong pattern matching known arson cases |

All scores include a calibration disclaimer: *"Modelo basado en patrones de incendios 2025-2026. No reemplaza investigacion forense."*

## Quick Start

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation) package manager
- A free [NASA FIRMS API key](https://firms.modaps.eosdis.nasa.gov/api/map_key/)

### Installation

```bash
# Clone the repository
git clone https://github.com/egiganti/FireSentinel.git
cd FireSentinel

# Install dependencies
poetry install

# Copy environment template and add your API key
cp .env.example .env
# Edit .env and set FIRMS_MAP_KEY=your_key_here
```

### Configuration

Edit `.env` with your credentials:

```bash
FIRMS_MAP_KEY=your_nasa_firms_key     # Required
TELEGRAM_BOT_TOKEN=your_bot_token     # Optional for alerts
ADMIN_PASSWORD=your_admin_password    # Optional for dashboard admin
ENVIRONMENT=dev                        # dev | staging | prod
```

### Run

```bash
# Run a single pipeline cycle (fetch, analyze, score)
poetry run python -m firesentinel.main --once

# Start continuous monitoring (polls every 15 minutes)
poetry run python -m firesentinel.main

# Launch the dashboard
poetry run streamlit run src/firesentinel/dashboard/app.py
```

## Architecture

```
                    +-------------------------------------+
                    |       External Data Sources          |
                    |  NASA FIRMS   Open-Meteo   Overpass  |
                    +--------+----------+---------+-------+
                             |          |         |
                    +--------v----------v---------v-------+
                    |         Ingestion Layer              |
                    |  FIRMS Fetcher  Weather   Road       |
                    |  (every 15min)  Enricher  Enricher   |
                    +-----------------+-------------------+
                                      |
                    +-----------------v-------------------+
                    |        Processing Layer              |
                    |  Deduplicator > Clusterer > Classifier|
                    +-----------------+-------------------+
                                      |
                    +-----------------v-------------------+
                    |         Storage Layer                |
                    |     SQLite (async via aiosqlite)     |
                    +--------+-------------------+--------+
                             |                   |
                +------------v------+  +---------v--------+
                | Alert Dispatcher  |  |    Dashboard     |
                | Telegram/WhatsApp |  | Streamlit+Folium |
                +-------------------+  +------------------+
```

**Tech stack:** Python 3.12, SQLAlchemy 2.0 (async), httpx, Streamlit, Folium, APScheduler, Pydantic Settings, ruff, mypy, pytest.

## Dashboard

The Streamlit dashboard provides three views:

- **Public Fire Map** -- Interactive Folium map with color-coded markers (green=natural, yellow=uncertain, orange=suspicious, red=likely intentional). Filterable by date, severity, and intentionality threshold.
- **Fire Event Detail** -- Full breakdown of a fire event: satellite detection timeline, weather conditions, road proximity, intent score breakdown with individual signal scores, and historical activity.
- **Admin Panel** -- Password-protected system health view with pipeline status, API health indicators, error logs, and manual controls.

## Configuration

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FIRMS_MAP_KEY` | Yes | NASA FIRMS API key |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token for alerts |
| `TWILIO_ACCOUNT_SID` | No | Twilio account for WhatsApp |
| `TWILIO_AUTH_TOKEN` | No | Twilio auth token |
| `TWILIO_WHATSAPP_FROM` | No | Twilio WhatsApp sender number |
| `ADMIN_PASSWORD` | No | Dashboard admin panel password |
| `ENVIRONMENT` | No | `dev` / `staging` / `prod` (default: dev) |
| `DB_PATH` | No | SQLite file path (default: ./data/firesentinel.db) |

### monitoring.yml

All scoring weights, thresholds, monitoring zones, and clustering parameters are configured in `config/monitoring.yml`. No magic numbers in Python code -- everything is version-controlled YAML.

## Monitoring Zones

Nine predefined zones covering the 2025-2026 crisis geography:

| Zone | Center | Radius | Province |
|------|--------|--------|----------|
| Epuyen | -42.22, -71.43 | 15 km | Chubut |
| Cholila | -42.52, -71.45 | 15 km | Chubut |
| El Hoyo | -42.07, -71.52 | 10 km | Chubut |
| El Bolson | -41.96, -71.53 | 15 km | Rio Negro |
| Los Alerces NP | -42.80, -71.89 | 30 km | Chubut |
| Lago Puelo NP | -42.10, -71.60 | 15 km | Chubut |
| Bariloche | -41.13, -71.31 | 25 km | Rio Negro |
| Esquel | -42.91, -71.32 | 15 km | Chubut |
| All Patagonia | -44.00, -69.50 | 500 km | All |

## Development

```bash
# Install all dependencies (including dev)
poetry install

# Run tests
poetry run pytest tests/ -x -q

# Run linter
poetry run ruff check src/ tests/

# Check formatting
poetry run ruff format --check src/ tests/

# Type checking
poetry run mypy src/

# Run a single pipeline cycle
poetry run python -m firesentinel.main --once

# Seed historical data (requires FIRMS_MAP_KEY)
poetry run python scripts/seed_historical.py --days 365
```

### Pre-Deploy Checklist

1. `poetry run ruff check src/ tests/`
2. `poetry run ruff format --check src/ tests/`
3. `poetry run mypy src/`
4. `poetry run pytest tests/ -x -q`
5. Security review (no keys in code, no injection vectors)
6. Commit, push, deploy

## Deployment

### Systemd Service

Copy the service file and configure:

```bash
# Copy service file
sudo cp scripts/systemd/firesentinel.service /etc/systemd/system/

# Edit to add your API keys
sudo systemctl edit firesentinel
# Add: Environment=FIRMS_MAP_KEY=your_key

# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable firesentinel
sudo systemctl start firesentinel

# Check status
sudo systemctl status firesentinel
journalctl -u firesentinel -f
```

### Deploy Script

```bash
# Set required environment variables
export FIRMS_MAP_KEY=your_key

# Run the deploy script
./scripts/deploy.sh
```

The deploy script pulls latest code, installs dependencies, runs lint and tests, initializes the database, and restarts the systemd service.

## Roadmap

### Phase 2 (Planned)

| Feature | Description | Priority |
|---------|-------------|----------|
| User Feedback Loop | "Was this fire intentional?" button for scoring calibration | High |
| Direct Lightning Data | Blitzortung.org or GOES-16 GLM integration | High |
| WhatsApp Production | Approved WhatsApp Business number (no sandbox limit) | High |
| Sentinel-2 Smoke Detection | Post-fire burn severity analysis via NBR/SWIR | Medium |
| ML Intent Classifier | Replace rules with trained model using labeled data | Medium |
| Email Alerts + Weekly Digest | For institutional users and park rangers | Medium |
| REST API | Public API for third-party integrations | Low |
| Multi-region Support | Chile (Araucania), Bolivia, California | Low |

## Contributing

FireSentinel is open source under the MIT License. Contributions are welcome.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Follow the coding standards in `CLAUDE.md`
4. Write tests for new functionality
5. Run the full pre-deploy checklist
6. Submit a pull request

All code and documentation in English. All user-facing outputs (alerts, dashboard, bot messages) in Spanish.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Acknowledgments

- **[NASA FIRMS](https://firms.modaps.eosdis.nasa.gov/)** -- Free global satellite fire detection data
- **[Open-Meteo](https://open-meteo.com/)** -- Free weather API for lightning and precipitation analysis
- **[OpenStreetMap](https://www.openstreetmap.org/)** and the **Overpass API** -- Road network data for proximity analysis
- **The brigadistas, park rangers, and citizens of Patagonia** -- who fight fires with inadequate resources and deserve better tools

---

*"Ojos en el cielo. Alerta en tu celular."*
