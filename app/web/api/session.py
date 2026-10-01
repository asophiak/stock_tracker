"""Session summary endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_state_manager
from app.services.session_summary_service import build_session_summary, get_session_summary
from app.utils.cache import StateManager
from app.utils.time_utils import session_date_str

router = APIRouter(prefix="/api/session-summary")


@router.get("/today")
async def today_summary(
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    summary = await build_session_summary(db, sm)
    return {
        "session_date": summary.session_date,
        "total_signals": summary.total_signals,
        "trades_taken": summary.trades_taken,
        "winners": summary.winners,
        "losers": summary.losers,
        "gross_pnl": summary.gross_pnl,
        "best_trade_symbol": summary.best_trade_symbol,
        "best_trade_score": summary.best_trade_score,
        "best_trade_direction": summary.best_trade_direction,
        "best_trade_thesis": summary.best_trade_thesis,
    }


@router.get("/{date}")
async def summary_by_date(date: str, db: AsyncSession = Depends(get_db)):
    summary = await get_session_summary(db, date)
    if not summary:
        return {"session_date": date, "message": "No summary for this date."}
    return {
        "session_date": summary.session_date,
        "total_signals": summary.total_signals,
        "trades_taken": summary.trades_taken,
        "winners": summary.winners,
        "losers": summary.losers,
        "gross_pnl": summary.gross_pnl,
        "best_trade_symbol": summary.best_trade_symbol,
        "best_trade_score": summary.best_trade_score,
        "best_trade_direction": summary.best_trade_direction,
        "best_trade_thesis": summary.best_trade_thesis,
    }
