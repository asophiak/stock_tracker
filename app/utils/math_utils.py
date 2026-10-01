"""
Numeric helper functions used by the signal engine and indicators.
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple


def clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    if denominator == 0:
        return default
    return numerator / denominator


def pct_change(new: float, old: float) -> float:
    """Percentage change from old to new."""
    if old == 0:
        return 0.0
    return (new - old) / abs(old)


def ema(values: List[float], period: int) -> Optional[float]:
    """Exponential moving average seeded with SMA of first `period` values."""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    result = sum(values[:period]) / period  # SMA seed
    for v in values[period:]:
        result = v * k + result * (1 - k)
    return result


def sma(values: Sequence[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    """Average True Range using Wilder's smoothing (EMA with k=1/period)."""
    if len(highs) < period + 1:
        return None
    trs: List[float] = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # Seed with SMA of first period TRs, then apply Wilder's smoothing
    result = sum(trs[:period]) / period
    k = 1.0 / period
    for tr in trs[period:]:
        result = tr * k + result * (1 - k)
    return result


def rolling_max(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return max(values[-period:])


def rolling_min(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return min(values[-period:])


def momentum(closes: List[float], period: int) -> Optional[float]:
    """Simple price momentum: (close_now - close_N) / close_N."""
    if len(closes) <= period:
        return None
    return pct_change(closes[-1], closes[-period - 1])


def normalize_score(raw: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp raw value into [0, 1]."""
    return clamp(safe_div(raw - lo, hi - lo), 0.0, 1.0)


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Relative Strength Index using Wilder's smoothing. Returns 0–100."""
    if len(closes) < period + 1:
        return None
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    # Wilder's smoothed averages (same as EMA with k = 1/period)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def macd(
    closes: List[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Optional[Tuple[float, float, float]]:
    """
    MACD indicator. Returns (macd_line, signal_line, histogram) or None.
    Uses standard EMA seeding (SMA for first period, then EMA).
    """
    if len(closes) < slow + signal_period:
        return None

    k_fast = 2.0 / (fast + 1)
    k_slow = 2.0 / (slow + 1)
    k_sig = 2.0 / (signal_period + 1)

    # Seed fast EMA with SMA of first `fast` bars, then advance to index slow-1
    ema_f = sum(closes[:fast]) / fast
    for v in closes[fast:slow]:
        ema_f = v * k_fast + ema_f * (1 - k_fast)

    # Seed slow EMA with SMA of first `slow` bars
    ema_s = sum(closes[:slow]) / slow

    # Compute rolling MACD values from index slow-1 onward
    macd_vals: List[float] = [ema_f - ema_s]
    for v in closes[slow:]:
        ema_f = v * k_fast + ema_f * (1 - k_fast)
        ema_s = v * k_slow + ema_s * (1 - k_slow)
        macd_vals.append(ema_f - ema_s)

    if len(macd_vals) < signal_period:
        return None

    # Signal line: EMA of MACD values
    sig_ema = sum(macd_vals[:signal_period]) / signal_period
    for v in macd_vals[signal_period:]:
        sig_ema = v * k_sig + sig_ema * (1 - k_sig)

    macd_line = macd_vals[-1]
    histogram = macd_line - sig_ema
    return macd_line, sig_ema, histogram


def higher_highs_lower_lows(highs: List[float], lows: List[float], lookback: int = 5) -> int:
    """
    Returns +1 for bullish structure (HH/HL), -1 for bearish (LH/LL), 0 for mixed.
    Uses swing points from `lookback` bars.
    """
    if len(highs) < lookback or len(lows) < lookback:
        return 0

    h = highs[-lookback:]
    l = lows[-lookback:]

    hh = all(h[i] >= h[i - 1] for i in range(1, len(h)))
    hl = all(l[i] >= l[i - 1] for i in range(1, len(l)))
    lh = all(h[i] <= h[i - 1] for i in range(1, len(h)))
    ll = all(l[i] <= l[i - 1] for i in range(1, len(l)))

    if hh and hl:
        return 1
    if lh and ll:
        return -1
    return 0
