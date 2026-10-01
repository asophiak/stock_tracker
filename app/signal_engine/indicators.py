"""
Technical indicator computations over SymbolState bar data.
All functions are pure and operate on lists of Bar objects.
"""
from __future__ import annotations

from typing import List, Optional, Tuple  # noqa: F401 (Tuple re-exported for compute_macd)

from app.schemas.market_data import Bar, SymbolState
from app.utils.math_utils import atr, ema, macd, momentum, rolling_max, rolling_min, rsi, safe_div, sma


# ── VWAP ─────────────────────────────────────────────────────────────────────

def compute_vwap_from_bars(bars: List[Bar]) -> Optional[float]:
    """Full recalculation of VWAP from a list of bars (used for historical load)."""
    cumulative_tp_vol = 0.0
    cumulative_vol = 0
    for b in bars:
        tp = (b.high + b.low + b.close) / 3.0
        cumulative_tp_vol += tp * b.volume
        cumulative_vol += b.volume
    if cumulative_vol == 0:
        return None
    return cumulative_tp_vol / cumulative_vol


# ── Opening range ─────────────────────────────────────────────────────────────

def compute_opening_range(bars: List[Bar], minutes: int = 15) -> Tuple[Optional[float], Optional[float]]:
    """
    Returns (opening_range_high, opening_range_low) from the first `minutes`
    bars in the provided list.
    """
    if not bars:
        return None, None
    or_bars = bars[:minutes]
    if not or_bars:
        return None, None
    orh = max(b.high for b in or_bars)
    orl = min(b.low for b in or_bars)
    return orh, orl


# ── ATR proxy ─────────────────────────────────────────────────────────────────

def compute_atr(bars: List[Bar], period: int = 14) -> Optional[float]:
    if len(bars) < period + 1:
        return None
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    return atr(highs, lows, closes, period)


# ── Momentum ──────────────────────────────────────────────────────────────────

def compute_momentum(bars: List[Bar], period: int = 10) -> Optional[float]:
    closes = [b.close for b in bars]
    return momentum(closes, period)


# ── Relative volume ───────────────────────────────────────────────────────────

def compute_rvol(session_volume: int, avg_daily_volume: Optional[float], elapsed_bars: int) -> Optional[float]:
    """
    Intraday RVOL: compares session volume so far against the expected
    fraction of average daily volume for this many 1-minute bars.
    """
    if not avg_daily_volume or avg_daily_volume <= 0 or elapsed_bars <= 0:
        return None
    expected_so_far = avg_daily_volume * (elapsed_bars / 390.0)
    return safe_div(session_volume, expected_so_far, default=0.0)


# ── Trend structure ───────────────────────────────────────────────────────────

def swing_highs_lows(bars: List[Bar], lookback: int = 3) -> Tuple[List[float], List[float]]:
    """
    Identify local swing highs and swing lows using a simple N-bar pivot method.
    Returns (swing_highs, swing_lows) as lists of price levels.
    """
    if len(bars) < lookback * 2 + 1:
        return [], []

    swing_h: List[float] = []
    swing_l: List[float] = []

    for i in range(lookback, len(bars) - lookback):
        window_highs = [bars[j].high for j in range(i - lookback, i + lookback + 1)]
        window_lows = [bars[j].low for j in range(i - lookback, i + lookback + 1)]
        if bars[i].high == max(window_highs):
            swing_h.append(bars[i].high)
        if bars[i].low == min(window_lows):
            swing_l.append(bars[i].low)

    return swing_h, swing_l


def trend_structure(bars: List[Bar], lookback: int = 5) -> int:
    """
    +1 = bullish (HH+HL), -1 = bearish (LH+LL), 0 = mixed/unclear.
    Uses swing point comparison over recent bars.
    """
    if len(bars) < lookback:
        return 0
    sh, sl = swing_highs_lows(bars[-lookback * 3 :], lookback=2)
    if len(sh) >= 2 and len(sl) >= 2:
        hh = sh[-1] > sh[-2]
        hl = sl[-1] > sl[-2]
        lh = sh[-1] < sh[-2]
        ll = sl[-1] < sl[-2]
        if hh and hl:
            return 1
        if lh and ll:
            return -1
    return 0


# ── EMA slope ─────────────────────────────────────────────────────────────────

def ema_slope(bars: List[Bar], period: int = 9) -> Optional[float]:
    """
    Approximate EMA slope: difference between EMA now and EMA N bars ago,
    normalised by current price.
    """
    closes = [b.close for b in bars]
    if len(closes) < period + 5:
        return None
    ema_now = ema(closes, period)
    ema_prev = ema(closes[:-5], period)
    if ema_now is None or ema_prev is None or closes[-1] == 0:
        return None
    return (ema_now - ema_prev) / closes[-1]


# ── Support / Resistance levels ───────────────────────────────────────────────

def support_resistance_levels(bars: List[Bar], lookback: int = 20) -> Tuple[List[float], List[float]]:
    """
    Returns (resistance_levels, support_levels) from recent swing points.
    """
    if len(bars) < lookback:
        return [], []
    sh, sl = swing_highs_lows(bars[-lookback:], lookback=3)
    # Keep only the most recent 3 of each
    return sh[-3:], sl[-3:]


# ── Distance from VWAP ───────────────────────────────────────────────────────

def compute_rsi(bars: List[Bar], period: int = 14) -> Optional[float]:
    """RSI(period) from bar closes."""
    closes = [b.close for b in bars]
    return rsi(closes, period)


def compute_macd(
    bars: List[Bar],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Optional[Tuple[float, float, float]]:
    """MACD from bar closes. Returns (macd_line, signal_line, histogram) or None."""
    closes = [b.close for b in bars]
    return macd(closes, fast, slow, signal_period)


def distance_from_vwap(price: float, vwap: Optional[float]) -> Optional[float]:
    """Percentage distance from VWAP. Positive = above."""
    if vwap and vwap > 0:
        return (price - vwap) / vwap
    return None


# ── Distance from opening range ───────────────────────────────────────────────

def distance_from_orh(price: float, orh: Optional[float]) -> Optional[float]:
    if orh and orh > 0:
        return (price - orh) / orh
    return None


def distance_from_orl(price: float, orl: Optional[float]) -> Optional[float]:
    if orl and orl > 0:
        return (price - orl) / orl
    return None
