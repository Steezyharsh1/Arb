"""Two-leg execution coordinator (§31).

The coordinator is the runtime enforcement boundary for paper execution:
PRECHECK → RISK RESERVATION → LEG A → EXPOSURE CHECK → LEG B → EXPOSURE
CHECK → bounded recovery.  Checks live here rather than only in the paper scan
so direct callers cannot bypass them.
"""
from __future__ import annotations

import asyncio
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
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
)
from ..risk import kill_switch
from ..risk.exposure import ExposureTracker, UnhedgedExposure
from ..risk.limits import (
    RiskLimits,
    RiskState,
    check_trade_against_limits,
)
from .executor import ExecutionEngine
from .partial_fills import PartialFillHandler, RecoveryOutcome

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
    risk_violations: list[str] = field(default_factory=list)
    recovery: Optional[RecoveryOutcome] = None


class ExecutionCoordinator:
    def __init__(self, engine: ExecutionEngine,
                 exposure: ExposureTracker,
                 partial_fill_handler: PartialFillHandler,
                 mode: Mode,
                 limits: RiskLimits,
                 risk_state: RiskState):
        self.engine = engine
        self.exposure = exposure
        self.partial_fills = partial_fill_handler
        self.mode = mode
        self.limits = limits
        self.risk_state = risk_state
        # One coordinator may be called by multiple tasks.  Serializing the
        # precheck/reservation/update transaction prevents both from passing
        # against the same stale RiskState.  The paper engine is sequential
        # today, but this makes the invariant explicit rather than accidental.
        self._lifecycle_lock = asyncio.Lock()

    async def execute(self, op: Opportunity) -> ExecutionReport:
        async with self._lifecycle_lock:
            return await self._execute_locked(op)

    async def _execute_locked(self, op: Opportunity) -> ExecutionReport:
        report = ExecutionReport(opportunity_id=op.opportunity_id,
                                 success=False)

        # PRECHECK: repeat execution-critical facts here.  A TRADE label from a
        # scanner is not authorization by itself.
        if not op.is_executable:
            report.notes.append(
                f"precheck failed: opportunity is not executable; status="
                f"{op.status.value}, net_profit={op.net_profit}, "
                f"capital_required={op.capital_required}, "
                f"reasons={op.block_reasons}")
            return report
        if self.mode != op.mode or self.mode != self.engine.mode:
            report.notes.append("precheck failed: mode mismatch — refusing")
            return report
        if len(op.legs) != 2:
            report.notes.append(
                f"precheck failed: coordinator requires exactly two legs, "
                f"got {len(op.legs)}")
            return report
        if kill_switch.is_engaged():
            report.notes.append("precheck failed: kill switch engaged")
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
        capital = op.capital_required
        assert capital is not None  # guarded by is_executable
        capital_by_venue = self._capital_by_venue(op)
        trade_violations = check_trade_against_limits(
            self.limits, self.risk_state, capital,
            [request.venue for request in requests], op.normalized_event,
            capital_by_venue=capital_by_venue,
            new_positions=len(requests), new_orders=len(requests))

        prospective = UnhedgedExposure(
            opportunity_id=f"prospective:{op.opportunity_id}",
            venue=requests[0].venue, contract_id=requests[0].contract_id,
            contracts=requests[0].quantity,
            dollars=requests[0].quantity * (
                requests[0].limit_price or Decimal("1")),
            since=utcnow())
        exposure_violations = await self.exposure.violations(
            self.limits, additional=prospective)
        report.risk_violations.extend(trade_violations + exposure_violations)
        if report.risk_violations:
            report.notes.append(
                f"precheck failed: risk limits: {report.risk_violations}")
            return report

        # RESERVE the complete lifecycle, not just the first synchronous
        # submit. Recovery runs under this reservation and may reduce risk even
        # if a newly-engaged kill switch blocks all unrelated new trades.
        self.risk_state.open_orders += len(requests)
        try:
            return await self._execute_reserved(
                op, requests, capital_by_venue, report)
        finally:
            self.risk_state.open_orders = max(
                0, self.risk_state.open_orders - len(requests))

    async def _execute_reserved(
        self,
        op: Opportunity,
        requests: list[OrderRequest],
        capital_by_venue: dict[str, Decimal],
        report: ExecutionReport,
    ) -> ExecutionReport:
        # LEG A
        t0 = time.monotonic()
        leg_a = LegOutcome(request=requests[0])
        report.legs.append(leg_a)
        try:
            leg_a.result = await self.engine.submit_order(requests[0])
        except Exception as exc:
            leg_a.error = str(exc)
            reconciled = await self._reconcile_order(requests[0])
            if reconciled is not None:
                leg_a.result = reconciled
            else:
                # D35: NEVER assume a leg-A exception was pre-transmission.
                await self._record_ambiguous_leg_a(
                    op, requests[0], capital_by_venue, report,
                    f"leg A raised ({exc}) and outcome is UNKNOWN — "
                    "exposure recorded and kill switch engaged")
                return report

        assert leg_a.result is not None
        if leg_a.result.status in (OrderStatus.SUBMITTED, OrderStatus.PENDING,
                                   OrderStatus.UNKNOWN):
            # D36: a resting (SUBMITTED/PENDING) or UNKNOWN leg A may still
            # fill at the venue. Attempt cancel + status reconciliation first;
            # only a confirmed terminal CANCELED/REJECTED with zero fill is
            # safe to abort without tracking exposure and engaging kill switch.
            if leg_a.result.status in (OrderStatus.SUBMITTED,
                                       OrderStatus.PENDING):
                await self._try_cancel_order(requests[0], leg_a.result)
            reconciled = await self._reconcile_order(
                requests[0], leg_a.result.venue_order_id)
            if reconciled is not None:
                leg_a.result = reconciled
            if (leg_a.result.status in (OrderStatus.CANCELED,
                                        OrderStatus.REJECTED)
                    and leg_a.result.filled_quantity <= 0):
                report.notes.append(
                    f"leg A resting order confirmed "
                    f"{leg_a.result.status.value} with zero fill — aborting "
                    "before leg B")
                return report
            if leg_a.result.status not in (OrderStatus.FILLED,
                                           OrderStatus.PARTIALLY_FILLED):
                await self._record_ambiguous_leg_a(
                    op, requests[0], capital_by_venue, report,
                    f"leg A unresolved ({leg_a.result.status.value}: "
                    f"{leg_a.result.message}) — exposure recorded and kill "
                    "switch engaged")
                return report

        if leg_a.result.status not in (OrderStatus.FILLED,
                                       OrderStatus.PARTIALLY_FILLED):
            report.notes.append(
                f"leg A not filled ({leg_a.result.status.value}: "
                f"{leg_a.result.message}) — aborting before leg B")
            return report

        filled_a = leg_a.result.filled_quantity
        if filled_a <= 0:
            await self._record_ambiguous_leg_a(
                op, requests[0], capital_by_venue, report,
                f"leg A reported {leg_a.result.status.value} with non-positive "
                f"filled_quantity={filled_a} — exposure recorded and kill "
                "switch engaged")
            return report
        # Match leg B to what leg A actually filled.
        requests[1].quantity = min(requests[1].quantity, filled_a)
        entry_price = (leg_a.result.average_fill_price
                       or requests[0].limit_price or Decimal("1"))
        since = utcnow()
        await self.exposure.record_unhedged(UnhedgedExposure(
            opportunity_id=op.opportunity_id,
            venue=requests[0].venue, contract_id=requests[0].contract_id,
            contracts=filled_a, dollars=filled_a * entry_price,
            since=since))
        await self._exposure_checkpoint(report, "after leg A")

        # LEG B
        leg_b = LegOutcome(request=requests[1])
        report.legs.append(leg_b)
        try:
            leg_b.result = await self.engine.submit_order(requests[1])
        except Exception as exc:
            leg_b.error = str(exc)
            reconciled_b = await self._reconcile_order(requests[1])
            if reconciled_b is not None:
                leg_b.result = reconciled_b
        report.time_between_legs_ms = (time.monotonic() - t0) * 1000.0

        if (leg_b.result is not None
                and leg_b.result.status in (OrderStatus.SUBMITTED,
                                            OrderStatus.PENDING,
                                            OrderStatus.UNKNOWN)):
            if leg_b.result.status in (OrderStatus.SUBMITTED,
                                       OrderStatus.PENDING):
                await self._try_cancel_order(requests[1], leg_b.result)
            reconciled_b = await self._reconcile_order(
                requests[1], leg_b.result.venue_order_id)
            if reconciled_b is not None:
                leg_b.result = reconciled_b
            if leg_b.result.status in (OrderStatus.SUBMITTED,
                                       OrderStatus.PENDING,
                                       OrderStatus.UNKNOWN):
                report.notes.append(
                    f"leg B unresolved ({leg_b.result.status.value}: "
                    f"{leg_b.result.message}) — halting before recovery to "
                    "prevent duplicate exposure")
                self._record_deployment(op, capital_by_venue, len(op.legs))
                self._engage_kill_switch(
                    report, f"ambiguous leg B outcome for {op.opportunity_id}")
                await self._exposure_checkpoint(report, "after ambiguous leg B")
                return report

        filled_b = (leg_b.result.filled_quantity
                    if leg_b.result else Decimal("0"))
        remaining = max(Decimal("0"), filled_a - filled_b)
        if remaining <= 0 and filled_a > 0:
            await self.exposure.clear_unhedged(op.opportunity_id)
            report.success = True
            report.notes.append("both legs filled")
            self._record_deployment(op, capital_by_venue, len(op.legs))
            return report

        # The tracker now represents the actual unmatched quantity, not all of
        # leg A.  Preserve ``since`` so timeout accounting starts at the first
        # naked fill and cannot be reset by a partial hedge.
        await self.exposure.record_unhedged(UnhedgedExposure(
            opportunity_id=op.opportunity_id,
            venue=requests[0].venue, contract_id=requests[0].contract_id,
            contracts=remaining, dollars=remaining * entry_price,
            since=since))
        await self._exposure_checkpoint(report, "after leg B")

        # PARTIAL / FAILED LEG B → recovery procedure (§32).  Exposure checks
        # run before every recovery order via the callback.
        try:
            recovery = await self.partial_fills.handle(
                op=op, engine=self.engine,
                filled_leg=requests[0], filled_quantity=filled_a,
                unfilled_leg=requests[1], unfilled_quantity=remaining,
                filled_average_price=entry_price,
                risk_checkpoint=lambda stage: self._exposure_checkpoint(
                    report, stage))
        except Exception as exc:
            report.notes.append(f"recovery procedure failed: {exc}")
            self._engage_kill_switch(
                report, f"partial-fill recovery raised for {op.opportunity_id}")
            self._record_deployment(
                op, capital_by_venue, 1 + int(filled_b > 0))
            return report

        report.recovery = recovery
        report.notes.extend(recovery.notes)
        # Recovery cost is the only realized paper loss currently emitted by
        # the execution lifecycle.  Post it conservatively so max_daily_loss
        # blocks a later trade in this process.
        self.risk_state.daily_pnl -= recovery.emergency_cost
        report.success = recovery.hedged
        if recovery.hedged:
            await self.exposure.clear_unhedged(op.opportunity_id)
            if recovery.open_hedged_quantity > 0:
                self._record_deployment(op, capital_by_venue, len(op.legs))
            report.notes.append(f"recovery resolved exposure: "
                                f"{recovery.resolution}")
        else:
            await self.exposure.record_unhedged(UnhedgedExposure(
                opportunity_id=op.opportunity_id,
                venue=requests[0].venue,
                contract_id=requests[0].contract_id,
                contracts=recovery.remaining_unhedged,
                dollars=recovery.remaining_unhedged * entry_price,
                since=since))
            # Capital remains deployed and the next trade must not rely on a
            # success-only accounting update.  The persistent kill switch is
            # the primary block; this conservative state survives manual
            # clearing within the same process.
            unresolved_positions = 1 + int(
                recovery.open_hedged_quantity > 0)
            self._record_deployment(
                op, capital_by_venue, unresolved_positions)
            self._engage_kill_switch(
                report, f"unresolved paper exposure for {op.opportunity_id}")
        await self._exposure_checkpoint(report, "after recovery")
        return report

    async def _reconcile_order(
        self,
        request: OrderRequest,
        venue_order_id: Optional[str] = None,
    ) -> Optional[OrderResult]:
        for lookup_id in (venue_order_id, request.client_order_id):
            if not lookup_id:
                continue
            try:
                status = await self.engine.get_order_status(
                    request.venue, lookup_id)
            except Exception as exc:
                log.warning("order status lookup failed for %s/%s: %s",
                            request.venue, lookup_id, exc)
                return None
            if status is not None:
                return status
        return None

    async def _try_cancel_order(self, request: OrderRequest,
                                result: OrderResult) -> bool:
        cancel_id = result.venue_order_id or request.client_order_id
        if not cancel_id:
            return False
        try:
            return await self.engine.cancel_order(request.venue, cancel_id)
        except Exception as exc:
            log.warning("cancel_order failed for %s/%s: %s",
                        request.venue, cancel_id, exc)
            return False

    async def _record_ambiguous_leg_a(
        self,
        op: Opportunity,
        request: OrderRequest,
        capital_by_venue: dict[str, Decimal],
        report: ExecutionReport,
        note: str,
    ) -> None:
        entry_price = request.limit_price or Decimal("1")
        await self.exposure.record_unhedged(UnhedgedExposure(
            opportunity_id=op.opportunity_id,
            venue=request.venue,
            contract_id=request.contract_id,
            contracts=request.quantity,
            dollars=request.quantity * entry_price,
            since=utcnow()))
        self._record_deployment(op, capital_by_venue, 1)
        report.notes.append(note)
        self._engage_kill_switch(
            report, f"ambiguous leg A outcome for {op.opportunity_id}")
        await self._exposure_checkpoint(report, "after ambiguous leg A")

    def _capital_by_venue(self, op: Opportunity) -> dict[str, Decimal]:
        by_venue: dict[str, Decimal] = {}
        for leg in op.legs:
            walked = (leg.walk.total_cost if leg.walk is not None
                      else leg.limit_price * leg.quantity)
            by_venue[leg.venue] = (
                by_venue.get(leg.venue, Decimal("0")) + walked + leg.fees)
        return by_venue

    def _record_deployment(self, op: Opportunity,
                           capital_by_venue: dict[str, Decimal],
                           positions: int) -> None:
        if positions <= 0:
            return
        capital = op.capital_required
        assert capital is not None
        # Use the complete opportunity capital even after a partial fill.  It
        # is conservative and prevents a paper fill model from opening more
        # risk merely because less than requested happened to fill.
        self.risk_state.deployed_total += capital
        for venue, amount in capital_by_venue.items():
            self.risk_state.deployed_by_venue[venue] = (
                self.risk_state.deployed_by_venue.get(venue, Decimal("0"))
                + amount)
        self.risk_state.deployed_by_event[op.normalized_event] = (
            self.risk_state.deployed_by_event.get(
                op.normalized_event, Decimal("0")) + capital)
        self.risk_state.open_positions += positions

    async def _exposure_checkpoint(self, report: ExecutionReport,
                                   stage: str) -> list[str]:
        violations = await self.exposure.violations(self.limits)
        if violations:
            new = [v for v in violations if v not in report.risk_violations]
            report.risk_violations.extend(new)
            report.notes.append(
                f"unhedged exposure breach {stage}: {violations}")
            # Recovery remains allowed after this point; the switch blocks
            # unrelated new trades, not risk-reducing hedge/unwind attempts.
            self._engage_kill_switch(
                report, f"unhedged exposure limit breach {stage}")
        return violations

    @staticmethod
    def _engage_kill_switch(report: ExecutionReport, reason: str) -> None:
        if kill_switch.is_engaged():
            return
        try:
            kill_switch.engage(reason)
            report.notes.append(f"kill switch engaged: {reason}")
        except Exception as exc:  # file-system failure must never look safe
            message = (f"CRITICAL: kill switch activation failed ({exc}); "
                       "manual process stop required")
            report.notes.append(message)
            log.critical(message, exc_info=True)
