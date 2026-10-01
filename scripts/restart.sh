#!/usr/bin/env bash
# Daily restart script — kills any running instance and starts fresh.
# Scheduled via launchd to run at 6:20 AM PDT (9:20 AM ET) Mon–Fri.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$PROJECT_ROOT/logs/stock-tracker.log"

mkdir -p "$PROJECT_ROOT/logs"

echo "$(date): Restarting stock-tracker..." >> "$LOG"

# Kill existing instance and wait for port release
PIDS=$(lsof -ti:8000 2>/dev/null)
if [ -n "$PIDS" ]; then
    echo "$PIDS" | xargs kill -TERM 2>/dev/null || true
    sleep 2
    # Force-kill anything still alive
    PIDS=$(lsof -ti:8000 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | xargs kill -9 2>/dev/null || true
    fi
fi

# Wait until port 8000 is free (up to 10 seconds)
for i in $(seq 1 10); do
    if ! lsof -ti:8000 > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Activate venv and restart — Python output captured in stock-tracker.log
cd "$PROJECT_ROOT"
export PYTHONUNBUFFERED=1
source venv/bin/activate
# macOS Python 3.10 doesn't pick up system certs automatically; point to certifi bundle
CERT_FILE=$(python -c "import certifi; print(certifi.where())" 2>/dev/null)
if [ -n "$CERT_FILE" ]; then
    export SSL_CERT_FILE="$CERT_FILE"
    export REQUESTS_CA_BUNDLE="$CERT_FILE"
fi
nohup python run.py >> "$LOG" 2>&1 &

echo "$(date): Started PID $!" >> "$LOG"
