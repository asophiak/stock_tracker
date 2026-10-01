"""
VWAP / opening range / level interaction scorer (0–15 points).

Evaluates:
 - Price position relative to VWAP
 - Distance from VWAP — normalised by ATR so thresholds are volatility-aware
   (NVDA at 1.5% from VWAP ≠ SOFI at 1.5% from VWAP)
 - VWAP trend (VWAP itself sloping up/down)
 - Opening range breakout/breakdown confirmation
 - Proximity to key intraday S/R levels
"""
from __future__ import annotations

from typing import Optional

from app.schemas.market_data import SymbolState
from app.schemas.signals import ComponentScore
from app.signal_engine.indicators import compute_atr
from app.utils.math_utils import clamp


def score_vwap(state: SymbolState, weight: int = 15) -> ComponentScore:
    price = state.last_price
    vwap = state.vwap
    orh = state.opening_range_high
    orl = state.opening_range_low

    if price is None or price == 0:
        return ComponentScore(
            name="vwap",
            raw_score=0.0,
            weight=weight,
            weighted_score=0.0,
            direction=0,
            details={"reason": "no_price"},
        )

    direction = 0
    vwap_score = 0.5          # neutral default when no VWAP
    or_score = 0.5            # neutral default

    # ── Compute ATR for volatility-normalised distance thresholds ─────────────
    # Use 5m bars when available; fall back to 1m.
    atr_bars = state.bars_5m if len(state.bars_5m) >= 15 else state.bars_1m
    atr_val = compute_atr(atr_bars, period=10) if len(atr_bars) >= 11 else None

    # ── VWAP analysis ─────────────────────────────────────────────────────────
    if vwap and vwap > 0:
        dist_abs = abs(price - vwap)
        above = price > vwap

        if atr_val and atr_val > 0:
            # Distance measured in ATR multiples — volatility-normalised
            dist_atr = dist_abs / atr_val
            if dist_atr < 0.20:
                vwap_score = 0.55   # AT VWAP — testing, could go either way
            elif dist_atr < 0.80:
                vwap_score = 0.88   # within 1 ATR — prime entry zone
            elif dist_atr < 1.50:
                vwap_score = 0.65   # acceptable — some extension
            elif dist_atr < 2.50:
                vwap_score = 0.38   # getting extended — chasing risk
            else:
                vwap_score = 0.18   # far overextended — high mean-reversion risk
            dist_metric = dist_atr
            dist_label = "atr_multiples"
        else:
            # Fallback to absolute % when ATR is unavailable (insufficient bars)
            dist_pct = dist_abs / vwap
            if dist_pct < 0.001:
                vwap_score = 0.55
            elif dist_pct < 0.004:
                vwap_score = 0.85
            elif dist_pct < 0.008:
                vwap_score = 0.65
            elif dist_pct < 0.015:
                vwap_score = 0.40
            else:
                vwap_score = 0.20
            dist_metric = dist_pct
            dist_label = "pct"

        direction = 1 if above else -1

        # VWAP slope bonus: if slope aligns with our side, boost slightly
        vwap_bars = [b.vwap for b in state.bars_1m[-10:] if b.vwap]
        if len(vwap_bars) >= 5:
            slope = (vwap_bars[-1] - vwap_bars[0]) / vwap_bars[0] if vwap_bars[0] != 0 else 0
            if (slope > 0 and above) or (slope < 0 and not above):
                vwap_score = min(1.0, vwap_score + 0.08)

    # ── Opening range analysis ────────────────────────────────────────────────
    if orh and orl and state.opening_range_set:
        or_range = orh - orl
        if or_range > 0:
            if price > orh:
                breakout_ext = (price - orh) / or_range
                if breakout_ext < 0.5:
                    or_score = 0.88   # fresh breakout — ideal entry
                elif breakout_ext < 1.5:
                    or_score = 0.65   # extended but still valid
                else:
                    or_score = 0.35   # too far from break
                direction = max(direction, 1)
            elif price < orl:
                breakdown_ext = (orl - price) / or_range
                if breakdown_ext < 0.5:
                    or_score = 0.88
                elif breakdown_ext < 1.5:
                    or_score = 0.65
                else:
                    or_score = 0.35
                direction = min(direction, -1)
            else:
                or_score = 0.30   # inside OR — no edge yet
                dist_to_orh = (orh - price) / or_range
                dist_to_orl = (price - orl) / or_range
                if dist_to_orh < 0.10:
                    or_score = 0.55   # testing ORH
                elif dist_to_orl < 0.10:
                    or_score = 0.55   # testing ORL

    # ── Composite ─────────────────────────────────────────────────────────────
    raw_score = vwap_score * 0.55 + or_score * 0.45
    raw_score = clamp(raw_score)

    return ComponentScore(
        name="vwap",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={
            "vwap": round(vwap, 4) if vwap else None,
            "price": round(price, 4),
            "above_vwap": direction == 1 if vwap else None,
            "dist_from_vwap_pct": round((price - vwap) / vwap * 100, 3) if vwap else None,
            "atr": round(atr_val, 4) if atr_val else None,
            "opening_range_high": round(orh, 4) if orh else None,
            "opening_range_low": round(orl, 4) if orl else None,
            "vwap_score": round(vwap_score, 2),
            "or_score": round(or_score, 2),
        },
    )
