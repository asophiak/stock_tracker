"""
Daily session summary service.

Creates/updates the session summary after market close.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.session_model import DailySessionSummary
from app.models.signals import SignalSnapshot
from app.models.trading import ClosedTrade
from app.utils.cache import StateManager
from app.utils.time_utils import session_date_str

logger = logging.getLogger(__name__)


async def build_session_summary(
    db: AsyncSession,
    state_manager: StateManager,
    date_str: Optional[str] = None,
) -> DailySessionSummary:
    """
    Build or refresh the daily session summary for `date_str` (defaults to today).
    """
    date_str = date_str or session_date_str()

    # Get trades for the day — exclude alpaca_import positions (no signal validation)
    result = await db.execute(
        select(ClosedTrade).where(ClosedTrade.session_date == date_str)
    )
    all_trades = list(result.scalars().all())
    trades = [t for t in all_trades if t.strategy_version != "alpaca_import"]
    winners = [t for t in trades if t.is_winner]
    losers = [t for t in trades if not t.is_winner]
    gross_pnl = round(sum(t.pnl for t in all_trades), 2)

    # Get signal count
    sig_result = await db.execute(
        select(SignalSnapshot).where(SignalSnapshot.session_date == date_str)
    )
    signals = list(sig_result.scalars().all())

    # Best trade info from in-memory state
    best = state_manager.best_trade_of_day
    best_symbol = best.symbol if best else None
    best_score = best.total_score if best else None
    best_dir = ("long" if best.direction == 1 else "short") if best else None
    best_thesis = best.thesis.why_now if best and best.thesis else None

    # Check for existing summary
    existing_result = await db.execute(
        select(DailySessionSummary).where(DailySessionSummary.session_date == date_str)
    )
    summary = existing_result.scalar_one_or_none()

    if summary:
        summary.total_signals = len(signals)
        summary.trades_taken = len(trades)
        summary.winners = len(winners)
        summary.losers = len(losers)
        summary.gross_pnl = gross_pnl
        summary.best_trade_symbol = best_symbol
        summary.best_trade_score = best_score
        summary.best_trade_direction = best_dir
        summary.best_trade_thesis = best_thesis
    else:
        summary = DailySessionSummary(
            session_date=date_str,
            total_signals=len(signals),
            trades_taken=len(trades),
            winners=len(winners),
            losers=len(losers),
            gross_pnl=gross_pnl,
            best_trade_symbol=best_symbol,
            best_trade_score=best_score,
            best_trade_direction=best_dir,
            best_trade_thesis=best_thesis,
        )
        db.add(summary)

    await db.commit()
    await db.refresh(summary)
    logger.info("Session summary updated for %s: PnL=%.2f trades=%d", date_str, gross_pnl, len(trades))
    return summary


async def get_session_summary(db: AsyncSession, date_str: str) -> Optional[DailySessionSummary]:
    result = await db.execute(
        select(DailySessionSummary).where(DailySessionSummary.session_date == date_str)
    )
    return result.scalar_one_or_none()
