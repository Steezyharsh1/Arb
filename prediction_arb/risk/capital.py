"""Standalone capital-allocation helpers (§29, §30); not runtime-wired.

The functions operate only on caller-supplied balances and demand. No paper
or live engine supplies those inputs, and rebalancing advice is informational
only — the system never moves money. Keep deferred until an evidenced account
balance/position lifecycle and regression tests exist (D26)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class VenueCapital:
    venue: str
    available_cash: Decimal
    unsettled: Decimal = Decimal("0")

    @property
    def deployable(self) -> Decimal:
        return max(Decimal("0"), self.available_cash - self.unsettled)


def max_quantity_for_capital(per_contract_cost_by_venue: dict[str, Decimal],
                             capital: dict[str, VenueCapital]) -> Decimal:
    """Max whole contracts fundable given each leg's per-contract cost and
    that venue's deployable cash."""
    best: Decimal | None = None
    for venue, cost in per_contract_cost_by_venue.items():
        if cost <= 0:
            continue
        vc = capital.get(venue)
        if vc is None:
            return Decimal("0")   # unknown balance → cannot fund
        q = Decimal(int(vc.deployable / cost))
        best = q if best is None or q < best else best
    return best or Decimal("0")


@dataclass
class RebalanceAdvice:
    from_venue: str
    to_venue: str
    amount: Decimal
    reason: str


def rebalancing_advice(capital: dict[str, VenueCapital],
                       demand_by_venue: dict[str, Decimal],
                       min_transfer: Decimal = Decimal("50")
                       ) -> list[RebalanceAdvice]:
    """Advisory only (§30): suggest transfers when one venue has idle cash
    and another can't fund detected opportunities."""
    advice: list[RebalanceAdvice] = []
    shortfalls = {v: demand_by_venue.get(v, Decimal("0")) - c.deployable
                  for v, c in capital.items()}
    for needy, deficit in shortfalls.items():
        if deficit <= 0:
            continue
        for rich, surplus in shortfalls.items():
            if rich == needy or surplus >= 0:
                continue
            amount = min(deficit, -surplus)
            if amount >= min_transfer:
                advice.append(RebalanceAdvice(
                    from_venue=rich, to_venue=needy, amount=amount,
                    reason=f"{needy} short {deficit} for detected "
                           f"opportunities; {rich} idle. ADVISORY ONLY — "
                           f"no automatic transfers."))
    return advice
