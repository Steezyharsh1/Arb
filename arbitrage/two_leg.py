"""Two-leg guaranteed (TYPE A) arbitrage detection — the v1 core (§25–§27).

Buy YES on venue A + buy NO on venue B for the SAME economic contract on
INDEPENDENT venues. Guaranteed profit iff, for the executable quantity q:

    yes_walk_cost + no_walk_cost + all_fees + settlement_delay_cost
        < payout * q

Everything is computed from actual order-book walks — never headline
prices. Missing fee data or unknown settlement lockup BLOCKS the
opportunity (never treated as zero).
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Optional

from ..core.clock import utcnow
from ..core.types import (
    FeeDataError,
    Independence,
    Leg,
    Mode,
    NormalizedContract,
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    OrderAction,
    OrderBook,
    Side,
)
from ..fees.calculator import FeeCalculator
from ..normalization.settlement import (
    capital_lockup_days,
    settlement_delay_cost,
    settlement_delay_spread_days,
)
from ..orderbooks.orderbook import slippage_vs_best, walk_buy
from ..provenance.independence import (
    contracts_independent,
    eligible_for_guaranteed_arb,
)


def find_max_profit_quantity(yes_book: OrderBook, no_book: OrderBook,
                             payout: Decimal) -> Decimal:
    """Largest quantity for which the MARGINAL pair cost stays < payout.

    Walk both ask ladders together; stop when the next marginal unit's
    combined price >= payout (that unit would lose money pre-fee).
    """
    def ladder(book: OrderBook) -> list[tuple[Decimal, Decimal]]:
        return [(l.price, l.quantity) for l in book.asks]

    ya, na = ladder(yes_book), ladder(no_book)
    qty = Decimal("0")
    yi = ni = 0
    yrem = ya[0][1] if ya else Decimal("0")
    nrem = na[0][1] if na else Decimal("0")
    while yi < len(ya) and ni < len(na):
        marginal = ya[yi][0] + na[ni][0]
        if marginal >= payout:
            break
        take = min(yrem, nrem)
        if take <= 0:
            break
        qty += take
        yrem -= take
        nrem -= take
        if yrem == 0:
            yi += 1
            yrem = ya[yi][1] if yi < len(ya) else Decimal("0")
        if nrem == 0:
            ni += 1
            nrem = na[ni][1] if ni < len(na) else Decimal("0")
    return qty


def evaluate_two_leg(
    yes_contract: NormalizedContract,
    no_contract: NormalizedContract,
    yes_book: OrderBook,
    no_book: OrderBook,
    fee_calc: FeeCalculator,
    mode: Mode,
    *,
    match_id: Optional[str] = None,
    match_confidence: Optional[float] = None,
    independence: Optional[Independence] = None,
    minimum_net_profit: Decimal = Decimal("2.00"),
    minimum_net_return: Decimal = Decimal("0.005"),
    maximum_settlement_delay_days: Decimal = Decimal("5"),
    cost_of_capital_annual: Decimal = Decimal("0.05"),
    max_quantity: Optional[Decimal] = None,
    strict_fee_freshness: bool = False,
) -> Optional[Opportunity]:
    """Evaluate buying YES on yes_contract's venue and NO on no_contract's.

    Returns None when there is no gross edge at all; otherwise an
    Opportunity whose status is TRADE / WATCH / BLOCK with reasons.
    """
    payout = yes_contract.payout
    if no_contract.payout != payout or yes_contract.currency != no_contract.currency:
        return None

    block: list[str] = []

    indep = independence or contracts_independent(yes_contract, no_contract)
    if not eligible_for_guaranteed_arb(indep):
        block.append(f"venue relationship {indep.value} — not INDEPENDENT")

    # sizing from marginal pair cost, then bounded by caller
    qty = find_max_profit_quantity(yes_book, no_book, payout)
    if qty <= 0:
        return None
    if max_quantity is not None:
        qty = min(qty, max_quantity)
    # whole contracts only
    qty = Decimal(int(qty))
    if qty <= 0:
        return None

    yes_walk = walk_buy(yes_book, qty)
    no_walk = walk_buy(no_book, qty)
    qty = min(yes_walk.filled_quantity, no_walk.filled_quantity)
    qty = Decimal(int(qty))
    if qty <= 0:
        return None
    if yes_walk.filled_quantity != qty or no_walk.filled_quantity != qty:
        yes_walk = walk_buy(yes_book, qty)
        no_walk = walk_buy(no_book, qty)

    gross_cost = yes_walk.total_cost + no_walk.total_cost

    # fees — taker on both legs (conservative)
    try:
        fees = (
            fee_calc.trade_fee(yes_contract.venue,
                               yes_walk.weighted_average_price, qty,
                               is_taker=True,
                               strict_freshness=strict_fee_freshness)
            + fee_calc.trade_fee(no_contract.venue,
                                 no_walk.weighted_average_price, qty,
                                 is_taker=True,
                                 strict_freshness=strict_fee_freshness)
            + fee_calc.settlement_fee(yes_contract.venue, qty,
                                      strict_freshness=strict_fee_freshness)
            + fee_calc.settlement_fee(no_contract.venue, qty,
                                      strict_freshness=strict_fee_freshness)
        )
    except FeeDataError as e:
        fees = Decimal("0")
        block.append(str(e))

    yes_best = yes_book.best_ask().price if yes_book.best_ask() else None
    no_best = no_book.best_ask().price if no_book.best_ask() else None
    slippage = (slippage_vs_best(yes_walk, yes_best)
                + slippage_vs_best(no_walk, no_best))

    capital = gross_cost + fees

    lockup = capital_lockup_days([yes_contract, no_contract])
    delay_cost = settlement_delay_cost(capital, lockup, cost_of_capital_annual)
    if delay_cost is None:
        delay_cost = Decimal("0")
        block.append("settlement lockup UNKNOWN — cannot cost capital delay")
    spread_days = settlement_delay_spread_days([yes_contract, no_contract])
    if spread_days is None:
        block.append("settlement delay spread UNKNOWN")
    elif spread_days > maximum_settlement_delay_days:
        block.append(
            f"settlement delay spread {spread_days} days exceeds limit "
            f"{maximum_settlement_delay_days}")
    if lockup is not None and lockup > Decimal("36500"):
        block.append("implausible settlement lockup")

    guaranteed_payout = payout * qty
    # NOTE: slippage is already inside gross_cost (walk uses real levels).
    # It is reported separately but not double-counted.
    net_profit = guaranteed_payout - gross_cost - fees - delay_cost
    net_per_contract = (net_profit / qty) if qty else Decimal("0")
    roc = (net_profit / capital) if capital > 0 else Decimal("0")

    status = OpportunityStatus.TRADE
    if block:
        status = OpportunityStatus.BLOCK
    elif net_profit < minimum_net_profit or roc < minimum_net_return:
        status = OpportunityStatus.WATCH
    if net_profit <= 0:
        status = OpportunityStatus.BLOCK
        block.append("net profit not positive after all costs")

    now = utcnow()
    legs = [
        Leg(venue=yes_contract.venue, contract=yes_contract, side=Side.YES,
            action=OrderAction.BUY, quantity=qty,
            limit_price=yes_walk.weighted_average_price, walk=yes_walk),
        Leg(venue=no_contract.venue, contract=no_contract, side=Side.NO,
            action=OrderAction.BUY, quantity=qty,
            limit_price=no_walk.weighted_average_price, walk=no_walk),
    ]
    return Opportunity(
        opportunity_id=str(uuid.uuid4()),
        mode=mode,
        opportunity_type=OpportunityType.TYPE_A_GUARANTEED,
        timestamp=now,
        normalized_event=yes_contract.normalized_event or yes_contract.event_description,
        legs=legs,
        payout_per_contract=payout,
        quantity=qty,
        gross_cost=gross_cost,
        total_fees=fees,
        slippage=slippage,
        settlement_delay_cost=delay_cost,
        net_profit=net_profit,
        net_profit_per_contract=net_per_contract,
        capital_required=capital,
        return_on_capital=roc,
        independence=indep,
        match_id=match_id,
        match_confidence=match_confidence,
        status=status,
        block_reasons=block,
        first_seen=now,
        last_seen=now,
    )
