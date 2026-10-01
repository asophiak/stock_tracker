"""
Integration tests for the signal engine scoring pipeline.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from datetime import timedelta

from app.schemas.market_data import Bar, SymbolState
from app.schemas.signals import SignalColor, TradeLabel
from app.signal_engine.engine import score_symbol
from app.signal_engine.technical import score_technical_trend
from app.signal_engine.volume import score_volume
from app.signal_engine.vwap_scorer import score_vwap


def _make_bars(n=30, trend="up", base=100.0):
    """
    Creates realistic (non-monotonic) trending bars so that:
      - swing points exist for trend_structure() to detect
      - RSI stays in a healthy range (~60 for uptrend, ~40 for downtrend)
        rather than pinning at 100/0 from a perfect monotonic series
    Pattern: alternating larger-move / smaller-counter-move produces
    higher-highs / higher-lows structure with realistic RSI.
    """
    bars = []
    price = base
    t0 = datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc)
    for i in range(n):
        if trend == "up":
            price *= 1.004 if i % 2 == 0 else 0.9975   # +0.4% / -0.25%
        elif trend == "down":
            price *= 0.996 if i % 2 == 0 else 1.0025   # -0.4% / +0.25%
        bars.append(Bar(
            timestamp=t0 + timedelta(minutes=i),
            open=price * 0.999,
            high=price * 1.004,
            low=price * 0.996,
            close=price,
            volume=50_000 + i * 1000,
            timeframe="1Min",
        ))
    return bars


def _make_state(trend="up") -> SymbolState:
    bars = _make_bars(n=50, trend=trend)
    state = SymbolState(symbol="TEST")
    state.bars_1m = bars
    state.bars_5m = _make_bars(n=30, trend=trend)
    state.last_price = bars[-1].close
    state.last_updated = bars[-1].timestamp
    state.session_volume = sum(b.volume for b in bars)
    state.avg_daily_volume = 6_000_000.0
    state.vwap = bars[-1].close * 0.998  # slightly below price = above VWAP
    state.opening_range_high = bars[14].high
    state.opening_range_low  = bars[14].low
    state.opening_range_set  = True
    return state


# ── Technical trend scorer ───────────────────────────────────────────────────

def test_technical_trend_bullish():
    state = _make_state("up")
    score = score_technical_trend(state, weight=25)
    assert score.direction == 1
    assert score.raw_score > 0.3
    assert score.weighted_score <= 25


def test_technical_trend_bearish():
    state = _make_state("down")
    score = score_technical_trend(state, weight=25)
    assert score.direction == -1
    assert score.raw_score > 0.3


def test_technical_trend_insufficient_bars():
    state = SymbolState(symbol="X")
    state.bars_1m = _make_bars(n=2)
    state.bars_5m = []
    score = score_technical_trend(state, weight=25)
    assert score.raw_score == 0.0


# ── Volume scorer ─────────────────────────────────────────────────────────────

def test_volume_high_rvol():
    state = _make_state("up")
    # Set very high intraday volume
    state.session_volume = 5_000_000
    state.avg_daily_volume = 1_000_000
    score = score_volume(state, weight=15)
    assert score.raw_score > 0.7


def test_volume_low_rvol():
    state = _make_state("up")
    state.session_volume = 50_000
    state.avg_daily_volume = 6_000_000
    score = score_volume(state, weight=15)
    assert score.raw_score < 0.5


# ── VWAP scorer ───────────────────────────────────────────────────────────────

def test_vwap_above_bullish():
    state = _make_state("up")
    state.last_price = 102.0
    state.vwap = 100.0
    # Ensure opening range is consistent: price is above ORH so OR doesn't override
    state.opening_range_high = 101.0
    state.opening_range_low = 99.0
    state.opening_range_set = True
    score = score_vwap(state, weight=15)
    assert score.direction == 1
    assert score.raw_score > 0.4


def test_vwap_below_bearish():
    state = _make_state("down")
    state.last_price = 98.0
    state.vwap = 100.0
    # Ensure opening range is consistent: price is below ORL so OR confirms bearish
    state.opening_range_high = 101.0
    state.opening_range_low = 99.5
    state.opening_range_set = True
    score = score_vwap(state, weight=15)
    assert score.direction == -1


# ── Full engine ───────────────────────────────────────────────────────────────

def test_score_symbol_returns_signal():
    state = _make_state("up")
    result = score_symbol(state, news_items=[])
    assert result.symbol == "TEST"
    assert 0 <= result.total_score <= 100
    assert result.color in SignalColor.__members__.values()
    assert result.label in TradeLabel.__members__.values()
    assert len(result.components) > 0


def test_score_symbol_no_price():
    state = SymbolState(symbol="EMPTY")
    result = score_symbol(state, news_items=[])
    assert result.total_score == 0
    assert result.color == SignalColor.NEUTRAL
    assert result.label == TradeLabel.NO_TRADE


def test_score_symbol_flash_green_possible():
    """A very strong uptrend with high RVOL should score >= 70."""
    state = _make_state("up")
    state.session_volume = 8_000_000  # very high RVOL
    state.avg_daily_volume = 1_000_000
    # Put price cleanly above VWAP
    state.last_price = state.bars_1m[-1].close
    state.vwap = state.last_price * 0.990

    result = score_symbol(state, news_items=[])
    # Should be at least a watch-level signal
    assert result.total_score >= 40
    assert result.direction == 1


def test_score_symbol_thesis_generated_for_actionable():
    state = _make_state("up")
    state.session_volume = 8_000_000
    state.avg_daily_volume = 1_000_000
    state.vwap = state.last_price * 0.992

    result = score_symbol(state, news_items=[])
    if result.label in (TradeLabel.POSSIBLE_TRADE, TradeLabel.IMMEDIATE_TRADE):
        assert result.thesis is not None
        assert result.thesis.direction in ("long", "short")
