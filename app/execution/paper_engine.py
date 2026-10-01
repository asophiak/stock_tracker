"""
Local simulated paper trading adapter.

Fills market orders immediately at current price.
All state persists to the SQLite DB via paper_trading_service.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.execution.base import ExecutionAdapter
from app.schemas.trading import CreateOrderRequest, OrderResponse
from app.services import paper_trading_service
from app.utils.cache import StateManager

logger = logging.getLogger(__name__)


class LocalPaperExecutionAdapter(ExecutionAdapter):

    def __init__(self, db_factory, state_manager: StateManager) -> None:
        self._db_factory = db_factory
        self._state_manager = state_manager

    async def submit_order(
        self,
        req: CreateOrderRequest,
        current_price: Optional[float] = None,
    ) -> OrderResponse:
        # Resolve current price
        if current_price is None:
            state = self._state_manager.get_state(req.symbol.upper())
            current_price = state.last_price

        async with self._db_factory() as db:
            order = await paper_trading_service.place_paper_order(
                req, db, "paper_local", current_price
            )

        return OrderResponse(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            status=order.status,
            filled_price=order.filled_price,
            created_at=order.created_at,
            execution_mode="paper_local",
        )

    async def close_position(self, symbol: str, current_price: Optional[float] = None) -> dict:
        if current_price is None:
            state = self._state_manager.get_state(symbol.upper())
            current_price = state.last_price

        if not current_price:
            return {"error": "no_price_available"}

        # Create a synthetic sell order to close
        from app.schemas.trading import OrderSide, OrderType
        req = CreateOrderRequest(
            symbol=symbol.upper(),
            side=OrderSide.SELL,
            qty=0,   # will be filled by service using position qty
            order_type=OrderType.MARKET,
        )
        async with self._db_factory() as db:
            # Get position qty first
            positions = await paper_trading_service.get_open_positions(db)
            pos = next((p for p in positions if p.symbol == symbol.upper()), None)
            if not pos:
                return {"error": "no_position"}
            req.qty = pos.qty
            await paper_trading_service.place_paper_order(req, db, "paper_local", current_price)

        return {"symbol": symbol, "status": "closed"}

    @property
    def mode_name(self) -> str:
        return "paper_local"
