from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from app.schemas.macro import EarningsEvent

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Any] = {"data": None, "expires_at": 0.0}
_CACHE_TTL = 4 * 3600  # 4 hours

MAJOR_TICKERS = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "NFLX",
    # Semiconductors
    "AMD", "INTC", "QCOM", "AVGO", "MU", "TSM", "AMAT", "LRCX",
    # Software / Cloud
    "CRM", "ORCL", "IBM", "ADBE", "SNOW", "PLTR", "NOW", "WDAY",
    # Big banks & financials
    "JPM", "BAC", "GS", "MS", "WFC", "C", "V", "MA", "PYPL", "AXP",
    "USB", "PNC", "TFC", "ALLY", "KEY", "RF", "HBAN", "MTB", "FITB",
    "COF", "SCHW", "BLK",
    # Retail & consumer
    "WMT", "TGT", "COST", "HD", "NKE", "DIS", "MCD", "SBUX",
    # Ride-share / travel / social
    "UBER", "LYFT", "ABNB", "SNAP", "PINS", "SPOT",
    # Fintech
    "XYF", "HOOD", "SOFI", "COIN",
    # Auto
    "F", "GM", "RIVN",
    # Defense / aerospace
    "LMT", "BA", "RTX", "NOC", "GD",
    # Energy
    "CVX", "XOM", "OXY", "SLB",
    # Healthcare / pharma
    "PFE", "JNJ", "MRK", "ABBV", "LLY", "UNH", "CVS", "CI", "HUM",
    # Industrials
    "GE", "MMM", "CAT", "HON", "EMR", "ETN", "ITW",
    # Telecom / media
    "T", "VZ", "CMCSA",
]


def _get_week_range() -> tuple[date, date]:
    """
    Return the same 4-week window as the calendar:
    last Monday → last Monday + 27 days (28 days total).
    """
    today = date.today()
    last_monday = today - timedelta(days=today.weekday() + 7)
    return last_monday, last_monday + timedelta(days=27)


def _get_anticipation(ticker_obj) -> str:
    try:
        change = ticker_obj.fast_info.year_change  # e.g. -0.25 means -25%
        if change is None:
            return "mixed_expectations"
        if abs(change) > 0.30:
            return "high_volatility_watch"
        if change > 0.10:
            return "bullish_watch"
        if change < -0.10:
            return "bearish_watch"
    except Exception:
        pass
    return "mixed_expectations"


def _get_importance(ticker_obj) -> int:
    try:
        cap = ticker_obj.fast_info.market_cap
        if cap is None:
            return 1
        if cap > 500e9:
            return 5
        if cap > 100e9:
            return 4
        if cap > 20e9:
            return 3
        if cap > 5e9:
            return 2
    except Exception:
        pass
    return 1


def _process_ticker(ticker: str, monday: date, sunday: date) -> Optional[EarningsEvent]:
    try:
        import yfinance as yf
        t = yf.Ticker(ticker)
        cal = t.calendar
        if not cal:
            return None

        earnings_dates = cal.get("Earnings Date")
        if earnings_dates is None:
            return None

        if not isinstance(earnings_dates, list):
            earnings_dates = [earnings_dates]

        # Find the first earnings date within the current week
        target_date: Optional[date] = None
        for ed in earnings_dates:
            if hasattr(ed, "date"):
                d = ed.date()
            elif isinstance(ed, date):
                d = ed
            else:
                try:
                    from datetime import datetime as dt
                    d = dt.strptime(str(ed)[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
            if monday <= d <= sunday:
                target_date = d
                break

        if target_date is None:
            return None

        anticipation = _get_anticipation(t)
        importance = _get_importance(t)

        # Get company name
        try:
            company = t.info.get("longName") or t.info.get("shortName") or ticker
        except Exception:
            company = ticker

        return EarningsEvent(
            date=target_date,
            day_of_week=target_date.strftime("%A"),
            ticker=ticker,
            company=company,
            anticipation=anticipation,
            importance=importance,
        )
    except Exception as exc:
        logger.debug("earnings_calendar: skipping %s: %s", ticker, exc)
        return None


def _fetch_sync() -> List[EarningsEvent]:
    now = time.time()
    if _CACHE["data"] is not None and now < _CACHE["expires_at"]:
        return _CACHE["data"]

    monday, sunday = _get_week_range()
    results: List[EarningsEvent] = []

    for ticker in MAJOR_TICKERS:
        event = _process_ticker(ticker, monday, sunday)
        if event is not None:
            results.append(event)

    _CACHE["data"] = results
    _CACHE["expires_at"] = time.time() + _CACHE_TTL
    return results


async def _process_ticker_async(
    ticker: str, monday: date, sunday: date, loop: asyncio.AbstractEventLoop
) -> Optional[EarningsEvent]:
    try:
        return await loop.run_in_executor(None, _process_ticker, ticker, monday, sunday)
    except Exception:
        return None


async def get_weekly_earnings_events() -> List[EarningsEvent]:
    now = time.time()
    if _CACHE["data"] is not None and now < _CACHE["expires_at"]:
        return _CACHE["data"]

    monday, sunday = _get_week_range()
    loop = asyncio.get_event_loop()

    # Process in batches of 10 to avoid overwhelming the API
    batch_size = 10
    all_events: List[EarningsEvent] = []

    for i in range(0, len(MAJOR_TICKERS), batch_size):
        batch = MAJOR_TICKERS[i : i + batch_size]
        tasks = [_process_ticker_async(t, monday, sunday, loop) for t in batch]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, EarningsEvent):
                all_events.append(r)

    _CACHE["data"] = all_events
    _CACHE["expires_at"] = time.time() + _CACHE_TTL
    return all_events
