"""
Broad market regime filter using SPY and/or QQQ (0–10 points).

Evaluates:
 - SPY and QQQ position relative to their VWAPs
 - SPY/QQQ trend structure
 - Regime alignment with proposed trade direction
"""
from __future__ import annotations

from typing import Optional

from app.schemas.market_data import SymbolState
from app.schemas.signals import ComponentScore
from app.signal_engine.indicators import trend_structure
from app.utils.math_utils import clamp


def score_market_regime(
    target_direction: int,
    spy_state: Optional[SymbolState],
    qqq_state: Optional[SymbolState],
    weight: int = 10,
) -> ComponentScore:
    """
    Score how well the broad market regime aligns with the proposed trade direction.

    target_direction: +1 for long, -1 for short, 0 for neutral.
    """
    if spy_state is None and qqq_state is None:
        return ComponentScore(
            name="market_regime",
            raw_score=0.5,
            weight=weight,
            weighted_score=0.5 * weight,
            direction=0,
            details={"reason": "no_regime_data"},
        )

    regime_signals: list[int] = []   # +1 bullish, -1 bearish, 0 neutral
    detail_parts: dict = {}

    for name, state in [("spy", spy_state), ("qqq", qqq_state)]:
        if state is None:
            continue
        bars = state.bars_5m if len(state.bars_5m) >= 8 else state.bars_1m
        if not bars:
            continue

        ts = trend_structure(bars, lookback=5)

        above = None
        if state.vwap and state.last_price:
            above = state.last_price > state.vwap

        # Combine
        if ts == 1 and above:
            sig = 1
        elif ts == -1 and above is False:
            sig = -1
        elif ts == 1:
            sig = 1
        elif ts == -1:
            sig = -1
        elif above is True:
            sig = 1
        elif above is False:
            sig = -1
        else:
            sig = 0

        regime_signals.append(sig)
        detail_parts[name] = {
            "trend_structure": ts,
            "above_vwap": above,
            "signal": sig,
            "price": round(state.last_price, 2) if state.last_price else None,
        }

    if not regime_signals:
        return ComponentScore(
            name="market_regime",
            raw_score=0.5,
            weight=weight,
            weighted_score=0.5 * weight,
            direction=0,
            details={"reason": "no_valid_regime_data"},
        )

    # Aggregate: average signal
    avg_regime = sum(regime_signals) / len(regime_signals)  # -1 to +1

    # Market direction
    market_dir = 1 if avg_regime > 0.3 else (-1 if avg_regime < -0.3 else 0)

    # Alignment with trade direction
    if target_direction == 0:
        # No direction yet — just report regime
        raw_score = 0.5 + abs(avg_regime) * 0.1
    elif target_direction == market_dir:
        # Full alignment
        raw_score = 0.7 + abs(avg_regime) * 0.3
    elif market_dir == 0:
        # Unclear market — neither helps nor hurts
        raw_score = 0.5
    else:
        # Counter-trend — penalise hard
        raw_score = 0.25 - abs(avg_regime) * 0.15

    raw_score = clamp(raw_score)

    return ComponentScore(
        name="market_regime",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=market_dir,
        details={
            "avg_regime_signal": round(avg_regime, 2),
            "market_direction": market_dir,
            "aligned_with_trade": target_direction == market_dir,
            **detail_parts,
        },
    )
