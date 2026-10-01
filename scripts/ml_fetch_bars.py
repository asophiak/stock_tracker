#!/usr/bin/env python3
"""
Download 1-minute regular-session bars for every symbol/session that appears in
rejected_setups, for ML dataset labelling.

Usage (from project root):
    venv/bin/python scripts/ml_fetch_bars.py [--days N] [--force]

Writes one pickle per session to data/ml/bars/YYYY-MM-DD.pkl containing a
DataFrame indexed by (symbol, timestamp) with open/high/low/close/volume.
Sessions already on disk are skipped unless --force is given.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import date, datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from app.config import settings
from app.providers.alpaca.client import get_historical_client

DB_PATH = ROOT / "stock_tracker.db"
BARS_DIR = ROOT / "data" / "ml" / "bars"
ET = ZoneInfo("America/New_York")


def sessions_and_symbols(days: int | None) -> dict[str, list[str]]:
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT session_date, symbol FROM rejected_setups ORDER BY session_date"
    ).fetchall()
    conn.close()
    out: dict[str, set[str]] = {}
    for d, s in rows:
        out.setdefault(d, set()).add(s)
    keys = sorted(out)
    if days:
        keys = keys[-days:]
    return {k: sorted(out[k]) for k in keys}


def fetch_session(client, session: str, symbols: list[str]) -> pd.DataFrame:
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    d = date.fromisoformat(session)
    start = datetime.combine(d, dtime(9, 30), tzinfo=ET)
    end = datetime.combine(d, dtime(16, 0), tzinfo=ET)
    feed = {"iex": DataFeed.IEX, "sip": DataFeed.SIP}.get(settings.ALPACA_DATA_FEED.lower(), DataFeed.IEX)
    req = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Minute,
        start=start,
        end=end,
        feed=feed,
    )
    df = client.get_stock_bars(req).df
    if df.empty:
        return df
    df = df[["open", "high", "low", "close", "volume"]].copy()
    # Keep regular session only (end is inclusive on Alpaca's side)
    ts = df.index.get_level_values("timestamp")
    df = df[ts < pd.Timestamp(end)]
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=None, help="only the most recent N sessions")
    ap.add_argument("--force", action="store_true", help="re-download sessions already cached")
    args = ap.parse_args()

    client = get_historical_client()
    if client is None:
        sys.exit("Alpaca credentials missing — cannot fetch history.")

    BARS_DIR.mkdir(parents=True, exist_ok=True)
    plan = sessions_and_symbols(args.days)
    now_et = datetime.now(ET)
    today = now_et.date().isoformat()
    session_over = now_et.time() >= dtime(16, 5)
    print(f"{len(plan)} sessions to check")

    for session, symbols in plan.items():
        out = BARS_DIR / f"{session}.pkl"
        # Never cache a session that is still in progress (market time, not local time)
        if session > today or (session == today and not session_over):
            continue
        if out.exists() and not args.force:
            continue
        for attempt in range(3):
            try:
                df = fetch_session(client, session, symbols)
                break
            except Exception as exc:  # rate limit / transient network
                print(f"  {session}: attempt {attempt + 1} failed: {exc}")
                time.sleep(5 * (attempt + 1))
        else:
            continue
        df.to_pickle(out)
        n_sym = df.index.get_level_values("symbol").nunique() if not df.empty else 0
        print(f"  {session}: {len(df):>6} bars, {n_sym} symbols")
        time.sleep(0.4)   # stay well under Alpaca's 200 req/min


if __name__ == "__main__":
    main()
