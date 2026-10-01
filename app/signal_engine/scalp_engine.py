"""
Scalp signal engine — optimized for 1m bar high-frequency day trading.

Runs entirely on 1m bars with fast indicators:
  EMA(3/8) cross, RSI(7), MACD(5/13/4), 5-bar quality, VWAP side, volume spike.

Direction consensus is intentionally lower than the swing engine:
  1 component with raw_score >= 0.40 is enough to set direction.

Stops: ATR(14, 1m) * 0.8  (min 0.3% of price)
Targets: 2× stop distance (2:1 RR)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from app.config import settings
from app.schemas.market_data import SymbolState
from app.schemas.signals import (
    ComponentScore,
    SignalColor,
    SignalScore,
    TradeLabel,
    TradingThesis,
)
from app.signal_engine.indicators import compute_atr, compute_macd, compute_rsi
from app.utils.math_utils import clamp, ema

logger = logging.getLogger(__name__)

_DIRECTION_MIN_RAW = 0.40


# ── Component scorers ──────────────────────────────────────────────────────────

def _ema_cross_score(bars, weight: int = 20) -> ComponentScore:
    """EMA(3) vs EMA(8) — primary direction + strength signal."""
    if len(bars) < 10:
        return ComponentScore(name="scalp_ema", raw_score=0.30, weight=weight,
                              weighted_score=0.30 * weight, direction=0,
                              details={"reason": "insufficient_bars"})

    closes = [b.close for b in bars]
    fast = ema(closes, 3)
    slow = ema(closes, 8)

    if fast is None or slow is None or closes[-1] == 0:
        return ComponentScore(name="scalp_ema", raw_score=0.30, weight=weight,
                              weighted_score=0.30 * weight, direction=0,
                              details={"reason": "ema_unavailable"})

    sep = (fast - slow) / closes[-1]

    if fast > slow:
        direction = 1
    elif fast < slow:
        direction = -1
    else:
        direction = 0

    raw_score = clamp(0.50 + abs(sep) * 300, 0.0, 1.0) if direction != 0 else 0.30

    return ComponentScore(
        name="scalp_ema",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={"ema3": round(fast, 4), "ema8": round(slow, 4),
                 "sep_pct": round(sep * 100, 4)},
    )


def _bar_quality_score(bars, weight: int = 20) -> ComponentScore:
    """% of last 5 bars moving in one direction — detects micro-trend."""
    if len(bars) < 5:
        return ComponentScore(name="scalp_bar_quality", raw_score=0.30, weight=weight,
                              weighted_score=0.30 * weight, direction=0,
                              details={"reason": "insufficient_bars"})

    recent = bars[-5:]
    bull = sum(1 for b in recent if b.is_bullish)
    ratio = bull / len(recent)

    if ratio >= 0.80:
        direction, raw_score = 1, clamp(ratio, 0.0, 1.0)
    elif ratio <= 0.20:
        direction, raw_score = -1, clamp(1.0 - ratio, 0.0, 1.0)
    elif ratio >= 0.60:
        direction, raw_score = 1, 0.55
    elif ratio <= 0.40:
        direction, raw_score = -1, 0.55
    else:
        direction, raw_score = 0, 0.30

    return ComponentScore(
        name="scalp_bar_quality",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={"bull_bars_5": bull, "ratio": round(ratio, 2)},
    )


def _vwap_side_score(state: SymbolState, weight: int = 15) -> ComponentScore:
    """Price above/below VWAP adds directional conviction."""
    price, vwap = state.last_price, state.vwap
    if not price or not vwap:
        return ComponentScore(name="scalp_vwap", raw_score=0.50, weight=weight,
                              weighted_score=0.50 * weight, direction=0,
                              details={"reason": "no_vwap"})

    dist = (price - vwap) / vwap
    direction = 1 if price > vwap else -1
    raw_score = clamp(0.55 + abs(dist) * 10, 0.0, 0.90)

    return ComponentScore(
        name="scalp_vwap",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={"price": price, "vwap": round(vwap, 4), "dist_pct": round(dist * 100, 3)},
    )


def _volume_surge_score(bars, weight: int = 15) -> ComponentScore:
    """Volume spike on the most recent bar confirms momentum."""
    if len(bars) < 10:
        return ComponentScore(name="scalp_volume", raw_score=0.40, weight=weight,
                              weighted_score=0.40 * weight, direction=0,
                              details={"reason": "insufficient_bars"})

    recent_vols = [b.volume for b in bars[-20:]]
    avg_vol = sum(recent_vols) / len(recent_vols) if recent_vols else 1
    last_vol = bars[-1].volume
    rvol = last_vol / avg_vol if avg_vol > 0 else 1.0

    if rvol >= 2.0:
        raw_score, label = 0.85, "surge"
    elif rvol >= 1.5:
        raw_score, label = 0.70, "elevated"
    elif rvol >= 1.0:
        raw_score, label = 0.55, "normal"
    else:
        raw_score, label = 0.35, "low"

    direction = 1 if bars[-1].is_bullish else -1

    return ComponentScore(
        name="scalp_volume",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={"rvol": round(rvol, 2), "label": label},
    )


def _fast_rsi_score(bars, direction: int, weight: int = 15) -> ComponentScore:
    """RSI(7) — penalises overbought longs and oversold shorts."""
    rsi_val = compute_rsi(bars, period=7)
    if rsi_val is None:
        return ComponentScore(name="scalp_rsi", raw_score=0.50, weight=weight,
                              weighted_score=0.50 * weight, direction=direction,
                              details={"reason": "unavailable"})

    label = "neutral"
    raw_score = 0.50

    if direction == 1:
        if rsi_val > 80:
            raw_score, label = 0.20, "overbought_block"
        elif rsi_val > 70:
            raw_score, label = 0.35, "overbought_warn"
        elif 40 <= rsi_val <= 65:
            raw_score, label = 0.75, "ideal"
    elif direction == -1:
        if rsi_val < 20:
            raw_score, label = 0.20, "oversold_block"
        elif rsi_val < 30:
            raw_score, label = 0.35, "oversold_warn"
        elif 35 <= rsi_val <= 60:
            raw_score, label = 0.75, "ideal"

    return ComponentScore(
        name="scalp_rsi",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={"rsi7": round(rsi_val, 1), "label": label},
    )


def _fast_macd_score(bars, direction: int, weight: int = 15) -> ComponentScore:
    """MACD(5, 13, 4) histogram direction confirms or contradicts momentum."""
    result = compute_macd(bars, fast=5, slow=13, signal_period=4)
    if result is None:
        return ComponentScore(name="scalp_macd", raw_score=0.40, weight=weight,
                              weighted_score=0.40 * weight, direction=direction,
                              details={"reason": "unavailable"})

    macd_line, signal_line, histogram = result
    label = "neutral"

    if direction == 1:
        if histogram > 0 and macd_line > signal_line:
            raw_score, label = 0.80, "bullish_confirmed"
        elif histogram > 0:
            raw_score, label = 0.65, "bullish_hist"
        elif histogram < 0:
            raw_score, label = 0.25, "bearish_hist"
        else:
            raw_score = 0.45
    elif direction == -1:
        if histogram < 0 and macd_line < signal_line:
            raw_score, label = 0.80, "bearish_confirmed"
        elif histogram < 0:
            raw_score, label = 0.65, "bearish_hist"
        elif histogram > 0:
            raw_score, label = 0.25, "bullish_hist"
        else:
            raw_score = 0.45
    else:
        raw_score = 0.40

    return ComponentScore(
        name="scalp_macd",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={"macd": round(macd_line, 4), "signal": round(signal_line, 4),
                 "hist": round(histogram, 4), "label": label},
    )


# ── Thesis builder ─────────────────────────────────────────────────────────────

def _build_scalp_thesis(price: float, direction: int, bars, score: float) -> TradingThesis:
    """Scalp thesis: ATR(14,1m)*1.5 stop (min 0.6%), 2× RR target."""
    atr_val = compute_atr(bars, period=14)
    stop_dist = max(atr_val * 1.5, price * 0.006) if atr_val else price * 0.006
    target_dist = stop_dist * 2.0

    if direction == 1:
        stop   = round(price - stop_dist, 4)
        target = round(price + target_dist, 4)
    else:
        stop   = round(price + stop_dist, 4)
        target = round(price - target_dist, 4)

    return TradingThesis(
        direction="long" if direction == 1 else "short",
        confidence=round(score, 1),
        why_now=f"Scalp — 1m momentum {'bullish' if direction == 1 else 'bearish'}",
        suggested_stop=stop,
        suggested_target=target,
        stop_pct=round(stop_dist / price * 100, 3),
        target_pct=round(target_dist / price * 100, 3),
        risk_reward=2.0,
        thesis_source="scalp_atr",
        risk_per_share=round(stop_dist, 4),
    )


# ── Main entry point ───────────────────────────────────────────────────────────

def score_scalp(state: SymbolState) -> SignalScore:
    """
    Score one symbol using the 1m scalp engine.
    Returns a SignalScore that can coexist with the swing engine's SignalScore.
    """
    bars = state.bars_1m

    if not state.last_price or len(bars) < 10:
        return SignalScore(
            symbol=state.symbol,
            scored_at=datetime.now(timezone.utc),
            total_score=0.0,
            direction=0,
            color=SignalColor.NEUTRAL,
            label=TradeLabel.NO_TRADE,
            price=state.last_price,
            vwap=state.vwap,
        )

    # ── Phase 1: direction-agnostic ───────────────────────────────────────────
    ema_score  = _ema_cross_score(bars, weight=20)
    bar_score  = _bar_quality_score(bars, weight=20)
    vwap_score = _vwap_side_score(state, weight=15)
    vol_score  = _volume_surge_score(bars, weight=15)

    # Direction: 1 confident component is enough (lower bar than swing engine)
    dir_votes: dict[int, float] = {1: 0.0, -1: 0.0, 0: 0.0}
    for comp in (ema_score, bar_score, vwap_score, vol_score):
        if comp.direction != 0 and comp.raw_score >= _DIRECTION_MIN_RAW:
            dir_votes[comp.direction] += comp.weighted_score

    if dir_votes[1] > dir_votes[-1] and dir_votes[1] > 0:
        dominant = 1
    elif dir_votes[-1] > dir_votes[1] and dir_votes[-1] > 0:
        dominant = -1
    else:
        dominant = 0

    # ── Phase 2: direction-aware ──────────────────────────────────────────────
    rsi_score  = _fast_rsi_score(bars, dominant, weight=15)
    macd_score = _fast_macd_score(bars, dominant, weight=15)

    components = [ema_score, bar_score, vwap_score, vol_score, rsi_score, macd_score]

    # ── Total score ───────────────────────────────────────────────────────────
    total = sum(c.weighted_score for c in components)
    max_w = sum(c.weight for c in components)
    if max_w > 0:
        total = (total / max_w) * 100.0
    total = max(0.0, min(100.0, total))

    # ── Color / label with scalp-specific thresholds ──────────────────────────
    if dominant == 0 or total < settings.SCALP_WATCH_THRESHOLD:
        color = SignalColor.NEUTRAL
        label = TradeLabel.NO_TRADE
    elif total >= settings.SCALP_FLASH_THRESHOLD:
        color = SignalColor.FLASH_GREEN if dominant == 1 else SignalColor.FLASH_RED
        label = TradeLabel.IMMEDIATE_TRADE
    elif total >= settings.SCALP_TRADE_THRESHOLD:
        color = SignalColor.GREEN if dominant == 1 else SignalColor.RED
        label = TradeLabel.POSSIBLE_TRADE
    else:
        color = SignalColor.GREEN if dominant == 1 else SignalColor.RED
        label = TradeLabel.WATCH

    thesis: Optional[TradingThesis] = None
    if dominant != 0 and label in (TradeLabel.POSSIBLE_TRADE, TradeLabel.IMMEDIATE_TRADE):
        thesis = _build_scalp_thesis(state.last_price, dominant, bars, total)

    return SignalScore(
        symbol=state.symbol,
        scored_at=datetime.now(timezone.utc),
        total_score=round(total, 1),
        direction=dominant,
        color=color,
        label=label,
        price=state.last_price,
        vwap=state.vwap,
        components=components,
        thesis=thesis,
    )
