"""
Alpaca market data provider: real-time streaming + historical REST.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional

from app.providers.base import BarCallback, MarketDataProvider, QuoteCallback
from app.providers.alpaca.client import get_historical_client, get_stock_stream
from app.providers.alpaca.normalizers import normalise_bar, normalise_quote
from app.schemas.market_data import Bar, Quote
from app.config import settings

logger = logging.getLogger(__name__)


class AlpacaMarketDataProvider(MarketDataProvider):
    """
    Wraps alpaca-py StockDataStream for real-time bars and
    StockHistoricalDataClient for historical OHLCV.
    """

    def __init__(self) -> None:
        self._stream = None
        self._hist_client = None
        self._subscribed_symbols: List[str] = []
        self._bar_callback: Optional[BarCallback] = None
        self._quote_callback: Optional[QuoteCallback] = None
        self._connected = False
        self._stream_task: Optional[asyncio.Task] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._hist_client = get_historical_client()
        self._stream = get_stock_stream()
        if self._stream is None:
            logger.warning("AlpacaMarketDataProvider: no stream (credentials missing).")
            return
        logger.info("AlpacaMarketDataProvider: initialised.")

    async def disconnect(self) -> None:
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        self._connected = False
        logger.info("AlpacaMarketDataProvider: disconnected.")

    # ── Subscription ─────────────────────────────────────────────────────────

    async def subscribe(self, symbols: List[str]) -> None:
        if not self._stream:
            return
        self._subscribed_symbols = symbols

        # Register handlers with the SDK
        async def _handle_bar(bar) -> None:
            try:
                internal = normalise_bar(bar, bar.symbol, "1Min")
                if self._bar_callback:
                    self._bar_callback(bar.symbol, internal)
            except Exception as exc:
                logger.debug("Bar normalisation error: %s", exc)

        async def _handle_minute_bar(bar) -> None:
            await _handle_bar(bar)

        try:
            self._stream.subscribe_bars(_handle_minute_bar, *symbols)
            # Start the stream in a background task
            self._stream_task = asyncio.create_task(self._run_stream())
            self._connected = True
            logger.info("Alpaca stream subscribed to: %s", symbols)
        except Exception as exc:
            logger.error("Alpaca subscribe failed: %s", exc)
            self._connected = False

    async def _run_stream(self) -> None:
        """Keep the stream running; reconnect on unexpected disconnections."""
        if not self._stream:
            return
        while True:
            try:
                await self._stream._run_forever()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Alpaca stream error: %s. Reconnecting in 5s.", exc)
                self._connected = False
                await asyncio.sleep(5)
                self._stream = get_stock_stream()
                if self._stream:
                    await self.subscribe(self._subscribed_symbols)
                    return  # subscribe() will create new task

    # ── Historical ────────────────────────────────────────────────────────────

    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Bar]:
        if not self._hist_client:
            return []
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

            tf_map = {
                "1Min": TimeFrame(1, TimeFrameUnit.Minute),
                "5Min": TimeFrame(5, TimeFrameUnit.Minute),
                "15Min": TimeFrame(15, TimeFrameUnit.Minute),
                "1Day": TimeFrame(1, TimeFrameUnit.Day),
            }
            tf = tf_map.get(timeframe, TimeFrame(1, TimeFrameUnit.Minute))

            from alpaca.data.enums import DataFeed
            feed_map = {"iex": DataFeed.IEX, "sip": DataFeed.SIP}
            feed = feed_map.get(settings.ALPACA_DATA_FEED.lower(), DataFeed.IEX)
            req = StockBarsRequest(
                symbol_or_symbols=symbol,
                timeframe=tf,
                start=start,
                end=end,
                feed=feed,
            )
            # Run in executor to avoid blocking async loop
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: self._hist_client.get_stock_bars(req)
            )
            bars_data = response.data.get(symbol, [])
            return [normalise_bar(b, symbol, timeframe) for b in bars_data]
        except Exception as exc:
            logger.error("Historical bars fetch failed for %s: %s", symbol, exc)
            return []

    async def fetch_latest_quote(self, symbol: str) -> Optional[Quote]:
        if not self._hist_client:
            return None
        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            req = StockLatestQuoteRequest(symbol_or_symbols=symbol)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, lambda: self._hist_client.get_stock_latest_quote(req)
            )
            q = response.get(symbol)
            return normalise_quote(q, symbol) if q else None
        except Exception as exc:
            logger.debug("Latest quote fetch failed for %s: %s", symbol, exc)
            return None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "alpaca"
