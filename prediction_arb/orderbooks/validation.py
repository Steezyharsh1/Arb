"""Fail-closed validation for normalized executable order books."""
from __future__ import annotations

from decimal import Decimal

from ..core.types import OrderBook


def order_book_problems(book: OrderBook, *,
                        require_two_sided: bool = True,
                        reject_crossed: bool = True) -> list[str]:
    """Return invariant failures for a normalized CLOB book.

    Prices and quantities must be finite, prices must be probabilities in
    ``(0, 1)``, sizes positive, and each side sorted best-first. Consumers
    that need an executable two-sided quote use the default ``True``; a
    one-sided diagnostic snapshot can opt out explicitly.
    """
    problems: list[str] = []
    if not book.bids and not book.asks:
        problems.append("book has no levels")
    if require_two_sided:
        if not book.bids:
            problems.append("book has no bid levels")
        if not book.asks:
            problems.append("book has no ask levels")

    valid_prices: dict[str, list[Decimal]] = {"bids": [], "asks": []}
    for name, levels, descending in (
            ("bids", book.bids, True), ("asks", book.asks, False)):
        for index, level in enumerate(levels):
            price, quantity = level.price, level.quantity
            if not price.is_finite():
                problems.append(f"{name}[{index}] price is non-finite")
                continue
            if not quantity.is_finite():
                problems.append(f"{name}[{index}] quantity is non-finite")
                continue
            if not Decimal(0) < price < Decimal(1):
                problems.append(f"{name}[{index}] price out of range: {price}")
            if quantity <= 0:
                problems.append(
                    f"{name}[{index}] quantity is non-positive: {quantity}")
            valid_prices[name].append(price)
        prices = valid_prices[name]
        if prices != sorted(prices, reverse=descending):
            problems.append(f"{name} are not sorted best-first")

    if reject_crossed and book.bids and book.asks:
        best_bid = book.best_bid()
        best_ask = book.best_ask()
        if (best_bid is not None and best_ask is not None
                and best_bid.price.is_finite()
                and best_ask.price.is_finite()
                and best_ask.price <= best_bid.price):
            problems.append(
                f"book is crossed: bid {best_bid.price} >= ask {best_ask.price}")
    return problems


def is_usable_order_book(book: OrderBook, *,
                         require_two_sided: bool = True) -> bool:
    """Whether the book is safe for a market-data consumer to use."""
    return not order_book_problems(book, require_two_sided=require_two_sided)
