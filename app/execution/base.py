"""
Abstract execution adapter interface.
All execution modes (disabled, paper_local, paper_alpaca, live) implement this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from app.schemas.trading import CreateOrderRequest, OrderResponse


class ExecutionAdapter(ABC):

    @abstractmethod
    async def submit_order(
        self,
        req: CreateOrderRequest,
        current_price: Optional[float] = None,
    ) -> OrderResponse:
        pass

    @abstractmethod
    async def close_position(self, symbol: str, current_price: Optional[float] = None) -> dict:
        pass

    @property
    @abstractmethod
    def mode_name(self) -> str:
        pass

    @property
    def is_live(self) -> bool:
        return False


class DisabledExecutionAdapter(ExecutionAdapter):
    """Rejects all order attempts. Used when execution is explicitly disabled."""

    async def submit_order(self, req: CreateOrderRequest, current_price=None):
        raise RuntimeError("Execution is disabled. Enable a paper or live mode to place orders.")

    async def close_position(self, symbol: str, current_price=None):
        raise RuntimeError("Execution is disabled.")

    @property
    def mode_name(self) -> str:
        return "disabled"
