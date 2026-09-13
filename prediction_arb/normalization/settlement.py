"""Settlement timing analysis (§28).

Tracks per-contract expected settlement delay and computes the cost of
capital locked up until the LATEST leg settles.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from ..core.clock import ensure_utc, utcnow
from ..core.types import NormalizedContract

# Expected post-resolution settlement delay per venue (days).
# Sources: Kalshi settles shortly after expiration (settlement_timer_seconds
# in market payload — typically minutes-to-hours); ForecastEx credits on
# resolution. UNKNOWN venues get None → treated as unacceptable.
_EXPECTED_SETTLEMENT_DELAY_DAYS: dict[str, Decimal] = {
    "kalshi": Decimal("0"),
    "forecast_ex": Decimal("0"),
}


def expected_settlement_delay_days(venue: str) -> Optional[Decimal]:
    return _EXPECTED_SETTLEMENT_DELAY_DAYS.get(venue)


def capital_lockup_days(contracts: list[NormalizedContract],
                        now: Optional[datetime] = None) -> Optional[Decimal]:
    """Days until the LAST leg's capital is returned. None if unknown."""
    now = now or utcnow()
    worst: Optional[Decimal] = None
    for c in contracts:
        if c.settlement_time is None:
            return None
        delay = expected_settlement_delay_days(c.venue)
        if delay is None:
            return None
        days = Decimal(str(max(
            0.0, (ensure_utc(c.settlement_time) - now).total_seconds() / 86400.0)))
        total = days + delay
        worst = total if worst is None or total > worst else worst
    return worst


def settlement_delay_cost(capital: Decimal, lockup_days: Optional[Decimal],
                          annual_rate: Decimal) -> Optional[Decimal]:
    """Simple (non-compounded) cost of capital for the lockup period.
    Returns None when lockup is unknown — callers must treat that as a
    blocking condition, not as zero."""
    if lockup_days is None:
        return None
    return (capital * annual_rate * lockup_days / Decimal("365")
            ).quantize(Decimal("0.0001"))


def settlement_delay_spread_days(contracts: list[NormalizedContract]
                                 ) -> Optional[Decimal]:
    """Max difference in settlement timing between legs (T+0 vs T+2 risk)."""
    times = []
    for c in contracts:
        if c.settlement_time is None:
            return None
        d = expected_settlement_delay_days(c.venue)
        if d is None:
            return None
        times.append(ensure_utc(c.settlement_time) + timedelta(days=float(d)))
    if not times:
        return None
    return Decimal(str((max(times) - min(times)).total_seconds() / 86400.0))
