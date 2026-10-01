"""
Shadow trade service.

A shadow trade is a setup that ALMOST qualified for live execution but did not
(score between SHADOW_TRADE_MIN_SCORE and A/B thresholds, or failed a soft gate).

This module:
  1. Creates ShadowTrade records at setup evaluation time.
  2. Updates max-favorable and max-adverse excursion on every price tick.
  3. Closes shadow trades when their stop, target, or EOD is hit.
  4. Persists RejectedSetup records for every evaluated setup (including A/B/shadow).

No real orders are sent for shadow trades.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.trading import RejectedSetup, ShadowTrade
from app.schemas.signals import SignalScore, TradeTier
from app.utils.time_utils import session_date_str

logger = logging.getLogger(__name__)

# In-memory open shadow trades for fast price-tick updates
# symbol → ShadowTrade.id
_open_shadow_ids: Dict[str, int] = {}


# ── Recording setups ──────────────────────────────────────────────────────────

async def log_rejected_setup(
    db: AsyncSession,
    sig: SignalScore,
    tier: TradeTier,
    rejection_reasons: List[str],
) -> RejectedSetup:
    """
    Persist a RejectedSetup record for any evaluated signal regardless of tier.
    Call this for EVERY signal that passes through the entry gate, including
    A-trades and B-trades that were actually executed — the rejection_reasons
    list will be empty in those cases.
    """
    # Extract indicator values from components where available
    rsi_val = _extract_component_detail(sig, "scalp_rsi", "rsi7") or \
              _extract_component_detail(sig, "technical_trend", "rsi14")
    macd_val = _extract_component_detail(sig, "scalp_macd", "hist") or \
               _extract_component_detail(sig, "technical_trend", "macd_hist")
    rvol = _extract_component_detail(sig, "scalp_volume", "rvol") or \
           _extract_component_detail(sig, "volume", "rvol")
    regime = _extract_component_detail(sig, "market_regime", "regime_label") or \
             _extract_component_detail(sig, "market_regime", "label")
    trend = _extract_component_detail(sig, "technical_trend", "label") or \
            _extract_component_detail(sig, "technical_trend", "trend")

    # VWAP condition
    vwap_cond = "no_vwap"
    if sig.vwap and sig.price:
        vwap_cond = "above" if sig.price >= sig.vwap else "below"

    # Signal JSON (truncated for safety)
    try:
        signal_json = json.dumps({
            "symbol": sig.symbol,
            "score": sig.total_score,
            "direction": sig.direction,
            "color": sig.color.value,
            "label": sig.label.value,
            "components": [
                {"name": c.name, "raw": round(c.raw_score, 3), "dir": c.direction}
                for c in sig.components
            ],
        })
    except Exception:
        signal_json = None

    setup = RejectedSetup(
        evaluated_at=sig.scored_at,
        session_date=session_date_str(),
        symbol=sig.symbol,
        price=sig.price,
        direction=sig.direction,
        score=sig.total_score,
        tier=tier.value,
        rejection_reasons=",".join(rejection_reasons) if rejection_reasons else None,
        vwap=sig.vwap,
        vwap_condition=vwap_cond,
        rsi=rsi_val,
        macd_hist=macd_val,
        volume_rvol=rvol,
        market_regime=str(regime) if regime else None,
        trend_condition=str(trend) if trend else None,
        suggested_stop=sig.thesis.suggested_stop if sig.thesis else None,
        suggested_target=sig.thesis.suggested_target if sig.thesis else None,
        signal_json=signal_json,
    )
    db.add(setup)
    try:
        await db.commit()
    except Exception as exc:
        logger.warning("Failed to persist rejected setup for %s: %s", sig.symbol, exc)
        await db.rollback()
    return setup


async def open_shadow_trade(
    db: AsyncSession,
    sig: SignalScore,
    strategy: str = "predictor",
) -> Optional[ShadowTrade]:
    """
    Create and persist a new open shadow trade.
    Only called when tier == SHADOW and ENABLE_SHADOW_TRADES is True.
    """
    if not settings.ENABLE_SHADOW_TRADES:
        return None

    if not sig.price or not sig.thesis:
        return None

    # Don't open a second shadow trade for the same symbol in the same session
    existing = await db.execute(
        select(ShadowTrade).where(
            ShadowTrade.symbol == sig.symbol,
            ShadowTrade.session_date == session_date_str(),
            ShadowTrade.status == "open",
        )
    )
    if existing.scalar_one_or_none():
        return None

    try:
        signal_json = json.dumps({
            "score": sig.total_score,
            "direction": sig.direction,
            "price": sig.price,
        })
    except Exception:
        signal_json = None

    trade = ShadowTrade(
        session_date=session_date_str(),
        symbol=sig.symbol,
        strategy=strategy,
        direction=sig.direction,
        score=sig.total_score,
        entry_price=sig.price,
        stop_price=sig.thesis.suggested_stop,
        target_price=sig.thesis.suggested_target,
        entered_at=sig.scored_at,
        status="open",
        signal_json=signal_json,
    )
    db.add(trade)
    try:
        await db.flush()
        await db.commit()
        _open_shadow_ids[sig.symbol] = trade.id
        logger.debug("Shadow trade opened: %s %.4f dir=%d score=%.1f",
                     sig.symbol, sig.price, sig.direction, sig.total_score)
    except Exception as exc:
        logger.warning("Shadow trade open failed for %s: %s", sig.symbol, exc)
        await db.rollback()
        return None

    return trade


async def update_shadow_prices(
    db: AsyncSession,
    symbol: str,
    current_price: float,
) -> Optional[ShadowTrade]:
    """
    Update MFE/MAE for open shadow trades on this symbol.
    Closes the shadow trade if stop or target is hit.
    Returns the ShadowTrade if it was closed.
    """
    result = await db.execute(
        select(ShadowTrade).where(
            ShadowTrade.symbol == symbol,
            ShadowTrade.status == "open",
        )
    )
    trade = result.scalar_one_or_none()
    if not trade:
        return None

    entry = trade.entry_price
    direction = trade.direction

    # Per-share excursion
    if direction == 1:
        excursion = current_price - entry
    else:
        excursion = entry - current_price

    # MFE — max in-the-money move
    if excursion > 0:
        if trade.max_favorable is None or excursion > trade.max_favorable:
            trade.max_favorable = round(excursion, 4)
    else:
        adverse = abs(excursion)
        if trade.max_adverse is None or adverse > trade.max_adverse:
            trade.max_adverse = round(adverse, 4)

    # Stop hit?
    if trade.stop_price:
        stop_hit = (
            (direction == 1  and current_price <= trade.stop_price) or
            (direction == -1 and current_price >= trade.stop_price)
        )
        if stop_hit:
            return await _close_shadow(db, trade, current_price, "stop_hit")

    # Target hit?
    if trade.target_price:
        target_hit = (
            (direction == 1  and current_price >= trade.target_price) or
            (direction == -1 and current_price <= trade.target_price)
        )
        if target_hit:
            return await _close_shadow(db, trade, current_price, "target_hit")

    try:
        await db.commit()
    except Exception:
        await db.rollback()
    return None


async def eod_close_shadows(db: AsyncSession, prices: Dict[str, float]) -> int:
    """Close all open shadow trades at EOD. Returns number closed."""
    result = await db.execute(
        select(ShadowTrade).where(ShadowTrade.status == "open")
    )
    trades = result.scalars().all()
    closed = 0
    for trade in trades:
        price = prices.get(trade.symbol)
        if price:
            await _close_shadow(db, trade, price, "eod")
            closed += 1
    if closed:
        logger.info("EOD: closed %d shadow trades", closed)
    return closed


async def _close_shadow(
    db: AsyncSession,
    trade: ShadowTrade,
    exit_price: float,
    reason: str,
) -> ShadowTrade:
    entry = trade.entry_price
    direction = trade.direction
    pnl = (exit_price - entry) if direction == 1 else (entry - exit_price)

    trade.exit_price = exit_price
    trade.exit_reason = reason
    trade.pnl_per_share = round(pnl, 4)
    trade.is_winner = pnl > 0
    trade.exited_at = datetime.now(timezone.utc)
    trade.status = f"closed_{reason}"

    _open_shadow_ids.pop(trade.symbol, None)
    try:
        await db.commit()
    except Exception as exc:
        logger.warning("Shadow trade close failed for %s: %s", trade.symbol, exc)
        await db.rollback()

    logger.debug(
        "Shadow trade closed: %s @ %.4f pnl=%.4f (%s)",
        trade.symbol, exit_price, pnl, reason,
    )
    return trade


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_component_detail(sig: SignalScore, component_name: str, key: str):
    comp = sig.component_by_name(component_name)
    if comp:
        return comp.details.get(key)
    return None
