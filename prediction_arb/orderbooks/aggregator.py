"""Task-safe order-book store (§9, §23).

Holds the latest normalized book per (venue, contract), guarded by an
asyncio.Lock, and answers freshness queries. Also records opportunity-
lifetime bookkeeping inputs (update counts, §68)."""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from ..core.types import OrderBook
from .orderbook import is_stale


class OrderBookAggregator:
    def __init__(self, max_staleness_ms: float = 1000.0):
        self.max_staleness_ms = max_staleness_ms
        self._books: dict[tuple[str, str], OrderBook] = {}
        self._update_counts: dict[tuple[str, str], int] = {}
        self._lock = asyncio.Lock()

    async def update(self, book: OrderBook) -> None:
        key = (book.venue, book.contract_id)
        async with self._lock:
            existing = self._books.get(key)
            if (existing is not None
                    and existing.sequence_number is not None
                    and book.sequence_number is not None
                    and book.sequence_number <= existing.sequence_number):
                return  # out-of-order update; ignore
            self._books[key] = book
            self._update_counts[key] = self._update_counts.get(key, 0) + 1

    async def get(self, venue: str, contract_id: str,
                  require_fresh: bool = True,
                  now: Optional[datetime] = None) -> Optional[OrderBook]:
        async with self._lock:
            book = self._books.get((venue, contract_id))
        if book is None:
            return None
        if require_fresh and is_stale(book, self.max_staleness_ms, now):
            return None
        return book

    async def update_count(self, venue: str, contract_id: str) -> int:
        async with self._lock:
            return self._update_counts.get((venue, contract_id), 0)

    async def snapshot(self) -> dict[tuple[str, str], OrderBook]:
        async with self._lock:
            return dict(self._books)

    # ---------------------------------------------------------- D11 helpers
    def peek(self, venue: str, contract_id: str, require_fresh: bool = True,
             now: Optional[datetime] = None) -> Optional[OrderBook]:
        """Synchronous, freshness-gated single-key read.

        The execution engines take a *sync* ``book_lookup`` callable, so
        they cannot await :meth:`get`. This reads one dict key and never
        mutates, which is safe without the lock — the lock exists to guard
        the read-modify-write in :meth:`update` (its sequence-number
        check), not single-key reads.

        Freshness is enforced here so a simulated fill cannot be priced
        against a book the scan would already have rejected (D11).
        """
        book = self._books.get((venue, contract_id))
        if book is None:
            return None
        if require_fresh and is_stale(book, self.max_staleness_ms, now):
            return None
        return book

    async def fresh_snapshot(self,
                             now: Optional[datetime] = None
                             ) -> dict[tuple[str, str], OrderBook]:
        """Only the books still inside the staleness window.

        This is what a scan should see. Passing the raw store instead
        means trading on books nobody checked the age of — the defect
        behind D11.
        """
        async with self._lock:
            items = list(self._books.items())
        return {k: b for k, b in items
                if not is_stale(b, self.max_staleness_ms, now)}

    async def stale_keys(self,
                         now: Optional[datetime] = None
                         ) -> list[tuple[str, str]]:
        """Keys held but no longer fresh — reported, never silently used."""
        async with self._lock:
            items = list(self._books.items())
        return [k for k, b in items
                if is_stale(b, self.max_staleness_ms, now)]

    async def keys(self) -> list[tuple[str, str]]:
        async with self._lock:
            return list(self._books)

    async def drop(self, key: tuple[str, str]) -> None:
        async with self._lock:
            self._books.pop(key, None)
            self._update_counts.pop(key, None)
