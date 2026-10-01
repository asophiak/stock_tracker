"""
Signal scoring service.

Runs the signal engine for all active symbols on a schedule,
persists snapshots, updates best-trade-of-day, and triggers alerts.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.signals import PatternHistoryEntry, SignalSnapshot
from app.schemas.market_data import SymbolState
from app.schemas.signals import SignalColor, SignalScore, TradeLabel
from app.services.market_session import should_score_signals
from app.services.news_service import get_cached_news
from app.signal_engine.engine import score_symbol
from app.signal_engine.scalp_engine import score_scalp
from app.utils.cache import StateManager
from app.utils.time_utils import session_date_str

logger = logging.getLogger(__name__)

# In-memory latest signal per symbol (swing engine)
_latest_signals: Dict[str, SignalScore] = {}

# In-memory latest scalp signal per symbol (1m engine)
_latest_scalp_signals: Dict[str, SignalScore] = {}

# Pattern dedup: tracks the last time each (symbol, pattern_name) was persisted.
# Prevents re-saving the same pattern on every 1-minute bar while it's still "active".
_pattern_last_saved: Dict[tuple, datetime] = {}
_PATTERN_COOLDOWN_SEC = 300   # 5 minutes


def get_latest_signal(symbol: str) -> Optional[SignalScore]:
    return _latest_signals.get(symbol)


def get_all_latest_signals() -> Dict[str, SignalScore]:
    return dict(_latest_signals)


def get_latest_scalp_signal(symbol: str) -> Optional[SignalScore]:
    return _latest_scalp_signals.get(symbol)


def get_all_latest_scalp_signals() -> Dict[str, SignalScore]:
    return dict(_latest_scalp_signals)


async def run_scoring_cycle(
    state_manager: StateManager,
    db: AsyncSession,
    symbols: List[str],
    offline: bool = False,
) -> None:
    """
    Score all symbols, update best-trade-of-day, persist snapshots,
    and broadcast SSE updates.

    offline=True scores outside market hours from whatever bars are loaded
    (the last session) so the dashboard has data after a restart. Nothing is
    persisted in that mode.
    """
    if not offline and not should_score_signals():
        return

    spy_state = state_manager.get_state("SPY") if "SPY" in symbols else None
    qqq_state = state_manager.get_state("QQQ") if "QQQ" in symbols else None

    for symbol in symbols:
        if symbol in ("SPY", "QQQ"):
            # Still score regime instruments but don't treat as trade candidates
            state = state_manager.get_state(symbol)
            news = get_cached_news(symbol)
            sig = score_symbol(state, news, spy_state=None, qqq_state=None)
            _latest_signals[symbol] = sig
            continue

        state = state_manager.get_state(symbol)
        if not state.last_price:
            continue

        news = get_cached_news(symbol)

        # Use adaptive weights if the bot has learned enough about this symbol
        adaptive_weights = None
        try:
            from app.services.learning_service import get_adaptive_weights
            adaptive_weights = await get_adaptive_weights(db, symbol)
        except Exception:
            pass

        sig = score_symbol(state, news, spy_state=spy_state, qqq_state=qqq_state,
                           adaptive_weights=adaptive_weights)
        _latest_signals[symbol] = sig

        # Scalp engine — runs in parallel, 1m bars only
        if settings.SCALP_MODE_ENABLED:
            scalp_sig = score_scalp(state)
            _latest_scalp_signals[symbol] = scalp_sig

        if not offline:
            # Persist significant signals
            if sig.label in (TradeLabel.POSSIBLE_TRADE, TradeLabel.IMMEDIATE_TRADE):
                await _persist_signal(sig, db)

            # Always persist any newly detected candlestick patterns to history
            await _persist_patterns(sig, db)

        # Broadcast to SSE clients
        _broadcast_signal_update(sig, state_manager)

    # Update best trade of day
    _update_best_trade(state_manager, symbols)
    state_manager.last_score_time = datetime.now(timezone.utc)

    logger.debug(
        "Scoring cycle complete. Best: %s",
        state_manager.best_trade_of_day.symbol if state_manager.best_trade_of_day else "none",
    )


def _update_best_trade(state_manager: StateManager, symbols: List[str]) -> None:
    best: Optional[SignalScore] = None
    for sym in symbols:
        if sym in ("SPY", "QQQ"):
            continue
        sig = _latest_signals.get(sym)
        if sig is None:
            continue
        if sig.label == TradeLabel.NO_TRADE:
            continue
        if sig.direction == 0 or not sig.price:
            continue
        if sig.total_score < settings.BOT_ENTRY_SCORE:
            continue
        if best is None or sig.total_score > best.total_score:
            best = sig

    if best:
        if best.thesis:
            best.thesis.is_best_trade_of_day = True
        best.is_best_trade_of_day = True
        state_manager.best_trade_of_day = best
        # Mark previous as not-best
        for sym, sig in _latest_signals.items():
            if sym != best.symbol and sig.is_best_trade_of_day:
                sig.is_best_trade_of_day = False


def _broadcast_signal_update(sig: SignalScore, state_manager: StateManager) -> None:
    payload = {
        "symbol": sig.symbol,
        "score": sig.total_score,
        "color": sig.color.value,
        "label": sig.label.value,
        "direction": sig.direction,
        "price": sig.price,
        "vwap": sig.vwap,
        "is_best": sig.is_best_trade_of_day,
    }
    state_manager.broadcast_sse("signal_update", payload)


async def _persist_patterns(sig: SignalScore, db: AsyncSession) -> None:
    """
    Save newly detected candlestick patterns to pattern_history.
    Each (symbol, pattern_name) is rate-limited to once per 5 minutes so the same
    pattern doesn't flood the history while it persists across multiple bars.
    """
    candle_comp = next((c for c in sig.components if c.name == "candlestick"), None)
    if not candle_comp:
        return
    patterns = candle_comp.details.get("patterns", [])
    if not patterns:
        return

    now = sig.scored_at
    added = False
    for p in patterns:
        key = (sig.symbol, p["name"])
        last = _pattern_last_saved.get(key)
        if last and (now - last).total_seconds() < _PATTERN_COOLDOWN_SEC:
            continue  # same pattern saved recently — skip

        _pattern_last_saved[key] = now
        db.add(PatternHistoryEntry(
            symbol=sig.symbol,
            detected_at=now,
            pattern_name=p["name"],
            direction=p["direction"],
            strength=round(p["strength"], 3),
            description=p.get("description", ""),
            price=sig.price,
            signal_score=round(sig.total_score, 2),
            signal_label=sig.label.value,
            session_date=session_date_str(),
        ))
        added = True

    if added:
        try:
            await db.commit()
        except Exception as exc:
            logger.warning("Pattern history save failed for %s: %s", sig.symbol, exc)
            await db.rollback()


async def _persist_signal(sig: SignalScore, db: AsyncSession) -> None:
    try:
        thesis_json = None
        if sig.thesis:
            from dataclasses import asdict
            thesis_json = json.dumps(asdict(sig.thesis))

        comp = {c.name: c.weighted_score for c in sig.components}

        snapshot = SignalSnapshot(
            symbol=sig.symbol,
            scored_at=sig.scored_at,
            total_score=sig.total_score,
            direction=sig.direction,
            color=sig.color.value,
            label=sig.label.value,
            price=sig.price,
            vwap=sig.vwap,
            score_technical=comp.get("technical_trend"),
            score_candlestick=comp.get("candlestick"),
            score_volume=comp.get("volume"),
            score_vwap=comp.get("vwap"),
            score_regime=comp.get("market_regime"),
            score_news=comp.get("news"),
            thesis_json=thesis_json,
            is_best_trade_of_day=sig.is_best_trade_of_day,
            session_date=session_date_str(),
        )
        db.add(snapshot)
        await db.commit()
    except Exception as exc:
        logger.warning("Signal persistence failed for %s: %s", sig.symbol, exc)
        await db.rollback()
