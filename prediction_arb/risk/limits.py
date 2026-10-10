"""Hard risk limits (§70).

``config/risk_limits.yaml`` is the single authoritative source.  The helpers in
this module are pure checks; the execution coordinator calls them immediately
before reserving an order lifecycle.  A configured value appearing in a
readiness report is not, by itself, evidence that it is enforced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class RiskLimits:
    max_total_capital: Decimal
    max_capital_per_venue: Decimal
    max_capital_per_event: Decimal
    max_capital_per_trade: Decimal
    max_unhedged_exposure: Decimal
    max_daily_loss: Decimal
    max_open_positions: int
    max_concurrent_orders: int
    max_unhedged_seconds: int
    max_unhedged_contracts: int
    max_unhedged_dollars: Decimal
    max_emergency_loss: Decimal

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "RiskLimits":
        lim = cfg.get("limits", cfg)
        pf = cfg.get("partial_fill", cfg)

        def d(src: dict, key: str, default: str) -> Decimal:
            return Decimal(str(src.get(key, default)))

        return cls(
            max_total_capital=d(lim, "max_total_capital", "1000"),
            max_capital_per_venue=d(lim, "max_capital_per_venue", "500"),
            max_capital_per_event=d(lim, "max_capital_per_event", "200"),
            max_capital_per_trade=d(lim, "max_capital_per_trade", "100"),
            max_unhedged_exposure=d(lim, "max_unhedged_exposure", "25"),
            max_daily_loss=d(lim, "max_daily_loss", "50"),
            max_open_positions=int(lim.get("max_open_positions", 20)),
            max_concurrent_orders=int(lim.get("max_concurrent_orders", 4)),
            max_unhedged_seconds=int(pf.get("max_unhedged_seconds", 30)),
            max_unhedged_contracts=int(pf.get("max_unhedged_contracts", 100)),
            max_unhedged_dollars=d(pf, "max_unhedged_dollars", "25"),
            max_emergency_loss=d(pf, "max_emergency_loss", "10"),
        )


@dataclass
class RiskState:
    """Coordinator-owned paper risk state.

    Capital and positions are deliberately conservative: they are added only
    after an actual simulated fill lifecycle leaves a position open, and are
    not released because paper settlement/position-close accounting is not yet
    implemented.  ``open_orders`` is a reservation count held for the entire
    two-leg lifecycle, including recovery.
    """

    deployed_total: Decimal = Decimal("0")
    deployed_by_venue: dict[str, Decimal] = field(default_factory=dict)
    deployed_by_event: dict[str, Decimal] = field(default_factory=dict)
    daily_pnl: Decimal = Decimal("0")
    open_positions: int = 0
    open_orders: int = 0


def check_trade_against_limits(
    limits: RiskLimits,
    state: RiskState,
    capital: Decimal,
    venues: list[str],
    event: str,
    *,
    capital_by_venue: Optional[Mapping[str, Decimal]] = None,
    new_positions: Optional[int] = None,
    new_orders: Optional[int] = None,
) -> list[str]:
    """Return every pre-trade violation (empty means the reservation may run).

    ``capital_by_venue`` should be the walked purchase cost plus that leg's
    venue fees.  The equal split remains only as a backwards-compatible,
    conservative helper fallback for callers that do not have leg detail.
    The coordinator always supplies the exact mapping.
    """
    violations: list[str] = []
    if capital > limits.max_capital_per_trade:
        violations.append(
            f"max_capital_per_trade: trade capital {capital} > "
            f"{limits.max_capital_per_trade}")
    if state.deployed_total + capital > limits.max_total_capital:
        violations.append(
            f"max_total_capital: projected {state.deployed_total + capital} > "
            f"{limits.max_total_capital}")

    if capital_by_venue is None:
        unique_venues = list(dict.fromkeys(venues))
        share = (capital / len(unique_venues)
                 if unique_venues else Decimal("0"))
        venue_capital: Mapping[str, Decimal] = {
            venue: share for venue in unique_venues
        }
    else:
        venue_capital = capital_by_venue
    for venue, added in venue_capital.items():
        projected = state.deployed_by_venue.get(venue, Decimal("0")) + added
        if projected > limits.max_capital_per_venue:
            violations.append(
                f"max_capital_per_venue: projected {projected} > "
                f"{limits.max_capital_per_venue} on {venue}")

    projected_event = state.deployed_by_event.get(event, Decimal("0")) + capital
    if projected_event > limits.max_capital_per_event:
        violations.append(
            f"max_capital_per_event: projected {projected_event} > "
            f"{limits.max_capital_per_event}")
    if state.daily_pnl <= -limits.max_daily_loss:
        violations.append(
            f"max_daily_loss: daily P&L {state.daily_pnl} reached "
            f"-{limits.max_daily_loss}; no new trades today")

    positions = len(venues) if new_positions is None else new_positions
    projected_positions = state.open_positions + positions
    if projected_positions > limits.max_open_positions:
        violations.append(
            f"max_open_positions: projected {projected_positions} > "
            f"{limits.max_open_positions}")

    orders = len(venues) if new_orders is None else new_orders
    projected_orders = state.open_orders + orders
    if projected_orders > limits.max_concurrent_orders:
        violations.append(
            f"max_concurrent_orders: projected {projected_orders} > "
            f"{limits.max_concurrent_orders}")
    return violations
