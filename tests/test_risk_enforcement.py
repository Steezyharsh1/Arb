"""Regression coverage for paper capital and runtime risk enforcement.

All execution is simulated. ``FaultEngine`` is an in-memory execution engine
used to inject partial fills, rejects, and failed recovery without any adapter
or real-order path.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from prediction_arb.core.clock import utcnow
from prediction_arb.core.config import AppConfig
from prediction_arb.core.types import (
    CostComponent,
    CostLedger,
    Independence,
    Leg,
    Mode,
    Opportunity,
    OpportunityStatus,
    OpportunityType,
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Side,
)
from prediction_arb.execution.coordinator import ExecutionCoordinator
from prediction_arb.execution.executor import ExecutionEngine
from prediction_arb.execution.partial_fills import PartialFillHandler
from prediction_arb.execution.simulated import PaperExecutionEngine
from prediction_arb.monitoring.alerts import AlertSink
from prediction_arb.orderbooks.orderbook import walk_buy
from prediction_arb.risk import kill_switch
from prediction_arb.risk.exposure import ExposureTracker, UnhedgedExposure
from prediction_arb.risk.limits import RiskLimits, RiskState

from .conftest import make_book, make_contract

ROOT = Path(__file__).resolve().parents[1]


class QuietAlerts(AlertSink):
    def __init__(self):
        self.messages: list[tuple[str, str]] = []

    async def alert(self, title: str, body: str) -> None:
        self.messages.append((title, body))


class FaultEngine(ExecutionEngine):
    """Scripted simulation-only engine for order-lifecycle fault injection."""

    mode = Mode.PAPER
    simulated = True

    def __init__(self, script, after_submit=None):
        self.script = list(script)
        self.after_submit = after_submit
        self.requests: list[OrderRequest] = []

    async def submit_order(self, request: OrderRequest) -> OrderResult:
        self.requests.append(request)
        if not self.script:
            raise AssertionError("fault script exhausted")
        status, quantity, price, fees = self.script.pop(0)
        if self.after_submit is not None:
            self.after_submit(len(self.requests))
        filled = request.quantity if quantity is None else Decimal(str(quantity))
        if status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            filled = Decimal("0")
            price = None
        return OrderResult(
            client_order_id=request.client_order_id,
            venue_order_id=f"fault-{len(self.requests)}",
            status=status,
            filled_quantity=filled,
            average_fill_price=(Decimal(str(price))
                                if price is not None else None),
            fees=Decimal(str(fees)),
            message="fault injection")

    async def cancel_order(self, venue: str, order_id: str) -> bool:
        return False

    async def get_order_status(self, venue: str, order_id: str):
        return None

    async def get_positions(self):
        return []

    async def get_balances(self):
        return {}


def _request(venue: str, contract_id: str, action: OrderAction,
             quantity: str = "10", price: str = "0.40") -> OrderRequest:
    return OrderRequest(
        venue=venue, contract_id=contract_id, side=Side.YES, action=action,
        quantity=Decimal(quantity), order_type=OrderType.LIMIT,
        limit_price=Decimal(price), client_order_id=str(uuid.uuid4()),
        mode=Mode.PAPER)


def _op(quantity: str = "10", event: str = "risk-event") -> Opportunity:
    qty = Decimal(quantity)
    yes = make_contract(venue="kalshi", contract_id=f"K-{uuid.uuid4().hex}")
    no = make_contract(venue="forecast_ex", contract_id=f"F-{uuid.uuid4().hex}")
    yes_book = make_book(
        "kalshi", yes.contract_id,
        asks=[("0.40", quantity)], bids=[("0.30", quantity)])
    no_book = make_book(
        "forecast_ex", no.contract_id,
        asks=[("0.50", quantity)], bids=[("0.40", quantity)])
    yes_walk = walk_buy(yes_book, qty)
    no_walk = walk_buy(no_book, qty)
    gross = yes_walk.total_cost + no_walk.total_cost
    ledger = CostLedger((CostComponent("test_cost", Decimal("0")),))
    return Opportunity(
        opportunity_id=str(uuid.uuid4()), mode=Mode.PAPER,
        opportunity_type=OpportunityType.TYPE_A_GUARANTEED,
        timestamp=utcnow(), normalized_event=event,
        legs=[
            Leg("kalshi", yes, Side.YES, OrderAction.BUY, qty,
                Decimal("0.40"), yes_walk, Decimal("0")),
            Leg("forecast_ex", no, Side.NO, OrderAction.BUY, qty,
                Decimal("0.50"), no_walk, Decimal("0")),
        ],
        payout_per_contract=Decimal("1"), quantity=qty,
        gross_cost=gross, total_fees=Decimal("0"), slippage=Decimal("0"),
        settlement_delay_cost=Decimal("0"),
        net_profit=qty - gross,
        net_profit_per_contract=(qty - gross) / qty,
        capital_required=gross,
        return_on_capital=(qty - gross) / gross,
        independence=Independence.INDEPENDENT,
        match_id="test-match", match_confidence=1.0,
        status=OpportunityStatus.TRADE, costs=ledger)


def _coordinator(engine: ExecutionEngine, limits: RiskLimits | None = None,
                 state: RiskState | None = None,
                 exposure: ExposureTracker | None = None):
    lim = limits or RiskLimits.from_config({})
    tracker = exposure or ExposureTracker()
    risk_state = state or RiskState()
    alerts = QuietAlerts()
    return (ExecutionCoordinator(
        engine, tracker, PartialFillHandler(lim, alerts), Mode.PAPER,
        lim, risk_state), tracker, risk_state, alerts)


def _full_fill_script():
    return [
        (OrderStatus.FILLED, None, "0.40", "0"),
        (OrderStatus.FILLED, None, "0.50", "0"),
    ]


class TestPaperCapitalConfiguration:
    def test_every_enabled_venue_has_explicit_paper_cash(self):
        venues = yaml.safe_load((ROOT / "config/venues.yaml").read_text())["venues"]
        paper = yaml.safe_load((ROOT / "config/paper.yaml").read_text())
        enabled = {venue for venue, cfg in venues.items() if cfg["enabled"]}
        starting = paper["paper_starting_cash"]
        assert enabled <= set(starting)
        assert starting["polymarket_us"] == 750
        assert all(Decimal(str(starting[venue])) > 0 for venue in enabled)

    def test_paper_startup_reports_an_enabled_venue_without_capital(self):
        from prediction_arb.modes.paper.engine import _paper_starting_cash
        config = AppConfig(
            mode=Mode.PAPER, settings={},
            venues={"venues": {
                "kalshi": {"enabled": True},
                "polymarket_us": {"enabled": True},
            }},
            fees={}, risk_limits={},
            mode_config={"paper_starting_cash": {"kalshi": 1000}})
        try:
            _paper_starting_cash(config)
        except ValueError as exc:
            assert "missing enabled venue(s): polymarket_us" in str(exc)
        else:
            raise AssertionError("missing paper capital must refuse startup")

    def test_order_on_unconfigured_venue_is_explicitly_rejected(
            self, complete_fee_calc):
        book = make_book("forecast_ex", "F", asks=[("0.50", "10")])
        engine = PaperExecutionEngine(
            {"kalshi": Decimal("100")}, complete_fee_calc,
            lambda _venue, _contract: book)
        result = asyncio.run(engine.submit_order(
            _request("forecast_ex", "F", OrderAction.BUY, "1", "0.50")))
        assert result.status is OrderStatus.REJECTED
        assert "starting cash is not configured" in result.message
        assert engine.state.fills == []

    def test_simulated_unwind_removes_position_instead_of_claiming_success_only(
            self, complete_fee_calc):
        book = make_book(
            "kalshi", "K", asks=[("0.40", "10")], bids=[("0.30", "10")])
        engine = PaperExecutionEngine(
            {"kalshi": Decimal("100")}, complete_fee_calc,
            lambda _venue, _contract: book)
        bought = asyncio.run(engine.submit_order(
            _request("kalshi", "K", OrderAction.BUY)))
        assert bought.status is OrderStatus.FILLED
        sold = asyncio.run(engine.submit_order(
            _request("kalshi", "K", OrderAction.SELL, price="0.01")))
        assert sold.status is OrderStatus.FILLED
        assert asyncio.run(engine.get_positions()) == []
        assert engine.state.realized_pnl < 0


class TestHardLimitCoordinatorBoundary:
    def test_configured_limit_inventory_matches_the_runtime_model(self):
        cfg = yaml.safe_load((ROOT / "config/risk_limits.yaml").read_text())
        configured = set(cfg["limits"]) | set(cfg["partial_fill"])
        runtime = set(RiskLimits.__dataclass_fields__)
        assert configured == runtime == {
            "max_total_capital", "max_capital_per_venue",
            "max_capital_per_event", "max_capital_per_trade",
            "max_unhedged_exposure", "max_daily_loss",
            "max_open_positions", "max_concurrent_orders",
            "max_unhedged_seconds", "max_unhedged_contracts",
            "max_unhedged_dollars", "max_emergency_loss",
        }

    def test_coordinator_requests_are_always_priced_limit_orders(self):
        engine = FaultEngine(_full_fill_script())
        coordinator, _tracker, _state, _alerts = _coordinator(engine)

        report = asyncio.run(coordinator.execute(_op()))

        assert report.success
        assert len(engine.requests) == 2
        assert all(request.order_type is OrderType.LIMIT
                   and request.limit_price is not None
                   for request in engine.requests)

    def test_each_capital_loss_position_and_order_limit_blocks_before_leg_a(self):
        cases = [
            (replace(RiskLimits.from_config({}), max_capital_per_trade=Decimal("8")),
             RiskState(), "max_capital_per_trade"),
            (replace(RiskLimits.from_config({}), max_total_capital=Decimal("10")),
             RiskState(deployed_total=Decimal("2")), "max_total_capital"),
            (replace(RiskLimits.from_config({}),
                     max_capital_per_venue=Decimal("3.99")),
             RiskState(), "max_capital_per_venue"),
            (replace(RiskLimits.from_config({}),
                     max_capital_per_event=Decimal("10")),
             RiskState(deployed_by_event={"risk-event": Decimal("2")}),
             "max_capital_per_event"),
            (RiskLimits.from_config({}), RiskState(daily_pnl=Decimal("-50")),
             "max_daily_loss"),
            (RiskLimits.from_config({}), RiskState(open_positions=19),
             "max_open_positions"),
            (RiskLimits.from_config({}), RiskState(open_orders=3),
             "max_concurrent_orders"),
        ]
        for limits, state, expected in cases:
            engine = FaultEngine(_full_fill_script())
            coordinator, _tracker, _state, _alerts = _coordinator(
                engine, limits, state)
            report = asyncio.run(coordinator.execute(_op()))
            assert not report.success, expected
            assert any(expected in violation
                       for violation in report.risk_violations), expected
            assert engine.requests == [], expected

    def test_success_updates_state_and_next_event_allocation_is_blocked(self):
        limits = replace(RiskLimits.from_config({}),
                         max_capital_per_event=Decimal("15"))
        engine = FaultEngine(_full_fill_script())
        coordinator, _tracker, state, _alerts = _coordinator(engine, limits)
        first = asyncio.run(coordinator.execute(_op(event="same")))
        assert first.success
        assert state.deployed_total == Decimal("9.00")
        assert state.deployed_by_event["same"] == Decimal("9.00")
        assert state.open_positions == 2
        assert state.open_orders == 0

        second = asyncio.run(coordinator.execute(_op(event="same")))
        assert not second.success
        assert any("max_capital_per_event" in v
                   for v in second.risk_violations)
        assert len(engine.requests) == 2

    def test_kill_switch_is_checked_inside_coordinator_not_only_paper_loop(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KILL")
        kill_switch.engage("fault injection")
        engine = FaultEngine(_full_fill_script())
        coordinator, _tracker, _state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        assert "kill switch engaged" in report.notes[0]
        assert engine.requests == []


class TestUnhedgedExposureLifecycle:
    def test_prospective_aggregate_dollar_and_contract_limits_block_leg_a(self):
        cases = [
            (replace(RiskLimits.from_config({}),
                     max_unhedged_exposure=Decimal("3")),
             "max_unhedged_exposure"),
            (replace(RiskLimits.from_config({}),
                     max_unhedged_dollars=Decimal("3")),
             "max_unhedged_dollars"),
            (replace(RiskLimits.from_config({}), max_unhedged_contracts=9),
             "max_unhedged_contracts"),
        ]
        for limits, expected in cases:
            engine = FaultEngine(_full_fill_script())
            coordinator, _tracker, _state, _alerts = _coordinator(
                engine, limits)
            report = asyncio.run(coordinator.execute(_op()))
            assert any(expected in v for v in report.risk_violations)
            assert engine.requests == []

    def test_aged_existing_exposure_blocks_a_new_trade(self):
        limits = replace(RiskLimits.from_config({}), max_unhedged_seconds=1)
        tracker = ExposureTracker()
        asyncio.run(tracker.record_unhedged(UnhedgedExposure(
            "old", "kalshi", "K-old", Decimal("1"), Decimal("1"),
            utcnow() - timedelta(seconds=2))))
        engine = FaultEngine(_full_fill_script())
        coordinator, _tracker, _state, _alerts = _coordinator(
            engine, limits, exposure=tracker)
        report = asyncio.run(coordinator.execute(_op()))
        assert any("max_unhedged_seconds" in v
                   for v in report.risk_violations)
        assert engine.requests == []

    def test_partial_fill_retry_safely_hedges_and_clears_exposure(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KILL")
        engine = FaultEngine([
            (OrderStatus.FILLED, None, "0.40", "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.FILLED, None, "0.52", "0"),
        ])
        coordinator, tracker, state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert report.success
        assert report.recovery is not None
        assert report.recovery.resolution == "HEDGED"
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("0")
        assert not kill_switch.is_engaged()
        assert state.open_orders == 0

    def test_failed_hedges_and_failed_unwind_engage_kill_switch(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KILL")
        engine = FaultEngine([
            (OrderStatus.FILLED, None, "0.40", "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
        ])
        coordinator, tracker, state, alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        assert report.recovery is not None
        assert report.recovery.resolution == "UNRESOLVED"
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("4.00")
        assert kill_switch.is_engaged()
        assert "unresolved paper exposure" in kill_switch.status()["reason"]
        assert state.open_orders == 0
        assert state.open_positions == 1
        assert any(title == "UNRESOLVED EXPOSURE — MANUAL ACTION REQUIRED"
                   for title, _body in alerts.messages)

    def test_timeout_breach_engages_kill_but_recovery_order_remains_allowed(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KILL")
        from prediction_arb.risk import exposure as exposure_module
        clock = [utcnow()]
        monkeypatch.setattr(exposure_module, "utcnow", lambda: clock[0])

        def advance_after_leg_b(submissions: int) -> None:
            if submissions == 2:
                clock[0] += timedelta(seconds=31)

        engine = FaultEngine([
            (OrderStatus.FILLED, None, "0.40", "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.FILLED, None, "0.52", "0"),
        ], after_submit=advance_after_leg_b)
        coordinator, tracker, _state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert report.success, "risk-reducing recovery must continue"
        assert len(engine.requests) == 3
        assert any("max_unhedged_seconds" in v
                   for v in report.risk_violations)
        assert kill_switch.is_engaged(), "future new trades must stop"
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("0")


class TestRecoveryBudgetsAndDailyLoss:
    def test_max_emergency_loss_stops_hedge_chase_before_submission(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KILL")
        limits = replace(RiskLimits.from_config({}),
                         max_emergency_loss=Decimal("0.10"))
        engine = FaultEngine([
            (OrderStatus.FILLED, None, "0.40", "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            # No retry: the first 0.02 * 10 concession is over the 0.10 cap.
            (OrderStatus.FILLED, None, "0.40", "0"),
        ])
        coordinator, tracker, _state, _alerts = _coordinator(engine, limits)
        report = asyncio.run(coordinator.execute(_op()))
        assert report.success and report.recovery is not None
        assert report.recovery.resolution == "FLATTENED"
        assert len(engine.requests) == 3
        assert any("max_emergency_loss" in note for note in report.notes)
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("0")

    def test_observed_recovery_loss_feeds_daily_loss_gate(
            self, tmp_path, monkeypatch):
        monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KILL")
        limits = replace(RiskLimits.from_config({}),
                         max_daily_loss=Decimal("2"))
        engine = FaultEngine([
            (OrderStatus.FILLED, None, "0.40", "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            # Unwind at 0.10 realizes a $3 price loss.
            (OrderStatus.FILLED, None, "0.10", "0"),
        ])
        coordinator, _tracker, state, _alerts = _coordinator(engine, limits)
        first = asyncio.run(coordinator.execute(_op()))
        assert first.success and first.recovery is not None
        assert first.recovery.resolution == "FLATTENED"
        assert state.daily_pnl == Decimal("-3.00")

        second = asyncio.run(coordinator.execute(_op(event="next")))
        assert not second.success
        assert any("max_daily_loss" in v for v in second.risk_violations)
        assert len(engine.requests) == 5
