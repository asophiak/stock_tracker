"""
Whale Service
─────────────
Orchestrates SEC 13F institutional holdings + live options flow data.

Caching strategy:
  • 13F filings  → refresh every WHALE_REFRESH_HOURS (default 6h)
                   These are quarterly filings so daily updates are plenty
  • Options flow → refresh every WHALE_OPTIONS_REFRESH_MIN (default 5m)
                   Options are live during market hours

Exposes:
    get_whale_data(symbol)   → WhaleSentiment
    refresh_all_whales()     → called at startup + periodically
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.config import settings
from app.providers.options_flow import OptionsFlowResult, fetch_options_flow
from app.providers.sec_edgar import fetch_whale_holdings_for_symbol

logger = logging.getLogger(__name__)


# ── Domain object ─────────────────────────────────────────────────────────────

@dataclass
class WhaleSentiment:
    """Aggregated whale data for one symbol."""
    symbol: str

    # 13F data
    institutional_holders: List[Dict] = field(default_factory=list)
    num_funds_long:   int   = 0
    num_funds_put:    int   = 0
    total_value_usd:  int   = 0          # total $ held by tracked funds
    institutional_sentiment: float = 0.0  # -1..+1

    # Options flow
    options: Optional[OptionsFlowResult] = None
    options_sentiment: float = 0.0       # -1..+1

    # Combined
    combined_sentiment: float = 0.0      # -1..+1  (used by signal engine)
    confidence:         float = 0.0      # 0..1  (how much data we have)
    summary:            str   = ""

    fetched_at: Optional[datetime] = None


# ── Cache ─────────────────────────────────────────────────────────────────────

_whale_cache: Dict[str, WhaleSentiment]          = {}
_options_cache: Dict[str, OptionsFlowResult]     = {}
_13f_last_refresh: Optional[datetime]            = None
_options_last_refresh: Dict[str, datetime]       = {}

WHALE_REFRESH_HOURS: int    = 6     # re-fetch 13F every 6 hours
OPTIONS_REFRESH_MIN: int    = 5     # re-fetch options every 5 minutes


def _cache_stale_13f() -> bool:
    if _13f_last_refresh is None:
        return True
    age_h = (datetime.now(timezone.utc) - _13f_last_refresh).total_seconds() / 3600
    return age_h >= WHALE_REFRESH_HOURS


def _cache_stale_options(symbol: str) -> bool:
    last = _options_last_refresh.get(symbol)
    if last is None:
        return True
    age_m = (datetime.now(timezone.utc) - last).total_seconds() / 60
    return age_m >= OPTIONS_REFRESH_MIN


# ── 13F refresh ───────────────────────────────────────────────────────────────

async def _refresh_13f(symbols: List[str]) -> None:
    global _13f_last_refresh

    logger.info("Whale: refreshing 13F holdings for %s", symbols)

    # Fetch concurrently — one symbol at a time to respect EDGAR rate limits
    for sym in symbols:
        try:
            holdings = await fetch_whale_holdings_for_symbol(sym)
            _merge_13f_into_cache(sym, holdings)
            await asyncio.sleep(0.5)   # be polite to EDGAR
        except Exception as exc:
            logger.warning("13F fetch error for %s: %s", sym, exc)

    _13f_last_refresh = datetime.now(timezone.utc)
    logger.info("Whale: 13F refresh complete.")


def _merge_13f_into_cache(symbol: str, holdings: List[Dict]) -> None:
    ws = _whale_cache.setdefault(symbol, WhaleSentiment(symbol=symbol))
    ws.institutional_holders = holdings
    ws.num_funds_long  = sum(1 for h in holdings if h["sentiment"] >= 0)
    ws.num_funds_put   = sum(1 for h in holdings if h["sentiment"] < 0)
    ws.total_value_usd = sum(h["value_usd"] for h in holdings)

    # Score: (long funds - put funds) / total funds tracked → -1..+1
    n = len(holdings)
    if n == 0:
        ws.institutional_sentiment = 0.0
    else:
        ws.institutional_sentiment = (ws.num_funds_long - ws.num_funds_put) / n

    _recompute_combined(ws)


# ── Options refresh ───────────────────────────────────────────────────────────

async def _refresh_options(symbol: str) -> None:
    try:
        result = await fetch_options_flow(symbol)
        _options_cache[symbol] = result
        _options_last_refresh[symbol] = datetime.now(timezone.utc)
        ws = _whale_cache.setdefault(symbol, WhaleSentiment(symbol=symbol))
        ws.options           = result
        ws.options_sentiment = result.sentiment
        _recompute_combined(ws)
    except Exception as exc:
        logger.warning("Options refresh error for %s: %s", symbol, exc)


# ── Combined score ────────────────────────────────────────────────────────────

def _recompute_combined(ws: WhaleSentiment) -> None:
    """
    Blend 13F institutional sentiment + options flow sentiment.
    Weight:  40% institutional  +  60% options (options are more timely).
    """
    has_13f     = bool(ws.institutional_holders)
    has_options = ws.options is not None and ws.options.error is None

    if not has_13f and not has_options:
        ws.combined_sentiment = 0.0
        ws.confidence         = 0.0
        ws.summary            = "No whale data available yet."
        return

    if has_13f and has_options:
        ws.combined_sentiment = (ws.institutional_sentiment * 0.4
                                 + ws.options_sentiment      * 0.6)
        ws.confidence         = 0.9
    elif has_options:
        ws.combined_sentiment = ws.options_sentiment
        ws.confidence         = 0.6
    else:
        ws.combined_sentiment = ws.institutional_sentiment
        ws.confidence         = 0.4

    ws.combined_sentiment = max(-1.0, min(1.0, ws.combined_sentiment))
    ws.fetched_at = datetime.now(timezone.utc)

    # Summary
    parts = []
    if has_13f:
        if ws.num_funds_long > 0:
            parts.append(f"{ws.num_funds_long} whale(s) holding long")
        if ws.num_funds_put > 0:
            parts.append(f"{ws.num_funds_put} holding puts")
    if has_options and ws.options:
        parts.append(ws.options.summary)

    ws.summary = ". ".join(parts) if parts else "Neutral whale activity."


# ── Public API ────────────────────────────────────────────────────────────────

def get_whale_data(symbol: str) -> WhaleSentiment:
    """Return the cached WhaleSentiment for `symbol` (may be empty on first run)."""
    return _whale_cache.get(symbol, WhaleSentiment(symbol=symbol))


async def ensure_whale_data(symbol: str) -> WhaleSentiment:
    """
    Ensure whale data is fresh for `symbol`.
    Refreshes options every 5 minutes. 13F refreshed globally.
    """
    if _cache_stale_options(symbol):
        await _refresh_options(symbol)
    return get_whale_data(symbol)


async def refresh_all_whales(symbols: List[str]) -> None:
    """
    Called at startup and periodically (every WHALE_REFRESH_HOURS).
    Fetches 13F for all symbols + options for all symbols.
    """
    # 13F first (slow — respects EDGAR rate limits)
    if _cache_stale_13f():
        await _refresh_13f(symbols)

    # Options (fast — runs concurrently)
    await asyncio.gather(*[_refresh_options(sym) for sym in symbols])
    logger.info("Whale: full refresh complete for %d symbols.", len(symbols))


async def whale_refresh_loop(symbols: List[str], interval_hours: int = 6) -> None:
    """
    Background loop — runs forever, refreshes 13F every `interval_hours` hours.
    Options are refreshed per-symbol on demand via `ensure_whale_data`.
    """
    interval_sec = interval_hours * 3600
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await _refresh_13f(symbols)
        except Exception as exc:
            logger.warning("Whale refresh loop error: %s", exc)
