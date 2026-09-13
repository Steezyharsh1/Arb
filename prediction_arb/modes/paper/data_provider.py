"""Paper-mode data provider: live market data via verified adapters,
REST polling in v1 (Kalshi WS requires an API key). Structurally
identical interface to historical/live providers (§57)."""
from __future__ import annotations

from typing import Optional

from ...adapters.base import PredictionMarketAdapter
from ...core.types import APIStatus, NormalizedContract, OrderBook


class PaperDataProvider:
    def __init__(self, adapters: dict[str, PredictionMarketAdapter]):
        self.adapters = {
            v: a for v, a in adapters.items()
            if a.get_api_status() == APIStatus.VERIFIED}

    async def get_markets(self, limit: int = 200
                          ) -> list[NormalizedContract]:
        out: list[NormalizedContract] = []
        for adapter in self.adapters.values():
            out.extend(await adapter.get_markets(limit=limit))
        return out

    async def get_order_book(self, venue: str,
                             contract_id: str) -> Optional[OrderBook]:
        adapter = self.adapters.get(venue)
        if adapter is None:
            return None
        return await adapter.get_order_book(contract_id)
