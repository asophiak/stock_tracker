"""
Mock market data provider for development / no-credentials mode.

Generates synthetic price bars so the UI and signal engine can be
exercised without a live data connection.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from app.providers.base import BarCallback, MarketDataProvider, QuoteCallback
from app.schemas.market_data import Bar, Quote

logger = logging.getLogger(__name__)

# Seed prices for the mock universe
_SEED_PRICES = {
    "SPY": 450.0,
    "QQQ": 370.0,
    "NVDA": 480.0,
    "TSLA": 200.0,
}
_DEFAULT_SEED = 100.0


class MockMarketDataProvider(MarketDataProvider):
    """
    Emits synthetic 1-minute bars on a configurable tick interval.
    Useful for UI development and offline testing.
    """

    def __init__(self, tick_interval_seconds: float = 5.0) -> None:
        self._tick_interval = tick_interval_seconds
        self._symbols: List[str] = []
        self._bar_callback: Optional[BarCallback] = None
        self._quote_callback: Optional[QuoteCallback] = None
        self._connected = False
        self._task: Optional[asyncio.Task] = None
        self._prices: dict[str, float] = {}

    async def connect(self) -> None:
        self._connected = True
        logger.info("MockMarketDataProvider: connected (synthetic data mode).")

    async def disconnect(self) -> None:
        self._connected = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("MockMarketDataProvider: disconnected.")

    async def subscribe(self, symbols: List[str]) -> None:
        self._symbols = symbols
        self._prices = {s: _SEED_PRICES.get(s, _DEFAULT_SEED) for s in symbols}
        self._task = asyncio.create_task(self._emit_loop())
        logger.info("MockMarketDataProvider: generating bars for %s", symbols)

    async def _emit_loop(self) -> None:
        bar_index = 0
        while True:
            try:
                await asyncio.sleep(self._tick_interval)
                now = datetime.now(timezone.utc)
                for sym in self._symbols:
                    bar = self._generate_bar(sym, now, bar_index)
                    self._prices[sym] = bar.close
                    if self._bar_callback:
                        self._bar_callback(sym, bar)
                bar_index += 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Mock emit error: %s", exc)

    def _generate_bar(self, symbol: str, timestamp: datetime, idx: int) -> Bar:
        price = self._prices.get(symbol, _DEFAULT_SEED)
        # Brownian motion with slight trend
        drift = math.sin(idx * 0.1) * 0.0002
        move_pct = random.gauss(drift, 0.003)
        close = round(price * (1 + move_pct), 2)
        open_ = round(price, 2)
        high = round(max(open_, close) * (1 + random.uniform(0, 0.002)), 2)
        low = round(min(open_, close) * (1 - random.uniform(0, 0.002)), 2)
        volume = int(random.gauss(50_000, 15_000))
        volume = max(volume, 1000)
        return Bar(
            timestamp=timestamp,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            timeframe="1Min",
        )

    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Bar]:
        """Generate synthetic historical bars for the given date range."""
        bars: List[Bar] = []
        price = _SEED_PRICES.get(symbol, _DEFAULT_SEED)
        current = start
        while current < end:
            move_pct = random.gauss(0.0001, 0.003)
            close = round(price * (1 + move_pct), 2)
            open_ = round(price, 2)
            high = round(max(open_, close) * (1 + random.uniform(0, 0.002)), 2)
            low = round(min(open_, close) * (1 - random.uniform(0, 0.002)), 2)
            volume = int(random.gauss(50_000, 15_000))
            bars.append(Bar(
                timestamp=current,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=max(volume, 1000),
                timeframe=timeframe,
            ))
            price = close
            if timeframe == "1Min":
                current += timedelta(minutes=1)
            elif timeframe == "5Min":
                current += timedelta(minutes=5)
            else:
                current += timedelta(minutes=15)
        return bars

    async def fetch_latest_quote(self, symbol: str) -> Optional[Quote]:
        price = self._prices.get(symbol, _SEED_PRICES.get(symbol, 100.0))
        spread = price * 0.0005
        return Quote(
            timestamp=datetime.now(timezone.utc),
            symbol=symbol,
            ask_price=round(price + spread / 2, 2),
            bid_price=round(price - spread / 2, 2),
            ask_size=100,
            bid_size=100,
        )

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def name(self) -> str:
        return "mock"
