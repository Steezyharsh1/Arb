"""Explicit research assumptions for causal historical replay (§102)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


MINIMUM_COLLECTION_REQUIREMENTS = [
    "Record local UTC receipt/availability time separately from venue quote time, "
    "with timestamp provenance and a synchronized collector clock.",
    "Identify the priced outcome, preserve both displayed ladders, and bind each "
    "snapshot to the contract terms/version already observed at receipt time.",
    "Capture both independent venues frequently enough for the configured book-age, "
    "pair-skew and latency windows; retain empty/halted books and feed gaps, too.",
    "For tick-accurate reconstruction, retain sequenced snapshots/deltas, feed/session "
    "IDs, disconnect/gap/recovery markers and coverage through both order arrivals.",
    "Supply effective-dated (intraday boundaries where applicable), complete "
    "fee/account/market context and evidenced "
    "capital-release timing; current production schedules remain INCOMPLETE.",
    "For realized P&L, collect actual resolution, payout-to-cash and position-close "
    "records. Displayed depth alone cannot establish real fills or queue position.",
]


@dataclass(frozen=True)
class ReplayPolicy:
    # No simulation is enabled by an unconfigured library caller.
    enabled: bool = False
    decision_latency_ms: int = 100
    leg_latency_ms: int = 100
    displayed_depth_fraction: Decimal = Decimal("0.5")
    max_book_age_ms: int = 1000
    max_pair_skew_ms: int = 250
    starting_cash: dict[str, Decimal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("historical simulation.enabled must be a boolean")
        for name in ("decision_latency_ms", "leg_latency_ms",
                     "max_book_age_ms", "max_pair_skew_ms"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"historical {name} must be a non-negative integer")
        fraction = self.displayed_depth_fraction
        if not fraction.is_finite() or not Decimal(0) <= fraction <= Decimal(1):
            raise ValueError("historical displayed_depth_fraction must be in [0, 1]")
        for venue, amount in self.starting_cash.items():
            if not amount.is_finite() or amount <= 0:
                raise ValueError(f"historical starting cash must be positive for {venue}")
        if self.enabled and not self.starting_cash:
            raise ValueError("historical simulation requires explicit venue starting_cash")

    @classmethod
    def from_config(cls, settings: dict, simulation: Optional[dict] = None
                    ) -> "ReplayPolicy":
        simulation = simulation or {}
        enabled = simulation.get("enabled", False)
        if not isinstance(enabled, bool):
            raise ValueError("historical simulation.enabled must be a boolean")
        return cls(
            enabled=enabled,
            decision_latency_ms=simulation.get("decision_latency_ms", 100),
            leg_latency_ms=simulation.get("leg_latency_ms", 100),
            displayed_depth_fraction=Decimal(str(
                simulation.get("displayed_depth_fraction", "0.5"))),
            max_book_age_ms=settings.get("arbitrage", {}).get(
                "maximum_book_age_ms", 1000),
            max_pair_skew_ms=simulation.get("max_pair_skew_ms", 250),
            starting_cash={v: Decimal(str(c)) for v, c in
                           simulation.get("starting_cash", {}).items()},
        )

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "decision_latency_ms": self.decision_latency_ms,
            "leg_latency_ms": self.leg_latency_ms,
            "displayed_depth_fraction": str(self.displayed_depth_fraction),
            "max_book_age_ms": self.max_book_age_ms,
            "max_pair_skew_ms": self.max_pair_skew_ms,
            "starting_cash": {v: str(c) for v, c in sorted(self.starting_cash.items())},
            "order_policy": "sequential marketable limits, immediate-or-cancel; no retry/unwind",
            "limit_policy": "worst consumed decision-time price; no price concession",
            "reentry_policy": "one attempt per unordered contract/version pair per run",
            "depth_policy": "floor displayed_depth_fraction of each level to whole contracts; "
                            "consume it once per snapshot/outcome across orders",
            "between_snapshots": "bounded last-observation carry-forward, ASSUMED unchanged; "
                                 "not tick-accurate exchange state",
            "settlement_policy": "conditional complementary contractual payout for paired "
                                 "quantity only; no cash credit or realized settlement invented",
            "unresolved_policy": "retain naked exposure and stop new entries; withhold aggregate P&L",
            "cost_input_policy": "supplied schedule/timing versions, not a historical registry; "
                                 "fee date bounds are day-granular, not intraday regime selection",
        }
