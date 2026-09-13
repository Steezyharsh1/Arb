"""Order-book utilities: staleness detection and the order-book walker
(§23, §26). The walker never uses midpoint or last-traded price — it
consumes actual displayed liquidity level by level."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..core.clock import age_ms, utcnow
from ..core.types import OrderBook, PriceLevel, WalkResult


def is_stale(book: OrderBook, max_staleness_ms: float,
             now: Optional[datetime] = None) -> bool:
    a = age_ms(book.timestamp, now)
    return a is None or a > max_staleness_ms


def walk_book(levels: list[PriceLevel], quantity: Decimal) -> WalkResult:
    """Walk price levels (already ordered best-first) for `quantity`.

    Returns the weighted-average price and total cost for whatever
    quantity is actually executable. Never extrapolates beyond displayed
    liquidity (Principle C / spec test case 2).
    """
    if quantity <= 0:
        return WalkResult(quantity, Decimal("0"), Decimal("0"),
                          Decimal("0"), [], False)
    remaining = quantity
    total_cost = Decimal("0")
    consumed: list[PriceLevel] = []
    for level in levels:
        if remaining <= 0:
            break
        take = min(level.quantity, remaining)
        if take <= 0:
            continue
        total_cost += take * level.price
        consumed.append(PriceLevel(price=level.price, quantity=take))
        remaining -= take
    filled = quantity - remaining
    wap = (total_cost / filled) if filled > 0 else Decimal("0")
    return WalkResult(
        requested_quantity=quantity,
        filled_quantity=filled,
        total_cost=total_cost,
        weighted_average_price=wap,
        levels_consumed=consumed,
        fully_filled=(remaining == 0),
    )


def walk_buy(book: OrderBook, quantity: Decimal) -> WalkResult:
    """Buy by consuming asks (best/lowest first)."""
    return walk_book(book.asks, quantity)


def walk_sell(book: OrderBook, quantity: Decimal) -> WalkResult:
    """Sell by consuming bids (best/highest first)."""
    return walk_book(book.bids, quantity)


def executable_ask_quantity(book: OrderBook) -> Decimal:
    return sum((l.quantity for l in book.asks), Decimal("0"))


def slippage_vs_best(walk: WalkResult, levels_best_price: Optional[Decimal]
                     ) -> Decimal:
    """Slippage = (WAP - best price) * filled quantity, floored at 0."""
    if levels_best_price is None or walk.filled_quantity <= 0:
        return Decimal("0")
    per = walk.weighted_average_price - levels_best_price
    return max(Decimal("0"), per) * walk.filled_quantity
