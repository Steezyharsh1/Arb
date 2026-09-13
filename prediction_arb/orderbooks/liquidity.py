"""Executable-liquidity analysis helpers (Principle C)."""
from __future__ import annotations

from decimal import Decimal

from ..core.types import OrderBook
from .orderbook import walk_buy


def max_common_buy_quantity(book_a: OrderBook, book_b: OrderBook) -> Decimal:
    """Largest quantity buyable from BOTH books' displayed asks."""
    qa = sum((l.quantity for l in book_a.asks), Decimal("0"))
    qb = sum((l.quantity for l in book_b.asks), Decimal("0"))
    return min(qa, qb)


def liquidity_ratio(book: OrderBook, quantity: Decimal) -> Decimal:
    """Fraction of the requested quantity available at ANY displayed ask."""
    if quantity <= 0:
        return Decimal("0")
    walk = walk_buy(book, quantity)
    return walk.filled_quantity / quantity
