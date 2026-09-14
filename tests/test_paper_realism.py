"""Phase 6 defect fixes: D11 (book freshness in paper) and D12 (configured
fill probability was ignored).

Both defects made paper results look *better* than reality, which is the
dangerous direction: a strategy that validates in paper and then fails
live is worse than one that never validated at all.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest

from prediction_arb.core.clock import utcnow
from prediction_arb.core.types import (
    Mode,
    OrderAction,
    OrderRequest,
    OrderType,
    Side,
)
from prediction_arb.execution.simulated import PaperExecutionEngine
from prediction_arb.orderbooks.aggregator import OrderBookAggregator

from .conftest import make_book


def _flat_fee_calc():
    """A flat $0.01 taker fee so the tests isolate fill behaviour."""
    from prediction_arb.fees.calculator import FeeCalculator
    return FeeCalculator.from_config({"schedules": [{
        "venue": "kalshi", "version": "t", "effective_date": "2020-01-01",
        "verified_at": "2026-09-13", "verification_status": "VERIFIED",
        "documentation_tested": True, "source": "test fixture",
        "trade_calculation_status": "COMPLETE",
        "settlement_calculation_status": "COMPLETE",
        "fees": [
            {"fee_type": "test_fixed", "fixed_amount": "0.01",
             "applies_to": "taker"},
            {"fee_type": "settlement", "fixed_amount": "0",
             "applies_to": "settlement"},
        ]}]})


def _buy(qty, limit, cid="KX"):
    return OrderRequest(
        venue="kalshi", contract_id=cid, side=Side.YES,
        action=OrderAction.BUY, quantity=Decimal(qty),
        order_type=OrderType.LIMIT, limit_price=Decimal(limit),
        client_order_id="c1", mode=Mode.PAPER)


class TestFillProbabilityAtTouch:
    """D12: `simulated_fill_probability_at_touch` was read, stored, and
    never consulted — every paper fill was 100% at the touch."""

    def _engine(self, books, prob):
        return PaperExecutionEngine(
            {"kalshi": Decimal("10000")}, _flat_fee_calc(),
            lambda v, c: books.get((v, c)),
            fill_probability_at_touch=prob, simulated_latency_ms=0)

    def test_touch_fill_is_scaled_by_the_configured_probability(self):
        book = make_book("kalshi", "KX", asks=[("0.40", "100")])
        books = {("kalshi", "KX"): book}
        for prob, expected in ((1.0, "100"), (0.9, "90"), (0.5, "50")):
            res = asyncio.run(
                self._engine(books, prob).submit_order(_buy("100", "0.40")))
            assert res.filled_quantity == Decimal(expected), prob
        # below 1.0 the order no longer claims to be fully filled
        res = asyncio.run(
            self._engine(books, 0.9).submit_order(_buy("100", "0.40")))
        assert res.status.value == "PARTIALLY_FILLED"

    def test_only_the_touch_level_is_scaled_not_deeper_liquidity(self):
        """Levels consumed beyond the touch are taker executions against
        displayed liquidity — scaling those would understate fills."""
        book = make_book("kalshi", "KX",
                         asks=[("0.40", "100"), ("0.50", "50")])
        books = {("kalshi", "KX"): book}
        res = asyncio.run(self._engine(books, 0.9).submit_order(
            _buy("120", "0.50")))
        # 100 at the touch * 0.9 = 90, plus all 20 of the deeper level
        assert res.filled_quantity == Decimal("110")

    def test_probability_rounds_down_never_up(self):
        """Rounding a fill up would reintroduce the exact optimism this
        fix removes."""
        book = make_book("kalshi", "KX", asks=[("0.40", "3")])
        books = {("kalshi", "KX"): book}
        res = asyncio.run(
            self._engine(books, 0.9).submit_order(_buy("3", "0.40")))
        # 3 * 0.9 = 2.7 -> 2, not 3
        assert res.filled_quantity == Decimal("2")

    def test_probability_one_is_a_no_op(self):
        """Historical replay must stay deterministic and unfaded."""
        book = make_book("kalshi", "KX", asks=[("0.40", "100")])
        books = {("kalshi", "KX"): book}
        res = asyncio.run(
            self._engine(books, 1.0).submit_order(_buy("100", "0.40")))
        assert res.filled_quantity == Decimal("100")
        assert res.status.value == "FILLED"

    def test_probability_zero_rejects_rather_than_inventing_a_fill(self):
        book = make_book("kalshi", "KX", asks=[("0.40", "100")])
        books = {("kalshi", "KX"): book}
        res = asyncio.run(
            self._engine(books, 0.0).submit_order(_buy("100", "0.40")))
        assert res.status.value == "REJECTED"
        assert res.filled_quantity == Decimal("0")

    def test_paper_yaml_value_is_actually_read(self):
        """The regression guard: the shipped config must reach the engine.
        Reverting the fix (ignoring the stored probability) fails this."""
        import yaml
        from pathlib import Path
        from prediction_arb.core.paths import config_file
        cfg = yaml.safe_load(config_file("paper.yaml").read_text())
        assert cfg["simulated_fill_probability_at_touch"] == 0.9

        book = make_book("kalshi", "KX", asks=[("0.40", "100")])
        books = {("kalshi", "KX"): book}
        res = asyncio.run(self._engine(
            books, float(cfg["simulated_fill_probability_at_touch"])
        ).submit_order(_buy("100", "0.40")))
        assert res.filled_quantity == Decimal("90")


class TestPaperBookFreshness:
    """D11: paper applied no freshness gate, so a book could age forever
    and still be scanned and traded against."""

    def test_paper_engine_builds_an_aggregator_from_config(self):
        from prediction_arb.modes.paper.engine import _book_age_ms
        # takes the STRICTER of the execution and arbitrage bounds
        assert _book_age_ms({
            "execution": {"max_book_age_ms": 1000},
            "arbitrage": {"maximum_book_age_ms": 400}}) == 400.0
        assert _book_age_ms({
            "execution": {"max_book_age_ms": 250},
            "arbitrage": {"maximum_book_age_ms": 1000}}) == 250.0
        # no config -> documented default, not 0 and not infinity
        assert _book_age_ms({}) == 1000.0

    def test_stale_book_is_hidden_from_the_execution_lookup(self):
        agg = OrderBookAggregator(max_staleness_ms=1000)
        fresh = make_book("kalshi", "KX", asks=[("0.40", "10")])
        old = make_book("kalshi", "FX", asks=[("0.40", "10")],
                        age_seconds=5)
        asyncio.run(agg.update(fresh))
        asyncio.run(agg.update(old))

        assert agg.peek("kalshi", "KX") is fresh
        assert agg.peek("kalshi", "FX") is None, "stale book must be hidden"
        # the raw store still holds it — it is excluded, not deleted
        assert ("kalshi", "FX") in asyncio.run(agg.snapshot())

    def test_fresh_snapshot_excludes_stale_and_stale_keys_reports_them(self):
        agg = OrderBookAggregator(max_staleness_ms=1000)
        asyncio.run(agg.update(make_book("kalshi", "KX",
                                         asks=[("0.40", "10")])))
        asyncio.run(agg.update(make_book("kalshi", "FX",
                                         asks=[("0.40", "10")],
                                         age_seconds=5)))
        fresh = asyncio.run(agg.fresh_snapshot())
        assert list(fresh) == [("kalshi", "KX")]
        assert asyncio.run(agg.stale_keys()) == [("kalshi", "FX")]

    def test_execution_refuses_to_fill_against_a_stale_book(self):
        """The scan rejecting a stale book is not enough — the simulator
        reads books independently, so it must gate too."""
        agg = OrderBookAggregator(max_staleness_ms=1000)
        asyncio.run(agg.update(make_book("kalshi", "KX",
                                         asks=[("0.40", "100")],
                                         age_seconds=5)))
        engine = PaperExecutionEngine(
            {"kalshi": Decimal("10000")}, _flat_fee_calc(),
            lambda v, c: agg.peek(v, c),
            fill_probability_at_touch=1.0, simulated_latency_ms=0)
        res = asyncio.run(engine.submit_order(_buy("100", "0.40")))
        assert res.status.value == "REJECTED"
        assert "no order book available" in (res.message or "")

    def test_a_book_with_no_timestamp_is_stale_not_assumed_fresh(self):
        """Unknown age must fail closed, exactly like UNKNOWN elsewhere."""
        agg = OrderBookAggregator(max_staleness_ms=1000)
        book = make_book("kalshi", "KX", asks=[("0.40", "10")])
        book.timestamp = None
        asyncio.run(agg.update(book))
        assert agg.peek("kalshi", "KX") is None
        assert asyncio.run(agg.stale_keys()) == [("kalshi", "KX")]


class TestPaperFreshnessEndToEnd:
    """Pins the gate *inside the paper engine*, not just the aggregator.

    Added after a mutation check showed that reverting the engine to read
    the raw store (``snapshot()`` instead of ``fresh_snapshot()``) failed
    nothing — testing a helper is not testing that it is used.
    """

    def _engine(self, tmp_path, adapters, complete_fee_calc, age_ms=1000):
        from prediction_arb.core.config import AppConfig
        from prediction_arb.database.repository import Repository
        from prediction_arb.matching.llm_matcher import LLMMatcher
        from prediction_arb.modes.paper.engine import PaperEngine
        from rich.console import Console
        config = AppConfig(
            mode=Mode.PAPER,
            settings={
                "arbitrage": {"minimum_net_profit": 2,
                              "minimum_net_return": 0.005,
                              "maximum_settlement_delay_days": 5},
                "execution": {"max_book_age_ms": age_ms},
                "scanner": {"poll_interval_seconds": 10,
                            "max_markets_per_venue": 50},
                "compliance": {"jurisdiction": "US-UT"},
                "cost_of_capital": {"annual_rate": 0.05},
            },
            venues={}, fees={}, risk_limits={},
            mode_config={"mode": "paper",
                         "execution_engine": "PaperExecutionEngine",
                         "database": "prediction_arb_paper",
                         "allow_live_orders": False})
        repo = Repository(tmp_path / "paper.sqlite3", Mode.PAPER)
        return PaperEngine(config, adapters, repo, complete_fee_calc,
                           LLMMatcher(client=None), Console())

    def test_stale_polled_book_never_reaches_the_scan(
            self, tmp_path, complete_fee_calc, monkeypatch):
        from .test_paper_streaming import FakeAdapter
        from .conftest import make_contract
        from prediction_arb.modes.paper import engine as paper_mod

        k = make_contract(venue="kalshi", contract_id="KXTEST-1")
        kalshi = FakeAdapter("kalshi", [k], streaming=False, credentials=False)
        # every book this venue returns is 5s old — far past the 1000ms gate
        kalshi.get_order_book = lambda mid: _async(
            make_book("kalshi", mid, asks=[("0.50", "10")],
                      bids=[("0.48", "10")], age_seconds=5))
        engine = self._engine(tmp_path, {"kalshi": kalshi}, complete_fee_calc)

        seen: list[dict] = []

        async def capture(contracts, books, approved, fee_calc, mode, **kw):
            seen.append(dict(books))
            return []
        monkeypatch.setattr(paper_mod, "run_arbitrage_scan", capture)

        asyncio.run(engine.run(cycles=1))

        assert seen, "the scan ran"
        # the book WAS fetched (it is in the raw store) ...
        assert ("kalshi", "KXTEST-1") in asyncio.run(engine.aggregator.snapshot())
        # THE gate: ... but a 5s-old book must not reach the scan
        assert ("kalshi", "KXTEST-1") not in seen[0]

    def test_fresh_polled_book_does_reach_the_scan(
            self, tmp_path, complete_fee_calc, monkeypatch):
        """Guard against the gate being so tight it starves paper mode:
        a book that is actually fresh must still be scanned."""
        from .test_paper_streaming import FakeAdapter
        from .conftest import make_contract
        from prediction_arb.modes.paper import engine as paper_mod

        k = make_contract(venue="kalshi", contract_id="KXTEST-1")
        kalshi = FakeAdapter("kalshi", [k], streaming=False, credentials=False)
        engine = self._engine(tmp_path, {"kalshi": kalshi}, complete_fee_calc)

        seen: list[dict] = []

        async def capture(contracts, books, approved, fee_calc, mode, **kw):
            seen.append(dict(books))
            return []
        monkeypatch.setattr(paper_mod, "run_arbitrage_scan", capture)

        asyncio.run(engine.run(cycles=1))

        assert seen and ("kalshi", "KXTEST-1") in seen[0]

    def test_stale_books_are_reported_not_silently_dropped(
            self, tmp_path, complete_fee_calc, capsys):
        """A scan that quietly sees nothing is indistinguishable from a
        quiet market, so exclusions must be surfaced."""
        from .test_paper_streaming import FakeAdapter
        from .conftest import make_contract

        k = make_contract(venue="kalshi", contract_id="KXTEST-1")
        kalshi = FakeAdapter("kalshi", [k], streaming=False, credentials=False)
        kalshi.get_order_book = lambda mid: _async(
            make_book("kalshi", mid, asks=[("0.50", "10")],
                      bids=[("0.48", "10")], age_seconds=5))
        engine = self._engine(tmp_path, {"kalshi": kalshi}, complete_fee_calc)
        asyncio.run(engine.run(cycles=1))

        out = capsys.readouterr().out
        assert "older than" in out and "excluded" in out


def _async(value):
    async def _c(*_a, **_k):
        return value
    return _c()
