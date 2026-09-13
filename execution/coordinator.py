"""Multi-leg execution coordinator (§31): PRECHECK → RESERVE → LEG A →
CONFIRM → LEG B → CONFIRM, with partial-fill handling delegated to
partial_fills.py. Engine-agnostic: works identically for simulated and
live engines through the ExecutionEngine interface (§57)."""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from ..core.clock import utcnow
from ..core.logging import get_logger
from ..core.types import (
    Mode,
    Opportunity,
    OpportunityStatus,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
)
from ..risk.exposure import ExposureTracker, UnhedgedExposure
from .executor import ExecutionEngine
from .partial_fills import PartialFillHandler

log = get_logger("execution.coordinator")


@dataclass
class LegOutcome:
    request: OrderRequest
    result: Optional[OrderResult] = None
    error: str = ""


@dataclass
class ExecutionReport:
    opportunity_id: str
    success: bool
    legs: list[LegOutcome] = field(default_factory=list)
    time_between_legs_ms: Optional[float] = None
    notes: list[str] = field(default_factory=list)


class ExecutionCoordinator:
    def __init__(self, engine: ExecutionEngine,
                 exposure: ExposureTracker,
                 partial_fill_handler: PartialFillHandler,
                 mode: Mode):
        self.engine = engine
        self.exposure = exposure
        self.partial_fills = partial_fill_handler
        self.mode = mode

    async def execute(self, op: Opportunity) -> ExecutionReport:
        report = ExecutionReport(opportunity_id=op.opportunity_id,
                                 success=False)

        # PRECHECK
        if op.status != OpportunityStatus.TRADE:
            report.notes.append(
                f"precheck failed: opportunity status {op.status.value}; "
                f"reasons: {op.block_reasons}")
            return report
        if self.mode != op.mode or self.mode != self.engine.mode:
            report.notes.append("precheck failed: mode mismatch — refusing")
            return report

        requests = [
            OrderRequest(
                venue=leg.venue, contract_id=leg.contract.contract_id,
                side=leg.side, action=leg.action, quantity=leg.quantity,
                order_type=OrderType.LIMIT,
                limit_price=leg.walk.weighted_average_price if leg.walk
                else leg.limit_price,
                client_order_id=str(uuid.uuid4()), mode=self.mode)
            for leg in op.legs
        ]

        # LEG A
        t0 = time.monotonic()
        leg_a = LegOutcome(request=requests[0])
        report.legs.append(leg_a)
        try:
            leg_a.result = await self.engine.submit_order(requests[0])
        except Exception as exc:
            leg_a.error = str(exc)
            report.notes.append(f"leg A failed pre-transmission: {exc}")
            return report

        if leg_a.result.status not in (OrderStatus.FILLED,
                                       OrderStatus.PARTIALLY_FILLED):
            report.notes.append(
                f"leg A not filled ({leg_a.result.status.value}: "
                f"{leg_a.result.message}) — aborting before leg B")
            return report

        filled_a = leg_a.result.filled_quantity
        # match leg B to what leg A actually filled
        requests[1].quantity = min(requests[1].quantity, filled_a)

        await self.exposure.record_unhedged(UnhedgedExposure(
            opportunity_id=op.opportunity_id,
            venue=requests[0].venue, contract_id=requests[0].contract_id,
            contracts=filled_a,
            dollars=filled_a * (leg_a.result.average_fill_price or
                                requests[0].limit_price or Decimal("1")),
            since=utcnow()))

        # LEG B
        leg_b = LegOutcome(request=requests[1])
        report.legs.append(leg_b)
        try:
            leg_b.result = await self.engine.submit_order(requests[1])
        except Exception as exc:
            leg_b.error = str(exc)
        report.time_between_legs_ms = (time.monotonic() - t0) * 1000.0

        filled_b = (leg_b.result.filled_quantity
                    if leg_b.result else Decimal("0"))

        if filled_b >= filled_a and filled_a > 0:
            await self.exposure.clear_unhedged(op.opportunity_id)
            report.success = True
            report.notes.append("both legs filled")
            return report

        # PARTIAL / FAILED LEG B → recovery procedure (§32)
        recovery = await self.partial_fills.handle(
            op=op, engine=self.engine,
            filled_leg=requests[0], filled_quantity=filled_a,
            unfilled_leg=requests[1], unfilled_quantity=filled_a - filled_b)
        report.notes.extend(recovery.notes)
        report.success = recovery.hedged
        if recovery.hedged:
            await self.exposure.clear_unhedged(op.opportunity_id)
        return report
