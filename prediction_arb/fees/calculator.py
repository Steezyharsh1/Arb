"""Fee engine (§24). Fees are versioned + timestamped; missing fee data
raises FeeDataError (FEE_DATA_INCOMPLETE) — NEVER assumed zero."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from ..core.clock import utcnow
from ..core.types import FeeDataError


@dataclass(frozen=True)
class FeeRule:
    fee_type: str
    applies_to: str                      # taker / maker / settlement
    multiplier: Optional[Decimal] = None  # quadratic multiplier
    fixed_amount: Optional[Decimal] = None  # per-contract fixed


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


def ceil_cents(x: Decimal) -> Decimal:
    return (Decimal(math.ceil(x * 100)) / Decimal(100)).quantize(Decimal("0.01"))


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
                    fee_type=r["fee_type"],
                    applies_to=r.get("applies_to", "taker"),
                    multiplier=Decimal(str(r["multiplier"])) if "multiplier" in r else None,
                    fixed_amount=Decimal(str(r["fixed_amount"])) if "fixed_amount" in r else None,
                )
                for r in entry.get("fees", [])
            )
            sched = FeeSchedule(
                venue=entry["venue"],
                version=str(entry["version"]),
                effective_date=date.fromisoformat(str(entry["effective_date"])),
                expiration_date=(date.fromisoformat(str(entry["expiration_date"]))
                                 if entry.get("expiration_date") else None),
                verified_at=date.fromisoformat(str(entry["verified_at"])),
                source=entry.get("source", ""),
                currency=entry.get("currency", "USD"),
                rules=rules,
            )
            schedules[sched.venue] = sched
        return cls(schedules, max_schedule_age_days)

    def schedule_for(self, venue: str, strict_freshness: bool = False
                     ) -> FeeSchedule:
        sched = self.schedules.get(venue)
        if sched is None:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: no fee schedule on file for venue "
                f"{venue!r}. Refusing to compute fees (never assume zero).")
        if strict_freshness:
            age = (utcnow().date() - sched.verified_at).days
            if age > self.max_schedule_age_days:
                raise FeeDataError(
                    f"FEE_DATA_INCOMPLETE: {venue} fee schedule verified "
                    f"{age} days ago (> {self.max_schedule_age_days}). "
                    f"Re-verify before live execution.")
        return sched

    def trade_fee(self, venue: str, price: Decimal, quantity: Decimal,
                  is_taker: bool = True,
                  strict_freshness: bool = False) -> Decimal:
        """Fee for executing `quantity` contracts at average price `price`."""
        sched = self.schedule_for(venue, strict_freshness)
        role = "taker" if is_taker else "maker"
        applicable = [r for r in sched.rules if r.applies_to == role]
        if not applicable:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} has no {role} fee rule.")
        total = Decimal("0")
        for rule in applicable:
            if rule.multiplier is not None:
                # quadratic: ceil_cents(m * C * P * (1-P))
                total += ceil_cents(rule.multiplier * quantity * price
                                    * (Decimal("1") - price))
            if rule.fixed_amount is not None:
                total += (rule.fixed_amount * quantity).quantize(Decimal("0.01"))
        return total

    def settlement_fee(self, venue: str, quantity: Decimal,
                       strict_freshness: bool = False) -> Decimal:
        sched = self.schedule_for(venue, strict_freshness)
        rules = [r for r in sched.rules if r.applies_to == "settlement"]
        if not rules:
            raise FeeDataError(
                f"FEE_DATA_INCOMPLETE: {venue} has no settlement fee rule.")
        total = Decimal("0")
        for rule in rules:
            if rule.fixed_amount is not None:
                total += (rule.fixed_amount * quantity).quantize(Decimal("0.01"))
        return total
