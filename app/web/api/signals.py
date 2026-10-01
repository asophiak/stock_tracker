"""Signal and alert history endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_state_manager
from app.models.signals import AlertRecord, SignalSnapshot
from app.services.signal_service import get_all_latest_signals
from app.utils.cache import StateManager
from app.utils.time_utils import session_date_str

router = APIRouter(prefix="/api")


@router.get("/signals")
async def current_signals(sm: StateManager = Depends(get_state_manager)):
    """Return latest scored signal for every tracked symbol."""
    all_sigs = get_all_latest_signals()
    result = {}
    for sym, sig in all_sigs.items():
        state = sm.get_state(sym)
        bars = state.bars_1m
        session_open = bars[0].open if bars else None
        result[sym] = {
            "symbol": sig.symbol,
            "score": sig.total_score,
            "color": sig.color.value,
            "label": sig.label.value,
            "direction": sig.direction,
            "price": sig.price,
            "vwap": sig.vwap,
            "rvol": round(state.rvol, 2) if state.rvol else None,
            "change_pct": round((sig.price - session_open) / session_open * 100, 2)
                          if sig.price and session_open else None,
            "is_best": sig.is_best_trade_of_day,
        }
    return result


@router.get("/alerts")
async def recent_alerts(db: AsyncSession = Depends(get_db), limit: int = 50):
    result = await db.execute(
        select(AlertRecord)
        .order_by(AlertRecord.sent_at.desc())
        .limit(limit)
    )
    alerts = result.scalars().all()
    return [
        {
            "id": a.id,
            "symbol": a.symbol,
            "type": a.alert_type,
            "message": a.message,
            "score": a.score,
            "direction": a.direction,
            "channels": a.channels,
            "sent_at": (a.sent_at.isoformat() + "Z") if a.sent_at.tzinfo is None else a.sent_at.isoformat(),
            "session_date": a.session_date,
        }
        for a in alerts
    ]


@router.get("/best-trade")
async def best_trade(sm: StateManager = Depends(get_state_manager)):
    best = sm.best_trade_of_day
    if not best:
        return {"best_trade": None}

    from dataclasses import asdict
    thesis = asdict(best.thesis) if best.thesis else None
    return {
        "best_trade": {
            "symbol": best.symbol,
            "score": best.total_score,
            "color": best.color.value,
            "label": best.label.value,
            "direction": best.direction,
            "price": best.price,
            "thesis": thesis,
        }
    }
