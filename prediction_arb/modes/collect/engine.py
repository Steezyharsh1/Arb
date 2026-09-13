"""Data-collection mode (§90): record markets + order books, place no
orders. This mode has NO execution engine at all — the safest possible
architecture for the first operational phase."""
from __future__ import annotations

import asyncio
import time

from rich.console import Console

from ...adapters.base import PredictionMarketAdapter
from ...core.logging import get_logger
from ...core.types import APIStatus, Mode
from ...database.repository import Repository
from ...monitoring.health import HealthMonitor

log = get_logger("modes.collect")


class CollectEngine:
    def __init__(self, adapters: dict[str, PredictionMarketAdapter],
                 repo: Repository, console: Console,
                 poll_interval: float = 10.0,
                 max_markets_per_venue: int = 200,
                 max_books_per_cycle: int = 40):
        self.adapters = adapters
        self.repo = repo
        self.console = console
        self.poll_interval = poll_interval
        self.max_markets = max_markets_per_venue
        self.max_books = max_books_per_cycle
        self.health = HealthMonitor()

    async def run(self, cycles: int | None = None) -> None:
        n = 0
        while cycles is None or n < cycles:
            n += 1
            await self._cycle(n)
            if cycles is None or n < cycles:
                await asyncio.sleep(self.poll_interval)

    async def _cycle(self, cycle: int) -> None:
        for venue, adapter in self.adapters.items():
            status = adapter.get_api_status()
            if status in (APIStatus.NO_API_AVAILABLE,
                          APIStatus.SAME_VENUE_ROUTE,
                          APIStatus.API_DOCUMENTED_UNTESTED):
                if cycle == 1:
                    self.console.print(
                        f"[yellow]{venue}[/yellow]: skipping collection "
                        f"(status={status.value})")
                continue
            try:
                t0 = time.monotonic()
                markets = await adapter.get_markets(limit=self.max_markets)
                await self.health.record_success(
                    venue, (time.monotonic() - t0) * 1000)
            except Exception as exc:
                await self.health.record_error(venue, str(exc))
                log.error(f"{venue}: market discovery failed: {exc}")
                continue

            self.console.print(
                f"[cycle {cycle}] {venue}: {len(markets)} open binary "
                f"markets discovered")
            for m in markets:
                self.repo.upsert_market(
                    venue=venue, market_id=m.market_id,
                    contract_id=m.contract_id,
                    underlying_exchange=m.underlying_exchange,
                    description=m.event_description,
                    outcome_type=m.outcome_type.value,
                    category=m.category, status="open")

            books_saved = 0
            for m in markets[: self.max_books]:
                try:
                    book = await adapter.get_order_book(m.market_id)
                except Exception as exc:
                    await self.health.record_error(venue, str(exc))
                    continue
                if book is None or (not book.bids and not book.asks):
                    continue
                self.repo.save_orderbook_snapshot(book)
                books_saved += 1
            self.console.print(
                f"[cycle {cycle}] {venue}: {books_saved} non-empty order "
                f"books recorded")
