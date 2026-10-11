"""Arb.16 / Arb.17 execution fault matrix (simulation and offline live-path doubles only).

Every test here is offline: in-memory engines, scripted fakes, a temporary
SQLite store, and an isolated kill-switch file. Nothing reaches a venue.

Groups:

* ``TestVerifiedProtections`` — baseline protections verified in Arb.16.
* ``TestOpenDefects`` — the seven Arb.16 defects (D31-D37), resolved in
  Arb.17 and retained under this class name so existing reproduction
  commands keep selecting them, plus additional edge-case regressions for
  each remediation.
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


# ------------------------------------------------------- D31-D37 regressions


class TestOpenDefects:
    def test_httpx_timeout_is_reconciled_by_client_id(self, tmp_path):
        """D31: httpx.ReadTimeout from the Kalshi adapter triggers venue
        reconciliation by client_order_id rather than raising uninspected."""
        request = _live_request()
        adapter = RecordingAdapter(
            place_exc=httpx.ReadTimeout("read timed out"),
            fills=[{"client_order_id": request.client_order_id,
                    "order_id": "v-9", "count": 5}])
        engine, repo = _live_engine(tmp_path, adapter)
        result = asyncio.run(engine.submit_order_with_context(
            request, _firewall_ctx()))
        assert result.status is OrderStatus.FILLED
        assert result.filled_quantity == Decimal("5")
        assert _intent_status(repo, request.client_order_id) == \
            OrderStatus.FILLED.value
        assert len(adapter.placed) == 1

    def test_httpx_transport_error_reconciles_resting_or_unknown(
            self, tmp_path):
        """D31: httpx.TransportError also reconciles; if the venue has no
        trace yet, the result and persisted intent remain UNKNOWN."""
        req_resting = _live_request()
        adapter_resting = RecordingAdapter(
            place_exc=httpx.ConnectError("connection reset"),
            open_orders=[{"client_order_id": req_resting.client_order_id,
                          "order_id": "v-rest"}])
        engine_r, repo_r = _live_engine(tmp_path, adapter_resting, "rest")
        res_r = asyncio.run(engine_r.submit_order_with_context(
            req_resting, _firewall_ctx()))
        assert res_r.status is OrderStatus.SUBMITTED
        assert res_r.venue_order_id == "v-rest"
        assert _intent_status(repo_r, req_resting.client_order_id) == \
            OrderStatus.SUBMITTED.value

        req_unknown = _live_request()
        adapter_unknown = RecordingAdapter(
            place_exc=httpx.ReadTimeout("read timed out"))
        engine_u, repo_u = _live_engine(tmp_path, adapter_unknown, "unk")
        res_u = asyncio.run(engine_u.submit_order_with_context(
            req_unknown, _firewall_ctx()))
        assert res_u.status is OrderStatus.UNKNOWN
        assert _intent_status(repo_u, req_unknown.client_order_id) == \
            OrderStatus.UNKNOWN.value
        assert any(row.client_order_id == req_unknown.client_order_id
                   for row in repo_u.unreconciled_trades())

    def test_unknown_intent_is_visible_to_startup_recovery(self, tmp_path):
        """D32: unreconciled_trades() includes UNKNOWN intents alongside
        PENDING and SUBMITTED."""
        request = _live_request()
        _adapter = RecordingAdapter()
        _engine, repo = _live_engine(tmp_path, _adapter)
        _seed_pending_intent(repo, request)
        repo.update_trade_status(request.client_order_id, OrderStatus.UNKNOWN)
        assert any(row.client_order_id == request.client_order_id
                   for row in repo.unreconciled_trades())

    def test_missing_from_listing_is_not_recorded_as_never_sent(self, tmp_path):
        """D33: absence from a venue open-orders/fills listing marks the
        intent UNKNOWN, never REJECTED, and leaves it visible for manual
        reconciliation."""
        request = _live_request()
        adapter = RecordingAdapter(open_orders=[], fills=[])
        engine, repo = _live_engine(tmp_path, adapter)
        _seed_pending_intent(repo, request)
        notes = asyncio.run(engine.recover_on_startup())
        assert _intent_status(repo, request.client_order_id) == \
            OrderStatus.UNKNOWN.value
        assert any("UNKNOWN" in note for note in notes)
        assert any(row.client_order_id == request.client_order_id
                   for row in repo.unreconciled_trades())

    def test_startup_recovery_accepts_definitive_order_lookup(self, tmp_path):
        """D33: when venue order_id is known and get_order returns a
        definitive REJECTED or FILLED result, startup recovery records it."""
        request = _live_request()
        definitive = OrderResult(
            client_order_id=request.client_order_id,
            venue_order_id="v-def",
            status=OrderStatus.REJECTED,
            filled_quantity=Decimal("0"),
            average_fill_price=None,
            fees=Decimal("0"))
        adapter = RecordingAdapter(order=definitive)
        engine, repo = _live_engine(tmp_path, adapter)
        _seed_pending_intent(repo, request)
        repo.update_trade_status(
            request.client_order_id, OrderStatus.SUBMITTED, order_id="v-def")
        asyncio.run(engine.recover_on_startup())
        assert _intent_status(repo, request.client_order_id) == \
            OrderStatus.REJECTED.value
        assert repo.unreconciled_trades() == []

    def test_resubmitting_the_same_client_order_id_does_not_double_fill(self):
        """D34: the simulator deduplicates client_order_id, rejecting a
        duplicate submission while preserving the original fill and order
        lookup."""
        book = make_book("kalshi", "K", asks=[("0.40", "100")])
        engine = PaperExecutionEngine({"kalshi": Decimal("1000")},
                                      _fee_calc(), lambda v, c: book)
        request = _request("kalshi", "K", OrderAction.BUY, quantity="5")
        first = asyncio.run(engine.submit_order(request))
        cash_after_first = engine.state.cash["kalshi"]
        second = asyncio.run(engine.submit_order(request))
        held = sum(p.quantity for p in engine.state.positions)
        assert first.status is OrderStatus.FILLED
        assert second.status is OrderStatus.REJECTED
        assert "duplicate client_order_id" in second.message
        assert held == Decimal("5")
        assert engine.state.cash["kalshi"] == cash_after_first
        assert len(engine.state.fills) == 1
        looked_up = asyncio.run(
            engine.get_order_status("kalshi", request.client_order_id))
        assert looked_up is not None
        assert looked_up.status is OrderStatus.FILLED
        assert looked_up.filled_quantity == Decimal("5")

    def test_ambiguous_hedge_retry_error_does_not_submit_another_hedge(
            self, kill_file):
        """D37: a hedge retry that raises an unresolvable error halts
        recovery immediately without submitting another hedge or unwind."""
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
        coordinator, tracker, _state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        # Leg A, leg B, retry 1: nothing else may be sent until retry 1 is
        # reconciled, because retry 1 may already have transmitted.
        assert len(engine.requests) == 3
        assert not report.success
        assert report.recovery is not None
        assert report.recovery.resolution == "UNRESOLVED"
        assert asyncio.run(tracker.total_unhedged_dollars()) > 0
        assert kill_switch.is_engaged()

    def test_unknown_status_on_hedge_retry_halts_before_unwind(
            self, kill_file):
        """D37: a hedge retry returning UNKNOWN or uncancelable SUBMITTED
        also halts recovery before retry 2 or leg-A unwind."""
        engine = FaultEngine([
            (OrderStatus.FILLED, None, "0.40", "0"),
            (OrderStatus.REJECTED, "0", None, "0"),
            (OrderStatus.UNKNOWN, "0", None, "0"),
            (OrderStatus.FILLED, None, "0.52", "0"),
        ])
        coordinator, tracker, _state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert len(engine.requests) == 3
        assert not report.success
        assert report.recovery is not None
        assert report.recovery.resolution == "UNRESOLVED"
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("4.00")
        assert kill_switch.is_engaged()

    def test_leg_a_exception_is_not_treated_as_safe_no_order(self, kill_file):
        """D35: an unresolvable leg-A exception records conservative unhedged
        exposure, updates deployment, engages the kill switch, and never
        submits leg B."""
        class RaisingLegA(FaultEngine):
            async def submit_order(self, request):
                self.requests.append(request)
                raise ConnectionResetError("reset after send?")

        engine = RaisingLegA([])
        coordinator, tracker, state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        assert len(engine.requests) == 1
        exposed = asyncio.run(tracker.total_unhedged_dollars()) > 0
        assert exposed and kill_switch.is_engaged()
        assert state.open_positions == 1

    def test_leg_a_exception_reconciled_to_fill_proceeds_to_hedge(
            self, kill_file):
        """D35: if leg A raises after filling and get_order_status reconciles
        the fill, the coordinator proceeds to leg B to hedge it."""
        class ReconciledLegA(FaultEngine):
            async def submit_order(self, request):
                if not self.requests:
                    self.requests.append(request)
                    raise TimeoutError("response lost after leg A fill")
                return await super().submit_order(request)

            async def get_order_status(self, venue, order_id):
                if (self.requests
                        and order_id == self.requests[0].client_order_id):
                    return OrderResult(
                        client_order_id=order_id,
                        venue_order_id="reconciled-a",
                        status=OrderStatus.FILLED,
                        filled_quantity=self.requests[0].quantity,
                        average_fill_price=Decimal("0.40"),
                        fees=Decimal("0"))
                return None

        engine = ReconciledLegA([(OrderStatus.FILLED, None, "0.50", "0")])
        coordinator, tracker, state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert report.success
        assert len(engine.requests) == 2
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("0")
        assert not kill_switch.is_engaged()
        assert state.open_positions == 2

    def test_leg_a_exception_reconciled_to_definitive_reject_aborts_cleanly(
            self, kill_file):
        """D35: if leg A raises and get_order_status confirms a definitive
        REJECTED with zero fill, the coordinator aborts before leg B without
        phantom exposure or kill-switch activation."""
        class RejectedAfterRaiseLegA(FaultEngine):
            async def submit_order(self, request):
                self.requests.append(request)
                raise TimeoutError("lost reject response")

            async def get_order_status(self, venue, order_id):
                return OrderResult(
                    client_order_id=order_id,
                    venue_order_id="rej-a",
                    status=OrderStatus.REJECTED,
                    filled_quantity=Decimal("0"),
                    average_fill_price=None,
                    fees=Decimal("0"),
                    message="venue rejected")

        engine = RejectedAfterRaiseLegA([])
        coordinator, tracker, state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        assert len(engine.requests) == 1
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("0")
        assert not kill_switch.is_engaged()
        assert state.open_positions == 0

    def test_ambiguous_leg_b_status_halts_before_recovery(self, kill_file):
        """D36/D37: if leg B returns UNKNOWN (or uncancelable SUBMITTED),
        the coordinator halts before recovery so no duplicate hedge or
        unwind is submitted while leg B may be live."""
        engine = FaultEngine([
            (OrderStatus.FILLED, None, "0.40", "0"),
            (OrderStatus.UNKNOWN, "0", None, "0"),
            (OrderStatus.FILLED, None, "0.52", "0"),
        ])
        coordinator, tracker, state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        assert len(engine.requests) == 2
        assert report.recovery is None
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("4.00")
        assert kill_switch.is_engaged()
        assert state.open_positions == 2

    def test_resting_leg_a_is_cancelled_or_tracked(self, kill_file):
        """D36: a resting (SUBMITTED) leg A whose cancel/status cannot be
        confirmed as zero-fill CANCELED records exposure and engages the
        kill switch."""
        engine = FaultEngine([(OrderStatus.SUBMITTED, "0", None, "0")])
        coordinator, tracker, state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        assert len(engine.requests) == 1
        exposed = asyncio.run(tracker.total_unhedged_dollars()) > 0
        assert exposed and kill_switch.is_engaged()
        assert state.open_positions == 1

    def test_resting_leg_a_confirmed_canceled_with_zero_fill_aborts_cleanly(
            self, kill_file):
        """D36: if a resting leg A is canceled and get_order_status confirms
        CANCELED with zero fill, the coordinator aborts before leg B without
        phantom exposure."""
        class CancelableRestingLegA(FaultEngine):
            def __init__(self):
                super().__init__([(OrderStatus.SUBMITTED, "0", None, "0")])
                self.canceled_ids: list[str] = []

            async def cancel_order(self, venue, order_id):
                self.canceled_ids.append(order_id)
                return True

            async def get_order_status(self, venue, order_id):
                if self.canceled_ids:
                    return OrderResult(
                        client_order_id=self.requests[0].client_order_id,
                        venue_order_id=order_id,
                        status=OrderStatus.CANCELED,
                        filled_quantity=Decimal("0"),
                        average_fill_price=None,
                        fees=Decimal("0"))
                return None

        engine = CancelableRestingLegA()
        coordinator, tracker, state, _alerts = _coordinator(engine)
        report = asyncio.run(coordinator.execute(_op()))
        assert not report.success
        assert len(engine.requests) == 1
        assert engine.canceled_ids == ["fault-1"]
        assert asyncio.run(tracker.total_unhedged_dollars()) == Decimal("0")
        assert not kill_switch.is_engaged()
        assert state.open_positions == 0


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
