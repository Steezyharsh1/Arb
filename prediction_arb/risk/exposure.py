"""Task-safe unhedged-exposure tracking (§32).

The tracker is part of the execution lifecycle, not a status-only counter:
``ExecutionCoordinator`` checks it before leg A, after each leg, before every
recovery order, and after recovery.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, TYPE_CHECKING

from ..core.clock import utcnow

if TYPE_CHECKING:  # avoid a runtime import cycle in type checkers
    from .limits import RiskLimits


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
            if exp.contracts <= 0 or exp.dollars <= 0:
                self._unhedged.pop(exp.opportunity_id, None)
            else:
                self._unhedged[exp.opportunity_id] = exp

    async def clear_unhedged(self, opportunity_id: str) -> None:
        async with self._lock:
            self._unhedged.pop(opportunity_id, None)

    async def snapshot(self) -> list[UnhedgedExposure]:
        async with self._lock:
            # Exposure records are replaced, never mutated, by production
            # callers; return a list so no caller can alter the tracker map.
            return list(self._unhedged.values())

    async def get(self, opportunity_id: str) -> Optional[UnhedgedExposure]:
        async with self._lock:
            return self._unhedged.get(opportunity_id)

    async def total_unhedged_dollars(self) -> Decimal:
        async with self._lock:
            return sum((e.dollars for e in self._unhedged.values()),
                       Decimal("0"))

    async def violations(
        self,
        limits: "RiskLimits",
        *,
        additional: Optional[UnhedgedExposure] = None,
        now: Optional[datetime] = None,
    ) -> list[str]:
        """Return aggregate and per-incident configured limit violations.

        ``additional`` is a non-mutating prospective exposure used immediately
        before leg A.  This closes the old gap where limits were checked only
        after a naked position already existed.
        """
        observed = await self.snapshot()
        if additional is not None:
            observed.append(additional)
        moment = now or utcnow()
        violations: list[str] = []
        total = sum((e.dollars for e in observed), Decimal("0"))
        if total > limits.max_unhedged_exposure:
            violations.append(
                f"max_unhedged_exposure: aggregate ${total} > "
                f"${limits.max_unhedged_exposure}")
        for exp in observed:
            age = (moment - exp.since).total_seconds()
            identity = (f"{exp.opportunity_id} "
                        f"({exp.venue}/{exp.contract_id})")
            if age > limits.max_unhedged_seconds:
                violations.append(
                    f"max_unhedged_seconds: {identity} age {age:.3f}s > "
                    f"{limits.max_unhedged_seconds}s")
            if exp.contracts > limits.max_unhedged_contracts:
                violations.append(
                    f"max_unhedged_contracts: {identity} "
                    f"{exp.contracts} > {limits.max_unhedged_contracts}")
            if exp.dollars > limits.max_unhedged_dollars:
                violations.append(
                    f"max_unhedged_dollars: {identity} ${exp.dollars} > "
                    f"${limits.max_unhedged_dollars}")
        return violations

    async def breaches(self, max_seconds: int, max_contracts: int,
                       max_dollars: Decimal,
                       now: Optional[datetime] = None
                       ) -> list[UnhedgedExposure]:
        """Compatibility helper returning records that breach partial bounds."""
        moment = now or utcnow()
        out = []
        async with self._lock:
            for exp in self._unhedged.values():
                age = (moment - exp.since).total_seconds()
                if (age > max_seconds or exp.contracts > max_contracts
                        or exp.dollars > max_dollars):
                    out.append(exp)
        return out
