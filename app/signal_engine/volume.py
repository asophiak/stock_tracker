"""
Volume / relative volume / liquidity scorer (0–15 points).

Evaluates:
 - Relative volume (RVOL vs average)
 - Volume trend (expanding or contracting)
 - Volume alignment with price direction (up-bars on high volume)
 - Liquidity proxy (spread)
"""
from __future__ import annotations

from app.schemas.market_data import SymbolState
from app.schemas.signals import ComponentScore
from app.signal_engine.indicators import compute_rvol
from app.utils.math_utils import clamp, safe_div


def score_volume(state: SymbolState, weight: int = 15) -> ComponentScore:
    bars = state.bars_1m

    # ── RVOL score ────────────────────────────────────────────────────────────
    rvol = compute_rvol(
        state.session_volume,
        state.avg_daily_volume,
        len(bars),
    )
    if rvol is None:
        rvol_score = 0.5  # neutral/unknown
    elif rvol >= 3.0:
        rvol_score = 1.0
    elif rvol >= 2.0:
        rvol_score = 0.85
    elif rvol >= 1.5:
        rvol_score = 0.65
    elif rvol >= 1.0:
        rvol_score = 0.45
    else:
        rvol_score = max(0.0, rvol * 0.45)

    # ── Volume trend (last 5 bars expanding or contracting) ───────────────────
    recent = bars[-6:] if len(bars) >= 6 else bars
    vol_trend_score = 0.5  # neutral default
    if len(recent) >= 4:
        vols = [b.volume for b in recent]
        # Compare first half vs second half
        h1 = sum(vols[: len(vols) // 2])
        h2 = sum(vols[len(vols) // 2 :])
        if h2 > h1 * 1.2:
            vol_trend_score = 0.8  # expanding
        elif h2 < h1 * 0.8:
            vol_trend_score = 0.2  # contracting — less conviction

    # ── Directional volume (up-bars vs down-bars by volume) ───────────────────
    if len(bars) >= 5:
        recent5 = bars[-5:]
        up_vol = sum(b.volume for b in recent5 if b.is_bullish)
        dn_vol = sum(b.volume for b in recent5 if b.is_bearish)
        total_vol = up_vol + dn_vol
        if total_vol > 0:
            up_ratio = up_vol / total_vol
            # Score how lopsided volume is in one direction
            dir_vol_score = clamp(abs(up_ratio - 0.5) * 2, 0.0, 1.0)
            direction = 1 if up_ratio > 0.60 else (-1 if up_ratio < 0.40 else 0)
        else:
            dir_vol_score = 0.0
            direction = 0
    else:
        dir_vol_score = 0.0
        direction = 0

    # ── Spread / liquidity (if quote available) ───────────────────────────────
    liquidity_score = 0.8  # assume OK if no quote
    if state.last_quote:
        spread_pct = state.last_quote.spread_pct
        if spread_pct < 0.0005:        # < 5bps — excellent
            liquidity_score = 1.0
        elif spread_pct < 0.001:       # < 10bps — good
            liquidity_score = 0.85
        elif spread_pct < 0.002:       # < 20bps — ok
            liquidity_score = 0.6
        elif spread_pct < 0.005:       # < 50bps — wide
            liquidity_score = 0.35
        else:
            liquidity_score = 0.1      # too wide

    # Composite
    raw_score = (
        rvol_score * 0.45
        + vol_trend_score * 0.25
        + dir_vol_score * 0.15
        + liquidity_score * 0.15
    )
    raw_score = clamp(raw_score)

    return ComponentScore(
        name="volume",
        raw_score=raw_score,
        weight=weight,
        weighted_score=raw_score * weight,
        direction=direction,
        details={
            "rvol": round(rvol, 2) if rvol is not None else None,
            "rvol_score": round(rvol_score, 2),
            "vol_trend_score": round(vol_trend_score, 2),
            "dir_vol_score": round(dir_vol_score, 2),
            "liquidity_score": round(liquidity_score, 2),
            "session_volume": state.session_volume,
            "avg_daily_volume": state.avg_daily_volume,
        },
    )
