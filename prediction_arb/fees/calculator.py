"""Versioned, fail-closed fee engine (§24).

A documented formula is not automatically executable. Series overrides,
listing cohorts, event state, contract type, access route, account precision,
and cumulative fill/order state can all change the amount charged. Schedules
whose required context is not represented exactly are marked INCOMPLETE and
raise ``FeeDataError`` rather than returning an estimate.

Fees are per FILL, not per order. A venue that charges ``Θ × C × p × (1-p)``
charges it on every fill of a walking order, rounds each fill separately, and
may cap the order total. Computing one fee from a walk's weighted-average
price is therefore not what the venue bills. The error direction depends on
the schedule's curvature: the quadratic term is concave in ``p``, so by
Jensen ``Q·f(p̄) >= Σ qᵢ·f(pᵢ)`` — a single average-price fee OVERSTATES a
taker charge (conservative, but wrong, and it hides the documented order cap),
while for a negative multiplier such as a maker rebate the same shortcut
UNDERSTATES the credit, which is the flattering direction. Either way, use
:meth:`FeeCalculator.trade_fee_fills` with the real walked levels; that is the
defect recorded as D13 in ``docs/ROADMAP.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_EVEN
from typing import Any, Optional, Sequence

from ..core.clock import utcnow
from ..core.types import FeeDataError

#: Rounding modes a documented schedule may specify. ``ceiling`` is the
#: historical default of this engine (round a non-negative amount up to the
#: next cent). ``half_even`` is banker's rounding — the documented rule for
#: Polymarket US (docs.polymarket.us/fees, evidence row P8/P11).
ROUNDING_CEILING = "ceiling"
ROUNDING_HALF_EVEN = "half_even"

#: Order-total fee caps. ``none`` = the venue documents no cap.
#: ``cumulative_exact_rounded`` = the venue caps the commission collected
#: across an aggressive order's fills at the rounded *cumulative exact* fee,
#: and the adjustment may only ever reduce a fill's charge (P8).
ORDER_FEE_CAP_NONE = "none"
ORDER_FEE_CAP_CUMULATIVE_EXACT_ROUNDED = "cumulative_exact_rounded"


@dataclass(frozen=True)
class FeeContext:
    """Contract/order dimensions that may select or statefully alter fees.

    The current adapters do not yet supply all of these fields. Merely passing
    a contract ID is not a substitute for verified fee metadata.
    """
    series: Optional[str] = None
    listing_cohort: Optional[str] = None
    event_status: Optional[str] = None
    contract_type: Optional[str] = None
    access_route: Optional[str] = None
    fee_type: Optional[str] = None
    fee_multiplier: Optional[Decimal] = None
    account_balance_precision: Optional[Decimal] = None
    cumulative_fill_quantity: Optional[Decimal] = None
    cumulative_fee_charged: Optional[Decimal] = None
    prior_month_taker_volume: Optional[Decimal] = None
    metadata_verified_at: Optional[date] = None


@dataclass(frozen=True)
class FillSlice:
    """One executed slice of a walking order: `quantity` at `price`.

    This is the unit a venue actually charges on. Pass the levels a
    ``WalkResult`` consumed, never a synthesized average.
    """
    price: Decimal
    quantity: Decimal


@dataclass(frozen=True)
class VolumeRebateTier:
    """Documented account-level volume rebate tier (P8).

    Recorded for evidence and for offline analysis only: this calculator
    never applies a rebate, because the documented taker rebate is a
    *weekly payout on prior-calendar-month notional volume*, not a
    per-trade discount. Applying it per order would understate cost.
    """
    threshold: Decimal
    rebate_fraction: Decimal


@dataclass(frozen=True)
class FeeBreakdown:
    """Per-fill fee detail for one role on one order."""
    venue: str
    role: str                              # taker / maker
    exact_total: Decimal                   # unrounded Σ over fills
    charged_per_fill: tuple[Decimal, ...]  # what the venue bills per fill
    rounded_sum: Decimal                   # Σ of independently rounded fills
    cap: Optional[Decimal]                 # order-total cap, if documented
    total: Decimal                         # what is actually charged
    adjustment: Decimal                    # rounded_sum - total (>= 0)

    @property
    def was_capped(self) -> bool:
        return self.adjustment > 0


@dataclass(frozen=True)
class FeeRule:
    fee_type: str
    applies_to: str                       # taker / maker / settlement
    multiplier: Optional[Decimal] = None  # quadratic coefficient
    fixed_amount: Optional[Decimal] = None  # per-contract fixed amount


@dataclass(frozen=True)
class FeeSchedule:
    venue: str
    version: str
    effective_date: Optional[date]
    expiration_date: Optional[date]
    verified_at: date
    source: str
    currency: str
    rules: tuple[FeeRule, ...]
    verification_status: str = "UNKNOWN"
    documentation_tested: bool = False
    live_test_status: str = "UNAVAILABLE"
    trade_calculation_status: str = "INCOMPLETE"
    settlement_calculation_status: str = "INCOMPLETE"
    fee_context: dict[str, str] = field(default_factory=dict)
    required_context: tuple[str, ...] = ()
    blocking_reason: str = ""
    rounding: str = ROUNDING_CEILING
    order_fee_cap: str = ORDER_FEE_CAP_NONE
    volume_rebate_tiers: tuple[VolumeRebateTier, ...] = ()
    rebate_policy: str = ""


def ceil_cents(value: Decimal) -> Decimal:
    """Round a non-negative dollar amount up to the next whole cent."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def round_cents(value: Decimal, mode: str = ROUNDING_CEILING) -> Decimal:
    """Round a (possibly negative) dollar amount to cents per venue rule.

    ``half_even`` is banker's rounding, symmetric about zero:
    ``0.025 -> 0.02`` and ``0.035 -> 0.04`` — the two examples the
    Polymarket US fee FAQ gives verbatim (evidence row P8/P11).
    """
    if mode == ROUNDING_HALF_EVEN:
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    if mode == ROUNDING_CEILING:
        return ceil_cents(value)
    raise FeeDataError(
        f"FEE_DATA_INCOMPLETE: unsupported fee rounding mode {mode!r}. "
        f"Refusing to guess a rounding rule.")


class FeeCalculator:
    def __init__(self, schedules: dict[str, FeeSchedule],
                 max_schedule_age_days: int = 30):
        self.schedules = schedules
        self.max_schedule_age_days = max_schedule_age_days

    @classmethod
    def from_config(cls, fees_config: dict[str, Any],
                    max_schedule_age_days: int = 30) -> "FeeCalculator":
        schedules: dict[str, FeeSchedule] = {}
        for entry in fees_config.get("schedules", []):
            rules = tuple(
                FeeRule(
                    fee_type=rule["fee_type"],
                    applies_to=rule.get("applies_to", "taker"),
                    multiplier=(Decimal(str(rule["multiplier"]))
                                if "multiplier" in rule else None),
                    fixed_amount=(Decimal(str(rule["fixed_amount"]))
                                  if "fixed_amount" in rule else None),
                )
                for rule in entry.get("fees", [])
            )
            calculation_status = str(
                entry.get("calculation_status", "INCOMPLETE")
            ).upper()
            rounding = str(entry.get("rounding", ROUNDING_CEILING)).lower()
            if rounding not in (ROUNDING_CEILING, ROUNDING_HALF_EVEN):
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: venue {entry.get('venue')!r} "
                    f"declares unknown fee rounding {rounding!r}. Add it to "
                    f"round_cents() deliberately or remove it from config.")
            order_fee_cap = str(
                entry.get("order_fee_cap", ORDER_FEE_CAP_NONE)).lower()
            if order_fee_cap not in (
                    ORDER_FEE_CAP_NONE,
                    ORDER_FEE_CAP_CUMULATIVE_EXACT_ROUNDED):
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: venue {entry.get('venue')!r} "
                    f"declares unknown order fee cap {order_fee_cap!r}.")
            schedule = FeeSchedule(
                venue=entry["venue"],
                version=str(entry["version"]),
                effective_date=(
                    date.fromisoformat(str(entry["effective_date"]))
                    if entry.get("effective_date") else None
                ),
                expiration_date=(
                    date.fromisoformat(str(entry["expiration_date"]))
                    if entry.get("expiration_date") else None
                ),
                verified_at=date.fromisoformat(str(entry["verified_at"])),
                source=entry.get("source", ""),
                currency=entry.get("currency", "USD"),
                rules=rules,
                verification_status=str(
                    entry.get("verification_status", "UNKNOWN")
                ).upper(),
                documentation_tested=bool(
                    entry.get("documentation_tested", False)
                ),
                live_test_status=str(
                    entry.get("live_test_status", "UNAVAILABLE")
                ).upper(),
                trade_calculation_status=str(
                    entry.get("trade_calculation_status", calculation_status)
                ).upper(),
                settlement_calculation_status=str(
                    entry.get("settlement_calculation_status", calculation_status)
                ).upper(),
                fee_context={
                    str(key): str(value)
                    for key, value in (entry.get("fee_context") or {}).items()
                },
                required_context=tuple(
                    str(value) for value in entry.get("required_context", [])
                ),
                blocking_reason=str(entry.get("blocking_reason", "")),
                rounding=rounding,
                order_fee_cap=order_fee_cap,
                volume_rebate_tiers=tuple(
                    VolumeRebateTier(
                        threshold=Decimal(str(tier["threshold"])),
                        rebate_fraction=Decimal(str(tier["rebate"])),
                    )
                    for tier in entry.get("volume_rebate_tiers", [])
                ),
                rebate_policy=str(entry.get("rebate_policy", "")),
            )
            schedules[schedule.venue] = schedule
        return cls(schedules, max_schedule_age_days)

    def schedule_for(self, venue: str, strict_freshness: bool = False
                     ) -> FeeSchedule:
        schedule = self.schedules.get(venue)
        if schedule is None:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: no fee schedule on file for venue "
                f"{venue!r}. Refusing to compute fees (never assume zero).")
        today = utcnow().date()
        if schedule.effective_date is None:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} fee schedule has no verified "
                "effective date. Refusing to compute fees.")
        if today < schedule.effective_date:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} fee schedule is not effective "
                f"until {schedule.effective_date}.")
        if schedule.expiration_date and today > schedule.expiration_date:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} fee schedule expired on "
                f"{schedule.expiration_date}.")
        if strict_freshness:
            age = (today - schedule.verified_at).days
            if age > self.max_schedule_age_days:
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: {venue} fee schedule verified "
                    f"{age} days ago (> {self.max_schedule_age_days}). "
                    f"Re-verify before live execution.")
        return schedule

    @staticmethod
    def _require_calculable(schedule: FeeSchedule, component: str,
                            context: Optional[FeeContext]) -> None:
        status = (
            schedule.trade_calculation_status
            if component == "trade"
            else schedule.settlement_calculation_status
        )
        if (schedule.verification_status != "VERIFIED"
                or not schedule.documentation_tested
                or not schedule.source.strip()):
            status = "INCOMPLETE"
        if status == "INCOMPLETE":
            reason = schedule.blocking_reason or "exact schedule unavailable"
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {schedule.venue} {component} fee is "
                f"execution-blocking ({reason}).")
        if status == "CONTEXT_REQUIRED":
            if context is None:
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: {schedule.venue} {component} fee "
                    "requires explicit contract/order context.")
            missing = [
                name for name in schedule.required_context
                if not hasattr(context, name) or getattr(context, name) is None
            ]
            if missing:
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: {schedule.venue} {component} fee "
                    f"context missing {', '.join(missing)}.")
        elif status != "COMPLETE":
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: unsupported calculation status "
                f"{status!r} for {schedule.venue}.")

    def trade_fee_fills(self, venue: str, fills: Sequence[FillSlice],
                        is_taker: bool = True,
                        strict_freshness: bool = False,
                        context: Optional[FeeContext] = None
                        ) -> FeeBreakdown:
        """Exact per-fill trade fee for one walking order, or fail closed.

        ``fills`` are the levels the order actually consumed (pass
        ``WalkResult.levels_consumed``). Charging one fee at the walk's
        weighted-average price is not what the venue bills: the venue
        rounds per fill and may cap the order total, and for the concave
        quadratic taker schedules in use an average-price fee overstates
        the charge (see the module docstring for the direction analysis).

        Rounding and order-total caps follow the venue's own documented
        rules (``schedule.rounding`` / ``schedule.order_fee_cap``):

        * ``half_even`` + ``cumulative_exact_rounded`` (Polymarket US, P8):
          each fill is billed its banker's-rounded fee, adjusted so the
          running total never exceeds the banker's rounding of the
          cumulative *exact* fee. The adjustment can only reduce a fill's
          charge, never increase it. Maker rebates are computed per fill,
          independently, and are never capped (P8).

        This method has no estimate mode. Research-only formula examples
        belong in tests/docs, not an execution-facing API.
        """
        schedule = self.schedule_for(venue, strict_freshness)
        self._require_calculable(schedule, "trade", context)
        role = "taker" if is_taker else "maker"
        applicable = [
            rule for rule in schedule.rules if rule.applies_to == role
        ]
        if not applicable:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} has no {role} fee rule.")

        slices = tuple(fills)
        if not slices:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} {role} fee was called with no "
                f"fill slices. Pass the levels the order actually consumed "
                f"(WalkResult.levels_consumed) — never an empty walk.")
        one = Decimal("1")
        exact_total = Decimal("0")
        uncapped: list[Decimal] = []
        charged: list[Decimal] = []
        running_exact = Decimal("0")
        running_charged = Decimal("0")
        cap_applies = (
            role == "taker"
            and schedule.order_fee_cap
            == ORDER_FEE_CAP_CUMULATIVE_EXACT_ROUNDED
        )
        for sl in slices:
            if sl.quantity <= 0:
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: {venue} fill slice has "
                    f"non-positive quantity {sl.quantity}; refusing to bill "
                    f"a fee for it.")
            if not (Decimal("0") < sl.price < one):
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: {venue} fill price {sl.price} is "
                    f"outside (0.00, 1.00) for a $1 event contract; refusing "
                    f"to compute a fee from it.")
            exact_fill = Decimal("0")
            billed_fill = Decimal("0")
            for rule in applicable:
                if rule.multiplier is not None:
                    raw = (rule.multiplier * sl.quantity * sl.price
                           * (one - sl.price))
                    exact_fill += raw
                    billed_fill += round_cents(raw, schedule.rounding)
                if rule.fixed_amount is not None:
                    raw = rule.fixed_amount * sl.quantity
                    exact_fill += raw
                    billed_fill += raw.quantize(Decimal("0.01"))
            exact_total += exact_fill
            running_exact += exact_fill
            uncapped.append(billed_fill)
            if cap_applies:
                # Documented cap (P8): the commission collected across the
                # order's fills never exceeds the banker's rounding of the
                # cumulative exact fee, and a fill's charge is only ever
                # reduced by the adjustment.
                running_cap = round_cents(running_exact, schedule.rounding)
                billed_fill = min(billed_fill, running_cap - running_charged)
                if billed_fill < 0:
                    billed_fill = Decimal("0")
            charged.append(billed_fill)
            running_charged += billed_fill

        rounded_sum = sum(uncapped, Decimal("0"))
        cap_total = (
            round_cents(exact_total, schedule.rounding) if cap_applies
            else None
        )
        total = running_charged
        adjustment = rounded_sum - total
        if adjustment < 0:
            # Cannot happen with the rules above; if it ever does, the model
            # is wrong and the safe answer is the uncapped (higher) number.
            adjustment = Decimal("0")
            total = rounded_sum
        return FeeBreakdown(
            venue=venue, role=role, exact_total=exact_total,
            charged_per_fill=tuple(charged), rounded_sum=rounded_sum,
            cap=cap_total, total=total, adjustment=adjustment)

    def trade_fee(self, venue: str, price: Decimal, quantity: Decimal,
                  is_taker: bool = True,
                  strict_freshness: bool = False,
                  context: Optional[FeeContext] = None) -> Decimal:
        """Single-fill trade fee. Prefer :meth:`trade_fee_fills`.

        Convenience wrapper for callers that genuinely have one fill. Using
        it with a walk's weighted-average price instead of the walked
        levels misprices the order — it overstates a concave taker charge
        and understates a maker credit (defect D13). Pass the real fills.
        """
        return self.trade_fee_fills(
            venue, (FillSlice(price=price, quantity=quantity),),
            is_taker=is_taker, strict_freshness=strict_freshness,
            context=context).total

    def settlement_fee(self, venue: str, quantity: Decimal,
                       strict_freshness: bool = False,
                       context: Optional[FeeContext] = None) -> Decimal:
        schedule = self.schedule_for(venue, strict_freshness)
        self._require_calculable(schedule, "settlement", context)
        rules = [
            rule for rule in schedule.rules
            if rule.applies_to == "settlement"
        ]
        if not rules:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} has no settlement fee rule.")
        total = Decimal("0")
        for rule in rules:
            if rule.fixed_amount is not None:
                total += (rule.fixed_amount * quantity).quantize(
                    Decimal("0.01")
                )
        return total
