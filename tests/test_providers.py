"""
Tests for provider normalisation and mock provider behaviour.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.providers.alpaca.normalizers import normalise_bar, normalise_news, normalise_quote
from app.schemas.market_data import Bar, Quote


# ── Alpaca normalizers ────────────────────────────────────────────────────────

def test_normalise_bar_basic():
    mock_bar = MagicMock()
    mock_bar.timestamp = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
    mock_bar.open = 150.0
    mock_bar.high = 155.0
    mock_bar.low  = 149.0
    mock_bar.close = 153.0
    mock_bar.volume = 100_000
    mock_bar.vwap = 152.0

    bar = normalise_bar(mock_bar, "NVDA", "1Min")
    assert isinstance(bar, Bar)
    assert bar.symbol if hasattr(bar, 'symbol') else bar.open == 150.0
    assert bar.high == 155.0
    assert bar.volume == 100_000
    assert bar.timeframe == "1Min"


def test_normalise_bar_no_vwap():
    mock_bar = MagicMock()
    mock_bar.timestamp = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
    mock_bar.open = mock_bar.high = mock_bar.low = mock_bar.close = 100.0
    mock_bar.volume = 1000
    mock_bar.vwap = None
    bar = normalise_bar(mock_bar, "SPY", "5Min")
    assert bar.vwap is None


def test_normalise_quote():
    mock_q = MagicMock()
    mock_q.timestamp = datetime(2024, 1, 2, 14, 30, tzinfo=timezone.utc)
    mock_q.ask_price = 150.05
    mock_q.bid_price = 149.95
    mock_q.ask_size  = 200
    mock_q.bid_size  = 150

    quote = normalise_quote(mock_q, "NVDA")
    assert isinstance(quote, Quote)
    assert abs(quote.spread - 0.10) < 0.001
    assert abs(quote.mid_price - 150.0) < 0.001


def test_normalise_news():
    mock_n = MagicMock()
    mock_n.id = 12345
    mock_n.headline = "Company beats earnings expectations"
    mock_n.summary = "Revenue exceeded analyst forecasts by 15%"
    mock_n.source = "Reuters"
    mock_n.url = "https://example.com"
    mock_n.created_at = datetime(2024, 1, 2, 10, 0, tzinfo=timezone.utc)
    mock_n.symbols = ["NVDA"]

    item = normalise_news(mock_n)
    assert item.headline == "Company beats earnings expectations"
    assert item.symbol == "NVDA"
    assert item.provider_id == "12345"


# ── Mock provider ─────────────────────────────────────────────────────────────

def test_mock_provider_generates_bars():
    from app.providers.mock.market_data import MockMarketDataProvider
    provider = MockMarketDataProvider()

    bars = asyncio.get_event_loop().run_until_complete(
        provider.fetch_historical_bars(
            "NVDA", "1Min",
            datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc),
            datetime(2024, 1, 2, 10, 30, tzinfo=timezone.utc),
        )
    )
    assert len(bars) == 60
    assert all(isinstance(b, Bar) for b in bars)
    assert all(b.high >= b.low for b in bars)
    assert all(b.volume > 0 for b in bars)


def test_mock_provider_quote():
    from app.providers.mock.market_data import MockMarketDataProvider
    provider = MockMarketDataProvider()

    quote = asyncio.get_event_loop().run_until_complete(
        provider.fetch_latest_quote("NVDA")
    )
    assert isinstance(quote, Quote)
    assert quote.ask_price > quote.bid_price
    assert quote.spread > 0


# ── Bar dataclass properties ──────────────────────────────────────────────────

def test_bar_properties():
    bar = Bar(
        timestamp=datetime(2024, 1, 2, tzinfo=timezone.utc),
        open=100.0, high=105.0, low=98.0, close=103.0, volume=50000,
    )
    assert bar.is_bullish
    assert not bar.is_bearish
    assert abs(bar.range - 7.0) < 0.001
    assert abs(bar.body_size - 3.0) < 0.001
    assert abs(bar.upper_wick - 2.0) < 0.001
    assert abs(bar.lower_wick - 2.0) < 0.001
    assert abs(bar.midpoint - 101.5) < 0.001
