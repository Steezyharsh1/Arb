"""Arb.16 execution fault matrix (simulation and offline live-path doubles only).

Every test here is offline: in-memory engines, scripted fakes, a temporary
SQLite store, and an isolated kill-switch file. Nothing reaches a venue.

Two groups:

* Protections verified now (plain asserts). They pass today and must keep
  passing.
* Open defects (``xfail(strict=True)``). Each one documents the expected safe
  behaviour. It is expected to fail until its defect is repaired. ``strict``
  means that once the defect is fixed the suite reports XPASS, so the marker
  must be removed deliberately. The register is in docs/ROADMAP.md (D30-D36);
  the evidence and remediation plan are in docs/EXECUTION_SAFETY_ASSESSMENT.md.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from prediction_arb.core.types import (
    Mode,
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Side,
)
from prediction_arb.database.models import TradeRow
from prediction_arb.database.repository import Repository
from prediction_arb.execution.firewall import FirewallContext
from prediction_arb.execution.live import LiveConfirmationToken, LiveExecutionEngine
from prediction_arb.execution.simulated import (
    HistoricalExecutionEngine,
    PaperExecutionEngine,
)
from prediction_arb.orderbooks.aggregator import OrderBookAggregator
from prediction_arb.risk import kill_switch

from .conftest import make_book
from .test_risk_enforcement import (
    FaultEngine,
    _coordinator,
    _full_fill_script,
    _op,
    _request,
)

CONFIRM = LiveConfirmationToken("I UNDERSTAND LIVE TRADING")


@pytest.fixture(autouse=True)
def kill_file(tmp_path, monkeypatch):
    """Every test gets a private kill-switch file; the real data/ one is never touched."""
    path = tmp_path / "KILL_SWITCH"
    monkeypatch.setattr(kill_switch, "KILL_FILE", path)
    return path


class RecordingAdapter:
    """Scripted live-path double: records calls, never touches the network."""

    venue = "kalshi"

    def __init__(self, place_exc=None, open_orders=(), fills=(), order=None):
        self.place_exc = place_exc
        self.open_orders = list(open_orders)
        self.fills = list(fills)
        self.order = order
        self.placed: list[OrderRequest] = []

    async def place_order(self, request):
        self.placed.append(request)
        if self.place_exc is not None:
            raise self.place_exc
        return OrderResult(client_order_id=request.client_order_id,
                           venue_order_id="v-1", status=OrderStatus.SUBMITTED,
                           filled_quantity=Decimal("0"),
                           average_fill_price=None, fees=Decimal("0"))

    async def get_open_orders(self):
        return self.open_orders

    async def get_fills(self):
        return self.fills

    async def get_order(self, order_id):
        return self.order

    async def cancel_order(self, order_id):
        return False


def _live_request(quantity: str = "5") -> OrderRequest:
    return OrderRequest(
        venue="kalshi", contract_id="T", side=Side.YES,
        action=OrderAction.BUY, quantity=Decimal(quantity),
        order_type=OrderType.LIMIT, limit_price=Decimal("0.40"),
        client_order_id=str(uuid.uuid4()), mode=Mode.LIVE)


def _firewall_ctx(**overrides) -> FirewallContext:
    base = dict(
        app_mode=Mode.LIVE, allow_live_orders=True,
        live_confirmation_given=True, credentials_loaded=True,
        account_verified=True, contract_eligible=True,
        eligibility_reason="fixture", independence_verified=True,
        match_approved=True, fees_verified=True, risk_violations=[],
        book_timestamp=datetime.now(timezone.utc), max_book_age_ms=10_000.0,
        opportunity_still_valid=lambda: True,
        expected_price=Decimal("0.40"),
        max_price_deviation=Decimal("0.05"),
        quantity_limit=Decimal("100"))
    base.update(overrides)
    return FirewallContext(**base)


def _live_engine(tmp_path: Path, adapter, name: str = "live"):
    repo = Repository(tmp_path / f"{name}.sqlite3", Mode.LIVE)
    engine = LiveExecutionEngine({"kalshi": adapter}, repo, CONFIRM,
                                 allow_live_orders=True)
    return engine, repo


def _intent_status(repo: Repository, client_order_id: str) -> str | None:
    from sqlalchemy.orm import Session
    from sqlalchemy import select
    with Session(repo.engine) as s:
        row = s.scalar(select(TradeRow).where(
            TradeRow.client_order_id == client_order_id))
        return None if row is None else row.status


def _seed_pending_intent(repo: Repository, request: OrderRequest) -> None:
    repo.write_trade_intent(
        trade_id=request.client_order_id,
        opportunity_id="", venue=request.venue,
        client_order_id=request.client_order_id,
        contract_id=request.contract_id, side=request.side.value,
        action=request.action.value, quantity=str(request.quantity),
        requested_price=str(request.limit_price))


# --------------------------------------------------- verified protections


class TestVerifiedProtections:
    def test_firewall_block_never_reaches_the_adapter(self, tmp_path):
        """§54: a failed firewall check stops the order before transmission."""
        from prediction_arb.core.types import FirewallBlocked
        adapter = RecordingAdapter()
        engine, repo = _live_engine(tmp_path, adapter)
        request = _live_request()
        with pytest.raises(FirewallBlocked):
            asyncio.run(engine.submit_order_with_context(
                request, _firewall_ctx(allow_live_orders=False)))
        assert adapter.placed == []
        assert _intent_status(repo, request.client_order_id) == \
            OrderStatus.REJECTED.value

    def test_stale_book_is_never_a_simulated_fill_source(self):
        """D11: the simulator reads through the freshness-gated aggregator, so
        a stale book is 'no order book' and the order is rejected."""
        stale = make_book("kalshi", "K", asks=[("0.40", "100")],
                          age_seconds=60)

        async def run():
            agg = OrderBookAggregator(max_staleness_ms=1000)
            await agg.update(stale)
            engine = PaperExecutionEngine(
                {"kalshi": Decimal("1000")}, _fee_calc(),
                lambda v, c: agg.peek(v, c))
            return await engine.submit_order(_request("kalshi", "K",
                                                      OrderAction.BUY))

        result = asyncio.run(run())
        assert result.status is OrderStatus.REJECTED
        assert "no order book" in result.message

    @pytest.mark.parametrize("engine_cls", [PaperExecutionEngine,
                                            HistoricalExecutionEngine])
    def test_simulated_engines_hold_no_order_transport(self, engine_cls):
        """Mode isolation: nothing reachable from a simulated engine instance
        exposes place_order/cancel_order to a venue."""
        engine = engine_cls({"kalshi": Decimal("1")}, _fee_calc(),
                            lambda v, c: None)
        reachable = list(vars(engine).values())
        assert not any(hasattr(obj, "place_order") for obj in reachable)
        assert not any(hasattr(obj, "firewall") for obj in reachable)

    def test_leg_b_exception_is_recovered_not_reported_as_success(
            self, kill_file):
        """A raised leg B counts as unfilled, so the hedge procedure runs and
        the retry fill resolves the exposure."""
        from prediction_arb.execution.executor import ExecutionEngine

        class LegBRaises(ExecutionEngine):
            mode = Mode.PAPER
            simulated = True

            def __init__(self):
                self.requests: list[OrderRequest] = []

            async def submit_order(self, request):
                self.requests.append(request)
                call = len(self.requests)
                if call == 2:                       # leg B transport error
                    raise ConnectionError("leg B transport failure")
                price = Decimal("0.40") if call == 1 else Decimal("0.42")
                return OrderResult(
                    client_order_id=request.client_order_id,
                    venue_order_id=f"sim-{call}", status=OrderStatus.FILLED,
                    filled_quantity=request.quantity,
                    average_fill_price=price, fees=Decimal("0"))

            async def cancel_order(self, venue, order_id):
                return False

            async def get_order_status(self, venue, order_id):
                return None

            async def get_positions(self):
                return []

            async def get_balances(self):
                return {}

        engine = LegBRaises()
        coordinator, tracker, _state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert report.success
        assert report.recovery is not None
        assert report.recovery.resolution == "HEDGED"
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("0")
        assert not kill_switch.is_engaged()
        assert len(engine.requests) == 3

    def test_live_timeout_by_asyncio_error_reconciles_by_client_id(
            self, tmp_path):
        """The reconciliation branch works for the exception type it expects."""
        request = _live_request()
        adapter = RecordingAdapter(
            place_exc=asyncio.TimeoutError(),
            fills=[{"client_order_id": request.client_order_id,
                    "order_id": "v-9", "count": 5}])
        engine, repo = _live_engine(tmp_path, adapter)
        result = asyncio.run(engine.submit_order_with_context(
            request, _firewall_ctx()))
        assert result.status is OrderStatus.FILLED
        assert _intent_status(repo, request.client_order_id) == \
            OrderStatus.FILLED.value
        assert len(adapter.placed) == 1   # reconciled, never re-sent


# ------------------------------------------------------- open defects


class TestOpenDefects:
    @pytest.mark.xfail(strict=True, reason=(
        "D31: an httpx.ReadTimeout from the real Kalshi adapter is not "
        "asyncio.TimeoutError, so the engine skips venue reconciliation and "
        "re-raises with the intent left UNKNOWN."))
    def test_httpx_timeout_is_reconciled_by_client_id(self, tmp_path):
        request = _live_request()
        adapter = RecordingAdapter(
            place_exc=httpx.ReadTimeout("read timed out"),
            fills=[{"client_order_id": request.client_order_id,
                    "order_id": "v-9", "count": 5}])
        engine, _repo = _live_engine(tmp_path, adapter)
        result = asyncio.run(engine.submit_order_with_context(
            request, _firewall_ctx()))
        assert result.status is OrderStatus.FILLED

    @pytest.mark.xfail(strict=True, reason=(
        "D32: unreconciled_trades() returns only PENDING/SUBMITTED, so an "
        "UNKNOWN intent is never reconciled by startup recovery."))
    def test_unknown_intent_is_visible_to_startup_recovery(self, tmp_path):
        request = _live_request()
        _adapter = RecordingAdapter()
        _engine, repo = _live_engine(tmp_path, _adapter)
        _seed_pending_intent(repo, request)
        repo.update_trade_status(request.client_order_id, OrderStatus.UNKNOWN)
        assert any(row.client_order_id == request.client_order_id
                   for row in repo.unreconciled_trades())

    @pytest.mark.xfail(strict=True, reason=(
        "D33: absence from a truncated venue fills/open-orders listing is "
        "treated as 'never transmitted' and the intent is marked REJECTED, "
        "even though the order may already have filled."))
    def test_missing_from_listing_is_not_recorded_as_never_sent(self, tmp_path):
        request = _live_request()
        adapter = RecordingAdapter(open_orders=[], fills=[])
        engine, repo = _live_engine(tmp_path, adapter)
        _seed_pending_intent(repo, request)
        asyncio.run(engine.recover_on_startup())
        assert _intent_status(repo, request.client_order_id) != \
            OrderStatus.REJECTED.value

    @pytest.mark.xfail(strict=True, reason=(
        "D34: the simulator does not deduplicate client_order_id; resubmitting "
        "one request fills it twice."))
    def test_resubmitting_the_same_client_order_id_does_not_double_fill(self):
        book = make_book("kalshi", "K", asks=[("0.40", "100")])
        engine = PaperExecutionEngine({"kalshi": Decimal("1000")},
                                      _fee_calc(), lambda v, c: book)
        request = _request("kalshi", "K", OrderAction.BUY, quantity="5")
        asyncio.run(engine.submit_order(request))
        asyncio.run(engine.submit_order(request))
        held = sum(p.quantity for p in engine.state.positions)
        assert held == Decimal("5")

    @pytest.mark.xfail(strict=True, reason=(
        "D37: a hedge retry that raises is treated as 'not filled' and the "
        "handler immediately submits another hedge under a NEW client_order_id. "
        "If the first retry was transmitted, the position is doubled."))
    def test_ambiguous_hedge_retry_error_does_not_submit_another_hedge(
            self, kill_file):
        from prediction_arb.execution.executor import ExecutionEngine

        class AmbiguousRetry(ExecutionEngine):
            mode = Mode.PAPER
            simulated = True

            def __init__(self):
                self.requests: list[OrderRequest] = []

            async def submit_order(self, request):
                self.requests.append(request)
                call = len(self.requests)
                if call == 1:
                    status, qty = OrderStatus.FILLED, request.quantity
                elif call == 2:
                    status, qty = OrderStatus.REJECTED, Decimal("0")
                elif call == 3:                     # retry 1: timeout-like
                    raise TimeoutError("retry 1 outcome unknown")
                else:
                    status, qty = OrderStatus.FILLED, request.quantity
                return OrderResult(
                    client_order_id=request.client_order_id,
                    venue_order_id=f"amb-{call}", status=status,
                    filled_quantity=qty, average_fill_price=Decimal("0.42"),
                    fees=Decimal("0"))

            async def cancel_order(self, venue, order_id):
                return False

            async def get_order_status(self, venue, order_id):
                return None

            async def get_positions(self):
                return []

            async def get_balances(self):
                return {}

        engine = AmbiguousRetry()
        coordinator, _tracker, _state, _alerts = _coordinator(engine)
        asyncio.run(coordinator.execute(_op()))
        # Leg A, leg B, retry 1: nothing else may be sent until retry 1 is
        # reconciled, because retry 1 may already have transmitted.
        assert len(engine.requests) == 3

    @pytest.mark.xfail(strict=True, reason=(
        "D35: a leg-A exception is assumed to be pre-transmission. The "
        "coordinator records no exposure and does not engage the kill switch, "
        "even though the order may be live at the venue."))
    def test_leg_a_exception_is_not_treated_as_safe_no_order(self, kill_file):
        class RaisingLegA(FaultEngine):
            async def submit_order(self, request):
                self.requests.append(request)
                raise ConnectionResetError("reset after send?")

        engine = RaisingLegA([])
        coordinator, tracker, _state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        exposed = asyncio.run(tracker.total_unhedged_dollars()) > 0
        assert exposed or kill_switch.is_engaged()

    @pytest.mark.xfail(strict=True, reason=(
        "D36: a resting (SUBMITTED) leg A aborts the lifecycle without cancel, "
        "without recording exposure, and without engaging the kill switch."))
    def test_resting_leg_a_is_cancelled_or_tracked(self, kill_file):
        engine = FaultEngine([(OrderStatus.SUBMITTED, "0", None, "0")])
        coordinator, tracker, _state, _alerts = _coordinator(engine)
        asyncio.run(coordinator.execute(_op()))
        exposed = asyncio.run(tracker.total_unhedged_dollars()) > 0
        assert exposed or kill_switch.is_engaged()


def _fee_calc():
    from prediction_arb.fees.calculator import FeeCalculator
    from tests.conftest import fresh_verification_date
    return FeeCalculator.from_config({"schedules": [{
        "venue": "kalshi", "version": "fault-matrix-v1",
        "effective_date": "2020-01-01",
        "verified_at": fresh_verification_date(),
        "verification_status": "VERIFIED", "documentation_tested": True,
        "source": "synthetic deterministic test fixture",
        "trade_calculation_status": "COMPLETE",
        "settlement_calculation_status": "COMPLETE",
        "fees": [
            {"fee_type": "test_quadratic", "multiplier": "0.07",
             "applies_to": "taker"},
            {"fee_type": "settlement", "fixed_amount": "0",
             "applies_to": "settlement"},
        ]}]})
