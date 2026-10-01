"""
Alpaca LIVE trading execution adapter.

Routes orders through the Alpaca LIVE trading API (real money).

Requirements:
 - LIVE_TRADING_ENABLED=true in .env
 - ALPACA_API_KEY / ALPACA_API_SECRET set to your LIVE (not paper) credentials
 - ALPACA_BASE_URL=https://api.alpaca.markets
 - EXECUTION_MODE=live

WARNING: This adapter submits REAL orders to your funded brokerage account.
         Always verify your kill switch and risk settings before enabling.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.execution.base import ExecutionAdapter
from app.providers.alpaca.trading import AlpacaTradingProvider
from app.schemas.trading import CreateOrderRequest, OrderResponse

logger = logging.getLogger(__name__)


class AlpacaLiveExecutionAdapter(ExecutionAdapter):
    """Submits real-money orders via the Alpaca live trading API."""

    def __init__(self) -> None:
        self._provider = AlpacaTradingProvider(paper=False)

    async def submit_order(
        self,
        req: CreateOrderRequest,
        current_price: Optional[float] = None,
    ) -> OrderResponse:
        logger.warning(
            "LIVE ORDER SUBMITTED: %s %s %.0f shares",
            req.side.value.upper(), req.symbol, req.qty,
        )
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
            execution_mode="live",
        )

    async def close_position(self, symbol: str, current_price: Optional[float] = None) -> dict:
        logger.warning("LIVE CLOSE POSITION: %s", symbol)
        return await self._provider.close_position(symbol.upper())

    async def get_account(self) -> dict:
        return await self._provider.get_account()

    @property
    def mode_name(self) -> str:
        return "live"
