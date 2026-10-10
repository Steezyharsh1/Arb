"""Pipeline telemetry (§103, Arb.14).

What these pin down, in the order an operator needs them:

* the vocabulary is closed, so counters cannot grow unbounded labels;
* a blocked opportunity is attributed to the mandatory input that blocked it,
  through the real fail-closed fee path rather than a hand-built object;
* a simulated attempt is classified exactly once, and a missing measurement
  stays NULL / UNAVAILABLE rather than becoming zero;
* counters are upserted per (mode, UTC day, metric, label), so repeated cycles
  add to a bounded set of rows;
* the report and lifecycle read the same recorded facts and say UNAVAILABLE
  where nothing was recorded;
* the paper engine's flush, adapter sampling and attempt rows work end to end.
"""
from __future__ import annotations

import asyncio
import dataclasses
import io
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from rich.console import Console
from sqlalchemy import text

from prediction_arb.arbitrage.two_leg import evaluate_two_leg
from prediction_arb.core.types import (
    Independence,
    Mode,
    OrderAction,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderType,
    Side,
)
from prediction_arb.database.repository import Repository
from prediction_arb.execution.coordinator import ExecutionReport, LegOutcome
from prediction_arb.execution.partial_fills import PartialFillHandler, RecoveryOutcome
from prediction_arb.monitoring import pipeline as tel
from prediction_arb.monitoring.pipeline_report import (
    UNAVAILABLE,
    build_lifecycle,
    build_pipeline_report,
    print_lifecycle,
    print_pipeline_report,
)
from prediction_arb.risk.limits import RiskLimits

from .conftest import make_book, make_contract
from .test_unavailable_costs import _evaluate, _fee_calc, _pair

ROOT = Path(__file__).resolve().parent.parent


def _req(venue="kalshi", cid="K", qty="10", price="0.40", action=OrderAction.BUY):
    return OrderRequest(venue=venue, contract_id=cid, side=Side.YES,
                        action=action, quantity=Decimal(qty),
                        order_type=OrderType.LIMIT,
                        limit_price=Decimal(price), client_order_id="c",
                        mode=Mode.PAPER)


def _res(status, filled="0", avg=None):
    return OrderResult(client_order_id="c", venue_order_id="v", status=status,
                       filled_quantity=Decimal(filled),
                       average_fill_price=Decimal(avg) if avg else None,
                       fees=Decimal("0"), message="")


def _report(legs, **kw) -> ExecutionReport:
    rep = ExecutionReport(opportunity_id="op", success=kw.pop("success", False),
                          legs=legs)
    for k, v in kw.items():
        setattr(rep, k, v)
    return rep


# ----------------------------------------------------------- vocabulary


class TestVocabulary:
    def test_unknown_metric_is_refused(self):
        with pytest.raises(ValueError):
            tel.PipelineTally().add("made.up.metric")

    @pytest.mark.parametrize("label", ["has space", "quote'", "x" * 49, "a;b"])
    def test_free_text_and_long_labels_are_refused(self, label):
        with pytest.raises(ValueError):
            tel.PipelineTally().add(tel.OPP_STATUS, label)

    @pytest.mark.parametrize("label", ["TRADE", "<=250ms", "A:FILLED",
                                       "kalshi:rate_limit=observed"])
    def test_bounded_tokens_are_accepted(self, label):
        t = tel.PipelineTally()
        t.add(tel.OPP_STATUS, label)
        assert t.items() == {(tel.OPP_STATUS, label): 1}

    def test_zero_increments_are_not_recorded(self):
        t = tel.PipelineTally()
        t.add(tel.EXEC_RECOVERY, n=0)
        assert not t

    @pytest.mark.parametrize("age,bucket", [
        (None, "unknown"), (0.0, "<=250ms"), (250.0, "<=250ms"),
        (250.1, "<=1000ms"), (1000.0, "<=1000ms"), (1000.1, ">1000ms"),
    ])
    def test_book_age_buckets_keep_unknown_distinct(self, age, bucket):
        assert tel.book_age_bucket(age) == bucket

    def test_leg_gap_buckets(self):
        assert tel.leg_gap_bucket(None) == "unknown"
        assert tel.leg_gap_bucket(5000.0) == "<=5000ms"
        assert tel.leg_gap_bucket(5000.5) == ">5000ms"

    @pytest.mark.parametrize("violation,kind", [
        ("max_daily_loss: daily P&L -50 reached", "max_daily_loss"),
        ("max_unhedged_seconds: OP (kalshi/K) age 31s", "max_unhedged_seconds"),
        ("some new limit: whatever", "other"),
    ])
    def test_risk_violations_map_to_known_limit_names(self, violation, kind):
        assert tel.risk_kind(violation) == kind


# --------------------------------------------- blocked mandatory inputs


class TestBlockedInputAttribution:
    def test_unavailable_trade_fee_is_named_from_the_ledger(self):
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        op = _evaluate(calc)
        assert op.status.value == "BLOCK" and op.net_profit is None
        assert "trade_fee" in tel.blocked_by_kinds(op)
        assert tel.edge_class(op) == tel.EDGE_UNPRICED
        # gross edge survives as research even though it is unpriced
        assert op.gross_edge > 0

    def test_independence_block_is_its_own_category(self):
        op = _evaluate(_fee_calc(), independence=Independence.SAME_VENUE)
        assert "independence" in tel.blocked_by_kinds(op)

    def test_trade_that_survives_costs_has_no_blockers(self, complete_fee_calc):
        yes, no = _pair()
        op = evaluate_two_leg(
            yes, no, make_book("kalshi", "Y", asks=[("0.40", "500")]),
            make_book("forecast_ex", "N", asks=[("0.50", "500")]),
            complete_fee_calc, Mode.PAPER, independence=Independence.INDEPENDENT)
        assert op.status.value == "TRADE"
        assert tel.blocked_by_kinds(op) == set()
        assert tel.edge_class(op) == tel.EDGE_NET_POSITIVE

    def test_gross_edge_consumed_by_costs_is_distinguished(self, complete_fee_calc):
        yes, no = _pair()
        op = evaluate_two_leg(
            yes, no, make_book("kalshi", "Y", asks=[("0.50", "100")]),
            make_book("forecast_ex", "N", asks=[("0.49", "100")]),
            complete_fee_calc, Mode.PAPER, independence=Independence.INDEPENDENT)
        assert op is not None
        assert op.gross_edge > 0 and op.net_profit is not None
        assert op.net_profit <= 0
        assert tel.edge_class(op) == tel.EDGE_CONSUMED_BY_COSTS
        assert "net_not_positive" in tel.blocked_by_kinds(op)

    def test_book_skew_is_unknown_without_timestamps(self):
        a = make_book("kalshi", "Y", asks=[("0.40", "1")])
        b = make_book("forecast_ex", "N", asks=[("0.50", "1")])
        assert tel.book_skew_ms(a, b) == pytest.approx(0.0, abs=50.0)
        b.timestamp = None
        assert tel.book_skew_ms(a, b) is None
        assert tel.skew_bucket(None) == "unknown"


# ---------------------------------------------------- execution lifecycle


class TestClassifyExecution:
    def test_no_legs_is_precheck_rejected_not_a_fill(self):
        s = tel.classify_execution(_report([]))
        assert s.outcome == tel.OUTCOME_PRECHECK_REJECTED
        assert s.hedge_state == tel.HEDGE_NOT_SUBMITTED
        assert s.leg_a is None and s.leg_b is None and not s.completed_position

    def test_risk_block_names_the_limit(self):
        rep = _report([], risk_violations=["max_daily_loss: reached"])
        s = tel.classify_execution(rep)
        assert s.outcome == tel.OUTCOME_RISK_BLOCKED
        assert s.risk_categories == ("max_daily_loss",)

    def test_leg_a_exception_is_error_not_zero_fill(self):
        leg = LegOutcome(request=_req(), error="boom")
        s = tel.classify_execution(_report([leg]))
        assert s.outcome == tel.OUTCOME_LEG_A_ERROR
        assert s.leg_a.status == "ERROR"
        assert s.leg_a.average_price is None      # unknown, not a price

    def test_leg_a_rejected_has_no_exposure(self):
        leg = LegOutcome(request=_req(), result=_res(OrderStatus.REJECTED))
        s = tel.classify_execution(_report([leg]))
        assert s.outcome == tel.OUTCOME_LEG_A_NOT_FILLED
        assert s.hedge_state == tel.HEDGE_NO_FILL
        assert s.leg_a.filled == Decimal("0")

    def test_both_legs_filled_is_a_completed_position(self):
        a = LegOutcome(request=_req(), result=_res(OrderStatus.FILLED, "10", "0.40"))
        b = LegOutcome(request=_req("forecast_ex", "N", price="0.50"),
                       result=_res(OrderStatus.FILLED, "10", "0.50"))
        s = tel.classify_execution(_report([a, b], success=True,
                                           time_between_legs_ms=42.0))
        assert s.outcome == tel.OUTCOME_COMPLETED
        assert s.hedge_state == tel.HEDGE_HEDGED
        assert s.completed_position
        assert s.unhedged_remaining == Decimal("0")
        assert tel.leg_gap_bucket(s.time_between_legs_ms) == "<=250ms"

    def test_failed_hedge_keeps_exposure_and_recovery_effort(self):
        a = LegOutcome(request=_req(), result=_res(OrderStatus.FILLED, "10", "0.40"))
        b = LegOutcome(request=_req("forecast_ex", "N"),
                       result=_res(OrderStatus.REJECTED))
        rec = RecoveryOutcome(hedged=False, remaining_unhedged=Decimal("10"),
                              resolution="UNRESOLVED", attempts=3)
        rep = _report([a, b], success=False, recovery=rec,
                      notes=["kill switch engaged: unresolved paper exposure"])
        s = tel.classify_execution(rep)
        assert s.outcome == tel.OUTCOME_UNHEDGED
        assert s.hedge_state == tel.HEDGE_FAILED           # leg B never filled
        assert s.unhedged_remaining == Decimal("10")
        assert s.recovery_attempts == 3
        assert s.kill_switch_engaged is True
        assert not s.completed_position

    def test_partial_hedge_is_distinct_from_failed_hedge(self):
        a = LegOutcome(request=_req(), result=_res(OrderStatus.FILLED, "10", "0.40"))
        b = LegOutcome(request=_req("forecast_ex", "N"),
                       result=_res(OrderStatus.PARTIALLY_FILLED, "4", "0.50"))
        rec = RecoveryOutcome(hedged=False, remaining_unhedged=Decimal("6"),
                              resolution="UNRESOLVED", attempts=2)
        s = tel.classify_execution(_report([a, b], success=False, recovery=rec))
        assert s.hedge_state == tel.HEDGE_PARTIAL
        assert s.unhedged_remaining == Decimal("6")

    def test_recovery_that_hedges_is_recovered(self):
        a = LegOutcome(request=_req(), result=_res(OrderStatus.FILLED, "10", "0.40"))
        b = LegOutcome(request=_req("forecast_ex", "N"),
                       result=_res(OrderStatus.REJECTED))
        rec = RecoveryOutcome(hedged=True, open_hedged_quantity=Decimal("10"),
                              resolution="HEDGED", attempts=1)
        s = tel.classify_execution(_report([a, b], success=True, recovery=rec))
        assert s.outcome == tel.OUTCOME_RECOVERED and s.completed_position

    def test_recovery_that_unwinds_is_flattened_not_a_position(self):
        a = LegOutcome(request=_req(), result=_res(OrderStatus.FILLED, "10", "0.40"))
        b = LegOutcome(request=_req("forecast_ex", "N"),
                       result=_res(OrderStatus.REJECTED))
        rec = RecoveryOutcome(hedged=True, open_hedged_quantity=Decimal("0"),
                              resolution="FLATTENED", attempts=1)
        s = tel.classify_execution(_report([a, b], success=True, recovery=rec))
        assert s.outcome == tel.OUTCOME_FLATTENED
        assert not s.completed_position

    def test_attempt_record_keeps_unobserved_fields_null(self):
        s = tel.classify_execution(_report([]))
        row = tel.attempt_record("op", datetime.now(timezone.utc), s)
        assert row["time_between_legs_ms"] is None
        assert row["leg_b_status"] == "" and row["leg_b_filled"] is None
        assert row["unhedged_remaining"] is None
        assert json.loads(row["risk_violations"]) == []

    def test_each_attempt_is_counted_once_per_family(self):
        a = LegOutcome(request=_req(), result=_res(OrderStatus.FILLED, "10", "0.40"))
        b = LegOutcome(request=_req("forecast_ex", "N"),
                       result=_res(OrderStatus.REJECTED))
        rec = RecoveryOutcome(hedged=False, remaining_unhedged=Decimal("10"),
                              resolution="UNRESOLVED", attempts=2)
        t = tel.PipelineTally()
        tel.tally_execution(t, tel.classify_execution(
            _report([a, b], success=False, recovery=rec)))
        items = t.items()
        assert items[(tel.EXEC_ATTEMPT, tel.OUTCOME_UNHEDGED)] == 1
        assert items[(tel.EXEC_HEDGE, tel.HEDGE_FAILED)] == 1
        assert items[(tel.EXEC_SUBMISSION, "A:FILLED")] == 1
        assert items[(tel.EXEC_SUBMISSION, "B:REJECTED")] == 1
        assert items[(tel.EXEC_RECOVERY, "")] == 2


class TestRecoveryEffortIsMeasured:
    class _Engine:
        def __init__(self):
            self.calls = 0

        async def submit_order(self, request):
            self.calls += 1
            return OrderResult(client_order_id=request.client_order_id,
                               venue_order_id=None, status=OrderStatus.REJECTED,
                               filled_quantity=Decimal("0"),
                               average_fill_price=None, fees=Decimal("0"),
                               message="no liquidity")

    class _Sink:
        async def alert(self, title, body):
            return None

    def test_attempts_equal_orders_actually_submitted(self):
        limits = RiskLimits.from_config(
            yaml.safe_load((ROOT / "config" / "risk_limits.yaml").read_text()))
        handler = PartialFillHandler(limits, self._Sink())
        engine = self._Engine()
        op = SimpleNamespace(opportunity_id="op-1")
        filled = _req("kalshi", "K", qty="10", price="0.40")
        unfilled = _req("forecast_ex", "N", qty="10", price="0.50")
        outcome = asyncio.run(handler.handle(
            op=op, engine=engine, filled_leg=filled, filled_quantity=Decimal("10"),
            unfilled_leg=unfilled, unfilled_quantity=Decimal("10")))
        assert outcome.attempts == engine.calls > 0
        assert outcome.hedged is False


# -------------------------------------------------------------- storage


def _store(tmp_path, mode=Mode.PAPER) -> Repository:
    return Repository(tmp_path / "paper.sqlite3", mode)


class TestRepositoryCounters:
    def test_upsert_accumulates_into_one_row_per_key(self, tmp_path):
        repo = _store(tmp_path)
        for _ in range(3):
            repo.add_pipeline_counts("2026-10-10", {(tel.SCAN_CYCLES, ""): 1,
                                                    (tel.OPP_STATUS, "BLOCK"): 2})
        assert repo.pipeline_counts("2026-10-10") == {
            (tel.SCAN_CYCLES, ""): 3, (tel.OPP_STATUS, "BLOCK"): 6}

    def test_row_count_is_bounded_by_keys_not_cycles(self, tmp_path):
        repo = _store(tmp_path)
        keys = {(tel.OPP_STATUS, "TRADE"): 1, (tel.OPP_STATUS, "BLOCK"): 1}
        for _ in range(200):          # 200 cycles on one UTC day
            repo.add_pipeline_counts("2026-10-10", keys)
        from prediction_arb.database.models import PipelineCounterRow
        from sqlalchemy import func, select
        from sqlalchemy.orm import Session
        with Session(repo.engine) as s:
            assert s.scalar(select(func.count(PipelineCounterRow.id))) == 2

    def test_days_are_kept_apart(self, tmp_path):
        repo = _store(tmp_path)
        repo.add_pipeline_counts("2026-10-10", {(tel.SCAN_CYCLES, ""): 1})
        repo.add_pipeline_counts("2026-10-11", {(tel.SCAN_CYCLES, ""): 5})
        assert repo.pipeline_counts("2026-10-10") == {(tel.SCAN_CYCLES, ""): 1}
        assert repo.pipeline_counts("2026-10-11") == {(tel.SCAN_CYCLES, ""): 5}
        assert repo.pipeline_counts("2026-10-12") == {}

    def test_read_only_handle_cannot_write_counters(self, tmp_path):
        _store(tmp_path)                              # create the store
        ro = Repository(tmp_path / "paper.sqlite3", Mode.PAPER, read_only=True)
        from sqlalchemy.exc import OperationalError
        with pytest.raises(OperationalError):
            ro.add_pipeline_counts("2026-10-10", {(tel.SCAN_CYCLES, ""): 1})

    def test_attempt_round_trip_keeps_null_as_null(self, tmp_path):
        repo = _store(tmp_path)
        summary = tel.classify_execution(_report([]))
        repo.record_execution_attempt(**tel.attempt_record(
            "op-x", datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
            summary))
        (row,) = repo.attempts_for_opportunity("op-x")
        assert row.outcome == tel.OUTCOME_PRECHECK_REJECTED
        assert row.time_between_legs_ms is None
        assert row.leg_b_filled is None


class TestMigration5:
    def test_schema4_store_gains_tables_without_rewriting_rows(self, tmp_path):
        from prediction_arb.database.migrations.runner import run_migrations
        repo = _store(tmp_path)
        repo.add_pipeline_counts("2026-10-10", {(tel.SCAN_CYCLES, ""): 1})
        with repo.engine.begin() as conn:            # roll back to schema 4
            conn.execute(text("DROP TABLE pipeline_counters"))
            conn.execute(text("DROP TABLE execution_attempts"))
            conn.execute(text("DELETE FROM schema_migrations WHERE version = 5"))
        repo.engine.dispose()
        ro = Repository(tmp_path / "paper.sqlite3", Mode.PAPER, read_only=True)
        assert ro.pipeline_store_ready() is False
        report = build_pipeline_report(ro, "2026-10-10")
        assert report["status"].startswith(UNAVAILABLE)
        upgraded = _store(tmp_path)                  # normal open migrates
        assert upgraded.pipeline_store_ready() is True
        with upgraded.engine.connect() as conn:
            assert conn.execute(text(
                "SELECT version FROM schema_migrations ORDER BY version")
            ).scalars().all() == [1, 2, 3, 4, 5]
        run_migrations(upgraded.engine)              # idempotent


# ---------------------------------------------------------- adapters


class TestAdapterRateLimitCounting:
    def test_kalshi_429_is_counted_even_when_retried(self):
        from prediction_arb.adapters.ratelimit import RetryPolicy
        from .test_kalshi_phase2 import _adapter, _inject_client
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, json={"error": "slow down"})
            return httpx.Response(200, json={"markets": []})

        adapter = _adapter()
        adapter._retry = RetryPolicy(base_delay=0.0)   # no real backoff sleep
        assert adapter.rate_limit_events == 0
        _inject_client(adapter, handler)
        asyncio.run(adapter._get("/markets"))
        assert adapter.rate_limit_events == 1      # counted, then absorbed

    def test_polymarket_counter_exists_and_starts_at_zero(self):
        from prediction_arb.adapters.polymarket_us import PolymarketUSAdapter
        assert PolymarketUSAdapter().rate_limit_events == 0


# ------------------------------------------------- paper engine end-to-end


def _paper_config(venues_cash: dict[str, str], *, fill_prob: float = 1.0):
    from prediction_arb.core.config import AppConfig
    limits = yaml.safe_load((ROOT / "config" / "risk_limits.yaml").read_text())
    # These tests exercise the lifecycle, not the caps: lift every cap so a
    # 500-contract paper trade is executable. Risk enforcement itself is
    # covered by tests/test_risk_enforcement.py.
    big = 10_000_000
    limits["limits"].update({k: big for k in (
        "max_total_capital", "max_capital_per_venue", "max_capital_per_event",
        "max_capital_per_trade", "max_unhedged_exposure", "max_daily_loss")})
    limits["partial_fill"].update({k: big for k in (
        "max_unhedged_contracts", "max_unhedged_dollars", "max_emergency_loss")})
    return AppConfig(
        mode=Mode.PAPER,
        settings={
            "arbitrage": {"minimum_net_profit": 2,
                          "minimum_net_return": 0.005,
                          "maximum_settlement_delay_days": 5},
            "execution": {"max_book_age_ms": 1000},
            "scanner": {"poll_interval_seconds": 0,
                        "max_markets_per_venue": 50},
            "compliance": {"jurisdiction": "US-UT"},
            "cost_of_capital": {"annual_rate": 0.05},
        },
        venues={}, fees={}, risk_limits=limits,
        mode_config={"mode": "paper", "execution_engine": "PaperExecutionEngine",
                     "database": "prediction_arb_paper",
                     "allow_live_orders": False,
                     "paper_starting_cash": venues_cash,
                     "simulated_fill_probability_at_touch": fill_prob,
                     "simulated_latency_ms": 0})


def _engine(tmp_path, adapters, fee_calc, **cfg):
    from prediction_arb.matching.llm_matcher import LLMMatcher
    from prediction_arb.modes.paper.engine import PaperEngine
    repo = Repository(tmp_path / "paper.sqlite3", Mode.PAPER)
    engine = PaperEngine(_paper_config(
        {"kalshi": "10000", "forecast_ex": "10000"}, **cfg),
        adapters, repo, fee_calc, LLMMatcher(client=None),
        Console(file=io.StringIO()))
    return engine, repo


def _two_venue_adapters(monkeypatch_books=True):
    from .test_paper_streaming import FakeAdapter
    k = make_contract(venue="kalshi", contract_id="K")
    n = make_contract(venue="forecast_ex", contract_id="N")
    kalshi = FakeAdapter("kalshi", [k], streaming=False, credentials=False)
    fx = FakeAdapter("forecast_ex", [n], streaming=False, credentials=False)
    kalshi.get_order_book = lambda mid: _async(
        make_book("kalshi", "K", asks=[("0.40", "500")], bids=[("0.38", "500")]))
    fx.get_order_book = lambda mid: _async(
        make_book("forecast_ex", "N", asks=[("0.50", "500")], bids=[("0.48", "500")]))
    return {"kalshi": kalshi, "forecast_ex": fx}


async def _async(value):
    return value


class TestPaperEngineTelemetry:
    def test_flush_happens_when_a_cycle_fails_midway(self, tmp_path,
                                                     complete_fee_calc,
                                                     monkeypatch):
        from prediction_arb.modes.paper import engine as paper_mod
        engine, repo = _engine(tmp_path, _two_venue_adapters(), complete_fee_calc)

        def boom(*a, **kw):
            raise RuntimeError("scan exploded")
        monkeypatch.setattr(paper_mod, "run_arbitrage_scan", boom)
        asyncio.run(engine.run(cycles=2))
        counts = repo.pipeline_counts(datetime.now(timezone.utc).date().isoformat())
        assert counts[(tel.SCAN_CYCLES, "")] == 2
        # the failed scan never produced an opportunity observation
        assert not any(m == tel.OPP_STATUS for (m, _l) in counts)

    def test_blocked_and_executed_opportunities_are_both_recorded(
            self, tmp_path, complete_fee_calc, monkeypatch):
        from prediction_arb.modes.paper import engine as paper_mod
        engine, repo = _engine(tmp_path, _two_venue_adapters(), complete_fee_calc)
        yes, no = _pair(no_venue="forecast_ex")
        yes = dataclasses.replace(yes, venue="kalshi", contract_id="K")
        no = dataclasses.replace(no, venue="forecast_ex", contract_id="N")
        trade = evaluate_two_leg(
            yes, no, make_book("kalshi", "K", asks=[("0.40", "500")]),
            make_book("forecast_ex", "N", asks=[("0.50", "500")]),
            complete_fee_calc, Mode.PAPER, independence=Independence.INDEPENDENT)
        blocked = _evaluate(_fee_calc({"kalshi": {
            "trade_calculation_status": "INCOMPLETE"}}))

        async def scan(*a, **kw):
            return [blocked, trade]
        monkeypatch.setattr(paper_mod, "run_arbitrage_scan", scan)
        asyncio.run(engine.run(cycles=1))

        today = datetime.now(timezone.utc).date().isoformat()
        counts = repo.pipeline_counts(today)
        assert counts[(tel.OPP_STATUS, "BLOCK")] == 1
        assert counts[(tel.OPP_STATUS, "TRADE")] == 1
        assert counts[(tel.OPP_BLOCKED_BY, "trade_fee")] == 1
        assert counts[(tel.OPP_EDGE, tel.EDGE_UNPRICED)] == 1
        assert counts[(tel.OPP_EDGE, tel.EDGE_NET_POSITIVE)] == 1
        # skew: the executable pair's books are both held and time-stamped
        # (near-simultaneous, so the lowest bucket); the blocked pair's books
        # are not in the held store, so its skew is UNKNOWN, never zero.
        assert counts[(tel.BOOK_SKEW, "<=250ms")] == 1
        assert counts[(tel.BOOK_SKEW, tel.UNKNOWN_BUCKET)] == 1
        # the executable one went through the coordinator and left a row
        attempts = repo.execution_attempts_for_day(today)
        assert [a.outcome for a in attempts] == [tel.OUTCOME_COMPLETED]
        assert attempts[0].leg_a_status == "FILLED"
        assert attempts[0].leg_b_status == "FILLED"
        assert attempts[0].time_between_legs_ms is not None
        assert counts[(tel.EXEC_ATTEMPT, tel.OUTCOME_COMPLETED)] == 1
        # the blocked one never reached execution: no attempt row for it
        assert all(a.opportunity_id != blocked.opportunity_id for a in attempts)

    def test_unobservable_adapter_families_are_unavailable_not_zero(
            self, tmp_path, complete_fee_calc):
        engine, repo = _engine(tmp_path, _two_venue_adapters(), complete_fee_calc)
        asyncio.run(engine.run(cycles=1))
        report = build_pipeline_report(
            Repository(tmp_path / "paper.sqlite3", Mode.PAPER, read_only=True),
            datetime.now(timezone.utc).date().isoformat())
        assert report["status"] == "AVAILABLE"
        kalshi = report["adapters"]["kalshi"]
        assert kalshi["rate_limited_responses"].startswith(UNAVAILABLE)
        assert kalshi["feed"] == "unavailable"
        assert kalshi["feed_reconnects"].startswith(UNAVAILABLE)

    @staticmethod
    def _venue_with_feed(contracts, stats):
        from .test_paper_streaming import FakeAdapter
        fake = FakeAdapter("kalshi", contracts, streaming=False, credentials=False)
        fake.rate_limit_events = 0
        fake.streaming_stats = lambda: stats
        return fake

    def test_polled_feed_reports_not_applicable_not_zero(self, tmp_path,
                                                         complete_fee_calc):
        k = make_contract(venue="kalshi", contract_id="K")
        fake = self._venue_with_feed([k], {"connected": False, "markets": [],
                                           "stats": None})
        engine, repo = _engine(tmp_path, {"kalshi": fake}, complete_fee_calc)
        asyncio.run(engine.run(cycles=1))
        report = build_pipeline_report(repo, datetime.now(timezone.utc)
                                       .date().isoformat())
        kalshi = report["adapters"]["kalshi"]
        assert kalshi["feed"] == "polled"
        assert kalshi["rate_limited_responses"] == 0   # observed: none seen
        assert kalshi["feed_reconnects"].startswith("N/A")

    def test_feed_reconnects_are_counted_as_deltas_across_cycles(
            self, tmp_path, complete_fee_calc):
        k = make_contract(venue="kalshi", contract_id="K")
        stats = {"connected": True, "markets": ["K"],
                 "stats": {"reconnects": 2}}
        fake = self._venue_with_feed([k], stats)
        engine, repo = _engine(tmp_path, {"kalshi": fake}, complete_fee_calc)
        asyncio.run(engine.run(cycles=1))
        stats["stats"]["reconnects"] = 3            # one more recovery
        asyncio.run(engine.run(cycles=1))
        counts = repo.pipeline_counts(datetime.now(timezone.utc).date().isoformat())
        assert counts[(tel.ADAPTER_FEED_RECONNECT, "kalshi")] == 3
        assert counts[(tel.ADAPTER_TELEMETRY, "kalshi:feed=streaming")] == 2

    def test_book_ages_are_recorded_for_every_held_book(self, tmp_path,
                                                        complete_fee_calc):
        engine, repo = _engine(tmp_path, _two_venue_adapters(), complete_fee_calc)
        asyncio.run(engine.run(cycles=1))
        counts = repo.pipeline_counts(datetime.now(timezone.utc).date().isoformat())
        observed = sum(n for (m, _l), n in counts.items() if m == tel.BOOK_AGE)
        assert observed == 2
        assert all(l != "unknown" for (m, l) in counts if m == tel.BOOK_AGE)


# ----------------------------------------------------- report & lifecycle


class TestReportHonesty:
    def test_store_without_counters_for_the_day_is_unavailable(self, tmp_path):
        repo = _store(tmp_path)
        report = build_pipeline_report(repo, "2026-10-10")
        assert report["status"].startswith(UNAVAILABLE)
        assert "candidate_pairs" not in report      # no zero-filled sections

    def test_missing_telemetry_tables_say_so(self, tmp_path):
        repo = _store(tmp_path)
        with repo.engine.begin() as conn:
            conn.execute(text("DROP TABLE pipeline_counters"))
            conn.execute(text("DROP TABLE execution_attempts"))
        assert build_pipeline_report(repo, "2026-10-10")["status"].startswith(
            UNAVAILABLE)

    def test_dominant_blockers_and_gross_vs_net_from_recorded_rows(
            self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        blocked = _evaluate(_fee_calc({"kalshi": {
            "trade_calculation_status": "INCOMPLETE"}}))
        repo.save_opportunity(blocked)
        day = blocked.timestamp.date().isoformat()   # counters and rows share a day
        repo.add_pipeline_counts(day, {
            (tel.SCAN_CYCLES, ""): 4,
            (tel.OPP_BLOCKED_BY, "trade_fee"): 4,
            (tel.OPP_BLOCKED_BY, "independence"): 1,
            (tel.OPP_STATUS, "BLOCK"): 4,
            (tel.OPP_EDGE, tel.EDGE_UNPRICED): 4,
            (tel.PAIRS_CONSIDERED, ""): 9,
            (tel.PAIRS_DET_PASSED, ""): 2,
            (tel.PAIRS_DET_REJECTED, "payout"): 7,
            (tel.PAIRS_DET_REJECTED, "units"): 2,
            (tel.ADAPTER_TELEMETRY, "kalshi:rate_limit=observed"): 4,
            (tel.ADAPTER_TELEMETRY, "kalshi:feed=polled"): 4,
            (tel.ADAPTER_RATE_LIMITED, "kalshi"): 1,
            (tel.ADAPTER_ERROR, "kalshi"): 2,
        })
        report = build_pipeline_report(repo, day)
        assert report["status"] == "AVAILABLE"
        # dominant blocker first, by observation count
        assert report["opportunities"]["dominant_blockers"][0] == {
            "input": "trade_fee", "observations": 4}
        # where candidates are lost, by deterministic check
        assert report["candidate_pairs"]["generated"] == 9
        assert report["candidate_pairs"]["deterministic_rejected_by_check"] == {
            "payout": 7, "units": 2}
        # gross vs net from the stored row: unpriced, so nothing is summed
        stored = report["opportunities"]["stored_observations"]
        assert stored["observations"] == 1
        assert stored["unpriced_observations"] == 1
        assert stored["costed_observations"] == 0
        assert stored["costed_net_edge_sum"] == "0.0000"
        # adapter counters: 429s observed (1), feed polled, no reconnects
        kalshi = report["adapters"]["kalshi"]
        assert kalshi["rate_limited_responses"] == 1
        assert kalshi["feed"] == "polled"
        assert kalshi["feed_reconnects"].startswith("N/A")
        assert kalshi["errors"] == 2

    def test_report_prints_unavailable_plainly(self, tmp_path):
        repo = _store(tmp_path)
        console = Console(record=True, width=160)
        print_pipeline_report(console, build_pipeline_report(repo, "2026-10-10"))
        text_out = console.export_text()
        assert UNAVAILABLE in text_out
        assert "generated" not in text_out

    def test_lifecycle_reads_one_opportunity_end_to_end(self, tmp_path,
                                                       complete_fee_calc):
        repo = _store(tmp_path)
        blocked = _evaluate(_fee_calc({"kalshi": {
            "trade_calculation_status": "INCOMPLETE"}}))
        repo.save_opportunity(blocked)
        life = build_lifecycle(repo, blocked.opportunity_id)
        assert life["found"] is True
        assert life["execution_attempts"] == 0
        ev = life["latest_evaluation"]
        assert ev["net_profit"] is None and ev["fees"] is None   # UNAVAILABLE
        assert ev["unavailable_costs"]
        console = Console(record=True, width=160)
        print_lifecycle(console, life)
        assert "unavailable mandatory inputs" in console.export_text()

    def test_lifecycle_for_unknown_id_is_explicit(self, tmp_path):
        repo = _store(tmp_path)
        life = build_lifecycle(repo, "does-not-exist")
        assert life["found"] is False
        console = Console(record=True, width=160)
        print_lifecycle(console, life)
        assert "No evaluation or execution attempt recorded" in console.export_text()

    def test_lifecycle_shows_the_attempt_and_its_legs(self, tmp_path):
        repo = _store(tmp_path)
        a = LegOutcome(request=_req(), result=_res(OrderStatus.FILLED, "10", "0.40"))
        b = LegOutcome(request=_req("forecast_ex", "N"),
                       result=_res(OrderStatus.REJECTED))
        rec = RecoveryOutcome(hedged=False, remaining_unhedged=Decimal("10"),
                              resolution="UNRESOLVED", attempts=2)
        summary = tel.classify_execution(_report([a, b], success=False,
                                                 recovery=rec,
                                                 time_between_legs_ms=80.0))
        repo.record_execution_attempt(**tel.attempt_record(
            "op-7", datetime(2026, 10, 10, 9, tzinfo=timezone.utc),
            summary))
        life = build_lifecycle(repo, "op-7")
        (att,) = life["recent_attempts"]
        assert att["outcome"] == tel.OUTCOME_UNHEDGED
        assert att["hedge_state"] == tel.HEDGE_FAILED
        assert att["leg_a"]["status"] == "FILLED"
        assert att["leg_b"]["status"] == "REJECTED"
        assert att["unhedged_remaining"] == "10"
        assert att["recovery_attempts"] == 2
        console = Console(record=True, width=200)
        print_lifecycle(console, life)
        out = console.export_text()
        assert "outcome=UNHEDGED" in out and "hedge=FAILED_HEDGE" in out

    def test_cli_report_is_read_only_and_never_creates_a_store(self, tmp_path,
                                                              monkeypatch):
        import prediction_arb.main as main
        missing = tmp_path / "data" / "paper" / "prediction_arb_paper.sqlite3"
        cfg = SimpleNamespace(mode=Mode.PAPER, database_file=missing)
        monkeypatch.setattr(main, "console", Console(record=True, width=160))
        assert main.cmd_pipeline_report(cfg, "2026-10-10", None) == 0
        assert not missing.exists() and not missing.parent.exists()
        assert "UNAVAILABLE" in main.console.export_text()

    def test_cli_rejects_a_malformed_date(self, tmp_path, monkeypatch):
        import prediction_arb.main as main
        monkeypatch.setattr(main, "console", Console(record=True, width=160))
        cfg = SimpleNamespace(mode=Mode.PAPER, database_file=tmp_path / "x.sqlite3")
        assert main.cmd_pipeline_report(cfg, "10/10/2026", None) == 2
