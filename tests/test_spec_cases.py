"""The ten mandatory test cases from spec §75."""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from prediction_arb.core.types import (
    Independence,
    Mode,
    OpportunityStatus,
    OrderStatus,
)
from prediction_arb.arbitrage.two_leg import evaluate_two_leg
from prediction_arb.provenance.independence import is_independent

from .conftest import make_book, make_contract


def _kalshi_forecastex_pair():
    yes_c = make_contract(venue="kalshi", contract_id="KXFED-26SEP-T4")
    no_c = make_contract(venue="forecast_ex", contract_id="FED26SEP")
    return yes_c, no_c


class TestCase1FeesEatGross:
    """YES=$0.45 + NO=$0.54 → gross $0.01/contract; Kalshi quadratic fees
    exceed that → NO TRADE."""

    def test_no_trade_when_fees_exceed_gross(self, complete_fee_calc):
        yes_c, no_c = _kalshi_forecastex_pair()
        yes_book = make_book("kalshi", yes_c.contract_id, asks=[("0.45", "100")])
        no_book = make_book("forecast_ex", no_c.contract_id, asks=[("0.54", "100")])
        op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, complete_fee_calc,
                              Mode.PAPER,
                              independence=Independence.INDEPENDENT)
        assert op is not None
        # gross = $1.00/contract; fees: kalshi taker on 0.45 ≈ 1.74 for 100
        # + forecastex 0.01*100 = 1.00 → fees $2.74 > gross $1.00
        assert op.net_profit <= 0
        assert op.status == OpportunityStatus.BLOCK


class TestCase2ExecutableQuantityOnly:
    """Only 5 YES available; request 100 → engine sizes to 5."""

    def test_sizes_to_displayed_liquidity(self, complete_fee_calc):
        yes_c, no_c = _kalshi_forecastex_pair()
        yes_book = make_book("kalshi", yes_c.contract_id, asks=[("0.40", "5")])
        no_book = make_book("forecast_ex", no_c.contract_id, asks=[("0.50", "1000")])
        op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, complete_fee_calc,
                              Mode.PAPER,
                              independence=Independence.INDEPENDENT,
                              max_quantity=Decimal("100"))
        assert op is not None
        assert op.quantity == Decimal("5")


class TestCase3SameUnderlyingExchange:
    """Two apps, same underlying exchange → NO ARBITRAGE."""

    def test_webull_kalshi_same_venue(self):
        assert is_independent("kalshi", "webull") in (
            Independence.SAME_VENUE, Independence.LIKELY_SAME_VENUE)

    def test_opportunity_blocked(self, complete_fee_calc):
        yes_c = make_contract(venue="kalshi", contract_id="K1")
        no_c = make_contract(venue="webull", contract_id="K1")
        yes_book = make_book("kalshi", "K1", asks=[("0.40", "100")])
        no_book = make_book("webull", "K1", asks=[("0.50", "100")])
        op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, complete_fee_calc,
                              Mode.PAPER)
        assert op is None or op.status == OpportunityStatus.BLOCK


class TestCase4UnknownRelationship:
    """Unknown venue relationship → BLOCK."""

    def test_unknown_is_never_independent(self):
        assert is_independent("kalshi", "mystery_venue") == Independence.UNKNOWN

    def test_unknown_blocks_trade(self, complete_fee_calc):
        yes_c, no_c = _kalshi_forecastex_pair()
        yes_book = make_book("kalshi", yes_c.contract_id, asks=[("0.40", "100")])
        no_book = make_book("forecast_ex", no_c.contract_id, asks=[("0.50", "100")])
        op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, complete_fee_calc,
                              Mode.PAPER, independence=Independence.UNKNOWN)
        assert op is not None
        assert op.status == OpportunityStatus.BLOCK


class TestCase5LLMOverruled:
    """LLM says match, but settlement source differs → REJECT."""

    def test_deterministic_filter_wins(self):
        from prediction_arb.matching.deterministic_matcher import (
            deterministic_match,
        )
        from prediction_arb.matching.llm_matcher import LLMMatchResult
        from prediction_arb.matching.rules_validator import validate_match

        a = make_contract(venue="kalshi", resolution_source="BLS CPI report")
        b = make_contract(venue="forecast_ex", resolution_source="")
        det = deterministic_match(a, b)
        assert not det.passed
        llm = LLMMatchResult(match=True, confidence=0.999,
                             reasoning_summary="looks the same",
                             valid=True)
        outcome = validate_match(a, b, det, llm)
        assert outcome.status == "REJECTED"


class TestCase6FeesPlusSlippageExceedSpread:
    """Displayed 2% edge, real costs 2.2% → NO TRADE."""

    def test_walked_costs_kill_thin_edge(self, complete_fee_calc):
        yes_c, no_c = _kalshi_forecastex_pair()
        # displayed best prices sum to 0.98 (2% edge) but only 1 contract
        # at touch; depth is at prices that erase the edge
        yes_book = make_book("kalshi", yes_c.contract_id,
                             asks=[("0.44", "1"), ("0.49", "99")])
        no_book = make_book("forecast_ex", no_c.contract_id,
                            asks=[("0.54", "1"), ("0.56", "99")])
        op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, complete_fee_calc,
                              Mode.PAPER,
                              independence=Independence.INDEPENDENT,
                              max_quantity=Decimal("100"))
        # only the 1-lot at the touch has positive marginal edge; after
        # fees the net is negative → BLOCK (or None if no sizing possible)
        if op is not None:
            assert op.quantity <= 1
            assert op.status in (OpportunityStatus.BLOCK,
                                 OpportunityStatus.WATCH)
            assert op.net_profit < Decimal("2.00")


class TestCase7PartialFillRecovery:
    """One leg fills, second doesn't → predefined recovery runs."""

    def test_partial_fill_handler_runs(self, complete_fee_calc):
        from prediction_arb.execution.coordinator import ExecutionCoordinator
        from prediction_arb.execution.partial_fills import PartialFillHandler
        from prediction_arb.execution.simulated import PaperExecutionEngine
        from prediction_arb.monitoring.alerts import ConsoleAlertSink
        from prediction_arb.risk.exposure import ExposureTracker
        from prediction_arb.risk.limits import RiskLimits

        yes_c, no_c = _kalshi_forecastex_pair()
        yes_book = make_book("kalshi", yes_c.contract_id,
                             asks=[("0.40", "50")],
                             bids=[("0.38", "100")])
        # NO leg has NO liquidity → leg B will fail
        no_book = make_book("forecast_ex", no_c.contract_id, asks=[])
        books = {("kalshi", yes_c.contract_id): yes_book,
                 ("forecast_ex", no_c.contract_id): no_book}

        engine = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("1000"),
                           "forecast_ex": Decimal("1000")},
            fee_calc=complete_fee_calc,
            book_lookup=lambda v, c: books.get((v, c)))

        limits = RiskLimits.from_config({})
        handler = PartialFillHandler(limits, ConsoleAlertSink())
        coord = ExecutionCoordinator(engine, ExposureTracker(), handler,
                                     Mode.PAPER)

        op = evaluate_two_leg(
            yes_c, no_c, yes_book,
            make_book("forecast_ex", no_c.contract_id, asks=[("0.50", "50")]),
            complete_fee_calc, Mode.PAPER,
            independence=Independence.INDEPENDENT,
            max_quantity=Decimal("20"),
            minimum_net_profit=Decimal("0.01"),
            minimum_net_return=Decimal("0.0001"))
        assert op is not None and op.status == OpportunityStatus.TRADE

        report = asyncio.run(coord.execute(op))
        # leg B had no book at execution time → recovery attempted
        assert not report.success or any("unwound" in n for n in report.notes)
        assert len(report.legs) >= 1
        # never silent: notes must explain what happened
        assert report.notes


class TestCase8CrashRecovery:
    """Crash after leg A → restart detects and reconciles intent."""

    def test_unreconciled_intents_found(self, tmp_path):
        from prediction_arb.database.repository import Repository

        repo = Repository(tmp_path / "t.sqlite3", Mode.PAPER)
        repo.write_trade_intent(
            trade_id="t1", opportunity_id="op1", venue="kalshi",
            client_order_id="coid-1", contract_id="K1", side="yes",
            action="buy", quantity="10", requested_price="0.40")
        rows = repo.unreconciled_trades()
        assert len(rows) == 1
        assert rows[0].status == OrderStatus.PENDING.value
        repo.update_trade_status("coid-1", OrderStatus.FILLED,
                                 filled_quantity="10")
        assert repo.unreconciled_trades() == []


class TestCase9SettlementDelayCost:
    """T+0 vs T+3 settlement difference must enter profitability."""

    def test_delay_cost_positive(self):
        from prediction_arb.normalization.settlement import (
            settlement_delay_cost,
        )
        cost = settlement_delay_cost(Decimal("1000"), Decimal("3"),
                                     Decimal("0.05"))
        assert cost is not None and cost > 0
        # unknown lockup returns None (blocking), never zero
        assert settlement_delay_cost(Decimal("1000"), None,
                                     Decimal("0.05")) is None


class TestCase10ModeConfigMismatch:
    """mode=paper + LiveExecutionEngine → refuse to start."""

    def test_startup_refused(self):
        from prediction_arb.core.config import validate_mode_consistency
        from prediction_arb.core.types import ModeIsolationError

        bad = {"mode": "paper", "execution_engine": "LiveExecutionEngine",
               "database": "prediction_arb_paper",
               "allow_live_orders": False}
        with pytest.raises(ModeIsolationError):
            validate_mode_consistency(Mode.PAPER, bad)

    def test_allow_live_orders_in_paper_refused(self):
        from prediction_arb.core.config import validate_mode_consistency
        from prediction_arb.core.types import ModeIsolationError

        bad = {"mode": "paper", "execution_engine": "PaperExecutionEngine",
               "database": "prediction_arb_paper",
               "allow_live_orders": True}
        with pytest.raises(ModeIsolationError):
            validate_mode_consistency(Mode.PAPER, bad)
