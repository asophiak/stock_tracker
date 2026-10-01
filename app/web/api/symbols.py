"""Per-symbol detail endpoint."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_state_manager
from app.services.news_service import get_news_bundle
from app.services.signal_service import get_latest_signal
from app.utils.cache import StateManager

router = APIRouter(prefix="/api/symbol")


def _state_to_dict(state) -> dict:
    """Serialise SymbolState to a JSON-safe dict."""
    def bar_dict(b):
        return {
            "t": b.timestamp.isoformat(),
            "o": b.open, "h": b.high, "l": b.low, "c": b.close,
            "v": b.volume, "vwap": getattr(b, "vwap", None),
        }

    def aggregate(dicts: list, period: int) -> list:
        """Downsample a list of bar dicts to a larger timeframe."""
        result = []
        for i in range(0, len(dicts), period):
            bucket = dicts[i : i + period]
            if not bucket:
                continue
            result.append({
                "t": bucket[0]["t"],
                "o": bucket[0]["o"],
                "h": max(b["h"] for b in bucket),
                "l": min(b["l"] for b in bucket),
                "c": bucket[-1]["c"],
                "v": sum(b["v"] or 0 for b in bucket),
                "vwap": None,
            })
        return result

    bars_1m = [bar_dict(b) for b in state.bars_1m]

    # Always derive 5m / 15m from live 1m bars so they stay fresh even when
    # the provider only streams 1-minute bars (e.g. yfinance polling mode).
    bars_5m  = aggregate(bars_1m, 5)
    bars_15m = aggregate(bars_1m, 15)

    return {
        "symbol": state.symbol,
        "last_price": state.last_price,
        "last_updated": state.last_updated.isoformat() if state.last_updated else None,
        "vwap": round(state.vwap, 4) if state.vwap else None,
        "opening_range_high": state.opening_range_high,
        "opening_range_low": state.opening_range_low,
        "session_high": state.session_high,
        "session_low": state.session_low,
        "premarket_high": state.premarket_high,
        "premarket_low": state.premarket_low,
        "session_volume": state.session_volume,
        "avg_daily_volume": state.avg_daily_volume,
        "rvol": round(state.rvol, 2) if state.rvol is not None else None,
        "above_vwap": state.above_vwap,
        "distance_from_vwap_pct": round(state.distance_from_vwap * 100, 3) if state.distance_from_vwap is not None else None,
        "bars_1m":  bars_1m,
        "bars_5m":  bars_5m,
        "bars_15m": bars_15m,
    }


def _signal_to_dict(sig) -> dict:
    if sig is None:
        return {}

    components = [
        {
            "name": c.name,
            "raw_score": round(c.raw_score, 3),
            "weight": c.weight,
            "weighted_score": round(c.weighted_score, 2),
            "direction": c.direction,
            "details": c.details,
        }
        for c in sig.components
    ]

    thesis = None
    if sig.thesis:
        from dataclasses import asdict
        thesis = asdict(sig.thesis)

    return {
        "symbol": sig.symbol,
        "scored_at": sig.scored_at.isoformat(),
        "total_score": sig.total_score,
        "direction": sig.direction,
        "color": sig.color.value,
        "label": sig.label.value,
        "price": sig.price,
        "vwap": sig.vwap,
        "is_best_trade_of_day": sig.is_best_trade_of_day,
        "components": components,
        "thesis": thesis,
    }


@router.get("/{symbol}")
async def symbol_detail(symbol: str, sm: StateManager = Depends(get_state_manager)):
    symbol = symbol.upper()
    state = sm.get_state(symbol)
    sig = get_latest_signal(symbol)
    news = get_news_bundle(symbol)

    return {
        "market_data": _state_to_dict(state),
        "signal": _signal_to_dict(sig),
        "news": news.model_dump(),
    }


@router.get("/{symbol}/signals")
async def symbol_signals(symbol: str, sm: StateManager = Depends(get_state_manager)):
    sig = get_latest_signal(symbol.upper())
    return _signal_to_dict(sig)


@router.get("/{symbol}/news")
async def symbol_news(symbol: str):
    bundle = get_news_bundle(symbol.upper())
    return bundle.model_dump()


@router.get("/{symbol}/pattern-history")
async def symbol_pattern_history(
    symbol: str,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """Return detected pattern history for a symbol, newest first."""
    from sqlalchemy import desc, select
    from app.models.signals import PatternHistoryEntry

    result = await db.execute(
        select(PatternHistoryEntry)
        .where(PatternHistoryEntry.symbol == symbol.upper())
        .order_by(desc(PatternHistoryEntry.detected_at))
        .limit(min(limit, 200))
    )
    entries = result.scalars().all()
    return {
        "symbol": symbol.upper(),
        "history": [
            {
                "detected_at":  e.detected_at.isoformat(),
                "pattern_name": e.pattern_name,
                "direction":    e.direction,
                "strength":     e.strength,
                "description":  e.description or "",
                "price":        e.price,
                "signal_score": e.signal_score,
                "signal_label": e.signal_label or "",
                "session_date": e.session_date or "",
            }
            for e in entries
        ],
    }
