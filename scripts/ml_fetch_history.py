#!/usr/bin/env python3
"""
Download years of 1-minute SIP (full-market) bars for ML research.

Usage (from project root):
    venv/bin/python scripts/ml_fetch_history.py [--years 2] [--workers 4]

The live bot sees IEX bars (a small slice of volume), but SIP history is
available on Alpaca's free plan for anything older than 15 minutes and gives
far more data than the bot has recorded. All price features are scale-free
and volume features are relative, so models trained on SIP transfer to IEX.

Universe: every symbol the bots have evaluated (rejected_setups) plus SPY/QQQ
for market context. One pickle per session in data/ml/bars_sip/; holidays are
saved as empty frames so they are not re-requested. Safe to interrupt and rerun.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.providers.alpaca.client import get_historical_client

DB_PATH = ROOT / "stock_tracker.db"
OUT_DIR = ROOT / "data" / "ml" / "bars_sip"
ET = ZoneInfo("America/New_York")
EXCLUDE = {"BBBY"}            # delisted — no history
CONTEXT = ["SPY", "QQQ"]

_rate_lock = threading.Lock()
_last_call = [0.0]


def universe() -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    syms = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM rejected_setups")}
    conn.close()
    return sorted((syms - EXCLUDE) | set(CONTEXT))



def _throttle(min_gap: float = 0.32) -> None:
    """Keep total request rate under Alpaca's 200/min across all threads."""
    with _rate_lock:
        wait = _last_call[0] + min_gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.monotonic()


def fetch_day(client, day: date, symbols: list[str]) -> pd.DataFrame:
    """
    Raw REST pagination (not the SDK) so that every page passes the shared
    throttle — the SDK fetches follow-up pages internally and would blow the
    200 req/min limit when several days download in parallel.
    """
    import requests
    from app.config import settings

    start = datetime.combine(day, dtime(9, 30), tzinfo=ET)
    end = datetime.combine(day, dtime(16, 0), tzinfo=ET)
    headers = {"APCA-API-KEY-ID": settings.ALPACA_API_KEY, "APCA-API-SECRET-KEY": settings.ALPACA_API_SECRET}
    params = {
        "symbols": ",".join(symbols), "timeframe": "1Min", "feed": "sip", "limit": 10000,
        "start": start.isoformat(), "end": end.isoformat(), "adjustment": "raw",
    }
    rows = []
    while True:
        _throttle()
        r = requests.get(f"{settings.ALPACA_DATA_URL}/v2/stocks/bars", headers=headers, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(5)
            continue
        r.raise_for_status()
        j = r.json()
        for sym, bars in (j.get("bars") or {}).items():
            for b in bars:
                rows.append((sym, b["t"], b["o"], b["h"], b["l"], b["c"], b["v"]))
        if not j.get("next_page_token"):
            break
        params["page_token"] = j["next_page_token"]
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows, columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index(["symbol", "timestamp"]).sort_index()
    return df[df.index.get_level_values("timestamp") < pd.Timestamp(end)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    client = get_historical_client()
    if client is None:
        sys.exit("Alpaca credentials missing.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    symbols = universe()

    today = datetime.now(ET).date()
    first = today - timedelta(days=int(365 * args.years))
    days = [first + timedelta(days=i) for i in range((today - first).days)]   # excludes today
    todo = [d for d in days if d.weekday() < 5 and not (OUT_DIR / f"{d}.pkl").exists()]
    print(f"{len(symbols)} symbols, {len(todo)} weekdays to fetch ({first} → {today - timedelta(days=1)})", flush=True)

    def work(d: date) -> tuple[date, int]:
        for attempt in range(4):
            try:
                df = fetch_day(client, d, symbols)
                tmp = OUT_DIR / f"{d}.pkl.tmp"
                df.to_pickle(tmp)
                tmp.replace(OUT_DIR / f"{d}.pkl")   # atomic — an interrupted run never leaves a partial day
                return d, len(df)
            except Exception as exc:
                print(f"  {d}: attempt {attempt + 1} failed: {exc}", flush=True)
                time.sleep(10 * (attempt + 1))
        return d, -1

    done = 0
    t0 = time.time()
    with ThreadPoolExecutor(args.workers) as pool:
        for fut in as_completed(pool.submit(work, d) for d in todo):
            d, n = fut.result()
            done += 1
            if done % 25 == 0 or n < 0:
                rate = done / (time.time() - t0)
                print(f"  {done}/{len(todo)} days  ({rate * 60:.0f}/min, ~{(len(todo) - done) / rate / 60:.0f} min left)"
                      + (f"  {d} FAILED" if n < 0 else ""), flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
