"""
Inputs and trade levels for the autonomous neural-net bot.

Unlike the filter, the autonomous model gets no help from the rule engines:
it sees only the raw bars and the market-structure features, from the long
side's point of view, and its two outputs answer "would a long here be
profitable?" and "would a short here be profitable?".

Trade levels follow the scalp convention so labels match how the bot trades:
stop = max(1.5 × ATR(14,1m), 0.6% of price), target = 2 × stop, max hold
HORIZON_BARS minutes.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from app.ml.features import FEATURE_NAMES, atr14, build_features
from app.ml.labeling import HORIZON_BARS  # noqa: F401  (re-exported for the bot's time exit)
from app.ml.sequence import build_sequence

# Engine-derived inputs are dropped — the autonomous model must stand on its own.
AUTO_FEATURES = [f for f in FEATURE_NAMES if f not in ("engine_scalp", "engine_score", "direction")]

# Plain-English names for the /ml explorer: name → (label, explanation)
FEATURE_HELP = {
    "minutes_since_open": ("Minutes since open", "Time of day — the open, lunch and close behave differently."),
    "atr_pct": ("Volatility (ATR %)", "Average 1-minute range as a % of price — how jumpy the stock is right now."),
    "stop_atr": ("Stop distance (ATR)", "How far the protective stop sits, in units of normal 1-minute movement."),
    "ret_1_atr": ("1-min momentum", "Last minute's move, scaled by volatility."),
    "ret_5_atr": ("5-min momentum", "Move over the last 5 minutes, scaled by volatility."),
    "ret_15_atr": ("15-min momentum", "Move over the last 15 minutes, scaled by volatility."),
    "ret_30_atr": ("30-min momentum", "Move over the last 30 minutes, scaled by volatility."),
    "vwap_dist_atr": ("Distance from VWAP", "Price vs the volume-weighted average price of the day, in volatility units."),
    "rsi7_signed": ("RSI(7) − 50", "Short-term overbought (+) / oversold (−) gauge."),
    "rsi14_signed": ("RSI(14) − 50", "Medium-term overbought (+) / oversold (−) gauge."),
    "ema9_21_atr": ("EMA 9/21 gap", "Fast vs slow moving average — positive means short-term uptrend."),
    "vol_ratio_1_30": ("Volume spike (1 bar)", "Last bar's volume vs the 30-bar average."),
    "vol_ratio_5_30": ("Volume surge (5 bars)", "Last 5 bars' volume vs the 30-bar average."),
    "range_pos": ("Position in day's range", "0 = at the day's low, 1 = at the day's high."),
    "session_range_atr": ("Day's range (ATR)", "How wide today's high–low range is, in volatility units."),
    "room_atr": ("Room to day's high", "Distance up to today's high, in volatility units."),
    "last_body_atr": ("Last candle body", "Size and direction of the most recent 1-minute candle."),
    "bar_trend_10": ("Up-bar share (10)", "Fraction of the last 10 candles that closed up."),
}

STOP_ATR_MULT = 1.5
MIN_STOP_PCT = 0.006
REWARD_RISK = 2.0


def trade_levels(price: float, atr: float, direction: int) -> Tuple[float, float]:
    """(stop, target) for an entry at `price`."""
    dist = max(atr * STOP_ATR_MULT, price * MIN_STOP_PCT)
    return price - dist * direction, price + dist * REWARD_RISK * direction


def build_auto_inputs(
    o: np.ndarray, h: np.ndarray, l: np.ndarray, c: np.ndarray, v: np.ndarray,
    *, minutes_since_open: float, vwap: Optional[float], session_high: float, session_low: float,
) -> Optional[Tuple[list, np.ndarray, float]]:
    """Returns (feature_row, sequence, atr) seen from the long side, or None if too little history."""
    w = slice(-61, None)
    atr = atr14(np.asarray(h[w], float), np.asarray(l[w], float), np.asarray(c[w], float))
    if not atr:
        return None
    price = float(c[-1])
    stop, _ = trade_levels(price, atr, 1)
    feats = build_features(
        o, h, l, c, v, direction=1, engine_score=0.0, engine_scalp=False,
        minutes_since_open=minutes_since_open, vwap=vwap,
        session_high=session_high, session_low=session_low, stop_price=stop,
    )
    seq = build_sequence(o, h, l, c, v, 1)
    if feats is None or seq is None:
        return None
    return [feats[k] for k in AUTO_FEATURES], seq, atr
