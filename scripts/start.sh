#!/usr/bin/env bash
# ── Stock Tracker start script ────────────────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Check for .env
if [ ! -f ".env" ]; then
  echo "⚠  .env not found — copying from .env.example"
  cp .env.example .env
  echo "   Edit .env and add your ALPACA_API_KEY / ALPACA_API_SECRET to enable live data."
  echo "   Without credentials the app runs in mock (synthetic data) mode."
fi

# Activate venv if it exists
if [ -d "venv" ]; then
  source venv/bin/activate
elif [ -d ".venv" ]; then
  source .venv/bin/activate
fi

echo "Starting Stock Tracker..."
python run.py
