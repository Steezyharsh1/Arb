"""Hard risk limits (§70). When any limit is reached: STOP NEW TRADES.
Existing positions remain monitored."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


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
    deployed_total: Decimal = Decimal("0")
    deployed_by_venue: dict[str, Decimal] = None  # type: ignore[assignment]
    deployed_by_event: dict[str, Decimal] = None  # type: ignore[assignment]
    daily_pnl: Decimal = Decimal("0")
    open_positions: int = 0
    open_orders: int = 0

    def __post_init__(self):
        if self.deployed_by_venue is None:
            self.deployed_by_venue = {}
        if self.deployed_by_event is None:
            self.deployed_by_event = {}


def check_trade_against_limits(limits: RiskLimits, state: RiskState,
                               capital: Decimal, venues: list[str],
                               event: str) -> list[str]:
    """Return list of violated limits (empty == pass)."""
    violations: list[str] = []
    if capital > limits.max_capital_per_trade:
        violations.append(
            f"trade capital {capital} > max_capital_per_trade "
            f"{limits.max_capital_per_trade}")
    if state.deployed_total + capital > limits.max_total_capital:
        violations.append("max_total_capital would be exceeded")
    for v in venues:
        if (state.deployed_by_venue.get(v, Decimal("0")) + capital / len(venues)
                > limits.max_capital_per_venue):
            violations.append(f"max_capital_per_venue would be exceeded on {v}")
    if (state.deployed_by_event.get(event, Decimal("0")) + capital
            > limits.max_capital_per_event):
        violations.append("max_capital_per_event would be exceeded")
    if state.daily_pnl <= -limits.max_daily_loss:
        violations.append("max_daily_loss reached — no new trades today")
    if state.open_positions >= limits.max_open_positions:
        violations.append("max_open_positions reached")
    if state.open_orders + len(venues) > limits.max_concurrent_orders:
        violations.append("max_concurrent_orders would be exceeded")
    return violations
