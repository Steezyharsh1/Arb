"""Arb.13: deterministic temporal, data-honesty and historical fill tests.

All contracts/depth/LLM verdicts, complete fees and ForecastEx timing are
TEST-ONLY. No venue, account, network, live placement or settlement is used.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from rich.console import Console
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from prediction_arb.backtesting.engine import BacktestEngine
from prediction_arb.backtesting.policy import ReplayPolicy
from prediction_arb.backtesting.simulator import BacktestFillSimulator, HistoricalFillSimulator
from prediction_arb.core.config import load_config
from prediction_arb.core.types import Mode, OrderBook, PriceLevel, Side
from prediction_arb.database.models import OpportunityRow, OrderBookSnapshotRow
from prediction_arb.database.repository import Repository
from prediction_arb.fees.calculator import FeeCalculator
from prediction_arb.matching.llm_matcher import LLMMatcher
from prediction_arb.modes.historical.data_provider import HistoricalDataProvider
from prediction_arb.modes.historical.engine import HistoricalEngine
from prediction_arb.orderbooks.orderbook import book_for_side

from .conftest import SETTLEMENT_TEST_NOW, make_contract

T0 = SETTLEMENT_TEST_NOW
D = Decimal


def _pair():
    return [make_contract(venue="kalshi", contract_id="K-ARB13"),
            make_contract(venue="forecast_ex", contract_id="F-ARB13")]


def _llm():
    async def scripted(_prompt):
        return json.dumps({
            "match": True, "confidence": 0.999,
            "reasoning_summary": "TEST-ONLY identical economics; not external evidence",
            "event_identity": True, "outcome_identity": True,
            "settlement_identity": True, "resolution_identity": True,
            "threshold_identity": True, "time_identity": True, "payout_identity": True,
            "ambiguities": [],
        })
    return LLMMatcher(client=scripted, model_version="ARB13-offline-synthetic")


def _settings(**arb):
    return {"arbitrage": {"minimum_net_profit": "2", "minimum_net_return": "0.005",
                           "maximum_book_age_ms": 1000, **arb},
            "cost_of_capital": {"annual_rate": "0.05"}}


def _simulation(**overrides):
    return {"enabled": True, "decision_latency_ms": 100, "leg_latency_ms": 100,
            "max_pair_skew_ms": 250, "displayed_depth_fraction": "1",
            "starting_cash": {"kalshi": "100", "forecast_ex": "100"}, **overrides}


def _book(contract, at=T0, *, asks=None, bids=None, quote_time=None, legacy=False):
    if asks is None:
        asks = [("0.40", "10")] if contract.venue == "kalshi" else [("0.72", "10")]
    if bids is None:
        bids = [("0.38", "10")] if contract.venue == "kalshi" else [("0.70", "10")]
    return OrderBook(
        contract.venue, contract.contract_id,
        bids=[PriceLevel(D(p), D(q)) for p, q in bids],
        asks=[PriceLevel(D(p), D(q)) for p, q in asks],
        timestamp=quote_time or at, received_at=None if legacy else at,
        timestamp_source=None if legacy else ("venue" if quote_time else "local_receipt"),
        outcome_side=None if legacy else Side.YES)


def _store(tmp_path, pair=None, base=T0):
    repo = Repository(tmp_path / "recorded.sqlite3", Mode.COLLECT)
    for contract in pair or _pair():
        repo.record_contract(contract, observed_at=base - timedelta(seconds=1))
    return repo


def _success_data(repo, *, base=T0, partial_second=False):
    yes, other = _pair()
    repo.save_orderbook_snapshot(_book(yes, base))
    repo.save_orderbook_snapshot(_book(other, base))
    repo.save_orderbook_snapshot(_book(yes, base + timedelta(milliseconds=100)))
    repo.save_orderbook_snapshot(_book(
        other, base + timedelta(milliseconds=200),
        bids=[("0.70", "4"), ("0.60", "6")] if partial_second else None))


def _run(repo, calc, *, simulation=None, settings=None, **kwargs):
    engine = BacktestEngine(repo, fee_calc=calc, llm=_llm(), settings=settings or _settings(),
                            simulation_config=simulation if simulation is not None else _simulation(),
                            **kwargs)
    return engine, asyncio.run(engine.run_backtest())


def _downgrade_to_schema3(repo):
    """Make an actual old store, not a v4 store with populated new columns."""
    from prediction_arb.database.migrations.runner import _M004_SNAPSHOT_COLUMNS
    with repo.engine.begin() as conn:
        conn.execute(text("DROP INDEX ix_orderbook_snapshots_received_at"))
        for name, _ in _M004_SNAPSHOT_COLUMNS:
            conn.execute(text(f"ALTER TABLE orderbook_snapshots DROP COLUMN {name}"))
        conn.execute(text("ALTER TABLE opportunities DROP COLUMN evaluation_json"))
        conn.execute(text("DELETE FROM schema_migrations WHERE version=4"))
        conn.execute(text("UPDATE meta SET value='3' WHERE key='schema_version'"))


class TestDataTemporalIntegrity:
    def test_empty_dataset_has_null_pnl_and_full_limitation_report(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _, result = _run(repo, complete_fee_calc)
        report = result.to_dict()
        assert result.frames_available == result.opportunities_observed == 0
        assert report["observed_data"]["snapshots_read"] == 0
        assert report["simulation"]["attempted_entries"] == 0
        assert report["simulation"]["net_simulated_pnl"] is None
        assert report["theoretical_net_edge_observations"]["net_edge_sum_known_subset"] is None
        assert report["theoretical_depth_coverage"]["full_coverage_rate"] is None
        assert len(report["observed_data"]["minimum_additional_collection"]) >= 5
        assert "modelled_equity" not in report and "fills" not in report

    def test_injected_future_decision_books_cannot_borrow_valid_provider_evidence(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        repo.save_orderbook_snapshot(_book(yes, asks=[("0.80", "10")], bids=[("0.78", "10")]))
        repo.save_orderbook_snapshot(_book(other))
        repo.save_orderbook_snapshot(_book(yes, T0 + timedelta(milliseconds=100)))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=200)))
        engine = BacktestEngine(repo, fee_calc=complete_fee_calc, llm=_llm(),
                                settings=_settings(), simulation_config=_simulation())
        frames = engine.provider.load_frames(None, None)
        # A library-injected future cheap quote is NOT the decision-time quote.
        # The provider has genuine expensive t=0 evidence and cheap arrival depth;
        # it must not certify calculations made from the later quote at t=0.
        false_decision = [(T0, frames[0][1], frames[1][2])]
        result = asyncio.run(engine.run_backtest(frames=false_decision))
        assert result.costed_positive_opportunities == 1  # separately labelled theory
        assert result.executable_observed == 0
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["net_simulated_pnl"] is None
        op = engine.opportunity_frames[0][1][0]
        assert any("decision book differs" in reason for reason in op.block_reasons)

    def test_missing_store_cli_engine_reports_same_empty_metrics(self, tmp_path, complete_fee_calc):
        config = load_config("historical")
        own = Repository(tmp_path / "own.sqlite3", Mode.HISTORICAL)
        console = Console(file=io.StringIO(), width=240, force_terminal=False)
        engine = HistoricalEngine(config, own, complete_fee_calc, _llm(), console)
        summary = asyncio.run(engine.run())
        assert summary["frames"] == 0
        assert summary["recorded_store"] is None
        assert summary["metrics"]["simulation"]["net_simulated_pnl"] is None
        assert summary["metrics"]["observed_data"]["snapshots_read"] == 0
        assert "No recorded historical order-book data" in console.file.getvalue()
        assert "NOT fabricate" in console.file.getvalue()

    def test_no_minute_bucket_lookahead_and_stale_prices_are_theory_only(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        first, second = T0 + timedelta(seconds=10), T0 + timedelta(seconds=55)
        repo.save_orderbook_snapshot(_book(yes, first))
        repo.save_orderbook_snapshot(_book(other, second))
        engine, result = _run(repo, complete_fee_calc)
        assert [ts for ts, _ in engine.opportunity_frames] == [first, second]
        assert engine.opportunity_frames[0][1] == []  # future second venue is unknown
        assert result.opportunities_observed == 1  # last-known theoretical gap only
        assert result.executable_observed == 0
        op = engine.opportunity_frames[1][1][0]
        assert op.evaluation_metadata["costed_trade_before_data_gates"]
        assert any("stale" in r for r in op.evaluation_metadata["data_limitations"])
        assert any("skew" in r for r in op.evaluation_metadata["data_limitations"])
        assert not op.is_executable and op.status.value == "BLOCK"
        assert op.timestamp == op.first_seen == op.last_seen == second
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["net_simulated_pnl"] is None
        assert result.theoretical_net.net_edge_sum_known_subset > 0

    def test_asof_state_never_contains_future_received_quotes(self, tmp_path):
        repo = _store(tmp_path)
        yes, other = _pair()
        # The source quote is OLD, but only received 500ms later. Sorting
        # on source time would leak it into an earlier decision.
        repo.save_orderbook_snapshot(_book(yes))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=500), quote_time=T0))
        provider = HistoricalDataProvider(repo)
        frames = provider.load_frames(None, None)
        assert (other.venue, other.contract_id) not in frames[0][2]
        assert provider.observation_at((other.venue, other.contract_id), T0) is None
        assert len(frames[-1][2]) == 2

    def test_out_of_order_input_rows_are_sorted_deterministically(self, tmp_path):
        repo = _store(tmp_path)
        _success_data(repo)
        rows = repo.orderbook_snapshots()
        reversed_repo = SimpleNamespace(orderbook_snapshots=lambda *_: list(reversed(rows)),
                                        get_market_row=repo.get_market_row)
        ordered = HistoricalDataProvider(repo)
        shuffled = HistoricalDataProvider(reversed_repo)
        first = ordered.load_frames(None, None)
        second = shuffled.load_frames(None, None)
        assert [(ts, cs, bs) for ts, cs, bs in first] == second
        assert shuffled.data_report()["out_of_order_input_rows"] > 0
        for ts, _, books in second:
            assert all(book.received_at <= ts and book.timestamp <= ts for book in books.values())

    def test_late_source_regression_does_not_replace_newer_book(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        repo.save_orderbook_snapshot(_book(yes))
        repo.save_orderbook_snapshot(_book(other))
        repo.save_orderbook_snapshot(_book(yes, T0 + timedelta(milliseconds=100),
                                           quote_time=T0 - timedelta(seconds=1),
                                           asks=[("0.20", "10")], bids=[("0.18", "10")]))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=200)))
        engine, result = _run(repo, complete_fee_calc)
        assert result.data_quality["source_timestamp_regressions_ignored"] == 1
        assert engine.provider.observation_at((yes.venue, yes.contract_id), T0 + timedelta(milliseconds=100)) \
            .book.asks[0].price == D("0.40")
        fill = result.simulation["entries"][0]["orders"][0]
        assert fill["purchase_cost"] == "4.00" and fill["snapshot_id"] == 1

    def test_future_source_timestamp_is_unavailable_not_fresh(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        repo.save_orderbook_snapshot(_book(yes, quote_time=T0 + timedelta(seconds=1)))
        repo.save_orderbook_snapshot(_book(other))
        _, result = _run(repo, complete_fee_calc)
        assert result.data_quality["invalid_or_future_source_timestamps"] == 1
        assert result.opportunities_observed == 0
        assert result.simulation["net_simulated_pnl"] is None

    @pytest.mark.parametrize("corruption", ["malformed", "empty", "crossed", "nonfinite"])
    def test_invalid_update_invalidates_previous_book(self, tmp_path, corruption):
        repo = _store(tmp_path)
        yes, other = _pair()
        _success_data(repo)
        broken = _book(yes, T0 + timedelta(milliseconds=300))
        if corruption == "empty":
            broken.bids, broken.asks = [], []
        elif corruption == "crossed":
            broken.bids = [PriceLevel(D("0.90"), D(10))]
        elif corruption == "nonfinite":
            broken.asks = [PriceLevel(D("NaN"), D(10))]
        repo.save_orderbook_snapshot(broken)
        if corruption == "malformed":
            with repo.engine.begin() as conn:
                conn.execute(text("UPDATE orderbook_snapshots SET asks_json='not json' WHERE id=5"))
        provider = HistoricalDataProvider(repo)
        frames = provider.load_frames(None, None)
        assert provider.data_report()["invalid_depth_snapshots"] == 1
        assert (yes.venue, yes.contract_id) not in frames[-1][2]
        book, _, reasons = provider.execution_book((yes.venue, yes.contract_id), frames[-1][0])
        assert book is None and reasons

    def test_conflicting_books_at_same_time_are_not_insertion_order_winners(self, tmp_path):
        repo = _store(tmp_path)
        yes = _pair()[0]
        repo.save_orderbook_snapshot(_book(yes))
        repo.save_orderbook_snapshot(_book(yes, asks=[("0.50", "10")]))
        provider = HistoricalDataProvider(repo)
        frames = provider.load_frames(None, None)
        assert frames[0][2] == {}
        assert provider.data_report()["ambiguous_same_time_books"] == 2

    def test_equal_quote_time_conflicting_depth_is_ambiguous(self, tmp_path):
        repo = _store(tmp_path)
        yes = _pair()[0]
        repo.save_orderbook_snapshot(_book(yes))
        repo.save_orderbook_snapshot(_book(yes, T0 + timedelta(milliseconds=10), quote_time=T0,
                                           asks=[("0.50", "10")]))
        provider = HistoricalDataProvider(repo)
        assert provider.load_frames(None, None)[-1][2] == {}
        assert provider.data_report()["ambiguous_same_source_time_books"] == 1

    def test_initial_state_can_be_seeded_only_from_before_range(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        repo.save_orderbook_snapshot(_book(yes, T0 - timedelta(milliseconds=50)))
        repo.save_orderbook_snapshot(_book(other))
        provider = HistoricalDataProvider(repo)
        frames = provider.load_frames(T0, T0)
        assert len(frames) == 1 and frames[0][0] == T0
        assert len(frames[0][2]) == 2
        assert provider.data_report()["seed_observation_times_before_range"] == 1

    def test_range_end_never_uses_later_data_to_complete_orders(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        engine = BacktestEngine(repo, complete_fee_calc, _llm(), settings=_settings(),
                                simulation_config=_simulation())
        result = asyncio.run(engine.run_backtest(end=T0 + timedelta(milliseconds=50)))
        assert result.simulation["orders_with_unavailable_data"] == 1
        assert result.simulation["net_simulated_pnl"] is None
        assert result.data_quality["snapshots_read"] == 2
        assert "ended before" in result.simulation["entries"][0]["orders"][0]["reasons"][0]


class TestRecordedEvidence:
    def test_schema3_readonly_store_remains_theory_only_and_unmodified(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        # Legacy consumers assumed these were direction-oriented asks, but
        # neither priced outcome nor availability was actually recorded.
        repo.save_orderbook_snapshot(_book(yes, legacy=True))
        repo.save_orderbook_snapshot(_book(other, asks=[("0.30", "10")],
                                           bids=[("0.28", "10")], legacy=True))
        _downgrade_to_schema3(repo)
        repo.engine.dispose()
        path = tmp_path / "recorded.sqlite3"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        reader = Repository(path, Mode.HISTORICAL, read_only=True)
        engine, result = _run(reader, complete_fee_calc)
        assert all(not o.evaluation_metadata["execution_data_supported"]
                   for _, ops in engine.opportunity_frames for o in ops)
        assert result.data_unsupported_opportunities == result.opportunities_observed
        assert result.costed_positive_opportunities >= 1
        assert result.executable_observed == 0
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["net_simulated_pnl"] is None
        assert result.data_quality["snapshots_without_receipt_time"] == 2
        assert result.data_quality["snapshots_without_asof_contract_context"] == 2
        reader.engine.dispose()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    def test_migration4_is_idempotent_and_does_not_backfill_receipts_or_terms(self, tmp_path):
        repo = _store(tmp_path)
        repo.save_orderbook_snapshot(_book(_pair()[0], legacy=True))
        _downgrade_to_schema3(repo)
        repo.engine.dispose()
        upgraded = Repository(tmp_path / "recorded.sqlite3", Mode.COLLECT)
        from prediction_arb.database.migrations.runner import run_migrations
        run_migrations(upgraded.engine)
        row = upgraded.orderbook_snapshots()[0]
        assert row.received_at is row.timestamp_source is row.outcome_side is row.contract_json is None
        assert row.asks_json == '[["0.40", "10"]]'
        with upgraded.engine.connect() as conn:
            assert conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).scalars().all() \
                == [1, 2, 3, 4]
            assert conn.execute(text("SELECT value FROM meta WHERE key='schema_version'")).scalar() == "4"

    def test_snapshot_bound_contract_survives_latest_market_overwrite(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        yes = _pair()[0]
        repo.record_contract(replace(yes, contract_version="future-v2", payout=D("2")),
                             observed_at=T0 + timedelta(days=1))
        engine, result = _run(repo, complete_fee_calc)
        assert result.simulation["fully_completed_target_positions"] == 1
        for _, ops in engine.opportunity_frames:
            assert all(l.contract.contract_version == "v1" for o in ops for l in o.legs)
        assert repo.get_market_row(yes.venue, yes.contract_id).contract_version == "future-v2"

    def test_terms_observed_after_book_cannot_be_attached_retrospectively(self, tmp_path, complete_fee_calc):
        repo = Repository(tmp_path / "recorded.sqlite3", Mode.COLLECT)
        for contract in _pair():
            repo.record_contract(contract, observed_at=T0 + timedelta(milliseconds=1))
            repo.save_orderbook_snapshot(_book(contract))
        assert all(row.contract_json is None for row in repo.orderbook_snapshots())
        _, result = _run(repo, complete_fee_calc)
        assert result.data_quality["snapshots_without_asof_contract_context"] == 2
        assert result.executable_observed == 0
        assert result.simulation["attempted_entries"] == 0

    def test_missing_contract_is_counted_including_all_unreconstructable_run(self, tmp_path, complete_fee_calc):
        repo = Repository(tmp_path / "recorded.sqlite3", Mode.COLLECT)
        repo.save_orderbook_snapshot(_book(_pair()[0]))
        _, result = _run(repo, complete_fee_calc)
        assert result.frames_available == 1
        assert result.contracts_unreconstructable == 1
        assert result.data_quality["unreconstructable_reasons"] == {"not recorded": 1}
        assert result.simulation["net_simulated_pnl"] is None

    @pytest.mark.parametrize("field", ["outcome_side", "timestamp_source", "contract_json"])
    def test_one_missing_execution_evidence_field_prevents_fills(self, tmp_path, complete_fee_calc, field):
        repo = _store(tmp_path)
        _success_data(repo)
        with repo.engine.begin() as conn:
            conn.execute(text(f"UPDATE orderbook_snapshots SET {field}=NULL"))
        _, result = _run(repo, complete_fee_calc)
        assert result.executable_observed == 0
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["net_simulated_pnl"] is None

    @pytest.mark.parametrize("field", ["tick_size", "min_order_size"])
    def test_invalid_recorded_trading_rule_cannot_qualify_execution(self, tmp_path, complete_fee_calc, field):
        yes, other = _pair()
        bad = replace(yes, **{field: D(0)})
        repo = _store(tmp_path, pair=[bad, other])
        _success_data(repo)
        _, result = _run(repo, complete_fee_calc)
        # The codec rejects corrupt terms rather than turning zero into a rule
        # or inventing a valid contract merely to compute/execute an opportunity.
        assert result.opportunities_observed == result.executable_observed == 0
        assert result.contracts_unreconstructable == 2
        assert result.data_quality["snapshots_without_asof_contract_context"] == 2
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["net_simulated_pnl"] is None

    def test_explicit_binary_outcome_view_does_not_buy_yes_twice(self):
        book = _book(_pair()[1])
        no = book_for_side(book, Side.NO)
        assert no.asks == [PriceLevel(D("0.30"), D(10))]
        assert no.bids == [PriceLevel(D("0.28"), D(10))]
        assert book.asks == [PriceLevel(D("0.72"), D(10))]  # no mutation
        assert no.received_at == book.received_at and no.snapshot_id == book.snapshot_id


class TestDatedHistoricalSimulation:
    def test_fully_simulated_success_has_fills_costs_and_no_invented_cash_settlement(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        engine, result = _run(repo, complete_fee_calc)
        sim = result.simulation
        assert result.frames_available == 3
        assert result.opportunities_observed == result.costed_positive_opportunities == result.executable_observed == 3
        assert sim["attempted_entries"] == sim["completed_two_leg_positions"] == 1
        assert sim["fully_completed_target_positions"] == 1
        assert sim["filled_orders"] == 2 and sim["partial_orders"] == 0
        assert sim["completed_pair_quantity"] == "10"
        assert sim["net_simulated_pnl"] == "2.7270"
        assert sim["realized_net_simulated_pnl"] is None
        assert sim["paired_positions_still_open"] == 1
        assert sim["remaining_simulated_cash"] == {"forecast_ex": "96.90", "kalshi": "95.83"}
        assert sim["unresolved_exposure"] == [] and not sim["incomplete_evaluation"]
        orders = sim["entries"][0]["orders"]
        assert [o["snapshot_id"] for o in orders] == [3, 4]
        assert [o["arrival_time"] for o in orders] == [
            (T0 + timedelta(milliseconds=100)).isoformat(),
            (T0 + timedelta(milliseconds=200)).isoformat()]
        assert [o["side"] for o in orders] == ["yes", "no"]
        assert [o["purchase_cost"] for o in orders] == ["4.00", "3.00"]
        assert [o["trade_fees"] for o in orders] == ["0.17", "0.10"]
        assert result.theoretical_net.net_edge_sum_known_subset > D(sim["net_simulated_pnl"])
        assert BacktestFillSimulator is HistoricalFillSimulator
        assert not hasattr(engine, "adapter") and not hasattr(engine, "adapters")

    def test_deterministic_rerun_has_identical_ids_orders_pnl_and_report(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        first_engine, first = _run(repo, complete_fee_calc)
        second_engine, second = _run(repo, complete_fee_calc)
        assert first.to_dict() == second.to_dict()
        assert [o.opportunity_id for _, ops in first_engine.opportunity_frames for o in ops] == \
            [o.opportunity_id for _, ops in second_engine.opportunity_frames for o in ops]

    def test_partial_second_leg_retains_exposure_and_withholds_aggregate_pnl(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo, partial_second=True)
        _, result = _run(repo, complete_fee_calc)
        sim = result.simulation
        assert sim["filled_orders"] == sim["partial_orders"] == 1
        assert sim["completed_two_leg_positions"] == 1
        assert sim["fully_completed_target_positions"] == 0
        assert sim["completed_pair_quantity"] == "4"
        assert D(sim["completed_pair_net_pnl_known_subset"]) > 0
        assert sim["net_simulated_pnl"] is None
        assert sim["new_entries_stopped_for_exposure"] and sim["incomplete_evaluation"]
        residual = sim["unresolved_exposure"][0]
        assert residual["quantity"] == "6" and residual["side"] == "yes"
        assert residual["purchase_cost"] == "2.400"
        assert residual["terminal_value"] is residual["net_pnl"] is None
        assert residual["age_ms_at_recording_end"] == 100

    def test_explicit_depth_haircut_is_whole_quantity_not_empirical_probability(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        _, result = _run(repo, complete_fee_calc, simulation=_simulation(displayed_depth_fraction="0.25"))
        sim = result.simulation
        assert sim["entries"][0]["orders"][0]["filled_quantity"] == "2"
        assert sim["entries"][0]["orders"][1]["requested_quantity"] == "2"
        assert sim["partial_orders"] == 1
        assert sim["completed_pair_quantity"] == "2"
        assert sim["unresolved_exposure"] == []
        assert sim["policy"]["displayed_depth_fraction"] == "0.25"
        assert "fill_probability_at_touch" not in result.to_dict()["theoretical_depth_coverage"]

    def test_negative_partial_pair_pnl_is_not_filtered_out(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        repo.save_orderbook_snapshot(_book(yes))
        repo.save_orderbook_snapshot(_book(other, bids=[("0.42", "10")], asks=[("0.44", "10")]))
        repo.save_orderbook_snapshot(_book(yes, T0 + timedelta(milliseconds=100),
                                           asks=[("0.40", "1")]))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=200),
                                           bids=[("0.42", "10")], asks=[("0.44", "10")]))
        # TEST-ONLY zero trade charge on the second venue. The first venue's
        # cent-rounded fee erases the small edge for a one-contract partial.
        schedules = dict(complete_fee_calc.schedules)
        fx = schedules["forecast_ex"]
        schedules["forecast_ex"] = replace(fx, rules=tuple(
            replace(rule, fixed_amount=D(0)) if rule.applies_to == "taker" else rule
            for rule in fx.rules))
        _, result = _run(repo, FeeCalculator(schedules), settings=_settings(
            minimum_net_profit="0.001", minimum_net_return="0.0001"))
        assert result.costed_positive_opportunities > 0
        assert result.simulation["completed_pair_quantity"] == "1"
        assert result.simulation["net_simulated_pnl"] == "-0.0005"
        assert result.simulation["unresolved_exposure"] == []

    def test_competing_pairs_cannot_consume_same_snapshot_depth_twice(self, tmp_path, complete_fee_calc):
        yes, other = _pair()
        third = replace(other, contract_id="F-ARB13-2", market_id="F-ARB13-2")
        repo = _store(tmp_path, pair=[yes, other, third])
        for contract in (yes, other, third):
            repo.save_orderbook_snapshot(_book(contract))
        _, result = _run(repo, complete_fee_calc, simulation=_simulation(
            decision_latency_ms=0, leg_latency_ms=0))
        assert result.executable_observed == 2
        assert result.simulation["attempted_entries"] == 2
        assert result.simulation["filled_orders"] == 2
        assert result.simulation["unfilled_or_rejected_orders"] == 1
        assert result.simulation["completed_two_leg_positions"] == 1
        assert result.simulation["net_simulated_pnl"] == "2.7270"

    def test_settlement_fees_are_reserved_once_and_cost_actual_filled_quantity(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        schedules = {v: replace(schedule, rules=tuple(
            replace(rule, fixed_amount=D("0.02")) if rule.applies_to == "settlement" else rule
            for rule in schedule.rules)) for v, schedule in complete_fee_calc.schedules.items()}
        _, result = _run(repo, FeeCalculator(schedules))
        sim = result.simulation
        assert sim["entries"][0]["settlement_fees"] == "0.40"
        assert sim["remaining_simulated_cash"] == {"forecast_ex": "96.70", "kalshi": "95.63"}
        assert sim["capital_committed"] == "7.67"
        assert sim["net_simulated_pnl"] == "2.3268"
        assert all(o["settlement_fees_reserved"] == "0.20" for o in sim["entries"][0]["orders"])

    def test_missing_arrival_coverage_is_unavailable_not_a_perfect_fill(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        for contract in _pair():
            repo.save_orderbook_snapshot(_book(contract))
        _, result = _run(repo, complete_fee_calc)
        assert result.executable_observed == 1  # decision-time data is sufficient
        sim = result.simulation
        assert sim["attempted_entries"] == 1
        assert sim["orders_with_unavailable_data"] == 1
        assert sim["filled_orders"] == 0
        assert sim["entries"][0]["orders"][0]["filled_quantity"] is None
        assert sim["net_simulated_pnl"] is None

    def test_missing_second_arrival_retains_first_leg_exposure(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        for contract in (yes, other):
            repo.save_orderbook_snapshot(_book(contract))
        repo.save_orderbook_snapshot(_book(yes, T0 + timedelta(milliseconds=100)))
        _, result = _run(repo, complete_fee_calc)
        sim = result.simulation
        assert sim["filled_orders"] == 1 and sim["orders_with_unavailable_data"] == 1
        assert sim["completed_two_leg_positions"] == 0
        assert sim["unresolved_exposure"][0]["quantity"] == "10"
        assert sim["net_simulated_pnl"] is None

    def test_arrival_uses_previous_quote_never_next_cheaper_quote(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        for contract in (yes, other):
            repo.save_orderbook_snapshot(_book(contract))
        repo.save_orderbook_snapshot(_book(yes, T0 + timedelta(milliseconds=100)))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=150),
                                           bids=[("0.60", "10")]))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=300),
                                           bids=[("0.80", "10")], asks=[("0.82", "10")]))
        _, result = _run(repo, complete_fee_calc)
        second = result.simulation["entries"][0]["orders"][1]
        assert second["snapshot_id"] == 4  # prior quote, NOT later profitable quote id=5
        assert second["status"] == "UNFILLED" and second["filled_quantity"] == "0"
        assert second["limit_price"] == "0.30"
        assert result.simulation["net_simulated_pnl"] is None
        assert result.simulation["unresolved_exposure"][0]["quantity"] == "10"

    def test_stale_at_arrival_is_rechecked_after_latency(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        for contract in (yes, other):
            repo.save_orderbook_snapshot(_book(contract))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=1100)))
        _, result = _run(repo, complete_fee_calc,
                         simulation=_simulation(decision_latency_ms=1001))
        first = result.simulation["entries"][0]["orders"][0]
        assert first["status"] == "UNAVAILABLE"
        assert any("stale" in r for r in first["reasons"])
        assert result.simulation["filled_orders"] == 0
        assert result.simulation["net_simulated_pnl"] is None

    def test_unknown_production_costs_do_not_reach_simulator(self, tmp_path, fee_calc, production_settlement_timing):
        repo = _store(tmp_path)
        _success_data(repo)
        _, result = _run(repo, fee_calc)
        assert result.opportunities_observed > 0 and result.gross_theoretical_edge_sum > 0
        assert result.costed_opportunities == result.executable_observed == 0
        assert result.theoretical_net.net_edge_sum_known_subset is None
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["net_simulated_pnl"] is None

    def test_fee_schedule_not_yet_effective_at_decision_is_not_used(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        schedules = dict(complete_fee_calc.schedules)
        schedules["kalshi"] = replace(schedules["kalshi"], effective_date=(T0 + timedelta(days=1)).date())
        _, result = _run(repo, FeeCalculator(schedules))
        assert result.costed_opportunities == 0
        assert result.executable_observed == 0
        assert result.simulation["attempted_entries"] == 0

    def test_fee_expired_now_can_price_covered_historical_decision(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        schedules = {v: replace(s, expiration_date=T0.date()) for v, s in complete_fee_calc.schedules.items()}
        _, result = _run(repo, FeeCalculator(schedules))
        assert result.simulation["net_simulated_pnl"] == "2.7270"

    def test_fee_expiry_between_legs_leaves_unknown_fill_cost_and_naked_exposure(self, tmp_path, complete_fee_calc):
        base = T0.replace(hour=23, minute=59, second=59, microsecond=850000)
        repo = _store(tmp_path, base=base)
        _success_data(repo, base=base)
        schedules = {v: replace(s, expiration_date=T0.date()) for v, s in complete_fee_calc.schedules.items()}
        _, result = _run(repo, FeeCalculator(schedules))
        sim = result.simulation
        assert sim["filled_orders"] == 1 and sim["orders_with_unavailable_data"] == 1
        assert any("expired" in r for r in sim["entries"][0]["orders"][1]["reasons"])
        assert sim["net_simulated_pnl"] is None
        assert sim["unresolved_exposure"][0]["quantity"] == "10"

    def test_asof_contract_change_invalidates_decision_before_fill(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        yes, other = _pair()
        for contract in (yes, other):
            repo.save_orderbook_snapshot(_book(contract))
        changed = replace(yes, contract_version="v2")
        repo.record_contract(changed, observed_at=T0 + timedelta(milliseconds=50))
        repo.save_orderbook_snapshot(_book(changed, T0 + timedelta(milliseconds=100)))
        repo.save_orderbook_snapshot(_book(other, T0 + timedelta(milliseconds=200)))
        _, result = _run(repo, complete_fee_calc)
        first = result.simulation["entries"][0]["orders"][0]
        assert first["status"] == "UNAVAILABLE" and first["filled_quantity"] is None
        assert any("changed since decision" in r for r in first["reasons"])

    def test_simulated_venue_cash_is_explicit_not_unlimited(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        _, result = _run(repo, complete_fee_calc, simulation=_simulation(
            starting_cash={"kalshi": "1", "forecast_ex": "100"}))
        assert result.executable_observed > 0
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["net_simulated_pnl"] is None
        assert any("capital" in r for r in result.simulation["skipped_opportunities"])

    def test_capital_limit_cannot_be_bypassed_by_theoretical_edge(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        _, result = _run(repo, complete_fee_calc, risk_limits={"limits": {"max_capital_per_trade": "1"}})
        assert result.simulation["attempted_entries"] == 0
        assert result.simulation["skipped_opportunities"]["configured capital limit"] == 3
        assert result.simulation["net_simulated_pnl"] is None

    def test_library_without_explicit_simulation_configuration_never_assumes_fills(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        _, result = _run(repo, complete_fee_calc, simulation={})
        assert result.executable_observed > 0
        assert result.simulation["attempted_entries"] == 0
        assert not result.simulation["policy"]["enabled"]
        assert result.simulation["net_simulated_pnl"] is None

    def test_zero_depth_fraction_is_known_no_fill_not_a_profit(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        _, result = _run(repo, complete_fee_calc, simulation=_simulation(displayed_depth_fraction="0"))
        assert result.simulation["unfilled_or_rejected_orders"] == 1
        assert result.simulation["filled_orders"] == 0
        assert result.simulation["net_simulated_pnl"] == "0"  # evaluated known zero, not unavailable


class TestNewCaptureMetadata:
    @pytest.mark.parametrize("surface", ["kalshi_rest", "kalshi_ws", "polymarket_venue", "polymarket_local"])
    def test_parsers_record_receipt_provenance_outcome_and_sequence(self, tmp_path, monkeypatch, surface):
        if surface.startswith("kalshi"):
            from prediction_arb.adapters import kalshi, kalshi_ws
            monkeypatch.setattr(kalshi, "utcnow", lambda: T0)
            monkeypatch.setattr(kalshi_ws, "utcnow", lambda: T0)
            payload = {"yes_dollars": [["0.38", "10"]], "no_dollars": [["0.60", "10"]]}
            if surface == "kalshi_rest":
                book = kalshi.KalshiAdapter(rest_base="https://external-api.kalshi.com/trade-api/v2").parse_order_book(
                    "K-ARB13", {"orderbook_fp": payload}, sequence_number=7)
            else:
                state = kalshi_ws._BookState("kalshi", "K-ARB13")
                state.apply_snapshot(payload, 7)
                book = state.to_order_book()
            assert book.sequence_number == 7
            assert book.timestamp_source == "local_receipt"
            assert book.timestamp == T0
            contract = _pair()[0]
        else:
            from prediction_arb.adapters import polymarket_us
            monkeypatch.setattr(polymarket_us, "utcnow", lambda: T0)
            payload = {"marketData": {"marketSlug": "P-ARB13", "state": "MARKET_STATE_OPEN",
                "bids": [{"px": {"value": "0.38"}, "qty": "10"}],
                "offers": [{"px": {"value": "0.40"}, "qty": "10"}]}}
            if surface == "polymarket_venue":
                source = T0 - timedelta(milliseconds=5)
                payload["marketData"]["transactTime"] = source.isoformat()
            book = polymarket_us.PolymarketUSAdapter().parse_order_book("P-ARB13", payload)
            assert book.timestamp == (source if surface == "polymarket_venue" else T0)
            assert book.timestamp_source == ("venue" if surface == "polymarket_venue" else "local_receipt")
            contract = make_contract(venue="polymarket_us", contract_id="P-ARB13")
        assert book.received_at == T0 and book.outcome_side == Side.YES
        repo = _store(tmp_path, pair=[contract])
        repo.save_orderbook_snapshot(book)
        row = repo.orderbook_snapshots()[0]
        assert row.received_at == T0.replace(tzinfo=None)
        assert row.contract_json is not None and row.outcome_side == "yes"
        assert row.sequence_number == book.sequence_number
        assert row.timestamp_source == book.timestamp_source


class TestReportingAndPolicy:
    def test_main_historical_never_constructs_or_connects_venue_adapters(self, tmp_path, monkeypatch):
        from prediction_arb import main
        from prediction_arb.core import config as config_module
        from prediction_arb.modes.historical import reports
        config = load_config("historical")
        assert config.execution_engine_name == "HistoricalFillSimulator"
        monkeypatch.setattr(config_module, "ROOT", tmp_path)
        monkeypatch.setattr(reports, "_REPORT_DIR", tmp_path / "reports")
        monkeypatch.setattr(main, "setup_logging", lambda _mode: None)
        monkeypatch.setattr(main, "console", Console(file=io.StringIO(), width=240))

        def forbidden(*_args, **_kwargs):
            raise AssertionError("historical mode must not construct venue adapters")

        async def forbidden_network(*_args, **_kwargs):
            raise AssertionError("historical mode must not connect/calibrate venue endpoints")

        monkeypatch.setattr(main, "build_adapters", forbidden)
        monkeypatch.setattr(main, "connect_all", forbidden_network)
        monkeypatch.setattr(main, "maybe_calibrate_kalshi_rate_limits", forbidden_network)
        args = SimpleNamespace(start=None, end=None, date="2020-01-01", cycles=None)
        assert asyncio.run(main.async_main(args, config)) == 0
        payload = json.loads(next((tmp_path / "reports").glob("*.json")).read_text())
        assert payload["metrics"]["simulation"]["net_simulated_pnl"] is None
        assert payload["metrics"]["observed_data"]["snapshots_read"] == 0
        assert not config.recorded_store_path.exists()

    def test_cli_and_library_share_results_and_persist_decision_evidence(self, tmp_path, complete_fee_calc):
        repo = _store(tmp_path)
        _success_data(repo)
        path = tmp_path / "recorded.sqlite3"
        repo.engine.dispose()
        reader = Repository(path, Mode.HISTORICAL, read_only=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        config = load_config("historical")
        assert config.execution_engine_name == "HistoricalFillSimulator"
        config.settings = _settings()
        config.mode_config["simulation"] = _simulation()
        own = Repository(tmp_path / "historical.sqlite3", Mode.HISTORICAL)
        console = Console(file=io.StringIO(), force_terminal=False, width=240)
        cli = HistoricalEngine(config, own, complete_fee_calc, _llm(), console, recorded=reader)
        summary = asyncio.run(cli.run())
        _, library = _run(reader, complete_fee_calc, risk_limits=config.risk_limits)
        assert summary["metrics"] == library.to_dict()
        with Session(own.engine) as session:
            rows = list(session.scalars(select(OpportunityRow).order_by(OpportunityRow.timestamp)))
        assert len(rows) == 3
        assert rows[0].timestamp.isoformat() == T0.replace(tzinfo=None).isoformat()
        for row in rows:
            metadata = json.loads(row.evaluation_json)
            assert metadata["kind"] == "CALCULATED_OPPORTUNITY_NOT_A_FILL"
            assert metadata["execution_data_supported"]
            assert len(metadata["input_books"]) == 2
            assert metadata["decision_time"].startswith(row.timestamp.isoformat())
            assert row.timestamp == row.first_seen == row.last_seen
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
        output = console.file.getvalue()
        assert "NOT equity/P&L" in output and "NOT realized cash" in output
        assert "No result guarantees real execution" in output
        assert "fill probability" not in output and "upper bound" not in output

    @pytest.mark.parametrize("kwargs", [
        {"decision_latency_ms": -1}, {"leg_latency_ms": -1},
        {"displayed_depth_fraction": D("1.1")}, {"displayed_depth_fraction": D("NaN")},
        {"max_pair_skew_ms": -1}, {"starting_cash": {"kalshi": D(0)}},
        {"enabled": True, "starting_cash": {}}, {"enabled": "false"},
    ])
    def test_invalid_simulation_assumptions_are_refused(self, kwargs):
        with pytest.raises(ValueError):
            ReplayPolicy(**kwargs)
