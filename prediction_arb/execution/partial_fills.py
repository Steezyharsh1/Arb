"""Partial-fill recovery (§32).

The procedure is deliberately bounded and risk-reducing:

1. Retry the unfilled leg with price concession bounded by the configured
   ``max_emergency_loss`` *concession budget*.
2. If retry fails, sell enough of leg A to leave only a matched pair.
3. Alert and record the outcome; unresolved exposure is never called success.

The coordinator supplies a risk checkpoint which runs before every recovery
order.  A breach engages the kill switch for new trades but does not prevent
these risk-reducing recovery orders.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable, Optional

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
RiskCheckpoint = Callable[[str], Awaitable[list[str]]]


@dataclass
class RecoveryOutcome:
    hedged: bool
    emergency_cost: Decimal = Decimal("0")
    remaining_unhedged: Decimal = Decimal("0")
    unwound_quantity: Decimal = Decimal("0")
    open_hedged_quantity: Decimal = Decimal("0")
    resolution: str = "UNRESOLVED"
    #: Recovery orders submitted (hedge retries plus unwind). Telemetry only:
    #: an operator reads recovery effort from here, not from the notes.
    attempts: int = 0
    notes: list[str] = field(default_factory=list)


class PartialFillHandler:
    def __init__(self, limits: RiskLimits, alerts: AlertSink,
                 max_retry_attempts: int = 2,
                 price_concession: Decimal = Decimal("0.02")):
        self.limits = limits
        self.alerts = alerts
        self.max_retry_attempts = max_retry_attempts
        self.price_concession = price_concession

    async def handle(
        self,
        op: Opportunity,
        engine: ExecutionEngine,
        filled_leg: OrderRequest,
        filled_quantity: Decimal,
        unfilled_leg: OrderRequest,
        unfilled_quantity: Decimal,
        *,
        filled_average_price: Optional[Decimal] = None,
        risk_checkpoint: Optional[RiskCheckpoint] = None,
    ) -> RecoveryOutcome:
        outcome = RecoveryOutcome(
            hedged=False,
            remaining_unhedged=max(Decimal("0"), unfilled_quantity),
            # Leg B may already have partially filled before this handler was
            # called. Preserve that paired quantity even if later recovery
            # remains unresolved.
            open_hedged_quantity=max(
                Decimal("0"), filled_quantity - unfilled_quantity))
        await self.alerts.alert(
            "PARTIAL FILL",
            f"opportunity {op.opportunity_id}: leg "
            f"{filled_leg.venue}/{filled_leg.contract_id} filled "
            f"{filled_quantity}, hedge leg {unfilled_leg.venue}/"
            f"{unfilled_leg.contract_id} short {unfilled_quantity}")

        # Strategy 1: chase the hedge leg with bounded price concession.
        base_price = unfilled_leg.limit_price or Decimal("0.50")
        remaining = outcome.remaining_unhedged
        for attempt in range(1, self.max_retry_attempts + 1):
            if remaining <= 0:
                break
            if risk_checkpoint is not None:
                await risk_checkpoint(f"before hedge retry {attempt}")
            concession = self.price_concession * attempt
            potential_concession = concession * remaining
            if (outcome.emergency_cost + potential_concession
                    > self.limits.max_emergency_loss):
                outcome.notes.append(
                    "hedge chase stopped: concession cost would exceed "
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
                outcome.attempts += 1
                result = await engine.submit_order(retry)
            except Exception as exc:
                outcome.notes.append(f"hedge retry {attempt} error: {exc}")
                continue
            if result.status in (OrderStatus.FILLED,
                                 OrderStatus.PARTIALLY_FILLED):
                got = min(remaining, result.filled_quantity)
                outcome.emergency_cost += concession * got
                outcome.open_hedged_quantity += got
                remaining -= got
                outcome.remaining_unhedged = remaining
                outcome.notes.append(
                    f"hedge retry {attempt}: filled {got} at concession "
                    f"+{concession}")
                if remaining <= 0:
                    outcome.hedged = True
                    outcome.resolution = "HEDGED"
                    outcome.open_hedged_quantity = filled_quantity
                    await self.alerts.alert(
                        "HEDGE COMPLETED",
                        f"opportunity {op.opportunity_id} hedged after "
                        f"retry; emergency cost {outcome.emergency_cost}")
                    return outcome

        # Strategy 2: unwind only the still-unmatched portion of leg A.  This
        # leaves any quantity already filled on both legs as a hedged pair.
        unwind_quantity = min(remaining, filled_quantity)
        if unwind_quantity > 0:
            if risk_checkpoint is not None:
                await risk_checkpoint("before unwind")
            unwind = OrderRequest(
                venue=filled_leg.venue, contract_id=filled_leg.contract_id,
                side=filled_leg.side, action=OrderAction.SELL,
                quantity=unwind_quantity,
                order_type=OrderType.LIMIT,
                limit_price=Decimal("0.01"),  # sell to displayed bids only
                client_order_id=str(uuid.uuid4()), mode=filled_leg.mode)
            try:
                outcome.attempts += 1
                result = await engine.submit_order(unwind)
                if result.status in (OrderStatus.FILLED,
                                     OrderStatus.PARTIALLY_FILLED):
                    unwound = min(unwind_quantity, result.filled_quantity)
                    outcome.unwound_quantity += unwound
                    remaining -= unwound
                    outcome.remaining_unhedged = max(Decimal("0"), remaining)
                    entry = (filled_average_price or filled_leg.limit_price
                             or Decimal("1"))
                    exit_price = result.average_fill_price or Decimal("0")
                    # Realized price loss and exit fees are observable after a
                    # simulated unwind and feed max_daily_loss accounting.
                    outcome.emergency_cost += (
                        max(Decimal("0"), entry - exit_price) * unwound
                        + result.fees)
                    outcome.notes.append(
                        f"unwound {unwound} of filled leg at "
                        f"{result.average_fill_price}")
                    outcome.hedged = outcome.remaining_unhedged <= 0
                else:
                    outcome.notes.append(
                        f"unwind not filled: {result.status.value}")
            except Exception as exc:
                outcome.notes.append(f"unwind error: {exc}")

        if outcome.hedged:
            outcome.open_hedged_quantity = max(
                Decimal("0"), filled_quantity - outcome.unwound_quantity)
            outcome.resolution = ("FLATTENED"
                                  if outcome.open_hedged_quantity <= 0
                                  else "HEDGED")

        await self.alerts.alert(
            "PARTIAL FILL RESOLUTION",
            f"opportunity {op.opportunity_id}: hedged={outcome.hedged}, "
            f"resolution={outcome.resolution}, "
            f"remaining_unhedged={outcome.remaining_unhedged}, "
            f"emergency_cost={outcome.emergency_cost}, "
            f"notes={outcome.notes}")
        if not outcome.hedged:
            await self.alerts.alert(
                "UNRESOLVED EXPOSURE — MANUAL ACTION REQUIRED",
                f"opportunity {op.opportunity_id} still has naked exposure. "
                "The coordinator will engage the kill switch; manual "
                "reconciliation is required.")
        return outcome
