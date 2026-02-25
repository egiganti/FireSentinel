#!/usr/bin/env bash
set -euo pipefail

# FireSentinel Patagonia - Deployment Script
# Pulls latest code, installs dependencies, runs checks, restarts service.

echo "========================================"
echo "  FireSentinel Deploy v1.0"
echo "========================================"
echo ""

# Step 1: Check required environment variables
echo "[1/9] Checking environment variables..."
if [ -z "${FIRMS_MAP_KEY:-}" ]; then
    echo "ERROR: FIRMS_MAP_KEY is not set. Export it before deploying."
    echo "  export FIRMS_MAP_KEY=your_api_key"
    exit 1
fi
echo "  FIRMS_MAP_KEY is set."

# Step 2: Pull latest code
echo "[2/9] Pulling latest code from origin/main..."
git fetch origin main
git reset --hard origin/main
echo "  Code updated to latest main."

# Step 3: Install dependencies
echo "[3/9] Installing dependencies..."
poetry install --no-interaction --only main
echo "  Dependencies installed."

# Step 4: Run linter
echo "[4/9] Running linter..."
poetry run ruff check src/
echo "  Lint passed."

# Step 5: Run tests
echo "[5/9] Running tests..."
poetry run pytest tests/ -x -q
echo "  All tests passed."

# Step 6: Initialize database (create tables if needed)
echo "[6/9] Running database initialization..."
poetry run python -c "
import asyncio
from firesentinel.db.engine import get_engine, init_db
from firesentinel.config import get_settings

async def _init():
    settings = get_settings()
    engine = get_engine(settings.db_path)
    await init_db(engine)
    await engine.dispose()
    print('  Database tables initialized.')

asyncio.run(_init())
"

# Step 7: Restart systemd service
echo "[7/9] Restarting firesentinel service..."
sudo systemctl restart firesentinel

# Step 8: Wait and check service status
echo "[8/9] Waiting 5 seconds for service startup..."
sleep 5

echo "[9/9] Checking service status..."
if sudo systemctl is-active --quiet firesentinel; then
    echo ""
    echo "========================================"
    echo "  DEPLOY SUCCESSFUL"
    echo "========================================"
    echo ""
    sudo systemctl status firesentinel --no-pager --lines=5
else
    echo ""
    echo "========================================"
    echo "  DEPLOY FAILED - Service not running"
    echo "========================================"
    echo ""
    sudo systemctl status firesentinel --no-pager --lines=20
    exit 1
fi
