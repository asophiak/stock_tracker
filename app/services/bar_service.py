"""
Bar loading service.

On startup, loads recent historical bars from the provider and seeds
the in-memory SymbolState windows. Also sets the average daily volume
baseline from the DB or a fresh provider fetch.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.config import settings
from app.providers.base import MarketDataProvider
from app.schemas.market_data import Bar, SymbolState
from app.utils.cache import StateManager
from app.utils.time_utils import market_open_dt, today_et, now_et

_TZ = ZoneInfo(settings.SESSION_TIMEZONE)


def _last_trading_session() -> Tuple[datetime, datetime]:
    """
    Return (start_utc, end_utc) for the most recent completed or in-progress
    trading session.

    Rules:
    • If today is a weekday AND market open time has passed → today's session
      (start = 9:30 ET, end = now or 4:00 PM ET whichever is earlier)
    • Otherwise → last weekday's full session (9:30 – 16:00 ET)
    """
    now_local = now_et()
    today = today_et()

    mkt_h, mkt_m   = map(int, settings.MARKET_OPEN_TIME.split(":"))
    close_h, close_m = map(int, settings.MARKET_CLOSE_TIME.split(":"))

    market_open_today  = datetime.combine(today, time(mkt_h,   mkt_m),   tzinfo=_TZ)
    market_close_today = datetime.combine(today, time(close_h, close_m), tzinfo=_TZ)

    # Check if we're in / past today's session
    if today.weekday() < 5 and now_local >= market_open_today:
        start = market_open_today
        end   = min(now_local, market_close_today)
        return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

    # Weekend or pre-market → find last weekday
    d = today - timedelta(days=1)
    while d.weekday() >= 5:   # 5 = Sat, 6 = Sun
        d -= timedelta(days=1)

    start = datetime.combine(d, time(mkt_h,   mkt_m),   tzinfo=_TZ)
    end   = datetime.combine(d, time(close_h, close_m), tzinfo=_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)

logger = logging.getLogger(__name__)


async def load_today_bars(
    symbol: str,
    provider: MarketDataProvider,
    state_manager: StateManager,
) -> None:
    """
    Fetch bars for the most recent trading session and populate the in-memory state.
    On weekdays during/after market hours this is today's intraday session.
    On weekends or pre-market it falls back to the last completed session (e.g. Friday).
    Also computes 5m and 15m aggregates from the 1m bars.
    """
    start, end = _last_trading_session()

    if start >= end:
        logger.debug("Bar load skipped for %s — no valid session window.", symbol)
        return

    logger.info("Loading 1m bars for %s (%s → %s) ...", symbol,
                start.astimezone(_TZ).strftime("%a %b %d %H:%M"),
                end.astimezone(_TZ).strftime("%H:%M ET"))
    bars_1m = await provider.fetch_historical_bars(symbol, "1Min", start, end)
    if not bars_1m:
        logger.warning("No historical bars returned for %s", symbol)
        return

    # Feed bars into state manager to rebuild VWAP, session high/low, etc.
    state = state_manager.get_state(symbol)
    state_manager.reset_session(symbol)

    for bar in bars_1m:
        state_manager.add_bar(symbol, bar)

    # Build 5m and 15m aggregates
    _build_aggregates(symbol, bars_1m, state_manager)

    # Set opening range if we have enough bars
    from app.utils.time_utils import opening_range_end_dt
    or_end = opening_range_end_dt().astimezone(timezone.utc)
    or_bars = [b for b in bars_1m if b.timestamp <= or_end]
    if or_bars:
        from app.signal_engine.indicators import compute_opening_range
        orh, orl = compute_opening_range(or_bars, settings.OPENING_RANGE_MINUTES)
        if orh and orl:
            state.opening_range_high = orh
            state.opening_range_low = orl
            state.opening_range_set = True
            logger.debug("%s OR: H=%.2f L=%.2f", symbol, orh, orl)

    logger.info("Loaded %d bars for %s. VWAP=%.2f", len(bars_1m), symbol, state.vwap or 0)


def _build_aggregates(symbol: str, bars_1m: List[Bar], state_manager: StateManager) -> None:
    """Build 5m and 15m aggregates from 1m bars by simple bucketing."""
    def aggregate(bars: List[Bar], period: int, timeframe: str) -> List[Bar]:
        result: List[Bar] = []
        for i in range(0, len(bars), period):
            bucket = bars[i : i + period]
            if not bucket:
                continue
            agg = Bar(
                timestamp=bucket[0].timestamp,
                open=bucket[0].open,
                high=max(b.high for b in bucket),
                low=min(b.low for b in bucket),
                close=bucket[-1].close,
                volume=sum(b.volume for b in bucket),
                timeframe=timeframe,
            )
            result.append(agg)
        return result

    bars_5m = aggregate(bars_1m, 5, "5Min")
    bars_15m = aggregate(bars_1m, 15, "15Min")
    state = state_manager.get_state(symbol)
    state.bars_5m = bars_5m[-settings.MAX_BARS_5M :]
    state.bars_15m = bars_15m[-settings.MAX_BARS_15M :]


async def load_avg_daily_volume(
    symbol: str,
    provider: MarketDataProvider,
    state_manager: StateManager,
) -> None:
    """
    Fetch ~20 days of daily bars to compute average daily volume baseline.
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=30)
    end = now - timedelta(days=1)

    try:
        daily_bars = await provider.fetch_historical_bars(symbol, "1Day", start, end)
        if daily_bars and len(daily_bars) >= 5:
            volumes = [b.volume for b in daily_bars[-20:]]
            avg_vol = sum(volumes) / len(volumes)
            state = state_manager.get_state(symbol)
            state.avg_daily_volume = avg_vol
            logger.debug("%s avg daily volume: %.0f", symbol, avg_vol)
    except Exception as exc:
        logger.warning("Could not load avg volume for %s: %s", symbol, exc)
