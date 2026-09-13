"""Shared machinery for structured stub adapters (§5, §7).

A stub adapter is importable, type-checks, reports an honest APIStatus,
and raises NotImplementedError with a descriptive message from every
capability method. It never fabricates data.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Optional

from ..core.types import (
    APIStatus,
    MarketMicrostructure,
    MicrostructureType,
    NormalizedContract,
    OrderBook,
    OrderRequest,
    OrderResult,
    OrderTypeSupport,
)
from .base import OrderBookCallback, PredictionMarketAdapter


class StubAdapter(PredictionMarketAdapter):
    """Base for venues whose API is unavailable/unverified."""

    venue = "stub"
    api_status = APIStatus.UNVERIFIED
    reason = "stub"

    def _blocked(self, capability: str) -> NotImplementedError:
        return NotImplementedError(
            f"[{self.venue}] {capability} is not available: {self.reason} "
            f"(status={self.api_status.value}). See docs/VENUE_RESEARCH.md.")

    async def connect(self) -> None:
        # Connecting to a stub is a no-op: the venue is registered so its
        # status is visible, but no network activity occurs.
        return None

    async def disconnect(self) -> None:
        return None

    async def get_account(self) -> dict[str, Any]:
        raise self._blocked("get_account")

    async def get_balances(self) -> dict[str, Decimal]:
        raise self._blocked("get_balances")

    async def get_positions(self) -> list[dict[str, Any]]:
        raise self._blocked("get_positions")

    async def get_markets(self, limit: int = 200) -> list[NormalizedContract]:
        raise self._blocked("get_markets")

    async def get_market(self, market_id: str) -> Optional[NormalizedContract]:
        raise self._blocked("get_market")

    async def get_order_book(self, market_id: str) -> Optional[OrderBook]:
        raise self._blocked("get_order_book")

    async def subscribe_order_book(self, market_id: str,
                                   callback: OrderBookCallback) -> None:
        raise self._blocked("subscribe_order_book")

    async def get_open_orders(self) -> list[dict[str, Any]]:
        raise self._blocked("get_open_orders")

    async def place_order(self, request: OrderRequest) -> OrderResult:
        raise self._blocked("place_order")

    async def cancel_order(self, order_id: str) -> bool:
        raise self._blocked("cancel_order")

    async def cancel_all_orders(self) -> int:
        raise self._blocked("cancel_all_orders")

    async def get_order(self, order_id: str) -> Optional[OrderResult]:
        raise self._blocked("get_order")

    async def get_fills(self) -> list[dict[str, Any]]:
        raise self._blocked("get_fills")

    def get_supported_order_types(self) -> OrderTypeSupport:
        return OrderTypeSupport(verified=False, source=self.reason)

    def get_market_microstructure(self) -> MarketMicrostructure:
        return MarketMicrostructure(
            type=MicrostructureType.UNKNOWN, verified=False,
            source=self.reason)

    def get_api_status(self) -> APIStatus:
        return self.api_status

    async def healthcheck(self) -> bool:
        return False
