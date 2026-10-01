"""
Alpaca trading provider for paper (and optionally live) order execution.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from app.config import settings
from app.providers.base import TradingProvider
from app.providers.alpaca.client import get_trading_client

logger = logging.getLogger(__name__)


class AlpacaTradingProvider(TradingProvider):

    def __init__(self, paper: bool = True) -> None:
        self._paper = paper
        self._client = None

    async def _ensure_client(self) -> bool:
        if self._client is None:
            self._client = get_trading_client(paper=self._paper)
        return self._client is not None

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
        if not await self._ensure_client():
            raise RuntimeError("Alpaca trading client unavailable.")
        try:
            from alpaca.trading.requests import (
                LimitOrderRequest,
                MarketOrderRequest,
                StopLimitOrderRequest,
                TakeProfitRequest,
                StopLossRequest,
            )
            from alpaca.trading.enums import OrderSide, TimeInForce

            a_side = OrderSide.BUY if side == "buy" else OrderSide.SELL

            if order_type == "market":
                order_req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=a_side,
                    time_in_force=TimeInForce.DAY,
                )
            elif order_type == "limit" and limit_price:
                order_req = LimitOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=a_side,
                    time_in_force=TimeInForce.DAY,
                    limit_price=limit_price,
                )
            elif order_type == "bracket" and stop_price:
                from alpaca.trading.requests import OrderRequest
                # Bracket order requires stop_loss and take_profit
                tp = TakeProfitRequest(limit_price=take_profit_price) if take_profit_price else None
                sl = StopLossRequest(stop_price=stop_price)
                order_req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=a_side,
                    time_in_force=TimeInForce.DAY,
                    take_profit=tp,
                    stop_loss=sl,
                )
            else:
                order_req = MarketOrderRequest(
                    symbol=symbol,
                    qty=qty,
                    side=a_side,
                    time_in_force=TimeInForce.DAY,
                )

            loop = asyncio.get_event_loop()
            order = await loop.run_in_executor(
                None, lambda: self._client.submit_order(order_req)
            )
            return {
                "order_id": str(order.id),
                "symbol": order.symbol,
                "side": side,
                "qty": float(order.qty),
                "status": order.status.value if hasattr(order.status, "value") else str(order.status),
                "filled_price": float(order.filled_avg_price) if order.filled_avg_price else None,
            }
        except Exception as exc:
            logger.error("Alpaca order placement failed: %s", exc)
            raise

    async def cancel_order(self, order_id: str) -> bool:
        if not await self._ensure_client():
            return False
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: self._client.cancel_order_by_id(order_id))
            return True
        except Exception as exc:
            logger.error("Cancel order failed: %s", exc)
            return False

    async def close_position(self, symbol: str) -> dict:
        if not await self._ensure_client():
            raise RuntimeError("Alpaca trading client unavailable.")
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: self._client.close_position(symbol)
            )
            return {"symbol": symbol, "status": "closed"}
        except Exception as exc:
            logger.error("Close position failed for %s: %s", symbol, exc)
            raise

    async def get_positions(self) -> List[dict]:
        if not await self._ensure_client():
            return []
        try:
            loop = asyncio.get_event_loop()
            positions = await loop.run_in_executor(None, self._client.get_all_positions)
            return [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                    "avg_entry_price": float(p.avg_entry_price),
                    "current_price": float(p.current_price) if p.current_price else None,
                    "unrealized_pl": float(p.unrealized_pl) if p.unrealized_pl else None,
                }
                for p in positions
            ]
        except Exception as exc:
            logger.error("Get positions failed: %s", exc)
            return []

    async def get_account(self) -> dict:
        if not await self._ensure_client():
            return {}
        try:
            loop = asyncio.get_event_loop()
            account = await loop.run_in_executor(None, self._client.get_account)
            return {
                "equity": float(account.equity),
                "cash": float(account.cash),
                "buying_power": float(account.buying_power),
                "portfolio_value": float(account.portfolio_value),
                "paper": self._paper,
            }
        except Exception as exc:
            logger.error("Get account failed: %s", exc)
            return {}

    @property
    def is_paper(self) -> bool:
        return self._paper
