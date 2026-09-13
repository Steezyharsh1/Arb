"""Partial-fill recovery (§32). Predefined, bounded procedure:

1. Retry the unfilled leg at a worse price up to max acceptable hedge
   cost (bounded by max_emergency_loss per incident).
2. If retry fails, unwind the filled leg (sell it back) to flatten.
3. Alert + record the incident regardless of outcome.

Never allows unbounded naked exposure: the loop is bounded by attempts
and by dollars.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from ..core.logging import get_logger
from ..core.types import (
    Opportunity,
    OrderAction,
    OrderRequest,
    OrderStatus,
    OrderType,
)
from ..monitoring.alerts import AlertSink
from ..risk.limits import RiskLimits
from .executor import ExecutionEngine

log = get_logger("execution.partial_fills")


@dataclass
class RecoveryOutcome:
    hedged: bool
    emergency_cost: Decimal = Decimal("0")
    notes: list[str] = field(default_factory=list)


class PartialFillHandler:
    def __init__(self, limits: RiskLimits, alerts: AlertSink,
                 max_retry_attempts: int = 2,
                 price_concession: Decimal = Decimal("0.02")):
        self.limits = limits
        self.alerts = alerts
        self.max_retry_attempts = max_retry_attempts
        self.price_concession = price_concession

    async def handle(self, op: Opportunity, engine: ExecutionEngine,
                     filled_leg: OrderRequest, filled_quantity: Decimal,
                     unfilled_leg: OrderRequest,
                     unfilled_quantity: Decimal) -> RecoveryOutcome:
        outcome = RecoveryOutcome(hedged=False)
        await self.alerts.alert(
            "PARTIAL FILL",
            f"opportunity {op.opportunity_id}: leg "
            f"{filled_leg.venue}/{filled_leg.contract_id} filled "
            f"{filled_quantity}, hedge leg {unfilled_leg.venue}/"
            f"{unfilled_leg.contract_id} short {unfilled_quantity}")

        # Strategy 1: chase the hedge leg with bounded concession
        base_price = unfilled_leg.limit_price or Decimal("0.50")
        remaining = unfilled_quantity
        for attempt in range(1, self.max_retry_attempts + 1):
            concession = self.price_concession * attempt
            extra_cost = concession * remaining
            if outcome.emergency_cost + extra_cost > self.limits.max_emergency_loss:
                outcome.notes.append(
                    f"hedge chase stopped: concession cost would exceed "
                    f"max_emergency_loss {self.limits.max_emergency_loss}")
                break
            retry = OrderRequest(
                venue=unfilled_leg.venue,
                contract_id=unfilled_leg.contract_id,
                side=unfilled_leg.side, action=unfilled_leg.action,
                quantity=remaining, order_type=OrderType.LIMIT,
                limit_price=min(Decimal("0.99"), base_price + concession),
                client_order_id=str(uuid.uuid4()), mode=unfilled_leg.mode)
            try:
                result = await engine.submit_order(retry)
            except Exception as exc:
                outcome.notes.append(f"hedge retry {attempt} error: {exc}")
                continue
            if result.status in (OrderStatus.FILLED,
                                 OrderStatus.PARTIALLY_FILLED):
                got = result.filled_quantity
                outcome.emergency_cost += concession * got
                remaining -= got
                outcome.notes.append(
                    f"hedge retry {attempt}: filled {got} at concession "
                    f"+{concession}")
                if remaining <= 0:
                    outcome.hedged = True
                    await self.alerts.alert(
                        "HEDGE COMPLETED",
                        f"opportunity {op.opportunity_id} hedged after "
                        f"retry; emergency cost {outcome.emergency_cost}")
                    return outcome

        # Strategy 2: unwind the filled leg
        unwind = OrderRequest(
            venue=filled_leg.venue, contract_id=filled_leg.contract_id,
            side=filled_leg.side, action=OrderAction.SELL,
            quantity=remaining if remaining < filled_quantity else filled_quantity,
            order_type=OrderType.LIMIT,
            limit_price=Decimal("0.01"),  # sell to best bid via walker
            client_order_id=str(uuid.uuid4()), mode=filled_leg.mode)
        try:
            result = await engine.submit_order(unwind)
            if result.status in (OrderStatus.FILLED,
                                 OrderStatus.PARTIALLY_FILLED):
                outcome.notes.append(
                    f"unwound {result.filled_quantity} of filled leg at "
                    f"{result.average_fill_price}")
                outcome.hedged = result.filled_quantity >= remaining
            else:
                outcome.notes.append(
                    f"unwind not filled: {result.status.value}")
        except Exception as exc:
            outcome.notes.append(f"unwind error: {exc}")

        await self.alerts.alert(
            "PARTIAL FILL RESOLUTION",
            f"opportunity {op.opportunity_id}: hedged={outcome.hedged}, "
            f"emergency_cost={outcome.emergency_cost}, "
            f"notes={outcome.notes}")
        if not outcome.hedged:
            await self.alerts.alert(
                "UNRESOLVED EXPOSURE — MANUAL ACTION REQUIRED",
                f"opportunity {op.opportunity_id} still has naked exposure. "
                f"Kill switch recommended.")
        return outcome
