"""Paper trading order and position endpoints."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, get_state_manager
from app.execution.risk_manager import check_risk, increment_trade_count
from app.schemas.trading import (
    ClosePositionRequest,
    ClosedTradeResponse,
    CreateOrderRequest,
    OrderResponse,
    PositionResponse,
)
from app.services import paper_trading_service
from app.utils.cache import StateManager

router = APIRouter(prefix="/api/paper")


@router.post("/order", response_model=OrderResponse)
async def create_order(
    req: CreateOrderRequest,
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    from app.config import settings as _cfg

    if sm.execution_mode == "disabled":
        raise HTTPException(status_code=403, detail="Execution is disabled.")

    if sm.execution_mode == "live" and not _cfg.LIVE_TRADING_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Live trading requires LIVE_TRADING_ENABLED=true in .env",
        )

    # Risk checks (applies to all modes)
    daily_pnl = await paper_trading_service.get_daily_pnl(db)
    positions = await paper_trading_service.get_open_positions(db)
    open_count = len(positions)

    rejection = check_risk(req, sm.kill_switch, daily_pnl, open_count)
    if rejection:
        raise HTTPException(status_code=422, detail=rejection)

    state = sm.get_state(req.symbol.upper())
    current_price = state.last_price

    # ── Live trading: submit real order to Alpaca, then persist locally ──────
    if sm.execution_mode == "live":
        from app.execution.alpaca_live import AlpacaLiveExecutionAdapter
        adapter = AlpacaLiveExecutionAdapter()
        resp = await adapter.submit_order(req, current_price)
        # Persist to local DB for P&L tracking
        await paper_trading_service.place_paper_order(
            req, db, "live", resp.filled_price or current_price
        )
        increment_trade_count()
        return resp

    # ── Paper / local ────────────────────────────────────────────────────────
    order = await paper_trading_service.place_paper_order(
        req, db, sm.execution_mode, current_price
    )
    if order.status == "filled":
        increment_trade_count()

    return OrderResponse(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        qty=order.qty,
        status=order.status,
        filled_price=order.filled_price,
        created_at=order.created_at,
        execution_mode=order.execution_mode,
    )


@router.post("/close")
async def close_position(
    req: ClosePositionRequest,
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    state = sm.get_state(req.symbol.upper())
    current_price = state.last_price
    if not current_price:
        raise HTTPException(status_code=422, detail="No current price available.")

    # For paper_alpaca mode, also close the position at the Alpaca paper broker
    if sm.execution_mode == "paper_alpaca":
        from app.config import settings as _cfg
        if _cfg.alpaca_credentials_present:
            try:
                from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
                adapter = AlpacaPaperExecutionAdapter()
                await adapter.close_position(req.symbol.upper(), current_price)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Alpaca paper close failed: %s", exc)

    # For live mode, also close the real position at the broker
    elif sm.execution_mode == "live":
        from app.config import settings as _cfg
        if _cfg.LIVE_TRADING_ENABLED:
            try:
                from app.execution.alpaca_live import AlpacaLiveExecutionAdapter
                adapter = AlpacaLiveExecutionAdapter()
                await adapter.close_position(req.symbol.upper(), current_price)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Live close failed: %s", exc)

    from app.services.paper_trading_service import _close_position
    trade = await _close_position(db, req.symbol.upper(), current_price, req.reason)
    if not trade:
        raise HTTPException(status_code=404, detail="No open position for this symbol.")
    await db.commit()
    return {"symbol": req.symbol, "pnl": trade.pnl, "status": "closed"}


@router.get("/positions", response_model=List[PositionResponse])
async def get_positions(
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    positions = await paper_trading_service.get_open_positions(db)
    result = []
    for p in positions:
        live_price = sm.get_state(p.symbol).last_price or p.current_price
        if live_price:
            unrealized = (live_price - p.avg_entry_price) * p.qty
            if p.side == "short":
                unrealized = -unrealized
        else:
            unrealized = p.unrealized_pnl
        result.append(PositionResponse(
            symbol=p.symbol,
            side=p.side,
            qty=p.qty,
            avg_entry_price=p.avg_entry_price,
            current_price=live_price,
            unrealized_pnl=round(unrealized, 2) if unrealized is not None else None,
            stop_price=p.stop_price,
            take_profit_price=p.take_profit_price,
            opened_at=p.opened_at,
            execution_mode=p.execution_mode,
            strategy_version=getattr(p, "strategy_version", None),
        ))
    return result


@router.get("/trades", response_model=List[ClosedTradeResponse])
async def get_trades(db: AsyncSession = Depends(get_db)):
    trades = await paper_trading_service.get_closed_trades(db)
    return [
        ClosedTradeResponse(
            id=t.id,
            symbol=t.symbol,
            side=t.side,
            qty=t.qty,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            stop_price=getattr(t, "stop_price", None),
            take_profit_price=getattr(t, "take_profit_price", None),
            pnl=t.pnl,
            pnl_pct=t.pnl_pct,
            opened_at=t.opened_at,
            closed_at=t.closed_at,
            close_reason=t.close_reason,
            session_date=t.session_date,
            grade=getattr(t, "grade", None),
            lesson=getattr(t, "lesson", None),
            thesis_summary=getattr(t, "thesis_summary", None),
            entry_signal_json=getattr(t, "entry_signal_json", None),
            time_bucket=getattr(t, "time_bucket", None),
            execution_mode=getattr(t, "execution_mode", None),
        )
        for t in trades
    ]


@router.get("/daily-pnl")
async def daily_pnl(db: AsyncSession = Depends(get_db)):
    pnl = await paper_trading_service.get_daily_pnl(db)
    return {"daily_pnl": pnl}


@router.get("/bot-status")
async def bot_status(
    db: AsyncSession = Depends(get_db),
    sm: StateManager = Depends(get_state_manager),
):
    """
    Live snapshot of the bot's $100 paper wallet.
    Returns starting capital, available cash, realized P&L, open position,
    and total portfolio value (cash + unrealized).
    """
    from app.config import settings as _cfg

    positions = await paper_trading_service.get_open_positions(db)
    today_trades = await paper_trading_service.get_closed_trades(db)

    # Only today's bot-executed trades (filter by session_date)
    from app.utils.time_utils import session_date_str
    today = session_date_str()
    today_trades = [t for t in today_trades if t.session_date == today]

    # Enrich positions with live prices from StateManager
    unrealized = 0.0
    open_value = 0.0
    open_positions_out = []
    for p in positions:
        live_price = sm.get_state(p.symbol).last_price or p.current_price
        position_value = (live_price or p.avg_entry_price) * p.qty
        open_value += position_value
        if live_price:
            upnl = (live_price - p.avg_entry_price) * p.qty
            if p.side == "short":
                upnl = -upnl
        else:
            upnl = p.unrealized_pnl or 0.0
        unrealized += upnl
        open_positions_out.append({
            "symbol":            p.symbol,
            "side":              p.side,
            "qty":               p.qty,
            "avg_entry_price":   p.avg_entry_price,
            "current_price":     live_price,
            "unrealized_pnl":    round(upnl, 2),
            "stop_price":        p.stop_price,
            "take_profit_price": p.take_profit_price,
        })

    # Total portfolio value = cash on hand + open position market value
    portfolio_value = sm.bot_cash + open_value

    return {
        "starting_capital":  sm.bot_starting_capital,
        "bot_cash":          round(sm.bot_cash, 2),
        "realized_pnl":      round(sm.bot_realized_pnl, 2),
        "unrealized_pnl":    round(unrealized, 2),
        "portfolio_value":   round(portfolio_value, 2),
        "total_return_pct":  round(
            (portfolio_value - sm.bot_starting_capital) / sm.bot_starting_capital * 100, 2
        ),
        "auto_trading":      sm.auto_paper_execution,
        "trades_today":      len(today_trades),
        "max_trades_per_day": sm.max_trades_per_day,
        "open_positions":    open_positions_out,
    }


@router.get("/scalp-signals")
async def scalp_signals(sm: StateManager = Depends(get_state_manager)):
    """Live scalp signal scores for all watched symbols."""
    from app.services.signal_service import get_all_latest_scalp_signals
    from app.config import settings as _cfg

    sigs = get_all_latest_scalp_signals()
    out = []
    for sym, sig in sigs.items():
        if sym in ("SPY", "QQQ"):
            continue
        comps = [
            {"name": c.name, "raw_score": round(c.raw_score, 3), "direction": c.direction,
             "details": c.details}
            for c in sig.components
        ]
        out.append({
            "symbol":    sym,
            "score":     sig.total_score,
            "direction": sig.direction,
            "color":     sig.color.value,
            "label":     sig.label.value,
            "price":     sig.price,
            "vwap":      sig.vwap,
            "components": comps,
            "thesis": {
                "stop":   sig.thesis.suggested_stop,
                "target": sig.thesis.suggested_target,
                "stop_pct":   sig.thesis.stop_pct,
                "target_pct": sig.thesis.target_pct,
                "rr":         sig.thesis.risk_reward,
            } if sig.thesis else None,
            "scored_at": sig.scored_at.isoformat() if sig.scored_at else None,
        })
    out.sort(key=lambda x: x["score"], reverse=True)

    return {
        "signals": out,
        "config": {
            "enabled":         _cfg.SCALP_MODE_ENABLED,
            "entry_score":     _cfg.SCALP_ENTRY_SCORE,
            "flash_threshold": _cfg.SCALP_FLASH_THRESHOLD,
            "trade_threshold": _cfg.SCALP_TRADE_THRESHOLD,
            "watch_threshold": _cfg.SCALP_WATCH_THRESHOLD,
            "cooldown_min":    _cfg.SCALP_COOLDOWN_MINUTES,
            "allocation_pct":  _cfg.SCALP_ALLOCATION_PCT,
        },
    }


@router.get("/journal/stats")
async def journal_stats(db: AsyncSession = Depends(get_db)):
    """
    Rich journal statistics for the learning dashboard:
    pattern win rates, time-bucket win rates, adaptive weight preview,
    and grade distribution.
    """
    from app.services.learning_service import get_pattern_win_rates, get_time_win_rates

    trades = await paper_trading_service.get_closed_trades(db)

    # Grade distribution
    grades: dict = {}
    for t in trades:
        g = t.grade or "?"
        grades[g] = grades.get(g, 0) + 1

    # Streak
    streak = 0
    streak_type = None
    for t in sorted(trades, key=lambda x: x.closed_at, reverse=True):
        if streak_type is None:
            streak_type = "win" if t.pnl > 0 else "loss"
            streak = 1
        elif (t.pnl > 0) == (streak_type == "win"):
            streak += 1
        else:
            break

    # Expectancy = avg_win * win_rate - avg_loss * loss_rate
    winners = [t.pnl for t in trades if t.pnl > 0]
    losers  = [t.pnl for t in trades if t.pnl <= 0]
    wr      = len(winners) / len(trades) if trades else 0
    avg_win = sum(winners) / len(winners) if winners else 0
    avg_loss= sum(losers)  / len(losers)  if losers  else 0
    expectancy = avg_win * wr + avg_loss * (1 - wr)

    # Best / worst trade
    best_trade  = max(trades, key=lambda t: t.pnl, default=None)
    worst_trade = min(trades, key=lambda t: t.pnl, default=None)

    pattern_stats = await get_pattern_win_rates(db)
    time_stats    = await get_time_win_rates(db)

    return {
        "total_trades":    len(trades),
        "total_winners":   len(winners),
        "total_losers":    len(losers),
        "win_rate":        round(wr * 100, 1),
        "avg_win":         round(avg_win,  2),
        "avg_loss":        round(avg_loss, 2),
        "expectancy":      round(expectancy, 2),
        "gross_pnl":       round(sum(t.pnl for t in trades), 2),
        "streak":          streak,
        "streak_type":     streak_type,
        "grades":          grades,
        "best_trade":      {"symbol": best_trade.symbol,  "pnl": round(best_trade.pnl, 2)}  if best_trade  else None,
        "worst_trade":     {"symbol": worst_trade.symbol, "pnl": round(worst_trade.pnl, 2)} if worst_trade else None,
        "pattern_stats":   pattern_stats,
        "time_stats":      time_stats,
    }
