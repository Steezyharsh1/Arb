"""Offline integration proof for one synthetic two-venue opportunity.

The test composes production matching, eligibility, book validation/freshness,
arbitrage scanning, fee/settlement costing, risk coordination, and the paper
simulator. Its LLM verdict, fee schedules, ForecastEx settlement timing,
contracts, and books are explicitly TEST-ONLY fixtures. Nothing here is venue,
account, fee, settlement, legal, or live-opportunity evidence.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

import pytest

from prediction_arb.compliance.eligibility import check_contract_eligibility
from prediction_arb.core.clock import utcnow
from prediction_arb.core.types import (
    Independence,
    Mode,
    NormalizedContract,
    Opportunity,
    OpportunityStatus,
    OrderBook,
    OrderStatus,
    PriceLevel,
)
from prediction_arb.execution.coordinator import ExecutionCoordinator
from prediction_arb.execution.partial_fills import PartialFillHandler
from prediction_arb.execution.simulated import PaperExecutionEngine
from prediction_arb.fees.calculator import FeeCalculator
from prediction_arb.matching.llm_matcher import LLMMatcher
from prediction_arb.modes.shared import ScanResult, run_arbitrage_scan, run_matching
from prediction_arb.normalization.settlement import (
    PRODUCTION_SETTLEMENT_TIMING,
    assess_lockup,
)
from prediction_arb.orderbooks.aggregator import OrderBookAggregator
from prediction_arb.orderbooks.orderbook import walk_buy
from prediction_arb.orderbooks.validation import order_book_problems
from prediction_arb.provenance.independence import contracts_independent
from prediction_arb.risk import kill_switch
from prediction_arb.risk.exposure import ExposureTracker
from prediction_arb.risk.limits import RiskLimits, RiskState

from .conftest import (
    SETTLE,
    SETTLEMENT_TEST_NOW,
    fresh_verification_date,
    make_contract,
)


# The wording is deliberately different but shares enough recall-oriented
# blocking evidence for the real candidate generator to propose the pair.
_KALSHI_EVENT = (
    "Will the Federal Reserve hold its target range at 4.25-4.50 percent "
    "in September 2026?"
)
_FORECASTEX_EVENT = "Fed keeps rates at 4.25% to 4.5% at the September meeting?"


@dataclass(frozen=True)
class SyntheticCase:
    yes_contract: NormalizedContract
    no_contract: NormalizedContract
    llm_prompts: list[str]
    approved_pairs: dict[tuple[str, str], tuple[str, float]]
    matching_result: ScanResult
    raw_books: dict[tuple[str, str], OrderBook]
    fresh_books: dict[tuple[str, str], OrderBook]
    opportunity: Opportunity


class _QuietAlerts:
    """No-op alert sink for deterministic coordinator tests."""

    async def alert(self, title: str, body: str) -> None:
        return None


class _RecordingPaperExecutionEngine(PaperExecutionEngine):
    """Record requests while retaining the production simulator behavior."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []

    async def submit_order(self, request):
        self.requests.append(request)
        return await super().submit_order(request)


def _new_contract_pair(
        yes_venue: str = "kalshi",
        no_venue: str = "forecast_ex",
        *, payout_no: Decimal = Decimal("1.00"),
        yes_id: str = "K-FED-ARB12",
        no_id: str = "FX-FED-ARB12",
) -> tuple[NormalizedContract, NormalizedContract]:
    yes = make_contract(
        venue=yes_venue, contract_id=yes_id, event=_KALSHI_EVENT,
        settlement=SETTLE, payout=Decimal("1.00"),
        resolution_source="Federal Reserve decision and official statement",
        early_resolution="")
    no = make_contract(
        venue=no_venue, contract_id=no_id, event=_FORECASTEX_EVENT,
        settlement=SETTLE, payout=payout_no,
        resolution_source="Federal Reserve rate-decision announcement",
        early_resolution="")
    return yes, no


def _offline_llm() -> tuple[LLMMatcher, list[str]]:
    """A schema-valid, scripted match result; no API or network is used."""
    prompts: list[str] = []

    async def client(prompt: str) -> str:
        prompts.append(prompt)
        return json.dumps({
            "match": True,
            "confidence": 0.999,
            "reasoning_summary": (
                "TEST-ONLY: fixture asserts identical event, outcome, "
                "settlement, resolution, threshold, time, and payout"),
            "event_identity": True,
            "outcome_identity": True,
            "settlement_identity": True,
            "resolution_identity": True,
            "threshold_identity": True,
            "time_identity": True,
            "payout_identity": True,
            "ambiguities": [],
        })

    return LLMMatcher(client=client, model_version="synthetic-fixture-v1"), prompts


def _synthetic_fee_calculator() -> FeeCalculator:
    """Complete, deterministic TEST-ONLY schedules with distinct venue rules.

    Kalshi's fixture uses a per-fill quadratic taker charge rounded UP to cents
    and a fixed settlement charge. ForecastEx's fixture uses a per-contract
    trade charge and a different fixed settlement charge. These are not real
    venue fee claims and do not modify config/fees.yaml.
    """
    verified = fresh_verification_date()
    return FeeCalculator.from_config({
        "schedules": [
            {
                "venue": "kalshi",
                "version": "ARB12-test-only",
                "effective_date": "2020-01-01",
                "verified_at": verified,
                "verification_status": "VERIFIED",
                "documentation_tested": True,
                "source": "TEST-ONLY synthetic schedule; not venue evidence",
                "trade_calculation_status": "COMPLETE",
                "settlement_calculation_status": "COMPLETE",
                "rounding": "ceiling",
                "fees": [
                    {"fee_type": "synthetic_taker_quadratic",
                     "multiplier": "0.07", "applies_to": "taker"},
                    {"fee_type": "synthetic_settlement",
                     "fixed_amount": "0.02", "applies_to": "settlement"},
                ],
            },
            {
                "venue": "forecast_ex",
                "version": "ARB12-test-only",
                "effective_date": "2020-01-01",
                "verified_at": verified,
                "verification_status": "VERIFIED",
                "documentation_tested": True,
                "source": "TEST-ONLY synthetic schedule; not venue evidence",
                "trade_calculation_status": "COMPLETE",
                "settlement_calculation_status": "COMPLETE",
                "fees": [
                    {"fee_type": "synthetic_trade_per_contract",
                     "fixed_amount": "0.01", "applies_to": "taker"},
                    {"fee_type": "synthetic_settlement",
                     "fixed_amount": "0.03", "applies_to": "settlement"},
                ],
            },
        ]
    })


def _book(contract: NormalizedContract,
          asks: tuple[tuple[str, str], ...],
          bids: tuple[tuple[str, str], ...] = (("0.10", "100"),),
          *, timestamp=None) -> OrderBook:
    return OrderBook(
        venue=contract.venue,
        contract_id=contract.contract_id,
        bids=[PriceLevel(Decimal(price), Decimal(quantity))
              for price, quantity in bids],
        asks=[PriceLevel(Decimal(price), Decimal(quantity))
              for price, quantity in asks],
        timestamp=timestamp or utcnow(),
    )


def _books_for_pair(
        yes: NormalizedContract,
        no: NormalizedContract,
        *,
        yes_asks: tuple[tuple[str, str], ...] = (("0.20", "3"),
                                                 ("0.20", "5")),
        no_asks: tuple[tuple[str, str], ...] = (("0.30", "4"),
                                                ("0.30", "4")),
        timestamp=None,
) -> dict[tuple[str, str], OrderBook]:
    return {
        (yes.venue, yes.contract_id): _book(
            yes, yes_asks, (("0.18", "100"),), timestamp=timestamp),
        (no.venue, no.contract_id): _book(
            no, no_asks, (("0.28", "100"),), timestamp=timestamp),
    }


async def _aggregate_fresh_books(
        books: dict[tuple[str, str], OrderBook],
        *, max_staleness_ms: float = 1000.0,
        now=None,
) -> tuple[OrderBookAggregator,
           dict[tuple[str, str], OrderBook]]:
    aggregator = OrderBookAggregator(max_staleness_ms=max_staleness_ms)
    for book in books.values():
        assert order_book_problems(book) == []
        await aggregator.update(book)
    return aggregator, await aggregator.fresh_snapshot(now=now)


def _scan(contracts, books, approved, fee_calc, *, max_quantity="8",
          minimum_net_profit="2") -> list[Opportunity]:
    return asyncio.run(run_arbitrage_scan(
        contracts, books, approved, fee_calc, Mode.PAPER,
        minimum_net_profit=Decimal(minimum_net_profit),
        minimum_net_return=Decimal("0.005"),
        maximum_settlement_delay_days=Decimal("5"),
        cost_of_capital_annual=Decimal("0.05"),
        max_quantity=Decimal(max_quantity),
        evaluation_time=SETTLEMENT_TEST_NOW,
    ))


def _op_for_direction(ops: list[Opportunity], yes_venue: str,
                      no_venue: str) -> Opportunity:
    return next(op for op in ops
                if [leg.venue for leg in op.legs] == [yes_venue, no_venue])


def _explicit_risk_limits(*, max_capital_per_trade: str = "5.00") -> RiskLimits:
    """All paper limits are explicit; no RiskLimits defaults are relied on."""
    return RiskLimits(
        max_total_capital=Decimal("10.00"),
        max_capital_per_venue=Decimal("5.00"),
        max_capital_per_event=Decimal("5.00"),
        max_capital_per_trade=Decimal(max_capital_per_trade),
        max_unhedged_exposure=Decimal("5.00"),
        max_daily_loss=Decimal("1.00"),
        max_open_positions=2,
        max_concurrent_orders=2,
        max_unhedged_seconds=30,
        max_unhedged_contracts=8,
        max_unhedged_dollars=Decimal("5.00"),
        max_emergency_loss=Decimal("1.00"),
    )


def _coordinator(engine, *, limits=None, risk_state=None, exposure=None):
    selected_limits = limits or _explicit_risk_limits()
    tracker = exposure or ExposureTracker()
    state = risk_state or RiskState()
    coordinator = ExecutionCoordinator(
        engine=engine,
        exposure=tracker,
        partial_fill_handler=PartialFillHandler(
            selected_limits, _QuietAlerts(), max_retry_attempts=2,
            price_concession=Decimal("0.02")),
        mode=Mode.PAPER,
        limits=selected_limits,
        risk_state=state,
    )
    return coordinator, tracker, state


def _assert_simulation_only(engine: PaperExecutionEngine) -> None:
    assert engine.simulated is True
    assert engine.mode is Mode.PAPER
    # The paper engine has no adapter reference, adapters mapping, or real
    # placement method. The test creates no adapter or live executor.
    assert not hasattr(engine, "adapter")
    assert not hasattr(engine, "adapters")
    assert not hasattr(engine, "place_order")
    assert all(fill["simulated"] is True for fill in engine.state.fills)


@pytest.fixture(autouse=True)
def isolate_persistent_kill_switch(tmp_path, monkeypatch):
    """Any fault test can engage only a temporary, test-local kill file."""
    monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "TEST_KILL")


@pytest.fixture
def synthetic_fee_calc() -> FeeCalculator:
    return _synthetic_fee_calculator()


@pytest.fixture
def eligible_synthetic_case(synthetic_fee_calc) -> SyntheticCase:
    yes, no = _new_contract_pair()
    # Paper mode permits research on UNKNOWN eligibility, while live remains
    # blocked. Assert the exact gate result instead of implying legal approval.
    eligibility = [
        check_contract_eligibility(contract, "US-UT", Mode.PAPER)
        for contract in (yes, no)
    ]
    assert all(decision.allowed for decision in eligibility)
    assert all(decision.status.value == "UNKNOWN" for decision in eligibility)

    matcher, prompts = _offline_llm()
    approved, matching_result = asyncio.run(run_matching([yes, no], matcher))
    raw_books = _books_for_pair(yes, no)
    _aggregator, fresh_books = asyncio.run(_aggregate_fresh_books(raw_books))
    opportunities = _scan([yes, no], fresh_books, approved, synthetic_fee_calc)
    opportunity = _op_for_direction(opportunities, "kalshi", "forecast_ex")
    return SyntheticCase(
        yes_contract=yes,
        no_contract=no,
        llm_prompts=prompts,
        approved_pairs=approved,
        matching_result=matching_result,
        raw_books=raw_books,
        fresh_books=fresh_books,
        opportunity=opportunity,
    )


class TestSyntheticOpportunityEndToEnd:
    def test_approved_pair_walked_costs_and_paper_fills(
            self, eligible_synthetic_case, synthetic_fee_calc):
        case = eligible_synthetic_case
        yes, no, op = (case.yes_contract, case.no_contract,
                       case.opportunity)

        # Independent, route-qualified venues and compatible binary economics.
        assert contracts_independent(yes, no) is Independence.INDEPENDENT
        assert yes.outcome_type.value == no.outcome_type.value == "BINARY"
        assert yes.payout == no.payout == Decimal("1.00")
        assert yes.currency == no.currency == "USD"
        assert yes.settlement_time == no.settlement_time == SETTLE
        assert yes.resolution_method == no.resolution_method
        assert yes.cancellation_rules == no.cancellation_rules
        assert yes.early_resolution_rules == no.early_resolution_rules
        lockup = assess_lockup([yes, no], now=SETTLEMENT_TEST_NOW)
        assert lockup.lockup_days == Decimal("3")
        assert lockup.spread_days == Decimal("0")
        assert all(release.release_time == SETTLE
                   for release in lockup.releases)

        # Real production candidate/matching/certificate stages ran. The
        # scripted LLM response is synthetic but is validated by LLMMatcher.
        assert case.matching_result.pairs_considered == 1
        assert case.matching_result.pairs_deterministic_pass == 1
        assert len(case.llm_prompts) == 1
        cert = case.matching_result.certificates[0]
        assert cert.status == "APPROVED"
        assert cert.independence == Independence.INDEPENDENT.value
        assert all(cert.checks.values())
        assert case.approved_pairs
        assert op.match_id == cert.match_id

        # Every displayed level passes the production book validator and both
        # books passed the same production freshness aggregator used in paper.
        assert set(case.fresh_books) == set(case.raw_books)
        assert all(order_book_problems(book) == []
                   for book in case.fresh_books.values())
        yes_walk, no_walk = op.legs[0].walk, op.legs[1].walk
        assert yes_walk is not None and no_walk is not None
        assert [(level.price, level.quantity)
                for level in yes_walk.levels_consumed] == [
                    (Decimal("0.20"), Decimal("3")),
                    (Decimal("0.20"), Decimal("5")),
                ]
        assert [(level.price, level.quantity)
                for level in no_walk.levels_consumed] == [
                    (Decimal("0.30"), Decimal("4")),
                    (Decimal("0.30"), Decimal("4")),
                ]
        assert yes_walk.fully_filled and no_walk.fully_filled
        assert (op.legs[0].limit_price, op.legs[1].limit_price) == (
            yes_walk.weighted_average_price,
            no_walk.weighted_average_price,
        ) == (Decimal("0.20"), Decimal("0.30"))

        # Venue-specific synthetic fee rules are applied per walked fill. The
        # fee ledger order is: Kalshi trade, ForecastEx trade, Kalshi
        # settlement, ForecastEx settlement, capital-lockup cost.
        assert [component.name for component in op.costs.components] == [
            "kalshi:trade_fee", "forecast_ex:trade_fee",
            "kalshi:settlement_fee", "forecast_ex:settlement_fee",
            "settlement_delay",
        ]
        assert [component.amount for component in op.costs.components] == [
            Decimal("0.10"), Decimal("0.08"), Decimal("0.16"),
            Decimal("0.24"), Decimal("0.0019"),
        ]
        assert op.costs.complete
        assert op.gross_cost == Decimal("4.00")
        assert op.total_fees == Decimal("0.58")
        assert op.capital_required == Decimal("4.58")
        assert op.settlement_delay_cost == Decimal("0.0019")
        assert op.net_profit == Decimal("3.4181")
        assert op.status is OpportunityStatus.TRADE
        assert op.is_executable

        # Explicit paper starting cash and explicit risk caps, both sufficient
        # for the known per-venue capital requirement; no real adapter exists.
        books = case.fresh_books
        engine = _RecordingPaperExecutionEngine(
            starting_cash={
                "kalshi": Decimal("5.00"),
                "forecast_ex": Decimal("5.00"),
            },
            fee_calc=synthetic_fee_calc,
            book_lookup=lambda venue, contract_id: books.get(
                (venue, contract_id)),
            fill_probability_at_touch=1.0,
            simulated_latency_ms=0,
        )
        _assert_simulation_only(engine)
        coordinator, exposure, risk_state = _coordinator(engine)
        report = asyncio.run(coordinator.execute(op))

        assert report.success
        assert report.risk_violations == []
        assert len(report.legs) == len(engine.requests) == 2
        assert all(leg.result is not None
                   and leg.result.status is OrderStatus.FILLED
                   for leg in report.legs)
        assert [leg.result.filled_quantity for leg in report.legs] == [
            Decimal("8"), Decimal("8")]
        assert all(request.mode is Mode.PAPER for request in engine.requests)
        assert all(request.order_type.value == "limit"
                   and request.limit_price is not None
                   for request in engine.requests)
        assert [position["quantity"] for position
                in asyncio.run(engine.get_positions())] == ["8", "8"]
        assert asyncio.run(exposure.total_unhedged_dollars()) == Decimal("0")
        assert risk_state.deployed_total == Decimal("4.58")
        assert risk_state.deployed_by_venue == {
            "kalshi": Decimal("1.86"), "forecast_ex": Decimal("2.72")}
        assert risk_state.open_orders == 0
        assert engine.state.cash == {
            "kalshi": Decimal("3.30"), "forecast_ex": Decimal("2.52")}
        assert len(engine.state.fills) == 2
        assert not kill_switch.is_engaged()

    def test_mismatched_payout_is_deterministically_rejected(self,
                                                             synthetic_fee_calc):
        yes, no = _new_contract_pair(payout_no=Decimal("0.50"))
        matcher, prompts = _offline_llm()
        approved, result = asyncio.run(run_matching([yes, no], matcher))

        assert result.pairs_considered == 1  # candidate proposal is not approval
        assert result.pairs_deterministic_pass == 0
        assert not approved
        assert prompts == []  # deterministic reject never reaches the LLM
        from prediction_arb.matching.deterministic_matcher import deterministic_match
        det = deterministic_match(yes, no)
        assert not det.passed
        assert det.checks["payout"] is False

        books = _books_for_pair(yes, no)
        ops = _scan([yes, no], books, approved, synthetic_fee_calc)
        assert ops == []

    def test_same_venue_pair_stays_unapproved_and_scan_rechecks_provenance(
            self, synthetic_fee_calc):
        yes, no = _new_contract_pair("kalshi", "webull")
        assert contracts_independent(yes, no) is Independence.SAME_VENUE
        matcher, _prompts = _offline_llm()
        approved, result = asyncio.run(run_matching([yes, no], matcher))
        assert result.pairs_deterministic_pass == 1
        assert approved == {}
        assert result.certificates[0].status == "REVIEW"
        assert result.certificates[0].independence == Independence.SAME_VENUE.value

        # Even a forged approval tuple cannot pass optimizer.search_event's
        # independent-venue gate or reach the two-leg evaluator.
        forged = {("kalshi:K-FED-ARB12", "webull:FX-FED-ARB12"):
                  ("forged-test-certificate", 1.0)}
        ops = _scan([yes, no], _books_for_pair(yes, no), forged,
                    synthetic_fee_calc)
        assert ops == []

    def test_walked_prices_use_each_consumed_level_not_a_headline_average(
            self, synthetic_fee_calc):
        yes, no = _new_contract_pair()
        matcher, _ = _offline_llm()
        approved, result = asyncio.run(run_matching([yes, no], matcher))
        assert result.certificates[0].status == "APPROVED"
        books = _books_for_pair(
            yes, no,
            yes_asks=(("0.20", "3"), ("0.22", "5")),
            no_asks=(("0.30", "4"), ("0.32", "4")),
        )
        _aggregator, fresh = asyncio.run(_aggregate_fresh_books(books))
        op = _op_for_direction(
            _scan([yes, no], fresh, approved, synthetic_fee_calc),
            "kalshi", "forecast_ex")

        assert [(level.price, level.quantity)
                for level in op.legs[0].walk.levels_consumed] == [
                    (Decimal("0.20"), Decimal("3")),
                    (Decimal("0.22"), Decimal("5")),
                ]
        assert [(level.price, level.quantity)
                for level in op.legs[1].walk.levels_consumed] == [
                    (Decimal("0.30"), Decimal("4")),
                    (Decimal("0.32"), Decimal("4")),
                ]
        assert op.legs[0].limit_price == Decimal("0.2125")
        assert op.legs[1].limit_price == Decimal("0.31")
        assert op.gross_cost == Decimal("4.18")
        assert [component.amount for component in op.costs.components] == [
            Decimal("0.11"), Decimal("0.08"), Decimal("0.16"),
            Decimal("0.24"), Decimal("0.0020"),
        ]
        assert op.capital_required == Decimal("4.77")
        assert op.net_profit == Decimal("3.2280")

    def test_unknown_production_costs_block_without_fabricated_net_profit(
            self, eligible_synthetic_case, fee_calc,
            production_settlement_timing):
        case = eligible_synthetic_case
        ops = _scan([case.yes_contract, case.no_contract], case.fresh_books,
                    case.approved_pairs, fee_calc)
        op = _op_for_direction(ops, "kalshi", "forecast_ex")

        # The checked-in production ledger is intentionally incomplete; the
        # real ForecastEx timing entry is also UNKNOWN. The gross edge is a
        # research observation only, never a net-profit substitute.
        assert PRODUCTION_SETTLEMENT_TIMING["forecast_ex"].status.value == "UNKNOWN"
        assert op.gross_edge > 0
        assert op.status is OpportunityStatus.BLOCK
        assert op.total_fees is None
        assert op.settlement_delay_cost is None
        assert op.capital_required is None
        assert op.net_profit is None
        assert not op.costs.complete
        assert {component.name for component in op.costs.unavailable} >= {
            "kalshi:trade_fee", "forecast_ex:trade_fee",
            "kalshi:settlement_fee", "forecast_ex:settlement_fee",
            "settlement_delay",
        }
        assert any("settlement lockup UNKNOWN" in reason
                   for reason in op.block_reasons)
        assert any("FEE_DATA_INCOMPLETE: kalshi" in reason
                   for reason in op.block_reasons)
        assert any("FEE_DATA_INCOMPLETE: forecast_ex" in reason
                   for reason in op.block_reasons)
        assert not op.is_executable

        engine = _RecordingPaperExecutionEngine(
            {"kalshi": Decimal("5"), "forecast_ex": Decimal("5")},
            fee_calc,
            lambda venue, contract_id: case.fresh_books.get(
                (venue, contract_id)),
            fill_probability_at_touch=1.0,
            simulated_latency_ms=0,
        )
        coordinator, _exposure, _state = _coordinator(engine)
        report = asyncio.run(coordinator.execute(op))
        assert not report.success
        assert engine.requests == []
        assert any("precheck failed" in note for note in report.notes)

    def test_thin_depth_caps_quantity_and_cannot_meet_execution_threshold(
            self, eligible_synthetic_case, synthetic_fee_calc):
        case = eligible_synthetic_case
        yes, no = case.yes_contract, case.no_contract
        thin = _books_for_pair(
            yes, no,
            yes_asks=(("0.20", "2"),),
            no_asks=(("0.30", "8"),),
        )
        _aggregator, fresh = asyncio.run(_aggregate_fresh_books(thin))
        partial_walk = walk_buy(fresh[(yes.venue, yes.contract_id)],
                                Decimal("8"))
        assert partial_walk.filled_quantity == Decimal("2")
        assert not partial_walk.fully_filled
        ops = _scan([yes, no], fresh, case.approved_pairs, synthetic_fee_calc)
        op = _op_for_direction(ops, "kalshi", "forecast_ex")
        assert op.quantity == Decimal("2")  # never extrapolates past depth
        assert op.status is OpportunityStatus.WATCH
        assert not op.is_executable

        engine = _RecordingPaperExecutionEngine(
            {"kalshi": Decimal("5"), "forecast_ex": Decimal("5")},
            synthetic_fee_calc,
            lambda venue, contract_id: fresh.get((venue, contract_id)),
            fill_probability_at_touch=1.0,
            simulated_latency_ms=0,
        )
        coordinator, _exposure, _state = _coordinator(engine)
        report = asyncio.run(coordinator.execute(op))
        assert not report.success
        assert engine.requests == []
        assert any("not executable" in note for note in report.notes)

    def test_stale_books_are_excluded_before_scan(self,
                                                 eligible_synthetic_case,
                                                 synthetic_fee_calc):
        case = eligible_synthetic_case
        stale_books = _books_for_pair(
            case.yes_contract, case.no_contract,
            timestamp=utcnow() - timedelta(seconds=2),
        )
        aggregator, fresh = asyncio.run(_aggregate_fresh_books(
            stale_books, max_staleness_ms=250))
        assert fresh == {}
        assert set(asyncio.run(aggregator.stale_keys())) == set(stale_books)
        assert _scan([case.yes_contract, case.no_contract], fresh,
                     case.approved_pairs, synthetic_fee_calc) == []

    def test_risk_cap_blocks_before_any_simulated_order(self,
                                                       eligible_synthetic_case,
                                                       synthetic_fee_calc):
        case = eligible_synthetic_case
        engine = _RecordingPaperExecutionEngine(
            {"kalshi": Decimal("5"), "forecast_ex": Decimal("5")},
            synthetic_fee_calc,
            lambda venue, contract_id: case.fresh_books.get(
                (venue, contract_id)),
            fill_probability_at_touch=1.0,
            simulated_latency_ms=0,
        )
        tight_limits = _explicit_risk_limits(max_capital_per_trade="4.57")
        coordinator, _exposure, _state = _coordinator(
            engine, limits=tight_limits)
        report = asyncio.run(coordinator.execute(case.opportunity))
        assert not report.success
        assert any("max_capital_per_trade" in violation
                   for violation in report.risk_violations)
        assert engine.requests == []

    def test_insufficient_venue_cash_is_rejected_before_any_fill(
            self, eligible_synthetic_case, synthetic_fee_calc):
        case = eligible_synthetic_case
        engine = _RecordingPaperExecutionEngine(
            # The first leg needs $1.70 (walked $1.60 + synthetic taker fee
            # $0.10); this venue-local paper balance is deliberately one cent
            # short. Capital is never transferred from ForecastEx.
            {"kalshi": Decimal("1.69"), "forecast_ex": Decimal("5.00")},
            synthetic_fee_calc,
            lambda venue, contract_id: case.fresh_books.get(
                (venue, contract_id)),
            fill_probability_at_touch=1.0,
            simulated_latency_ms=0,
        )
        coordinator, _exposure, _state = _coordinator(engine)
        report = asyncio.run(coordinator.execute(case.opportunity))
        assert not report.success
        assert len(engine.requests) == 1
        assert report.legs[0].result.status is OrderStatus.REJECTED
        assert "insufficient simulated cash on kalshi" in report.legs[0].result.message
        assert engine.state.fills == []
        assert engine.state.cash["kalshi"] == Decimal("1.69")
        assert asyncio.run(engine.get_positions()) == []

    def test_partial_fills_are_reported_and_only_matched_remainder_stays_open(
            self, eligible_synthetic_case, synthetic_fee_calc):
        case = eligible_synthetic_case
        forecast_calls = 0

        def execution_books(venue: str, contract_id: str):
            nonlocal forecast_calls
            if venue == "kalshi":
                return _book(
                    case.yes_contract,
                    (("0.20", "4"),), (("0.18", "8"),))
            forecast_calls += 1
            if forecast_calls == 1:  # leg B has only 2 of the 4 requested
                return _book(
                    case.no_contract,
                    (("0.30", "2"),), (("0.28", "8"),))
            return None  # both bounded hedge retries fail; then unwind A

        engine = _RecordingPaperExecutionEngine(
            {"kalshi": Decimal("5"), "forecast_ex": Decimal("5")},
            synthetic_fee_calc, execution_books,
            fill_probability_at_touch=1.0,
            simulated_latency_ms=0,
        )
        coordinator, exposure, _state = _coordinator(engine)
        report = asyncio.run(coordinator.execute(case.opportunity))

        assert report.success  # risk was resolved, not a full 8-contract fill
        assert report.recovery is not None
        assert report.recovery.resolution == "HEDGED"
        assert report.recovery.open_hedged_quantity == Decimal("2")
        assert report.recovery.unwound_quantity == Decimal("2")
        assert report.recovery.remaining_unhedged == Decimal("0")
        assert report.legs[0].result.status is OrderStatus.PARTIALLY_FILLED
        assert report.legs[0].result.filled_quantity == Decimal("4")
        assert report.legs[1].request.quantity == Decimal("4")
        assert report.legs[1].result.status is OrderStatus.PARTIALLY_FILLED
        assert report.legs[1].result.filled_quantity == Decimal("2")
        assert "both legs filled" not in report.notes
        assert any("recovery resolved exposure: HEDGED" in note
                   for note in report.notes)
        positions = asyncio.run(engine.get_positions())
        assert {(p["venue"], p["side"], p["quantity"]) for p in positions} == {
            ("kalshi", "yes", "2"), ("forecast_ex", "no", "2")}
        assert asyncio.run(exposure.total_unhedged_dollars()) == Decimal("0")
        assert len(engine.requests) == 5  # A, initial B, two retries, unwind
        _assert_simulation_only(engine)

    def test_failed_hedge_and_failed_unwind_remain_unresolved_and_kill(
            self, eligible_synthetic_case, synthetic_fee_calc):
        case = eligible_synthetic_case
        kalshi_lookups = 0

        def failing_execution_books(venue: str, contract_id: str):
            nonlocal kalshi_lookups
            if venue == "forecast_ex":
                return None  # B and both retries cannot fill
            kalshi_lookups += 1
            if kalshi_lookups == 1:
                return _book(
                    case.yes_contract,
                    (("0.20", "8"),), (("0.18", "8"),))
            return _book(
                case.yes_contract,
                (("0.20", "8"),), (),  # unwind has no executable bid
            )

        engine = _RecordingPaperExecutionEngine(
            {"kalshi": Decimal("5"), "forecast_ex": Decimal("5")},
            synthetic_fee_calc, failing_execution_books,
            fill_probability_at_touch=1.0,
            simulated_latency_ms=0,
        )
        coordinator, exposure, state = _coordinator(engine)
        report = asyncio.run(coordinator.execute(case.opportunity))

        assert not report.success
        assert report.recovery is not None
        assert not report.recovery.hedged
        assert report.recovery.resolution == "UNRESOLVED"
        assert report.recovery.remaining_unhedged == Decimal("8")
        assert report.legs[0].result.status is OrderStatus.FILLED
        assert report.legs[1].result.status is OrderStatus.REJECTED
        assert "both legs filled" not in report.notes
        assert asyncio.run(exposure.total_unhedged_dollars()) == Decimal("1.60")
        assert {(p["venue"], p["side"], p["quantity"])
                for p in asyncio.run(engine.get_positions())} == {
                    ("kalshi", "yes", "8")}
        assert kill_switch.is_engaged()
        assert "unresolved paper exposure" in kill_switch.status()["reason"]
        assert state.open_orders == 0
        assert len(engine.requests) == 5  # A, failed B, two retries, failed unwind
        _assert_simulation_only(engine)
