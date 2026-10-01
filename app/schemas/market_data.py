"""
Pydantic schemas for market data: bars, quotes, and symbol snapshots.
These are the canonical in-memory data structures used throughout the app.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Bar:
    """OHLCV bar for a given timeframe."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None
    timeframe: str = "1Min"  # 1Min | 5Min | 15Min

    @property
    def body_size(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def midpoint(self) -> float:
        return (self.high + self.low) / 2.0


@dataclass
class Quote:
    """Latest bid/ask snapshot."""

    timestamp: datetime
    symbol: str
    ask_price: float
    bid_price: float
    ask_size: int
    bid_size: int

    @property
    def spread(self) -> float:
        return self.ask_price - self.bid_price

    @property
    def mid_price(self) -> float:
        return (self.ask_price + self.bid_price) / 2.0

    @property
    def spread_pct(self) -> float:
        if self.mid_price == 0:
            return 0.0
        return self.spread / self.mid_price


@dataclass
class SymbolState:
    """
    Complete in-memory state for a single symbol.
    Updated on every new bar arrival and re-used by the signal engine.
    """

    symbol: str

    # Rolling bar windows
    bars_1m: List[Bar] = field(default_factory=list)
    bars_5m: List[Bar] = field(default_factory=list)
    bars_15m: List[Bar] = field(default_factory=list)

    # Latest market data
    last_price: Optional[float] = None
    last_quote: Optional[Quote] = None
    last_updated: Optional[datetime] = None

    # VWAP state (cumulated during session)
    vwap: Optional[float] = None
    cumulative_tp_vol: float = 0.0   # sum(typical_price * volume)
    cumulative_vol: int = 0

    # Session levels
    opening_range_high: Optional[float] = None
    opening_range_low: Optional[float] = None
    opening_range_set: bool = False
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    premarket_high: Optional[float] = None
    premarket_low: Optional[float] = None

    # Volume
    session_volume: int = 0
    avg_daily_volume: Optional[float] = None

    @property
    def rvol(self) -> Optional[float]:
        """Relative volume: today's session volume vs average daily volume."""
        if self.avg_daily_volume and self.avg_daily_volume > 0:
            # Scale by fraction of day elapsed (approximation)
            return self.session_volume / (self.avg_daily_volume / 390 * max(len(self.bars_1m), 1))
        return None

    @property
    def distance_from_vwap(self) -> Optional[float]:
        if self.vwap and self.last_price:
            return (self.last_price - self.vwap) / self.vwap
        return None

    @property
    def above_vwap(self) -> Optional[bool]:
        if self.vwap and self.last_price:
            return self.last_price > self.vwap
        return None
