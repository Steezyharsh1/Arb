"""Failure tests (§74): stale book, missing fee data, unknown venue,
insufficient sim cash, empty books, historical no-data honesty."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from prediction_arb.core.types import FeeDataError, Independence, Mode
from .conftest import make_book, make_contract


class TestStaleBook:
    def test_aggregator_hides_stale_books(self):
        from prediction_arb.orderbooks.aggregator import OrderBookAggregator

        async def run():
            agg = OrderBookAggregator(max_staleness_ms=1000)
            stale = make_book("kalshi", "X", asks=[("0.5", "10")],
                              age_seconds=10)
            await agg.update(stale)
            assert await agg.get("kalshi", "X") is None
            assert await agg.get("kalshi", "X",
                                 require_fresh=False) is not None
        asyncio.run(run())

    def test_out_of_order_sequence_ignored(self):
        from prediction_arb.orderbooks.aggregator import OrderBookAggregator

        async def run():
            agg = OrderBookAggregator()
            b1 = make_book("kalshi", "X", asks=[("0.5", "10")])
            b1.sequence_number = 5
            b2 = make_book("kalshi", "X", asks=[("0.6", "10")])
            b2.sequence_number = 4
            await agg.update(b1)
            await agg.update(b2)
            book = await agg.get("kalshi", "X")
            assert book.asks[0].price == Decimal("0.5")
        asyncio.run(run())


class TestMissingFeeData:
    def test_opportunity_blocked_not_zero(self, fee_calc):
        """Venue with no fee schedule → FEE_DATA_INCOMPLETE block, never
        silently zero fees."""
        yes_c = make_contract(venue="kalshi", contract_id="Y")
        no_c = make_contract(venue="forecast_ex", contract_id="N")
        # remove forecast_ex schedule
        import copy
        crippled = copy.copy(fee_calc)
        crippled.schedules = {"kalshi": fee_calc.schedules["kalshi"]}
        from prediction_arb.arbitrage.two_leg import evaluate_two_leg
        op = evaluate_two_leg(
            yes_c, no_c,
            make_book("kalshi", "Y", asks=[("0.40", "100")]),
            make_book("forecast_ex", "N", asks=[("0.50", "100")]),
            crippled, Mode.PAPER, independence=Independence.INDEPENDENT)
        assert op is not None
        assert op.status.value == "BLOCK"
        assert any("FEE_DATA_INCOMPLETE" in r for r in op.block_reasons)

    def test_stale_fee_schedule_strict(self, fee_calc):
        with pytest.raises(FeeDataError):
            # verified_at 2026-08-30, but 0-day tolerance in strict mode
            fee_calc.max_schedule_age_days = -1
            fee_calc.trade_fee("kalshi", Decimal("0.5"), Decimal("10"),
                               strict_freshness=True)


class TestSimulationEdgeCases:
    def test_context_incomplete_fee_rejects_simulated_order(self, fee_calc):
        import uuid
        from prediction_arb.core.types import (
            OrderAction, OrderRequest, OrderType, Side,
        )
        from prediction_arb.execution.simulated import PaperExecutionEngine
        eng = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("100")}, fee_calc=fee_calc,
            book_lookup=lambda v, c: make_book(
                "kalshi", "K1", asks=[("0.50", "10")]))
        req = OrderRequest(
            venue="kalshi", contract_id="K1", side=Side.YES,
            action=OrderAction.BUY, quantity=Decimal("1"),
            order_type=OrderType.LIMIT, limit_price=Decimal("0.50"),
            client_order_id=str(uuid.uuid4()), mode=Mode.PAPER)
        result = asyncio.run(eng.submit_order(req))
        assert result.status.value == "REJECTED"
        assert "FEE_DATA_INCOMPLETE" in result.message

    def test_insufficient_cash_rejected(self, complete_fee_calc):
        import uuid
        from prediction_arb.core.types import (
            OrderAction, OrderRequest, OrderType, Side,
        )
        from prediction_arb.execution.simulated import PaperExecutionEngine
        eng = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("1")}, fee_calc=complete_fee_calc,
            book_lookup=lambda v, c: make_book("kalshi", "K1",
                                               asks=[("0.50", "100")]))
        req = OrderRequest(venue="kalshi", contract_id="K1", side=Side.YES,
                           action=OrderAction.BUY, quantity=Decimal("100"),
                           order_type=OrderType.LIMIT,
                           limit_price=Decimal("0.50"),
                           client_order_id=str(uuid.uuid4()),
                           mode=Mode.PAPER)
        res = asyncio.run(eng.submit_order(req))
        assert res.status.value == "REJECTED"
        assert "insufficient" in res.message

    def test_limit_price_respected(self, complete_fee_calc):
        import uuid
        from prediction_arb.core.types import (
            OrderAction, OrderRequest, OrderType, Side,
        )
        from prediction_arb.execution.simulated import PaperExecutionEngine
        book = make_book("kalshi", "K1",
                         asks=[("0.40", "10"), ("0.60", "100")])
        eng = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("1000")}, fee_calc=complete_fee_calc,
            book_lookup=lambda v, c: book)
        req = OrderRequest(venue="kalshi", contract_id="K1", side=Side.YES,
                           action=OrderAction.BUY, quantity=Decimal("50"),
                           order_type=OrderType.LIMIT,
                           limit_price=Decimal("0.45"),
                           client_order_id=str(uuid.uuid4()),
                           mode=Mode.PAPER)
        res = asyncio.run(eng.submit_order(req))
        # only the 10 @ 0.40 are at-or-better than 0.45
        assert res.filled_quantity == Decimal("10")


class TestHistoricalHonesty:
    def test_no_data_returns_empty_never_fabricates(self, tmp_path):
        from prediction_arb.database.repository import Repository
        from prediction_arb.modes.historical.data_provider import (
            HistoricalDataProvider,
        )
        repo = Repository(tmp_path / "h.sqlite3", Mode.HISTORICAL)
        frames = HistoricalDataProvider(repo).load_frames(None, None)
        assert frames == []

    def test_backtest_empty_data_reports_empty_never_fabricates(self, tmp_path):
        """The property the old NotImplementedError stub protected: with no
        recorded data the backtest returns an EMPTY result — it never
        invents frames, opportunities, or an equity curve (§92)."""
        from prediction_arb.backtesting.engine import BacktestEngine
        from prediction_arb.database.repository import Repository
        repo = Repository(tmp_path / "h2.sqlite3", Mode.HISTORICAL)
        engine = BacktestEngine(repo, fee_calc=_fee_calc())
        inv = engine.data_inventory()
        assert inv.frames_available == 0
        result = asyncio.run(engine.run_backtest())
        assert result.frames_available == 0
        assert result.first_frame is None and result.last_frame is None
        assert result.opportunities_observed == 0
        assert result.theoretical_net.points == []
        assert result.theoretical_net.net_edge_sum_known_subset is None
        assert result.simulation["net_simulated_pnl"] is None
        assert "modelled_equity" not in result.to_dict()

    def test_backtest_refuses_to_run_without_fee_data(self, tmp_path):
        """Cost is what separates a real trade from a fantasy one, so a
        backtest with no fee calculator refuses rather than reporting
        profit it could not have priced (§92)."""
        from prediction_arb.backtesting.engine import BacktestEngine
        from prediction_arb.database.repository import Repository
        repo = Repository(tmp_path / "h3.sqlite3", Mode.HISTORICAL)
        engine = BacktestEngine(repo)  # no fee_calc
        with pytest.raises(ValueError, match="FeeCalculator"):
            asyncio.run(engine.run_backtest())


def _fee_calc():
    """A real calculator from the checked-in schedules (never fabricated)."""
    from prediction_arb.core.config import load_config
    from prediction_arb.fees.venue_fees import load_fee_calculator
    config = load_config("historical")
    return load_fee_calculator(config.fees, 30)


class TestCapitalConstraints:
    def test_capital_not_fungible(self):
        from prediction_arb.risk.capital import (
            VenueCapital, max_quantity_for_capital,
        )
        cap = {"kalshi": VenueCapital("kalshi", Decimal("100")),
               "forecast_ex": VenueCapital("forecast_ex", Decimal("10"))}
        q = max_quantity_for_capital(
            {"kalshi": Decimal("0.40"), "forecast_ex": Decimal("0.50")},
            cap)
        # forecast_ex constrains: 10/0.50 = 20, despite kalshi's 250
        assert q == Decimal("20")

    def test_unknown_balance_blocks(self):
        from prediction_arb.risk.capital import (
            VenueCapital, max_quantity_for_capital,
        )
        cap = {"kalshi": VenueCapital("kalshi", Decimal("100"))}
        q = max_quantity_for_capital(
            {"kalshi": Decimal("0.40"), "forecast_ex": Decimal("0.50")},
            cap)
        assert q == Decimal("0")

    def test_rebalance_advice_is_advisory(self):
        from prediction_arb.risk.capital import (
            VenueCapital, rebalancing_advice,
        )
        cap = {"kalshi": VenueCapital("kalshi", Decimal("1000")),
               "forecast_ex": VenueCapital("forecast_ex", Decimal("0"))}
        advice = rebalancing_advice(cap, {"forecast_ex": Decimal("200"),
                                          "kalshi": Decimal("0")})
        assert len(advice) == 1
        assert "ADVISORY ONLY" in advice[0].reason
