"""Health and provider status endpoints."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_state_manager
from app.services.market_session import session_status_dict
from app.utils.cache import StateManager

router = APIRouter()


@router.get("/health")
async def health():
    """Minimal liveness probe — returns 200 OK if the process is alive."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@router.get("/api/health")
async def full_health(
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    """
    Rich health snapshot used by the dashboard header and external monitors.

    Returns:
      last_restart_time   — ISO-8601 UTC timestamp of last server startup
      startup_time        — ISO-8601 UTC timestamp of this process's start
      websocket_connected — True when the Alpaca stream is live
      provider_name       — "alpaca" | "yfinance" | "none"
      market_phase        — current session phase (see SessionPhase enum)
      bars_loaded         — {symbol: bar_count} for every tracked symbol
      total_bars          — sum of all 1m bars across all symbols
      trades_today        — number of closed trades for today's session
      daily_pnl           — gross P&L for today's session (USD)
      bot_cash            — bot wallet available cash
      bot_realized_pnl    — cumulative realized P&L since wallet was created
      session_summary_exists — True if today's session summary is in the DB
      kill_switch         — current kill-switch state
      execution_mode      — current execution mode
    """
    from sqlalchemy import text

    from app.services import bot_state_service
    from app.services.paper_trading_service import get_daily_pnl
    from app.utils.time_utils import session_date_str

    today = session_date_str()

    # Last restart time from DB (persisted across restarts)
    last_restart = await bot_state_service.get_restart_time(db)

    # Trades and P&L for today
    daily_pnl = await get_daily_pnl(db, today)
    trades_result = await db.execute(
        text("SELECT COUNT(*) FROM closed_trades WHERE session_date = :d"),
        {"d": today},
    )
    trades_today = trades_result.scalar_one()

    # Session summary existence
    summary_result = await db.execute(
        text("SELECT COUNT(*) FROM daily_session_summaries WHERE session_date = :d"),
        {"d": today},
    )
    session_summary_exists = summary_result.scalar_one() > 0

    # Bar counts per symbol (1m only — most recent data indicator)
    bars_loaded: dict[str, int] = {}
    for sym in sm.all_symbols():
        state = sm.get_state(sym)
        bars_loaded[sym] = len(state.bars_1m)
    total_bars = sum(bars_loaded.values())

    session = session_status_dict()

    return {
        "last_restart_time":      last_restart,
        "startup_time":           sm.startup_time.isoformat(),
        "websocket_connected":    sm.stream_connected,
        "provider_name":          sm.provider_name,
        "market_phase":           session.get("phase"),
        "is_market_open":         session.get("is_open"),
        "minutes_until_close":    session.get("minutes_until_close"),
        "bars_loaded":            bars_loaded,
        "total_bars":             total_bars,
        "trades_today":           trades_today,
        "daily_pnl":              daily_pnl,
        "bot_cash":               round(sm.bot_cash, 2),
        "bot_realized_pnl":       round(sm.bot_realized_pnl, 2),
        "session_summary_exists": session_summary_exists,
        "kill_switch":            sm.kill_switch,
        "execution_mode":         sm.execution_mode,
        "startup_complete":       sm.startup_complete,
        "last_score_time":        sm.last_score_time.isoformat() if sm.last_score_time else None,
    }


@router.get("/api/status")
async def app_status(sm: StateManager = Depends(get_state_manager)):
    return {
        "provider_connected": sm.provider_connected,
        "provider_name": sm.provider_name,
        "stream_connected": sm.stream_connected,
        "startup_complete": sm.startup_complete,
        "kill_switch": sm.kill_switch,
        "execution_mode": sm.execution_mode,
        "session": session_status_dict(),
        "last_score_time": sm.last_score_time.isoformat() if sm.last_score_time else None,
        "tracked_symbols": sm.all_symbols(),
    }


@router.get("/api/provider-status")
async def provider_status(sm: StateManager = Depends(get_state_manager)):
    return {
        "name": sm.provider_name,
        "market_data_connected": sm.stream_connected,
        "startup_complete": sm.startup_complete,
    }
