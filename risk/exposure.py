"""Exposure tracking, including unhedged (naked) exposure from partial
fills (§32). Task-safe."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..core.clock import utcnow


@dataclass
class UnhedgedExposure:
    opportunity_id: str
    venue: str
    contract_id: str
    contracts: Decimal
    dollars: Decimal
    since: datetime


class ExposureTracker:
    def __init__(self):
        self._unhedged: dict[str, UnhedgedExposure] = {}
        self._lock = asyncio.Lock()

    async def record_unhedged(self, exp: UnhedgedExposure) -> None:
        async with self._lock:
            self._unhedged[exp.opportunity_id] = exp

    async def clear_unhedged(self, opportunity_id: str) -> None:
        async with self._lock:
            self._unhedged.pop(opportunity_id, None)

    async def total_unhedged_dollars(self) -> Decimal:
        async with self._lock:
            return sum((e.dollars for e in self._unhedged.values()),
                       Decimal("0"))

    async def breaches(self, max_seconds: int, max_contracts: int,
                       max_dollars: Decimal,
                       now: Optional[datetime] = None
                       ) -> list[UnhedgedExposure]:
        now = now or utcnow()
        out = []
        async with self._lock:
            for e in self._unhedged.values():
                age = (now - e.since).total_seconds()
                if (age > max_seconds or e.contracts > max_contracts
                        or e.dollars > max_dollars):
                    out.append(e)
        return out
