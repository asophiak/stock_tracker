"""
Technical trend quality scorer (0–25 points).

Evaluates (in order of impact):
 1. Trend structure (HH/HL vs LH/LL) on 5m bars           — backbone signal
 2. 15-minute multi-timeframe confirmation                  — highest-impact addition
 3. RSI overbought/oversold filter                          — prevents chasing
 4. MACD momentum confirmation                             — early trend exhaustion
 5. EMA slope direction and magnitude
 6. Momentum (price change over window)
 7. Bar quality (% of recent bars in direction)
 8. EMA cross (price vs 9-EMA and 21-EMA)
"""
from __future__ import annotations

from typing import Optional

from app.schemas.market_data import SymbolState
from app.schemas.signals import ComponentScore
from app.signal_engine.indicators import compute_macd, compute_rsi, ema_slope, trend_structure
from app.utils.math_utils import clamp, ema, momentum, safe_div


def score_technical_trend(state: SymbolState, weight: int = 25) -> ComponentScore:
    bars = state.bars_5m if len(state.bars_5m) >= 10 else state.bars_1m

    if len(bars) < 5:
        return ComponentScore(
            name="technical_trend",
            raw_score=0.0,
            weight=weight,
            weighted_score=0.0,
            direction=0,
            details={"reason": "insufficient_bars"},
        )

    closes = [b.close for b in bars]

    # ── 1. Trend structure from swing points ──────────────────────────────────
    ts = trend_structure(bars, lookback=5)

    # ── 2. EMA slope ──────────────────────────────────────────────────────────
    slope = ema_slope(bars, period=9) or 0.0
    slope_score = clamp(abs(slope) * 200, 0.0, 1.0)

    # ── 3. Momentum (10-bar) ──────────────────────────────────────────────────
    mom = momentum(closes, 10) or 0.0
    mom_score = clamp(abs(mom) * 20, 0.0, 1.0)

    # ── 4. Directional bar ratio (last 10 bars mostly in one direction) ───────
    recent = bars[-10:] if len(bars) >= 10 else bars
    bullish_bars = sum(1 for b in recent if b.is_bullish)
    bar_ratio = bullish_bars / len(recent) if len(recent) > 0 else 0.5
    directional_score = clamp(abs(bar_ratio - 0.5) * 2, 0.0, 1.0)

    # ── 5. EMA cross: price vs 9-EMA and 21-EMA ───────────────────────────────
    ema9 = ema(closes, 9)
    ema21 = ema(closes, 21)
    above_ema9 = closes[-1] > ema9 if ema9 else None
    above_ema21 = closes[-1] > ema21 if ema21 else None
    ema_score = 0.0
    if above_ema9 is not None:
        ema_score += 0.5 if above_ema9 else 0.0
    if above_ema21 is not None:
        ema_score += 0.5 if above_ema21 else 0.0

    # ── Determine dominant direction ──────────────────────────────────────────
    direction = ts  # swing-point structure is most reliable
    if direction == 0:
        if mom > 0.002:
            direction = 1
        elif mom < -0.002:
            direction = -1

    # ── Base score from trend structure ───────────────────────────────────────
    base = 0.35 if ts != 0 else 0.0

    raw_score = (
        base
        + slope_score * 0.18
        + mom_score * 0.18
        + directional_score * 0.09
        + ema_score * 0.10
    )

    # Penalise when momentum contradicts the structural direction
    if direction != 0 and ((direction == 1 and mom < 0) or (direction == -1 and mom > 0)):
        raw_score *= 0.70

    # ── 6. RSI overbought / oversold filter ───────────────────────────────────
    rsi_val = compute_rsi(bars, period=14)
    rsi_adjustment = 0.0
    rsi_label = "n/a"
    if rsi_val is not None:
        if direction == 1:   # Long — penalise overbought entries
            if rsi_val > 80:
                rsi_adjustment = -0.28   # severely overbought — high reversal risk
                rsi_label = "severely_overbought"
            elif rsi_val > 75:
                rsi_adjustment = -0.18
                rsi_label = "overbought"
            elif rsi_val > 70:
                rsi_adjustment = -0.10
                rsi_label = "mildly_overbought"
            elif 45 <= rsi_val <= 65:
                rsi_adjustment = +0.08   # pullback-to-momentum sweet spot
                rsi_label = "ideal_zone"
        elif direction == -1:   # Short — penalise oversold entries
            if rsi_val < 20:
                rsi_adjustment = -0.28
                rsi_label = "severely_oversold"
            elif rsi_val < 25:
                rsi_adjustment = -0.18
                rsi_label = "oversold"
            elif rsi_val < 30:
                rsi_adjustment = -0.10
                rsi_label = "mildly_oversold"
            elif 35 <= rsi_val <= 55:
                rsi_adjustment = +0.08
                rsi_label = "ideal_zone"

    raw_score = clamp(raw_score + rsi_adjustment)

    # ── 7. MACD momentum confirmation ─────────────────────────────────────────
    macd_result = compute_macd(bars, fast=12, slow=26, signal_period=9)
    macd_adjustment = 0.0
    macd_label = "n/a"
    if macd_result is not None:
        macd_line, signal_line, histogram = macd_result
        if direction == 1:
            if macd_line > 0 and histogram > 0:
                macd_adjustment = +0.08   # above zero AND histogram expanding
                macd_label = "bullish_confirmed"
            elif macd_line > signal_line:
                macd_adjustment = +0.04   # bullish cross
                macd_label = "bullish_cross"
            elif histogram < 0 and abs(histogram) > abs(macd_line) * 0.5:
                macd_adjustment = -0.06   # histogram pulling hard against direction
                macd_label = "bearish_divergence"
        elif direction == -1:
            if macd_line < 0 and histogram < 0:
                macd_adjustment = +0.08
                macd_label = "bearish_confirmed"
            elif macd_line < signal_line:
                macd_adjustment = +0.04
                macd_label = "bearish_cross"
            elif histogram > 0 and abs(histogram) > abs(macd_line) * 0.5:
                macd_adjustment = -0.06
                macd_label = "bullish_divergence"

    raw_score = clamp(raw_score + macd_adjustment)

    # ── 8. 15-minute multi-timeframe confirmation (highest impact) ────────────
    mtf_adjustment = 0.0
    ts_15m = 0
    mtf_label = "no_data"
    bars_15m = state.bars_15m
    if bars_15m and len(bars_15m) >= 8:
        ts_15m = trend_structure(bars_15m, lookback=3)
        if direction != 0 and ts_15m == direction:
            mtf_adjustment = +0.15   # all timeframes aligned — high confidence
            mtf_label = "aligned"
        elif ts_15m == -direction and direction != 0:
            mtf_adjustment = -0.20   # 15m directly contradicts 5m — major warning
            mtf_label = "contradicted"
        else:
            mtf_label = "neutral"

    raw_score = clamp(raw_score + mtf_adjustment)

    return ComponentScore(
        name="technical_trend",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={
            "trend_structure_5m": ts,
            "trend_structure_15m": ts_15m,
            "mtf_label": mtf_label,
            "ema_slope": round(slope, 5),
            "momentum_10": round(mom, 4),
            "directional_bar_ratio": round(bar_ratio, 2),
            "above_ema9": above_ema9,
            "above_ema21": above_ema21,
            "rsi": round(rsi_val, 1) if rsi_val is not None else None,
            "rsi_label": rsi_label,
            "macd_line": round(macd_result[0], 4) if macd_result else None,
            "macd_signal": round(macd_result[1], 4) if macd_result else None,
            "macd_histogram": round(macd_result[2], 4) if macd_result else None,
            "macd_label": macd_label,
            "rsi_adjustment": round(rsi_adjustment, 3),
            "macd_adjustment": round(macd_adjustment, 3),
            "mtf_adjustment": round(mtf_adjustment, 3),
            "bars_used": len(bars),
        },
    )
