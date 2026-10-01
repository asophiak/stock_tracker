"""
Abstract provider interfaces.

All concrete providers (Alpaca, Polygon, mock) must implement these ABCs.
The rest of the application only depends on these interfaces, making
provider swaps transparent.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Callable, List, Optional

from app.schemas.market_data import Bar, Quote, SymbolState
from app.schemas.news import NewsItemSchema


BarCallback = Callable[[str, Bar], None]
QuoteCallback = Callable[[str, Quote], None]
NewsCallback = Callable[[NewsItemSchema], None]


class MarketDataProvider(ABC):
    """Real-time and historical market data."""

    @abstractmethod
    async def connect(self) -> None:
        """Start streaming connection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Stop streaming connection."""

    @abstractmethod
    async def subscribe(self, symbols: List[str]) -> None:
        """Subscribe to real-time bars for the given symbols."""

    @abstractmethod
    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> List[Bar]:
        """Fetch historical OHLCV bars."""

    @abstractmethod
    async def fetch_latest_quote(self, symbol: str) -> Optional[Quote]:
        """Fetch the latest bid/ask quote."""

    def on_bar(self, callback: BarCallback) -> None:
        self._bar_callback = callback

    def on_quote(self, callback: QuoteCallback) -> None:
        self._quote_callback = callback

    @property
    def is_connected(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return self.__class__.__name__


class NewsProvider(ABC):
    """Real-time and historical news."""

    @abstractmethod
    async def connect(self) -> None:
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        pass

    @abstractmethod
    async def subscribe(self, symbols: List[str]) -> None:
        pass

    @abstractmethod
    async def fetch_recent_news(
        self,
        symbol: str,
        limit: int = 20,
    ) -> List[NewsItemSchema]:
        pass

    def on_news(self, callback: NewsCallback) -> None:
        self._news_callback = callback

    @property
    def is_connected(self) -> bool:
        return False


class TradingProvider(ABC):
    """Order placement and account management."""

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "market",
        limit_price: Optional[float] = None,
        stop_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
    ) -> dict:
        """Place an order. Returns order details dict."""

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    async def close_position(self, symbol: str) -> dict:
        pass

    @abstractmethod
    async def get_positions(self) -> List[dict]:
        pass

    @abstractmethod
    async def get_account(self) -> dict:
        pass

    @property
    def is_paper(self) -> bool:
        return True
