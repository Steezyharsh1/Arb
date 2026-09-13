"""Outcome-type helpers (§17).

v1 focuses on BINARY YES/NO. Multi-outcome/ranged/bracket support has a
clean interface here; portfolio construction over N mutually exclusive
outcomes is stubbed in arbitrage/multi_leg.py.
"""
from __future__ import annotations

from decimal import Decimal

from ..core.types import NormalizedContract, OutcomeType


def is_binary(contract: NormalizedContract) -> bool:
    return contract.outcome_type == OutcomeType.BINARY and contract.outcome_count == 2


def covers_full_outcome_space(contracts: list[NormalizedContract]) -> bool:
    """For bracket markets: do these contracts partition the outcome space?

    v1: only the trivial binary case is decided; anything else returns
    False (conservative — never assume coverage).
    """
    if len(contracts) == 1 and is_binary(contracts[0]):
        return True
    return False


def multi_outcome_payout(contracts: list[NormalizedContract]) -> Decimal | None:
    """Common payout for a mutually exclusive outcome set, or None if the
    payouts differ (in which case no sum-based arbitrage applies)."""
    payouts = {c.payout for c in contracts}
    if len(payouts) == 1:
        return payouts.pop()
    return None
