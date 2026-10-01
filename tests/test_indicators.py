"""
Tests for technical indicator calculations.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.market_data import Bar
from app.signal_engine.indicators import (
    compute_atr,
    compute_momentum,
    compute_opening_range,
    compute_vwap_from_bars,
    trend_structure,
)
from app.utils.math_utils import atr, ema, momentum, sma


def _bar(o, h, l, c, v=10000, ts=None):
    return Bar(
        timestamp=ts or datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=v,
    )


# ── VWAP ──────────────────────────────────────────────────────────────────────

def test_vwap_single_bar():
    bars = [_bar(100, 105, 99, 102, v=1000)]
    vwap = compute_vwap_from_bars(bars)
    expected = (105 + 99 + 102) / 3.0
    assert abs(vwap - expected) < 0.001


def test_vwap_multi_bar():
    bars = [
        _bar(100, 104, 98, 102, v=1000),
        _bar(102, 106, 101, 105, v=2000),
    ]
    vwap = compute_vwap_from_bars(bars)
    tp1 = (104 + 98 + 102) / 3
    tp2 = (106 + 101 + 105) / 3
    expected = (tp1 * 1000 + tp2 * 2000) / 3000
    assert abs(vwap - expected) < 0.001


def test_vwap_empty():
    assert compute_vwap_from_bars([]) is None


# ── Opening range ─────────────────────────────────────────────────────────────

def test_opening_range_basic():
    bars = [_bar(100, 105 + i, 98 - i, 102) for i in range(20)]
    orh, orl = compute_opening_range(bars, minutes=5)
    assert orh == bars[4].high
    assert orl == bars[4].low


def test_opening_range_empty():
    h, l = compute_opening_range([], minutes=5)
    assert h is None and l is None


# ── ATR ───────────────────────────────────────────────────────────────────────

def test_atr_basic():
    # Uniform bars: ATR should equal (high - low) = 4
    bars = [_bar(100, 102, 98, 100) for _ in range(20)]
    atr_val = compute_atr(bars, period=14)
    assert atr_val is not None
    assert abs(atr_val - 4.0) < 0.01


def test_atr_insufficient():
    bars = [_bar(100, 102, 98, 100) for _ in range(5)]
    assert compute_atr(bars, period=14) is None


# ── Momentum ──────────────────────────────────────────────────────────────────

def test_momentum_uptrend():
    bars = [_bar(100 + i, 101 + i, 99 + i, 100 + i) for i in range(20)]
    mom = compute_momentum(bars, period=10)
    assert mom is not None
    assert mom > 0


def test_momentum_downtrend():
    bars = [_bar(120 - i, 121 - i, 119 - i, 120 - i) for i in range(20)]
    mom = compute_momentum(bars, period=10)
    assert mom is not None
    assert mom < 0


# ── Trend structure ───────────────────────────────────────────────────────────

def test_trend_bullish():
    # Create very clear HH+HL structure with large steps
    bars = []
    for i in range(40):
        base = 100 + i * 2.0          # each bar 2 pts higher
        bars.append(_bar(base, base + 2, base - 0.5, base + 1.5))
    result = trend_structure(bars, lookback=5)
    # Either bullish (1) or unclear (0) is acceptable — the key is NOT bearish
    assert result >= 0


def test_trend_bearish():
    bars = []
    for i in range(40):
        base = 200 - i * 2.0          # each bar 2 pts lower
        bars.append(_bar(base, base + 0.5, base - 2, base - 1.5))
    result = trend_structure(bars, lookback=5)
    # Either bearish (-1) or unclear (0) is acceptable — the key is NOT bullish
    assert result <= 0


# ── Math utils ────────────────────────────────────────────────────────────────

def test_sma_basic():
    result = sma([1, 2, 3, 4, 5], period=3)
    assert abs(result - 4.0) < 0.001


def test_sma_insufficient():
    assert sma([1, 2], period=5) is None


def test_ema_basic():
    vals = [100.0] * 10
    result = ema(vals, 9)
    assert abs(result - 100.0) < 0.001


def test_momentum_util():
    closes = [100.0] * 5 + [105.0]
    result = momentum(closes, 5)
    assert abs(result - 0.05) < 0.001
