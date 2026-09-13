"""Hedge-option analysis for partial fills (§32).

Given a naked position, enumerate available hedges and their costs so
PartialFillHandler (and future strategies) can pick the cheapest bounded
recovery. Deterministic and side-effect-free."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from ..core.types import OrderBook, Side
from ..orderbooks.orderbook import walk_buy, walk_sell


@dataclass(frozen=True)
class HedgeOption:
    description: str
    venue: str
    contract_id: str
    cost: Decimal              # incremental cost vs original plan
    achievable_quantity: Decimal


def enumerate_hedges(naked_side: Side, quantity: Decimal,
                     entry_price: Decimal,
                     same_book: Optional[OrderBook],
                     opposite_books: dict[tuple[str, str], OrderBook],
                     planned_hedge_price: Decimal) -> list[HedgeOption]:
    """Options: (a) complete the hedge on any independent venue's opposite
    side; (b) unwind on the original book."""
    options: list[HedgeOption] = []

    for (venue, contract_id), book in opposite_books.items():
        walk = walk_buy(book, quantity)
        if walk.filled_quantity > 0:
            extra = (walk.weighted_average_price - planned_hedge_price
                     ) * walk.filled_quantity
            options.append(HedgeOption(
                description="complete hedge leg",
                venue=venue, contract_id=contract_id,
                cost=max(Decimal("0"), extra),
                achievable_quantity=walk.filled_quantity))

    if same_book is not None:
        walk = walk_sell(same_book, quantity)
        if walk.filled_quantity > 0:
            loss = (entry_price - walk.weighted_average_price
                    ) * walk.filled_quantity
            options.append(HedgeOption(
                description="unwind naked leg",
                venue=same_book.venue, contract_id=same_book.contract_id,
                cost=max(Decimal("0"), loss),
                achievable_quantity=walk.filled_quantity))

    return sorted(options, key=lambda o: o.cost)
