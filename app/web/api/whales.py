"""
Whale Data API
──────────────
Exposes SEC 13F institutional holdings + options flow data.

GET /api/whales                 → summary for all watchlist symbols
GET /api/whales/{symbol}        → full whale detail for one symbol
POST /api/whales/{symbol}/refresh → force-refresh (skips cache)
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from app.config import settings
from app.dependencies import get_state_manager
from app.services.whale_service import (
    WhaleSentiment,
    ensure_whale_data,
    get_whale_data,
    refresh_all_whales,
)
from app.utils.cache import StateManager

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/whales", tags=["whales"])


def _sentiment_to_label(sentiment: float) -> str:
    if sentiment >= 0.4:
        return "BULLISH"
    if sentiment >= 0.15:
        return "LEANING BULLISH"
    if sentiment <= -0.4:
        return "BEARISH"
    if sentiment <= -0.15:
        return "LEANING BEARISH"
    return "NEUTRAL"


def _whale_summary_dict(ws: WhaleSentiment) -> dict:
    """Compact dict for the dashboard overview."""
    return {
        "symbol":           ws.symbol,
        "sentiment_label":  _sentiment_to_label(ws.combined_sentiment),
        "sentiment_score":  round(ws.combined_sentiment, 2),
        "confidence":       round(ws.confidence, 2),
        "num_funds_long":   ws.num_funds_long,
        "num_funds_put":    ws.num_funds_put,
        "total_value_m":    round(ws.total_value_usd / 1_000_000, 1),
        "pcr":              ws.options.put_call_ratio if ws.options else None,
        "summary":          ws.summary,
        "fetched_at":       ws.fetched_at.isoformat() if ws.fetched_at else None,
    }


def _whale_full_dict(ws: WhaleSentiment) -> dict:
    """Full detail dict for the symbol detail page."""
    opts = ws.options
    options_data = None
    if opts:
        options_data = {
            "put_call_ratio":  opts.put_call_ratio,
            "call_oi":         opts.call_oi,
            "put_oi":          opts.put_oi,
            "call_volume":     opts.call_volume,
            "put_volume":      opts.put_volume,
            "unusual_calls":   opts.unusual_calls,
            "unusual_puts":    opts.unusual_puts,
            "sentiment":       round(opts.sentiment, 2),
            "summary":         opts.summary,
            "error":           opts.error,
        }

    top_holders = sorted(
        ws.institutional_holders, key=lambda h: -h["value_usd"]
    )

    return {
        "symbol":                 ws.symbol,
        "sentiment_label":        _sentiment_to_label(ws.combined_sentiment),
        "combined_sentiment":     round(ws.combined_sentiment, 2),
        "institutional_sentiment":round(ws.institutional_sentiment, 2),
        "options_sentiment":      round(ws.options_sentiment, 2),
        "confidence":             round(ws.confidence, 2),
        "num_funds_long":         ws.num_funds_long,
        "num_funds_put":          ws.num_funds_put,
        "total_value_m":          round(ws.total_value_usd / 1_000_000, 1),
        "institutional_holders":  [
            {
                "fund_name": h["fund_name"],
                "position":  h["put_call"],
                "value_m":   round(h["value_usd"] / 1_000_000, 1),
                "shares":    h["shares"],
                "sentiment": h["sentiment"],
            }
            for h in top_holders[:10]
        ],
        "options":                options_data,
        "summary":                ws.summary,
        "fetched_at":             ws.fetched_at.isoformat() if ws.fetched_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def get_all_whale_data(sm: StateManager = Depends(get_state_manager)):
    """
    Summary whale sentiment for every symbol in the watchlist.
    Returns cached data immediately; refresh happens in the background.
    """
    symbols = settings.watchlist_symbols
    result  = []
    for sym in symbols:
        ws = get_whale_data(sym)
        result.append(_whale_summary_dict(ws))
    return {"whales": result}


@router.get("/portfolio-changes")
async def get_whale_portfolio_changes_api(force: int = 0):
    """
    Returns 13F position diffs for all tracked whale funds.
    Shows new buys, adds, reduces, and closes vs prior quarter.
    Cached 6 hours — pass ?force=1 to bypass cache.
    """
    from datetime import datetime, timezone
    from app.services.whale_wallet_service import get_whale_portfolio_changes
    data = await get_whale_portfolio_changes(force=bool(force))
    return {
        "portfolio_changes": data,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{symbol}")
async def get_symbol_whale_data(
    symbol: str,
    sm: StateManager = Depends(get_state_manager),
):
    """
    Full whale detail for one symbol.
    Triggers an options-flow refresh if the cache is stale (> 5 min).
    """
    sym = symbol.upper()
    if sym not in settings.watchlist_symbols:
        raise HTTPException(status_code=404, detail=f"Symbol {sym} not in watchlist")

    ws = await ensure_whale_data(sym)
    return _whale_full_dict(ws)


@router.post("/{symbol}/refresh")
async def force_refresh_symbol(
    symbol: str,
    sm: StateManager = Depends(get_state_manager),
):
    """Force a fresh 13F + options fetch for this symbol (ignores cache TTL)."""
    sym = symbol.upper()
    if sym not in settings.watchlist_symbols:
        raise HTTPException(status_code=404, detail=f"Symbol {sym} not in watchlist")

    ws = await ensure_whale_data(sym)
    logger.info("Force-refreshed whale data for %s", sym)
    return {"status": "refreshed", "symbol": sym, **_whale_summary_dict(ws)}


@router.post("/refresh-all")
async def force_refresh_all(sm: StateManager = Depends(get_state_manager)):
    """Force a full 13F + options refresh for all watchlist symbols."""
    symbols = settings.watchlist_symbols
    await refresh_all_whales(symbols)
    return {"status": "refreshed", "symbols": symbols}
