# FireSentinel Patagonia - CLAUDE.md

## Project Overview
Satellite-based wildfire detection and intentionality analysis platform for Argentine Patagonia.
See `docs/PRD.md` for full product requirements. See `docs/BUILD_PLAN.md` for agent architecture.

## Tech Stack
- **Language:** Python 3.12
- **Package Manager:** Poetry
- **Database:** SQLite (dev AND prod MVP) -- single file on persistent volume
- **ORM:** SQLAlchemy 2.0 (async with aiosqlite)
- **Dashboard:** Streamlit + Folium
- **Alerts:** python-telegram-bot, Twilio (WhatsApp)
- **HTTP Client:** httpx (async)
- **Geospatial:** geopandas, shapely
- **Scheduler:** APScheduler
- **Config:** Pydantic Settings (env vars) + YAML (scoring weights, zones)
- **Linting:** ruff
- **Type Checking:** mypy (strict)
- **Testing:** pytest + pytest-asyncio

## Project Structure
```
FireSentinel/
├── config/
│   └── monitoring.yml          # Scoring weights, zones, thresholds
├── src/firesentinel/
│   ├── core/                   # Shared types, pipeline orchestrator, scheduler
│   │   ├── types.py            # Dataclass contracts between all modules
│   │   ├── pipeline.py         # Full cycle orchestrator
│   │   └── scheduler.py        # APScheduler cron setup
│   ├── config.py               # Pydantic settings (env vars + YAML)
│   ├── main.py                 # Entry point
│   ├── db/                     # Models + engine (no business logic)
│   ├── ingestion/              # FIRMS, weather, roads API clients
│   ├── processing/             # Dedup, clustering, intent classifier
│   ├── alerts/                 # Telegram, WhatsApp, dispatcher
│   └── dashboard/              # Streamlit app + pages
├── tests/                      # pytest test suite
├── scripts/                    # Seed data, deploy
└── docs/                       # PRD, build plan
```

## Module Dependency Rules
- `ingestion/` NEVER imports from `processing/` or `alerts/`
- `processing/` NEVER imports from `alerts/` or `ingestion/`
- `alerts/` NEVER imports from `ingestion/` or `processing/`
- ALL modules import shared types from `core/types.py`
- ALL modules import config from `config.py`
- ONLY `core/pipeline.py` imports from all layers (it's the orchestrator)

## Commands
- `poetry install` -- Install dependencies
- `poetry run python -m firesentinel.main` -- Run the pipeline
- `poetry run streamlit run src/firesentinel/dashboard/app.py` -- Run dashboard
- `poetry run pytest tests/ -x -q` -- Run tests
- `poetry run ruff check src/ tests/` -- Lint
- `poetry run ruff format --check src/ tests/` -- Format check
- `poetry run mypy src/` -- Type check

## Code Standards
- All user-facing outputs (alerts, dashboard labels, bot messages) in **Spanish**
- All code, comments, variable names, and documentation in **English**
- Type hints on ALL function signatures
- Every API client must handle: timeouts, rate limits (exponential backoff), and graceful degradation
- Scoring weights, thresholds, and zones come from `config/monitoring.yml` -- NEVER hardcode magic numbers
- All coordinates in WGS84 (EPSG:4326)
- Test every API client with mocked HTTP responses (no real API calls in tests)
- Use `httpx.AsyncClient` for all HTTP requests
- Store raw API responses before processing (zero data loss policy)
- When a data source fails, log the error and continue pipeline with remaining sources
- Intent scoring renormalizes weights when signals are unavailable (never zero them)

## Environment Variables (see .env.example)
- `FIRMS_MAP_KEY` -- NASA FIRMS API key (required)
- `TELEGRAM_BOT_TOKEN` -- Telegram bot token
- `TWILIO_ACCOUNT_SID` -- Twilio account (WhatsApp)
- `TWILIO_AUTH_TOKEN` -- Twilio auth
- `TWILIO_WHATSAPP_FROM` -- Twilio WhatsApp sender number
- `ADMIN_PASSWORD` -- Dashboard admin panel
- `ENVIRONMENT` -- dev | staging | prod
- `DB_PATH` -- SQLite file path (default: ./data/firesentinel.db)

## Pre-Deploy Checklist
1. `poetry run ruff check src/ tests/`
2. `poetry run ruff format --check src/ tests/`
3. `poetry run mypy src/`
4. `poetry run pytest tests/ -x -q`
5. Security review (no keys in code, no injection vectors)
6. Commit, push, deploy
