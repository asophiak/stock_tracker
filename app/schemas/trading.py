"""
Pydantic schemas for paper trading requests and responses.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    BRACKET = "bracket"


class ExecutionMode(str, Enum):
    DISABLED = "disabled"
    PAPER_LOCAL = "paper_local"
    PAPER_ALPACA = "paper_alpaca"
    LIVE = "live"


class CreateOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    side: OrderSide
    qty: float = Field(..., gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    signal_id: Optional[int] = None
    thesis_summary: Optional[str] = None
    strategy_version: Optional[str] = None   # "4b" | "scalp"


class ClosePositionRequest(BaseModel):
    symbol: str
    reason: str = "manual"


class OrderResponse(BaseModel):
    order_id: str
    symbol: str
    side: str
    qty: float
    status: str
    filled_price: Optional[float] = None
    created_at: datetime
    execution_mode: str


class PositionResponse(BaseModel):
    symbol: str
    side: str
    qty: float
    avg_entry_price: float
    current_price: Optional[float]
    unrealized_pnl: Optional[float]
    stop_price: Optional[float]
    take_profit_price: Optional[float]
    opened_at: datetime
    execution_mode: str
    strategy_version: Optional[str] = None
    thesis_summary: Optional[str] = None


class ClosedTradeResponse(BaseModel):
    id: int
    symbol: str
    side: str
    qty: float
    entry_price: float
    exit_price: float
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    pnl: float
    pnl_pct: Optional[float]
    opened_at: datetime
    closed_at: datetime
    close_reason: Optional[str]
    session_date: Optional[str]
    grade: Optional[str] = None
    lesson: Optional[str] = None
    thesis_summary: Optional[str] = None
    entry_signal_json: Optional[str] = None
    time_bucket: Optional[str] = None
    execution_mode: Optional[str] = None
