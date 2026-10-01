"""
Alpaca paper trading execution adapter.

Routes orders through the Alpaca paper trading API.
Requires valid Alpaca credentials and EXECUTION_MODE=paper_alpaca.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.execution.base import ExecutionAdapter
from app.providers.alpaca.trading import AlpacaTradingProvider
from app.schemas.trading import CreateOrderRequest, OrderResponse

logger = logging.getLogger(__name__)


class AlpacaPaperExecutionAdapter(ExecutionAdapter):

    def __init__(self) -> None:
        self._provider = AlpacaTradingProvider(paper=True)

    async def submit_order(
        self,
        req: CreateOrderRequest,
        current_price: Optional[float] = None,
    ) -> OrderResponse:
        result = await self._provider.place_order(
            symbol=req.symbol.upper(),
            side=req.side.value,
            qty=req.qty,
            order_type=req.order_type.value,
            limit_price=req.limit_price,
            stop_price=req.stop_price,
            take_profit_price=req.take_profit_price,
        )
        return OrderResponse(
            order_id=result["order_id"],
            symbol=result["symbol"],
            side=result["side"],
            qty=result["qty"],
            status=result["status"],
            filled_price=result.get("filled_price"),
            created_at=datetime.now(timezone.utc),
            execution_mode="paper_alpaca",
        )

    async def close_position(self, symbol: str, current_price: Optional[float] = None) -> dict:
        return await self._provider.close_position(symbol.upper())

    @property
    def mode_name(self) -> str:
        return "paper_alpaca"
