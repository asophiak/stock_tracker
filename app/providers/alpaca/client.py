"""
Alpaca SDK client factory.

Creates typed clients for:
  - Historical market data (StockHistoricalDataClient)
  - Live streaming (StockDataStream)
  - Trading (TradingClient)
  - News streaming (NewsDataStream)

All clients are None when credentials are absent, enabling graceful degradation.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


def get_historical_client():
    """Return StockHistoricalDataClient or None."""
    if not settings.alpaca_credentials_present:
        logger.warning("Alpaca credentials missing — historical data unavailable.")
        return None
    try:
        from alpaca.data.historical import StockHistoricalDataClient
        return StockHistoricalDataClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_API_SECRET,
        )
    except ImportError:
        logger.error("alpaca-py not installed — pip install alpaca-py")
        return None


def get_stock_stream():
    """Return StockDataStream or None."""
    if not settings.alpaca_credentials_present:
        return None
    try:
        from alpaca.data.live import StockDataStream
        from alpaca.data.enums import DataFeed
        feed_map = {"iex": DataFeed.IEX, "sip": DataFeed.SIP}
        feed = feed_map.get(settings.ALPACA_DATA_FEED.lower(), DataFeed.IEX)
        return StockDataStream(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_API_SECRET,
            feed=feed,
        )
    except ImportError:
        logger.error("alpaca-py not installed")
        return None


def get_news_stream():
    """Return NewsDataStream or None."""
    if not settings.alpaca_credentials_present:
        return None
    try:
        from alpaca.data.live import NewsDataStream
        return NewsDataStream(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_API_SECRET,
        )
    except ImportError:
        logger.warning("alpaca-py NewsDataStream not available in this version.")
        return None


def get_trading_client(paper: bool = True):
    """Return TradingClient or None."""
    if not settings.alpaca_credentials_present:
        return None
    try:
        from alpaca.trading.client import TradingClient
        return TradingClient(
            api_key=settings.ALPACA_API_KEY,
            secret_key=settings.ALPACA_API_SECRET,
            paper=paper,
        )
    except ImportError:
        logger.error("alpaca-py not installed")
        return None
