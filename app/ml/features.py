"""
Feature extraction for the ML trade filter.

The same function is used offline (training, over historical 1m bars) and live
(over SymbolState.bars_1m), so the model sees identical inputs in both places.

Directional features are multiplied by the candidate's direction (+1 long,
-1 short) so "positive" always means "in favour of the trade". This lets one
model handle longs and shorts without learning everything twice.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np

# Order matters — the trained model expects columns in exactly this order.
FEATURE_NAMES = [
    "engine_scalp",        # 1 = scalp engine candidate, 0 = swing engine
    "direction",           # +1 long / -1 short
    "engine_score",        # engine's own 0–100 score
    "minutes_since_open",
    "atr_pct",             # ATR(14,1m) / price
    "stop_atr",            # stop distance in ATR units
    "ret_1_atr",           # signed returns over N bars, in ATR units
    "ret_5_atr",
    "ret_15_atr",
    "ret_30_atr",
    "vwap_dist_atr",       # signed distance from session VWAP
    "rsi7_signed",         # (RSI - 50) * direction
    "rsi14_signed",
    "ema9_21_atr",         # signed EMA(9) - EMA(21) gap
    "vol_ratio_1_30",      # last bar volume / mean of last 30
    "vol_ratio_5_30",
    "range_pos",           # where price sits in the session range (1 = at the favourable extreme)
    "session_range_atr",
    "room_atr",            # distance to session extreme in the trade direction
    "last_body_atr",       # signed body of the last bar
    "bar_trend_10",        # fraction of last 10 bars closing in the trade direction
]

WINDOW = 60   # bars of history used for indicator features


def _ema(x: np.ndarray, period: int) -> float:
    if len(x) < period:
        return float(x[-1])
    k = 2.0 / (period + 1)
    val = float(x[:period].mean())
    for v in x[period:]:
        val = v * k + val * (1 - k)
    return val


def _rsi(c: np.ndarray, period: int) -> float:
    if len(c) < period + 1:
        return 50.0
    d = np.diff(c[-(period + 1):])
    gain = d[d > 0].sum() / period
    loss = -d[d < 0].sum() / period
    if loss == 0:
        return 100.0 if gain > 0 else 50.0
    return 100.0 - 100.0 / (1.0 + gain / loss)


def atr14(h: np.ndarray, l: np.ndarray, c: np.ndarray) -> Optional[float]:
    if len(c) < 15:
        return None
    prev = c[-15:-1]
    tr = np.maximum(h[-14:] - l[-14:], np.maximum(abs(h[-14:] - prev), abs(l[-14:] - prev)))
    val = float(tr.mean())
    return val if val > 0 else None


def build_features(
    o: np.ndarray,
    h: np.ndarray,
    l: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
    *,
    direction: int,
    engine_score: float,
    engine_scalp: bool,
    minutes_since_open: float,
    vwap: Optional[float],
    session_high: float,
    session_low: float,
    stop_price: Optional[float],
) -> Optional[Dict[str, float]]:
    """
    Arrays hold completed 1m bars for the session up to the decision point
    (last element = most recent bar). Returns None if there is too little
    history to compute features.
    """
    o, h, l, c, v = (np.asarray(a[-WINDOW:], dtype=float) for a in (o, h, l, c, v))
    if len(c) < 15:
        return None
    atr = atr14(h, l, c)
    if not atr:
        return None
    price = float(c[-1])
    d = 1 if direction >= 0 else -1

    def ret(n: int) -> float:
        if len(c) <= n:
            return 0.0
        return (price - c[-1 - n]) / atr * d

    vol30 = v[-30:].mean() if v[-30:].mean() > 0 else 1.0
    rng = session_high - session_low
    pos = (price - session_low) / rng if rng > 0 else 0.5
    closes_dir = np.sign(c[-10:] - o[-10:]) * d

    return {
        "engine_scalp": 1.0 if engine_scalp else 0.0,
        "direction": float(d),
        "engine_score": float(engine_score),
        "minutes_since_open": float(minutes_since_open),
        "atr_pct": atr / price,
        "stop_atr": abs(price - stop_price) / atr if stop_price else np.nan,
        "ret_1_atr": ret(1),
        "ret_5_atr": ret(5),
        "ret_15_atr": ret(15),
        "ret_30_atr": ret(30),
        "vwap_dist_atr": (price - vwap) / atr * d if vwap else np.nan,
        "rsi7_signed": (_rsi(c, 7) - 50.0) * d,
        "rsi14_signed": (_rsi(c, 14) - 50.0) * d,
        "ema9_21_atr": (_ema(c, 9) - _ema(c, 21)) / atr * d,
        "vol_ratio_1_30": v[-1] / vol30,
        "vol_ratio_5_30": v[-5:].mean() / vol30,
        "range_pos": pos if d == 1 else 1.0 - pos,
        "session_range_atr": rng / atr,
        "room_atr": ((session_high - price) if d == 1 else (price - session_low)) / atr,
        "last_body_atr": (c[-1] - o[-1]) / atr * d,
        "bar_trend_10": float((closes_dir > 0).mean()),
    }


def to_row(feats: Dict[str, float]) -> list[float]:
    return [feats[k] for k in FEATURE_NAMES]
