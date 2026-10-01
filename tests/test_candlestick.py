"""
Tests for candlestick pattern detection.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.schemas.market_data import Bar
from app.signal_engine.candlestick import (
    detect_all_patterns,
    detect_bearish_engulfing,
    detect_bullish_engulfing,
    detect_hammer,
    detect_shooting_star,
    detect_vwap_reclaim,
    detect_vwap_rejection,
    detect_failed_breakdown,
    detect_failed_breakout,
)


def _bar(o, h, l, c, v=10000):
    return Bar(
        timestamp=datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=v,
    )


# ── Engulfing ─────────────────────────────────────────────────────────────────

def test_bullish_engulfing_detected():
    bars = [
        _bar(105, 106, 103, 104),   # bearish prev
        _bar(103, 108, 102, 107),   # bullish engulfs
    ]
    result = detect_bullish_engulfing(bars)
    assert result is not None
    assert result.direction == 1


def test_bullish_engulfing_not_detected_when_not_engulfing():
    bars = [
        _bar(100, 105, 99, 104),    # bullish prev
        _bar(104, 106, 103, 105),   # also bullish, not engulfing a bearish
    ]
    result = detect_bullish_engulfing(bars)
    assert result is None


def test_bearish_engulfing_detected():
    bars = [
        _bar(100, 105, 99, 104),    # bullish prev
        _bar(105, 106, 98, 99),     # bearish engulfs
    ]
    result = detect_bearish_engulfing(bars)
    assert result is not None
    assert result.direction == -1


# ── Hammer ────────────────────────────────────────────────────────────────────

def test_hammer_detected():
    # Long lower wick (2× body), small upper wick
    bars = [_bar(100, 100.5, 95, 100.2)]   # body=0.2, lower_wick=5.2, upper_wick=0.3
    result = detect_hammer(bars)
    assert result is not None
    assert result.direction == 1


def test_hammer_not_on_doji():
    bars = [_bar(100, 100.1, 99.9, 100.05)]  # tiny bar, no clear wick
    result = detect_hammer(bars)
    assert result is None


# ── Shooting star ─────────────────────────────────────────────────────────────

def test_shooting_star_detected():
    # Long upper wick, small body at bottom
    bars = [_bar(100, 105, 99.8, 100.1)]   # upper_wick=4.9, body=0.1, lower_wick=0.2
    result = detect_shooting_star(bars)
    assert result is not None
    assert result.direction == -1


# ── VWAP patterns ─────────────────────────────────────────────────────────────

def test_vwap_reclaim_detected():
    bars = [
        _bar(98, 99, 97, 98.5),    # closed below VWAP=100
        _bar(98.5, 101, 98, 100.5), # closed above VWAP=100
    ]
    result = detect_vwap_reclaim(bars, vwap=100.0)
    assert result is not None
    assert result.direction == 1


def test_vwap_rejection_detected():
    bars = [
        _bar(99, 99.5, 98, 99.2),   # below VWAP=100
        _bar(99.2, 101.5, 98.5, 99.5), # tested above VWAP, closed below
    ]
    result = detect_vwap_rejection(bars, vwap=100.0)
    assert result is not None
    assert result.direction == -1


# ── Failed breakout / breakdown ───────────────────────────────────────────────

def test_failed_breakout():
    bars = [
        _bar(99, 103, 99, 102),   # prev closed above ORH=101
        _bar(102, 103, 99, 99.5), # curr closed below ORH=101
    ]
    result = detect_failed_breakout(bars, orh=101.0)
    assert result is not None
    assert result.direction == -1


def test_failed_breakdown():
    bars = [
        _bar(99, 100, 97, 97.5),  # prev closed below ORL=98
        _bar(97.5, 99.5, 97, 99), # curr closed above ORL=98
    ]
    result = detect_failed_breakdown(bars, orl=98.0)
    assert result is not None
    assert result.direction == 1


# ── detect_all_patterns ───────────────────────────────────────────────────────

def test_detect_all_returns_list():
    bars = [
        _bar(105, 106, 103, 104, v=5000),
        _bar(103, 108, 102, 107, v=15000),
    ]
    results = detect_all_patterns(bars, vwap=105.0)
    assert isinstance(results, list)


def test_detect_all_empty_bars():
    results = detect_all_patterns([])
    assert results == []
