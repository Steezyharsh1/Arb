"""Abstract venue adapter interface (§10).

Every adapter implements this normalized interface. Venue-specific quirks
stay inside the adapter. Adapters that cannot be verified are structured
stubs that raise NotImplementedError with descriptive messages and report
an honest APIStatus.
"""
from __future__ import annotations

import abc
from decimal import Decimal
from typing import Any, Awaitable, Callable, Optional

from ..core.types import (
    APIStatus,
    MarketMicrostructure,
    NormalizedContract,
    OrderBook,
    OrderRequest,
    OrderResult,
    OrderTypeSupport,
)

OrderBookCallback = Callable[[OrderBook], Awaitable[None]]


class PredictionMarketAdapter(abc.ABC):
    """Normalized async adapter interface for one venue."""

    venue: str = "abstract"

    # Class-level capability flags (Phase 2 streaming). Adapters that can
    # push real-time books set this to True; modes check it before trying
    # to subscribe and fall back to REST polling otherwise.
    supports_streaming: bool = False

    # -- lifecycle ---------------------------------------------------------
    @abc.abstractmethod
    async def connect(self) -> None: ...

    @abc.abstractmethod
    async def disconnect(self) -> None: ...

    # -- account -----------------------------------------------------------
    @abc.abstractmethod
    async def get_account(self) -> dict[str, Any]: ...

    @abc.abstractmethod
    async def get_balances(self) -> dict[str, Decimal]: ...

    @abc.abstractmethod
    async def get_positions(self) -> list[dict[str, Any]]: ...

    # -- market data ---------------------------------------------------
    @abc.abstractmethod
    async def get_markets(self, limit: int = 200) -> list[NormalizedContract]: ...

    @abc.abstractmethod
    async def get_market(self, market_id: str) -> Optional[NormalizedContract]: ...

    @abc.abstractmethod
    async def get_order_book(self, market_id: str) -> Optional[OrderBook]: ...

    @abc.abstractmethod
    async def subscribe_order_book(
        self, market_id: str, callback: OrderBookCallback
    ) -> None: ...

    async def unsubscribe_order_book(self, market_id: str) -> None:
        """Drop a streaming subscription started by subscribe_order_book.

        Default is a no-op: adapters without streaming (supports_streaming
        False) have nothing to tear down. Streaming adapters override this."""
        return None

    # -- orders --------------------------------------------------------
    @abc.abstractmethod
    async def get_open_orders(self) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResult: ...

    @abc.abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abc.abstractmethod
    async def cancel_all_orders(self) -> int: ...

    @abc.abstractmethod
    async def get_order(self, order_id: str) -> Optional[OrderResult]: ...

    @abc.abstractmethod
    async def get_fills(self) -> list[dict[str, Any]]: ...

    # -- metadata --------------------------------------------------------
    @abc.abstractmethod
    def get_supported_order_types(self) -> OrderTypeSupport: ...

    @abc.abstractmethod
    def get_market_microstructure(self) -> MarketMicrostructure: ...

    @abc.abstractmethod
    def get_api_status(self) -> APIStatus: ...

    # -- health ----------------------------------------------------------
    async def healthcheck(self) -> bool:
        """Default health check: adapter is healthy if it can list markets."""
        try:
            markets = await self.get_markets(limit=1)
            return bool(markets)
        except Exception:
            return False
