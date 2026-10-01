#!/usr/bin/env bash
# ── Reset the SQLite database (DEV only) ─────────────────────────────────────
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DB_FILE="$PROJECT_ROOT/stock_tracker.db"

echo "WARNING: This will delete all data in $DB_FILE"
read -p "Type 'yes' to confirm: " confirm

if [ "$confirm" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

if [ -f "$DB_FILE" ]; then
  rm "$DB_FILE"
  echo "Database deleted: $DB_FILE"
else
  echo "No database file found at $DB_FILE"
fi

echo "Tables will be recreated automatically on next app start."
