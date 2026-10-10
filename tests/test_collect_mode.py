"""Collect-mode safety: only evidenced data, valid snapshots, no execution."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from rich.console import Console

from prediction_arb.core.types import APIStatus
from prediction_arb.modes.collect.engine import CollectEngine
from .conftest import make_book, make_contract


class CollectAdapter:
    venue = "kalshi"

    def __init__(self, status: APIStatus, book):
        self.status = status
        self.book = book
        self.market_calls = 0
        self.book_calls = 0
        self.order_calls = 0
        self.contract = make_contract(venue=self.venue, contract_id="KX-COLLECT")

    def get_api_status(self):
        return self.status

    async def get_markets(self, limit=200):
        self.market_calls += 1
        return [self.contract][:limit]

    async def get_order_book(self, market_id):
        self.book_calls += 1
        return self.book

    async def place_order(self, request):
        self.order_calls += 1
        raise AssertionError("collect mode must never invoke execution")


class MemoryRepository:
    def __init__(self):
        self.contracts = []
        self.books = []

    def record_contract(self, contract, status="open"):
        self.contracts.append((contract, status))

    def save_orderbook_snapshot(self, book):
        self.books.append(book)


def _engine(adapter):
    repo = MemoryRepository()
    engine = CollectEngine(
        {adapter.venue: adapter}, repo, Console(), poll_interval=0,
        max_markets_per_venue=10, max_books_per_cycle=10)
    return engine, repo


def test_market_data_observed_records_valid_snapshot_without_execution():
    adapter = CollectAdapter(
        APIStatus.MARKET_DATA_OBSERVED,
        make_book("kalshi", "KX-COLLECT", bids=[("0.40", "10")],
                  asks=[("0.45", "12")]))
    engine, repo = _engine(adapter)

    asyncio.run(engine._cycle(1))

    assert len(repo.contracts) == 1
    assert len(repo.books) == 1
    assert repo.books[0].contract_id == "KX-COLLECT"
    assert adapter.market_calls == 1 and adapter.book_calls == 1
    assert adapter.order_calls == 0
    assert not hasattr(engine, "execution")


@pytest.mark.parametrize("status", [
    APIStatus.CONNECTIVITY_OBSERVED,
    APIStatus.API_DOCUMENTED_UNTESTED,
    APIStatus.UNVERIFIED,
    APIStatus.NO_API_AVAILABLE,
    APIStatus.SAME_VENUE_ROUTE,
])
def test_connectivity_or_docs_alone_never_admit_collection(status):
    adapter = CollectAdapter(status, make_book(
        "kalshi", "KX-COLLECT", bids=[("0.40", "10")],
        asks=[("0.45", "12")]))
    engine, repo = _engine(adapter)

    asyncio.run(engine._cycle(1))

    assert adapter.market_calls == 0
    assert adapter.book_calls == 0
    assert repo.contracts == [] and repo.books == []
    assert adapter.order_calls == 0


def test_crossed_book_is_not_recorded_as_a_usable_snapshot():
    adapter = CollectAdapter(
        APIStatus.MARKET_DATA_OBSERVED,
        make_book("kalshi", "KX-COLLECT", bids=[("0.51", "10")],
                  asks=[("0.50", "12")]))
    engine, repo = _engine(adapter)

    asyncio.run(engine._cycle(1))

    assert len(repo.contracts) == 1
    assert repo.books == []
    assert adapter.order_calls == 0


def test_nonfinite_or_out_of_range_book_levels_are_not_recorded():
    book = make_book("kalshi", "KX-COLLECT",
                     bids=[("0.40", "10")], asks=[("0.45", "12")])
    from prediction_arb.core.types import PriceLevel
    book.asks[0] = PriceLevel(Decimal("NaN"), book.asks[0].quantity)
    adapter = CollectAdapter(APIStatus.MARKET_DATA_OBSERVED, book)
    engine, repo = _engine(adapter)

    asyncio.run(engine._cycle(1))

    assert repo.books == []
    assert adapter.order_calls == 0
