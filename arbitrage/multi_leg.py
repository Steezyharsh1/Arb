"""N-leg / multi-outcome arbitrage — V1 STUB with clean interface (§3, §17).

Interface contract (stable): `find_multi_leg_opportunities` takes a
mutually exclusive outcome set with books and returns Opportunity
objects. The two-leg engine does NOT depend on this module; future
implementation requires no changes to the arbitrage engine (§3).
"""
from __future__ import annotations

from decimal import Decimal

from ..core.types import Mode, NormalizedContract, Opportunity, OrderBook
from ..fees.calculator import FeeCalculator


def find_multi_leg_opportunities(
    outcome_contracts: list[NormalizedContract],
    books: dict[str, OrderBook],
    fee_calc: FeeCalculator,
    mode: Mode,
    payout: Decimal = Decimal("1.00"),
) -> list[Opportunity]:
    """Find portfolios over N mutually exclusive outcomes where
    sum(cheapest executable price per outcome across independent venues)
    < payout, after fees.

    NOT IMPLEMENTED IN V1. Requirements before implementation:
      * verified full-outcome-space coverage
        (normalization/outcomes.covers_full_outcome_space must be
        generalized beyond binary);
      * per-outcome independence classification;
      * joint sizing across N ask ladders (generalization of
        two_leg.find_max_profit_quantity).
    """
    raise NotImplementedError(
        "Multi-leg (N-outcome) arbitrage is a v1 stub (spec §3, §17). "
        "Two-leg binary arbitrage is implemented in arbitrage/two_leg.py. "
        "This function's signature is the stable interface for the future "
        "implementation.")
