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
    Side,
)
from ..fees.calculator import FeeCalculator
from ..orderbooks.orderbook import book_for_side
from ..provenance.independence import contracts_independent
from .two_leg import evaluate_two_leg


def _is_priced(op: Opportunity) -> bool:
    """True when the opportunity carries a verified net profit and every
    input the ranking score needs."""
    return (op.net_profit is not None
            and op.return_on_capital is not None
            and op.settlement_delay_cost is not None)


def score(op: Opportunity) -> Decimal:
    """Composite ranking score (§41). Not percentage-return-only.

    Requires a verified net profit. An opportunity whose mandatory costs
    could not be computed has no score: ranking it on gross edge would put
    an unpriced, non-executable candidate ahead of a costed one, and gross
    edge is not profit.
    """
    if not _is_priced(op):
        missing = ", ".join(op.costs.missing_names()) or "cost ledger empty"
        raise ValueError(
            f"opportunity {op.opportunity_id} has no verified net profit "
            f"(unavailable mandatory costs: {missing}); it cannot be "
            f"scored. Gross edge ({op.gross_edge}) is not profit.")
    s = op.net_profit
    if op.capital_required is not None and op.capital_required > 0:
        s += op.return_on_capital * Decimal("10")     # capital efficiency
    s += min(op.quantity, Decimal("500")) / Decimal("100")  # liquidity
    if op.match_confidence is not None:
        s *= Decimal(str(op.match_confidence))        # execution confidence
    s -= Decimal(len(op.block_reasons))               # data uncertainty
    s -= op.settlement_delay_cost                     # delay cost
    return s


def rank(opportunities: list[Opportunity]) -> list[Opportunity]:
    """Scored opportunities first, best first; unpriced ones last.

    Opportunities whose mandatory costs could not be computed are never
    scored. They are appended after every scored opportunity, ordered by
    gross edge for research only, so a large positive gross edge can never
    outrank — or be mistaken for — a profitable executable opportunity.
    """
    priced = [op for op in opportunities if _is_priced(op)]
    unpriced = [op for op in opportunities if not _is_priced(op)]
    unpriced.sort(key=lambda op: op.gross_edge, reverse=True)
    return sorted(priced, key=score, reverse=True) + unpriced


def search_event(
    contracts: list[NormalizedContract],
    books: dict[tuple[str, str], OrderBook],
    fee_calc: FeeCalculator,
    mode: Mode,
    approved_pairs: dict[tuple[str, str], tuple[str, float]],
    resolve_recorded_sides: bool = False,
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
            if resolve_recorded_sides:
                yes_book = book_for_side(yes_book, Side.YES, yes_c.payout)
                no_book = book_for_side(no_book, Side.NO, no_c.payout)
            op = evaluate_two_leg(
                yes_c, no_c, yes_book, no_book, fee_calc, mode,
                match_id=approval[0], match_confidence=approval[1],
                independence=indep, **eval_kwargs)
            if op is not None:
                out.append(op)
    return rank(out)
