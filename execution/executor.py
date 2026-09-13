"""Execution engine interface + the three isolated implementations'
common base (§44–§46, §60).

Architecture as security boundary:
  * ExecutionEngine is abstract.
  * HistoricalExecutionEngine / PaperExecutionEngine hold NO venue
    adapters capable of order placement — there is no code path from
    them to a live endpoint, not a boolean flag.
  * LiveExecutionEngine is the ONLY class that may transmit orders, and
    every order passes through LiveOrderFirewall first.
  * No automatic fallback between engines exists anywhere (§55).
"""
from __future__ import annotations

import abc
from decimal import Decimal
from typing import Any, Optional

from ..core.types import Mode, OrderRequest, OrderResult


class ExecutionEngine(abc.ABC):
    """Common interface (§46). The arbitrage engine does not know which
    implementation it holds."""

    mode: Mode
    simulated: bool

    @abc.abstractmethod
    async def submit_order(self, request: OrderRequest) -> OrderResult: ...

    @abc.abstractmethod
    async def cancel_order(self, venue: str, order_id: str) -> bool: ...

    @abc.abstractmethod
    async def get_order_status(self, venue: str,
                               order_id: str) -> Optional[OrderResult]: ...

    @abc.abstractmethod
    async def get_positions(self) -> list[dict[str, Any]]: ...

    @abc.abstractmethod
    async def get_balances(self) -> dict[str, dict[str, Decimal]]: ...
