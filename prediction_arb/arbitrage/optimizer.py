"""Opportunity scoring and ranking (§41). Also the multi-venue search
driver (§27, §64): for each normalized event it evaluates ALL independent
(YES-venue, NO-venue) orderings rather than a hard-coded pair."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from ..core.types import (
    Independence,
    Mode,
    NormalizedContract,
    Opportunity,
    OpportunityStatus,
    OrderBook,
)
from ..fees.calculator import FeeCalculator
from ..provenance.independence import contracts_independent
from .two_leg import evaluate_two_leg


def score(op: Opportunity) -> Decimal:
    """Composite ranking score (§41). Not percentage-return-only."""
    s = op.net_profit
    if op.capital_required > 0:
        s += op.return_on_capital * Decimal("10")     # capital efficiency
    s += min(op.quantity, Decimal("500")) / Decimal("100")  # liquidity
    if op.match_confidence is not None:
        s *= Decimal(str(op.match_confidence))        # execution confidence
    s -= Decimal(len(op.block_reasons))               # data uncertainty
    s -= op.settlement_delay_cost                     # delay cost
    return s


def rank(opportunities: list[Opportunity]) -> list[Opportunity]:
    return sorted(opportunities, key=score, reverse=True)


def search_event(
    contracts: list[NormalizedContract],
    books: dict[tuple[str, str], OrderBook],
    fee_calc: FeeCalculator,
    mode: Mode,
    approved_pairs: dict[tuple[str, str], tuple[str, float]],
    **eval_kwargs,
) -> list[Opportunity]:
    """Search all independent venue orderings for one normalized event.

    `approved_pairs` maps (key_a, key_b) — sorted contract keys — to
    (match_id, confidence) for pairs holding an APPROVED match
    certificate. Pairs without an approved certificate are never
    evaluated (Principle B).
    """
    out: list[Opportunity] = []
    n = len(contracts)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            yes_c, no_c = contracts[i], contracts[j]
            ka = f"{yes_c.venue}:{yes_c.contract_id}"
            kb = f"{no_c.venue}:{no_c.contract_id}"
            pair_key = (min(ka, kb), max(ka, kb))
            approval = approved_pairs.get(pair_key)
            if approval is None:
                continue
            indep = contracts_independent(yes_c, no_c)
            if indep != Independence.INDEPENDENT:
                continue
            yes_book = books.get((yes_c.venue, yes_c.contract_id))
            no_book = books.get((no_c.venue, no_c.contract_id))
            if yes_book is None or no_book is None:
                continue
            op = evaluate_two_leg(
                yes_c, no_c, yes_book, no_book, fee_calc, mode,
                match_id=approval[0], match_confidence=approval[1],
                independence=indep, **eval_kwargs)
            if op is not None:
                out.append(op)
    return rank(out)
