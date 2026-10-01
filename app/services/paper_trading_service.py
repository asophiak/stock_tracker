"""
Paper trading management service.

Handles position lifecycle, mark-to-market PnL updates,
stop/target checks, and EOD flattening.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.trading import ClosedTrade, PaperOrder, PaperPosition
from app.schemas.trading import CreateOrderRequest, ExecutionMode
from app.utils.time_utils import session_date_str

logger = logging.getLogger(__name__)


async def get_open_positions(db: AsyncSession) -> List[PaperPosition]:
    result = await db.execute(
        select(PaperPosition).order_by(PaperPosition.opened_at)
    )
    return list(result.scalars().all())


async def get_closed_trades(db: AsyncSession, session_date: Optional[str] = None) -> List[ClosedTrade]:
    stmt = select(ClosedTrade).order_by(ClosedTrade.closed_at.desc())
    if session_date:
        stmt = stmt.where(ClosedTrade.session_date == session_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_orders(db: AsyncSession, limit: int = 50) -> List[PaperOrder]:
    result = await db.execute(
        select(PaperOrder).order_by(PaperOrder.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def place_paper_order(
    req: CreateOrderRequest,
    db: AsyncSession,
    execution_mode: str,
    current_price: Optional[float] = None,
) -> PaperOrder:
    """
    Simulate a paper order fill (market orders fill immediately at current_price).
    """
    order_id = str(uuid.uuid4())
    filled_price = current_price or req.limit_price
    filled_at = datetime.now(timezone.utc) if filled_price else None
    status = "filled" if filled_price else "pending"

    order = PaperOrder(
        order_id=order_id,
        symbol=req.symbol.upper(),
        side=req.side.value,
        order_type=req.order_type.value,
        qty=req.qty,
        limit_price=req.limit_price,
        stop_price=req.stop_price,
        take_profit_price=req.take_profit_price,
        status=status,
        filled_price=filled_price,
        filled_qty=req.qty if status == "filled" else None,
        filled_at=filled_at,
        execution_mode=execution_mode,
        signal_id=req.signal_id,
        thesis_summary=req.thesis_summary,
        session_date=session_date_str(),
    )
    db.add(order)
    await db.flush()

    # Open or update position on fill.
    # A BUY closes an existing short first; a SELL closes an existing long first.
    # If there is no opposing position, we open a new one in the requested direction.
    if status == "filled" and filled_price:
        sym = req.symbol.upper()
        existing_result = await db.execute(
            select(PaperPosition).where(PaperPosition.symbol == sym)
        )
        existing_pos = existing_result.scalar_one_or_none()

        if req.side.value == "buy":
            if existing_pos and existing_pos.side == "short":
                # Closing an existing short position
                await _close_position(db, sym, filled_price, "manual", order)
            else:
                # Opening (or averaging into) a long
                await _open_or_add_position(
                    db, order, filled_price,
                    req.stop_price, req.take_profit_price, execution_mode,
                    strategy_version=req.strategy_version or "4b",
                )
        elif req.side.value == "sell":
            if existing_pos and existing_pos.side == "long":
                # Closing an existing long position
                await _close_position(db, sym, filled_price, "manual", order)
            else:
                # Opening a new short position
                await _open_or_add_position(
                    db, order, filled_price,
                    req.stop_price, req.take_profit_price, execution_mode,
                    strategy_version=req.strategy_version or "4b",
                )

    await db.commit()
    logger.info(
        "Paper order %s: %s %s %.1f @ %.2f",
        status, req.side.value.upper(), req.symbol.upper(), req.qty, filled_price or 0
    )
    return order


async def _open_or_add_position(
    db: AsyncSession,
    order: PaperOrder,
    fill_price: float,
    stop_price: Optional[float],
    take_profit_price: Optional[float],
    execution_mode: str,
    strategy_version: str = "4b",
) -> None:
    result = await db.execute(
        select(PaperPosition).where(PaperPosition.symbol == order.symbol)
    )
    existing = result.scalar_one_or_none()
    if existing:
        # Average in
        total_qty = existing.qty + order.qty
        existing.avg_entry_price = (
            (existing.avg_entry_price * existing.qty + fill_price * order.qty) / total_qty
        )
        existing.qty = total_qty
    else:
        pos = PaperPosition(
            symbol=order.symbol,
            side="long" if order.side == "buy" else "short",
            qty=order.qty,
            avg_entry_price=fill_price,
            current_price=fill_price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            execution_mode=execution_mode,
            order_id=order.order_id,
            session_date=session_date_str(),
            strategy_version=strategy_version,
        )
        db.add(pos)


async def _close_position(
    db: AsyncSession,
    symbol: str,
    exit_price: float,
    reason: str,
    closing_order: Optional[PaperOrder] = None,
) -> Optional[ClosedTrade]:
    result = await db.execute(
        select(PaperPosition).where(PaperPosition.symbol == symbol)
    )
    pos = result.scalar_one_or_none()
    if not pos:
        return None

    pnl_per_share = exit_price - pos.avg_entry_price
    if pos.side == "short":
        pnl_per_share = -pnl_per_share
    pnl = pnl_per_share * pos.qty
    pnl_pct = pnl_per_share / pos.avg_entry_price if pos.avg_entry_price else 0

    # Carry the entry thesis through to the closed trade journal
    thesis_note: Optional[str] = None
    if pos.order_id:
        order_result = await db.execute(
            select(PaperOrder).where(PaperOrder.order_id == pos.order_id)
        )
        entry_order = order_result.scalar_one_or_none()
        if entry_order:
            thesis_note = entry_order.thesis_summary

    from app.services.learning_service import _time_bucket, build_entry_snapshot

    # Phase 4B: classify thesis quality based on whether stop/TP were set at entry.
    _tq = (
        "valid"          if pos.stop_price is not None and pos.take_profit_price is not None else
        "partial"        if pos.stop_price is not None or  pos.take_profit_price is not None else
        "missing_thesis"
    )

    trade = ClosedTrade(
        symbol=symbol,
        side=pos.side,
        qty=pos.qty,
        entry_price=pos.avg_entry_price,
        exit_price=exit_price,
        stop_price=pos.stop_price,
        take_profit_price=pos.take_profit_price,
        pnl=round(pnl, 2),
        pnl_pct=round(pnl_pct * 100, 3),
        opened_at=pos.opened_at,
        closed_at=datetime.now(timezone.utc),
        close_reason=reason,
        thesis_summary=thesis_note,
        execution_mode=pos.execution_mode,
        session_date=session_date_str(),
        is_winner=pnl > 0,
        entry_signal_json=pos.entry_signal_json,
        time_bucket=_time_bucket(pos.opened_at),
        thesis_quality=_tq,
        strategy_version=pos.strategy_version or "pre_4b",
    )
    db.add(trade)
    await db.delete(pos)
    await db.flush()   # ensure trade.id is available for learning annotations

    # Generate lesson + grade + record component accuracy
    from app.services.learning_service import analyze_and_annotate
    await analyze_and_annotate(db, trade)

    logger.info("Position closed: %s PnL=%.2f (%s) grade=%s", symbol, pnl, reason, trade.grade)
    return trade


async def attach_entry_signal(
    db: AsyncSession,
    symbol: str,
    signal_json: str,
) -> None:
    """Store the entry signal snapshot on the open position so it carries to ClosedTrade."""
    result = await db.execute(
        select(PaperPosition).where(PaperPosition.symbol == symbol)
    )
    pos = result.scalar_one_or_none()
    if pos:
        pos.entry_signal_json = signal_json
        await db.flush()


async def update_position_prices(
    db: AsyncSession,
    symbol: str,
    current_price: float,
) -> Optional[ClosedTrade]:
    """
    Update mark-to-market and check stop/target.
    Returns the ClosedTrade if the position was closed, else None.
    """
    result = await db.execute(
        select(PaperPosition).where(PaperPosition.symbol == symbol)
    )
    pos = result.scalar_one_or_none()
    if not pos:
        return None

    pos.current_price = current_price
    if pos.side == "long":
        pos.unrealized_pnl = round((current_price - pos.avg_entry_price) * pos.qty, 2)
    else:
        pos.unrealized_pnl = round((pos.avg_entry_price - current_price) * pos.qty, 2)

    # ── Break-even stop / trailing stop ratchet ───────────────────────────────
    # Once a position reaches 40% toward its target, lift the stop to entry
    # (break-even). Once at 75%, lock in 50% of the gain.
    # This prevents winners from giving back all profit and eliminates $0 exits.
    if pos.stop_price and pos.take_profit_price and pos.avg_entry_price:
        entry = pos.avg_entry_price
        target = pos.take_profit_price
        total_dist = abs(target - entry)
        if total_dist > 0:
            progress = abs(current_price - entry) / total_dist  # 0.0 – 1.0+

            if pos.side == "long" and current_price > entry:
                if progress >= 0.75:
                    # Lock in 50% of the gain
                    new_stop = entry + 0.5 * (target - entry)
                    if new_stop > pos.stop_price:
                        pos.stop_price = round(new_stop, 4)
                elif progress >= 0.40:
                    # Break-even stop
                    new_stop = entry + entry * 0.001  # entry + 0.1% buffer
                    if new_stop > pos.stop_price:
                        pos.stop_price = round(new_stop, 4)

            elif pos.side == "short" and current_price < entry:
                if progress >= 0.75:
                    new_stop = entry - 0.5 * (entry - target)
                    if new_stop < pos.stop_price:
                        pos.stop_price = round(new_stop, 4)
                elif progress >= 0.40:
                    new_stop = entry - entry * 0.001
                    if new_stop < pos.stop_price:
                        pos.stop_price = round(new_stop, 4)

    # Check stop
    if pos.stop_price:
        if (pos.side == "long" and current_price <= pos.stop_price) or \
           (pos.side == "short" and current_price >= pos.stop_price):
            trade = await _close_position(db, symbol, current_price, "stop_hit")
            await db.commit()
            return trade

    # Check target
    if pos.take_profit_price:
        if (pos.side == "long" and current_price >= pos.take_profit_price) or \
           (pos.side == "short" and current_price <= pos.take_profit_price):
            trade = await _close_position(db, symbol, current_price, "target_hit")
            await db.commit()
            return trade

    await db.commit()
    return None


async def sync_alpaca_positions(db: AsyncSession, sm) -> None:
    """
    Sync the local paper_positions table with the real Alpaca paper account.

    - Any Alpaca position not in the local DB is imported so the bot can
      monitor and exit it automatically.
    - Any local position that no longer exists in Alpaca is removed (it was
      closed directly in Alpaca outside the bot).
    - bot_cash / bot_realized_pnl are reconciled from Alpaca account equity
      so position-sizing always reflects real buying power.

    Only runs when execution_mode == "paper_alpaca" and creds are present.
    """
    if not settings.alpaca_credentials_present:
        return

    from app.execution.alpaca_paper import AlpacaPaperExecutionAdapter
    adapter = AlpacaPaperExecutionAdapter()

    # ── Fetch Alpaca state ────────────────────────────────────────────────────
    alpaca_positions = await adapter._provider.get_positions()
    account         = await adapter._provider.get_account()

    # ── Reconcile bot_cash from Alpaca buying power ───────────────────────────
    if account:
        new_cash = account.get("cash", sm.bot_cash)
        if abs(new_cash - sm.bot_cash) > 0.01:
            logger.info(
                "ALPACA SYNC: cash reconciled $%.2f → $%.2f",
                sm.bot_cash, new_cash,
            )
            sm.bot_cash = new_cash

    # ── Build lookup maps ─────────────────────────────────────────────────────
    local_positions = await get_open_positions(db)
    local_by_symbol  = {p.symbol: p for p in local_positions}
    alpaca_by_symbol = {p["symbol"]: p for p in alpaca_positions}

    # ── Import positions that exist in Alpaca but not locally ─────────────────
    # Guard: skip import if we closed this symbol in the last 3 minutes —
    # the Alpaca close may still be in-flight and we don't want a zombie re-import.
    from sqlalchemy import select as _select
    _recent_cutoff = datetime.now(timezone.utc) - timedelta(minutes=3)
    _recent_closed_result = await db.execute(
        _select(ClosedTrade.symbol)
        .where(ClosedTrade.session_date == session_date_str())
        .where(ClosedTrade.closed_at >= _recent_cutoff)
    )
    _recently_closed_symbols = {row[0] for row in _recent_closed_result.fetchall()}

    for sym, ap in alpaca_by_symbol.items():
        if sym not in local_by_symbol:
            if sym in _recently_closed_symbols:
                logger.info(
                    "ALPACA SYNC: skipping re-import of %s — closed locally <3 min ago (close still in-flight)",
                    sym,
                )
                continue
            side = ap.get("side", "long")
            # Alpaca returns "long"/"short"; normalise
            if side not in ("long", "short"):
                side = "long"
            pos = PaperPosition(
                symbol=sym,
                side=side,
                qty=abs(ap["qty"]),
                avg_entry_price=ap["avg_entry_price"],
                current_price=ap.get("current_price") or ap["avg_entry_price"],
                stop_price=None,
                take_profit_price=None,
                execution_mode="paper_alpaca",
                order_id=None,
                session_date=session_date_str(),
                strategy_version="alpaca_import",
            )
            db.add(pos)
            logger.info(
                "ALPACA SYNC: imported %s %s qty=%.4f @ $%.2f",
                side, sym, abs(ap["qty"]), ap["avg_entry_price"],
            )

    # ── Remove local positions that no longer exist in Alpaca ─────────────────
    for sym, lp in local_by_symbol.items():
        if sym not in alpaca_by_symbol:
            await db.delete(lp)
            logger.info("ALPACA SYNC: removed stale local position %s (closed in Alpaca)", sym)

    await db.commit()


async def flatten_all_positions(db: AsyncSession, prices: Dict[str, float]) -> List[ClosedTrade]:
    """Close all open positions at provided prices (EOD flatten)."""
    positions = await get_open_positions(db)
    closed: List[ClosedTrade] = []
    for pos in positions:
        price = prices.get(pos.symbol)
        if price:
            trade = await _close_position(db, pos.symbol, price, "eod_flatten")
            if trade:
                closed.append(trade)
    await db.commit()
    if closed:
        logger.info("EOD flatten: closed %d positions.", len(closed))
    return closed


async def get_daily_pnl(db: AsyncSession, session_date: Optional[str] = None) -> float:
    trades = await get_closed_trades(db, session_date or session_date_str())
    return round(sum(t.pnl for t in trades), 2)
