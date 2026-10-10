"""Two-leg guaranteed (TYPE A) arbitrage detection — the v1 core (§25–§27).

Buy YES on venue A + buy NO on venue B for the SAME economic contract on
INDEPENDENT venues. Guaranteed profit iff, for the executable quantity q:

    yes_walk_cost + no_walk_cost + all_fees + settlement_delay_cost
        < payout * q

Everything is computed from actual order-book walks — never headline
prices. Missing fee data or unknown settlement lockup BLOCKS the
opportunity (never treated as zero).

An unavailable mandatory cost is never substituted with a number, so an
opportunity whose fees or settlement timing cannot be computed has
``net_profit=None`` — UNVERIFIED — and not a flattering positive figure.
Each cost component is evaluated independently and recorded in a
:class:`~prediction_arb.core.types.CostLedger`: the components that *can*
be computed stay available for research, the ones that cannot carry their
reason, and the ledger has no total until all of them are known. Gross
edge (payout minus walked cost, before any cost) is always reported and is
kept semantically separate from net profit: a positive gross edge is not a
profitable executable opportunity.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from ..core.clock import utcnow
from ..core.types import (
    CostComponent,
    CostLedger,
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
    WalkResult,
)
from ..fees.calculator import FeeCalculator, FillSlice
from ..fees.freshness import freshness_enforced
from ..normalization.settlement import assess_lockup, settlement_delay_cost
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


def _fill_slices(walk: WalkResult) -> list[FillSlice]:
    """The levels a walk consumed, as the fill slices a venue bills on.

    Fees are per fill, so the walked levels — not the weighted-average
    price — are what a venue charges (defect D13). The single-slice
    fallback only triggers if a walk ever reports filled quantity without
    levels; ``walk_book`` always records them, so it is a guard, not a
    path. An empty walk yields no slices, which fails closed downstream.
    """
    if walk.levels_consumed:
        return [FillSlice(price=level.price, quantity=level.quantity)
                for level in walk.levels_consumed]
    if walk.filled_quantity > 0:
        return [FillSlice(price=walk.weighted_average_price,
                          quantity=walk.filled_quantity)]
    return []


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
    strict_fee_freshness: Optional[bool] = None,
    evaluation_time: Optional[datetime] = None,
) -> Optional[Opportunity]:
    """Evaluate buying YES on yes_contract's venue and NO on no_contract's.

    Returns None when there is no gross edge at all; otherwise an
    Opportunity whose status is TRADE / WATCH / BLOCK with reasons.

    Net profit is only ever a number when every mandatory cost could be
    computed. If a fee schedule or the settlement lockup is unavailable the
    opportunity is BLOCK and ``net_profit`` is ``None`` (UNVERIFIED) — the
    missing cost is never assumed to be zero, and the gross edge that made
    the pair worth sizing is reported separately as research data.

    ``evaluation_time`` is the instant the books describe. It defaults to
    now; replay passes the recorded frame time so lockup is measured from
    when the opportunity existed, not from the wall clock (D18).

    ``strict_fee_freshness`` overrides the mode-derived fee-freshness policy
    (``fees/freshness.py``, §98). ``None`` — what every production caller
    passes — lets the mode decide: LIVE and PAPER refuse a schedule verified
    more than ``fees.max_schedule_age_days`` ago (D19: the flag used to
    default to ``False``, so the documented live gate never ran), while
    HISTORICAL replay still costs it and is labelled research.
    """
    payout = yes_contract.payout
    if no_contract.payout != payout or yes_contract.currency != no_contract.currency:
        return None

    # One resolution point for the mandatory-freshness policy (§98).
    strict_freshness = freshness_enforced(mode, strict_fee_freshness)

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

    # --- mandatory costs, each computed independently -------------------
    #
    # Fees — taker on both legs (conservative). Charged PER FILL from the
    # levels each walk actually consumed: a venue bills each fill of a
    # walking order separately, rounds each fill, and may cap the order
    # total. One fee at the walk's weighted-average price is not what the
    # venue charges (defect D13).
    #
    # A component that cannot be computed is recorded as UNAVAILABLE with
    # its reason — never as zero (§24). Components are evaluated one at a
    # time so a single missing schedule cannot hide the others: every
    # mandatory-cost failure is reported, and every cost that *is* known
    # stays available for research.
    def _component(name: str,
                   compute: Callable[[], Decimal]) -> CostComponent:
        try:
            return CostComponent(name=name, amount=compute())
        except FeeDataError as exc:
            # One reason per distinct message: a venue with no schedule at
            # all makes both of its components unavailable for the same
            # cause, and repeating it would bury the other failures. The
            # ledger still names every unavailable component.
            if str(exc) not in block:
                block.append(str(exc))
            return CostComponent(name=name, amount=None, reason=str(exc))

    fee_components = (
        _component(
            f"{yes_contract.venue}:trade_fee",
            lambda: fee_calc.trade_fee_fills(
                yes_contract.venue, _fill_slices(yes_walk), is_taker=True,
                strict_freshness=strict_freshness).total),
        _component(
            f"{no_contract.venue}:trade_fee",
            lambda: fee_calc.trade_fee_fills(
                no_contract.venue, _fill_slices(no_walk), is_taker=True,
                strict_freshness=strict_freshness).total),
        _component(
            f"{yes_contract.venue}:settlement_fee",
            lambda: fee_calc.settlement_fee(
                yes_contract.venue, qty,
                strict_freshness=strict_freshness)),
        _component(
            f"{no_contract.venue}:settlement_fee",
            lambda: fee_calc.settlement_fee(
                no_contract.venue, qty,
                strict_freshness=strict_freshness)),
    )
    # None whenever any fee component is unavailable: an unknown fee is not
    # a zero fee, so the fee total is unknown too.
    fees = CostLedger(fee_components).total
    # Capital locked = purchase cost + fees. Unknown fees ⇒ unknown capital
    # (a gross-cost-only figure would understate the cash the trade needs).
    capital = (gross_cost + fees) if fees is not None else None

    yes_best = yes_book.best_ask().price if yes_book.best_ask() else None
    no_best = no_book.best_ask().price if no_book.best_ask() else None
    slippage = (slippage_vs_best(yes_walk, yes_best)
                + slippage_vs_best(no_walk, no_best))

    # Capital lockup runs to the LAST leg's evidenced capital-release time
    # (resolution basis + post-resolution settlement delay), not to the
    # event time; see normalization/settlement.py (§28, D18).
    timing = assess_lockup([yes_contract, no_contract], evaluation_time)
    lockup = timing.lockup_days
    spread_days = timing.spread_days
    lockup_detail = "; ".join(timing.reasons)
    if spread_days is None:
        block.append("settlement delay spread UNKNOWN")
    elif spread_days > maximum_settlement_delay_days:
        block.append(
            f"settlement delay spread {spread_days} days exceeds limit "
            f"{maximum_settlement_delay_days}")
    if lockup is None:
        # Reported whether or not fees are known: every mandatory-cost
        # failure is listed (D17), and an unknown lockup is its own failure.
        block.append(f"settlement lockup UNKNOWN — cannot cost capital "
                     f"delay ({lockup_detail})")
    elif lockup > Decimal("36500"):
        block.append("implausible settlement lockup")

    if capital is None:
        # The cost of capital is charged on locked capital, and locked
        # capital includes fees. With unknown fees the base is unknown, so
        # the delay cost is unavailable — it is not computed on a
        # fee-free capital figure that would understate it.
        delay = CostComponent(
            "settlement_delay", None,
            "cost-of-capital base unknown — mandatory trade/settlement fees "
            "are unavailable")
        block.append(f"settlement delay cost unavailable — {delay.reason}")
    elif lockup is None:
        delay = CostComponent("settlement_delay", None,
                              f"settlement lockup UNKNOWN ({lockup_detail})")
    else:
        delay = CostComponent(
            "settlement_delay",
            settlement_delay_cost(capital, lockup, cost_of_capital_annual))

    costs = CostLedger(fee_components + (delay,))
    total_cost = costs.total          # None ⇒ a mandatory cost is unknown
    delay_cost = delay.amount

    guaranteed_payout = payout * qty
    # NOTE: slippage is already inside gross_cost (walk uses real levels).
    # It is reported separately but not double-counted.
    #
    # gross_edge is the pre-cost research figure; net_profit is the profit
    # test. They are never the same thing, and net_profit is None whenever
    # any mandatory cost is unavailable.
    gross_edge = guaranteed_payout - gross_cost
    net_profit = (gross_edge - total_cost) if total_cost is not None else None
    net_per_contract = ((net_profit / qty)
                        if net_profit is not None and qty else None)
    if net_profit is None or capital is None:
        roc = None
    elif capital > 0:
        roc = net_profit / capital
    else:
        roc = Decimal("0")

    status = OpportunityStatus.TRADE
    if block:
        status = OpportunityStatus.BLOCK
    elif net_profit is None or roc is None or delay_cost is None:
        # Unreachable by construction: every unavailable component above
        # appends a block reason. It stays because "TRADE/WATCH without a
        # verified net profit" is the one outcome this module must never
        # produce, so the safe verdict is asserted here rather than hoped
        # for upstream.
        status = OpportunityStatus.BLOCK
        block.append("net profit unavailable — mandatory costs incomplete")
    elif net_profit < minimum_net_profit or roc < minimum_net_return:
        status = OpportunityStatus.WATCH
    if net_profit is not None and net_profit <= 0:
        status = OpportunityStatus.BLOCK
        block.append("net profit not positive after all costs")

    now = utcnow()
    # Preserve venue-specific fee allocation for capital-at-venue checks in
    # the execution coordinator. An unavailable component is not turned into
    # an executable zero: such an opportunity is BLOCK above and never reaches
    # the coordinator; the known portion remains useful for diagnostics.
    yes_leg_fees = sum(
        (component.amount for component in
         (fee_components[0], fee_components[2])
         if component.amount is not None), Decimal("0"))
    no_leg_fees = sum(
        (component.amount for component in
         (fee_components[1], fee_components[3])
         if component.amount is not None), Decimal("0"))
    legs = [
        Leg(venue=yes_contract.venue, contract=yes_contract, side=Side.YES,
            action=OrderAction.BUY, quantity=qty,
            limit_price=yes_walk.weighted_average_price, walk=yes_walk,
            fees=yes_leg_fees),
        Leg(venue=no_contract.venue, contract=no_contract, side=Side.NO,
            action=OrderAction.BUY, quantity=qty,
            limit_price=no_walk.weighted_average_price, walk=no_walk,
            fees=no_leg_fees),
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
        costs=costs,
    )
