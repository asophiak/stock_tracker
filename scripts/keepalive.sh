#!/usr/bin/env bash
# Stock Tracker persistent server wrapper.
#
# This script is called by launchd (KeepAlive=true). If the server exits for
# any reason launchd will restart this script automatically, so there is no
# kill-and-restart logic here — just start the server cleanly.
#
# caffeinate -i: prevents the Mac from idle-sleeping while this process is
# running. This guarantees the scoring loop and market-hours checks are never
# interrupted by automatic sleep. The Mac can still be manually slept (lid
# close) but will not sleep from inactivity alone.
#
# Day-boundary resets (VWAP flush, session date rollover, EOD flatten) are
# handled internally by the app's _eod_monitor — no daily restart is needed.

set -e

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

mkdir -p "$PROJECT_ROOT/logs"
echo "$(date): keepalive.sh — starting stock-tracker (PID will follow)..." \
    >> "$PROJECT_ROOT/logs/stock-tracker.log"

# Activate venv
if [ -d "venv" ]; then
    source venv/bin/activate
elif [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# macOS Python 3.10+ does not trust the system CA store by default.
# Point all TLS connections at the certifi bundle.
CERT_FILE=$(python -c "import certifi; print(certifi.where())" 2>/dev/null)
if [ -n "$CERT_FILE" ]; then
    export SSL_CERT_FILE="$CERT_FILE"
    export REQUESTS_CA_BUNDLE="$CERT_FILE"
fi

export PYTHONUNBUFFERED=1

# exec replaces this shell with caffeinate so launchd tracks the right PID.
# caffeinate -i: idle-sleep prevention assertion for the lifetime of the child.
exec caffeinate -i python run.py
