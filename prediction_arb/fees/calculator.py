"""Versioned, fail-closed fee engine (§24).

A documented formula is not automatically executable. Series overrides,
listing cohorts, event state, contract type, access route, account precision,
and cumulative fill/order state can all change the amount charged. Schedules
whose required context is not represented exactly are marked INCOMPLETE and
raise ``FeeDataError`` rather than returning an estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_CEILING
from typing import Any, Optional

from ..core.clock import utcnow
from ..core.types import FeeDataError


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
    metadata_verified_at: Optional[date] = None


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
    effective_date: date
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


def ceil_cents(value: Decimal) -> Decimal:
    """Round a non-negative dollar amount up to the next whole cent."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


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
            schedule = FeeSchedule(
                venue=entry["venue"],
                version=str(entry["version"]),
                effective_date=date.fromisoformat(str(entry["effective_date"])),
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

    def trade_fee(self, venue: str, price: Decimal, quantity: Decimal,
                  is_taker: bool = True,
                  strict_freshness: bool = False,
                  context: Optional[FeeContext] = None) -> Decimal:
        """Calculate an exact configured trade fee or fail closed.

        This method intentionally has no estimate mode. Research-only formula
        examples belong in tests/docs, not an execution-facing API.
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
        total = Decimal("0")
        for rule in applicable:
            if rule.multiplier is not None:
                total += ceil_cents(
                    rule.multiplier * quantity * price
                    * (Decimal("1") - price)
                )
            if rule.fixed_amount is not None:
                total += (rule.fixed_amount * quantity).quantize(
                    Decimal("0.01")
                )
        return total

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
