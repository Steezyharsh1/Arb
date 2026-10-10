"""Phase 4 tests: faithful recording + replay (D3, D4) and the backtest
metric battery (§33).

The through-line is §92 — never fabricate. Every test here pins either
(a) that recorded truth survives the round trip exactly, or (b) that a
gap in the record surfaces as a gap in the output rather than as an
invented default.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from prediction_arb.core.types import (
    CostComponent,
    CostLedger,
    MarketMicrostructure,
    MicrostructureType,
    Mode,
    NormalizedContract,
    OrderTypeSupport,
    OutcomeType,
)
from prediction_arb.database.contract_codec import (
    decode_contract,
    encode_contract,
)
from prediction_arb.database.models import SCHEMA_VERSION, MarketRow
from prediction_arb.database.repository import Repository

from .conftest import make_book, make_contract


# --------------------------------------------------------------------- D4
class TestContractRecording:
    def test_schema_is_version_4(self):
        assert SCHEMA_VERSION == 4

    def test_migration_002_is_registered_and_idempotent(self, tmp_path):
        """A fresh DB already has the columns from create_all, so 002 must
        not blow up with 'duplicate column name' when it runs anyway."""
        from prediction_arb.database.migrations.runner import MIGRATIONS
        versions = [v for v, _d, _f in MIGRATIONS]
        assert 2 in versions
        repo = Repository(tmp_path / "m.sqlite3", Mode.COLLECT)
        with repo.engine.begin() as conn:
            applied = [r[0] for r in conn.execute(
                text("SELECT version FROM schema_migrations"))]
        assert 2 in applied

    def test_migration_002_upgrades_a_v1_database(self, tmp_path):
        """The case that actually matters: a store created by the OLD
        schema gains the new columns without losing its existing rows."""
        from prediction_arb.database.migrations.runner import (
            _m002_market_contract_fields,
        )
        path = tmp_path / "old.sqlite3"
        engine = __import__("sqlalchemy").create_engine(
            f"sqlite:///{path}", future=True)
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE markets (id INTEGER PRIMARY KEY, mode TEXT, "
                "market_id TEXT, venue TEXT, underlying_exchange TEXT, "
                "contract_id TEXT, description TEXT, outcome_type TEXT, "
                "category TEXT, status TEXT, created_at DATETIME, "
                "updated_at DATETIME)"))
            conn.execute(text(
                "INSERT INTO markets (mode, market_id, venue, description, "
                "outcome_type) VALUES ('collect','M1','kalshi','old row',"
                "'binary')"))
        _m002_market_contract_fields(engine)
        with engine.begin() as conn:
            cols = {r[1] for r in conn.execute(
                text("PRAGMA table_info(markets)"))}
            row = conn.execute(text(
                "SELECT description, payout FROM markets")).fetchone()
        assert {"payout", "tick_size", "settlement_time", "raw_json"} <= cols
        # pre-existing data survived; new columns are NULL, not guessed
        assert row[0] == "old row"
        assert row[1] is None

    def test_record_contract_round_trips_every_field(self, tmp_path):
        """All 37 NormalizedContract fields must survive store → reload.
        A silent drop here is exactly how replay starts inventing."""
        original = self._rich_contract()
        repo = Repository(tmp_path / "rt.sqlite3", Mode.COLLECT)
        repo.record_contract(original, status="open")

        with Session(repo.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.mode == "collect"))
            restored = decode_contract(row)

        assert restored is not None
        missing = [f for f in original.__dataclass_fields__
                   if getattr(original, f) != getattr(restored, f)]
        assert missing == [], f"fields lost in round trip: {missing}"

    def test_unknown_market_rules_round_trip_as_null_not_defaults(self, tmp_path):
        original = replace(self._rich_contract(),
                           tick_size=None, min_order_size=None)
        repo = Repository(tmp_path / "unknown-rules.sqlite3", Mode.COLLECT)
        repo.record_contract(original, status="open")

        with Session(repo.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.mode == "collect"))
            assert row.tick_size is None and row.min_order_size is None
            restored = decode_contract(row)

        assert restored is not None
        assert restored.tick_size is None
        assert restored.min_order_size is None

    def test_decode_rejects_corrupt_nonpositive_or_nonfinite_market_rules(
            self, tmp_path):
        repo = Repository(tmp_path / "corrupt-rules.sqlite3", Mode.COLLECT)
        repo.record_contract(self._rich_contract(), status="open")
        with Session(repo.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.mode == "collect"))
            row.tick_size = "NaN"
            s.commit()
        with Session(repo.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.mode == "collect"))
            assert decode_contract(row) is None

    def test_decode_never_invents_for_under_recorded_rows(self, tmp_path):
        """A row written by the legacy narrow upsert has no contract data.
        It must decode to None — never to a plausible-looking contract."""
        repo = Repository(tmp_path / "legacy.sqlite3", Mode.COLLECT)
        repo.upsert_market("kalshi", "M1", "yes", "kalshi_dcm",
                           "Fed raises rates", "binary", "KXFED", "open")
        with Session(repo.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.market_id == "M1"))
        assert row.payout is None and row.settlement_time is None
        assert decode_contract(row) is None

    def test_encode_leaves_unknown_fields_null(self):
        """Unset strings/Decimals become NULL, not '' or '0'."""
        c = make_contract()
        values = encode_contract(c)
        assert values["clearing_entity"] is None
        assert values["max_order_size"] is None
        assert values["resolution_method"] is None

        unknown_rules = encode_contract(replace(
            c, tick_size=None, min_order_size=None))
        assert unknown_rules["tick_size"] is None
        assert unknown_rules["min_order_size"] is None

    def test_provider_counts_unreconstructable_instead_of_dropping(self,
                                                                   tmp_path):
        from prediction_arb.modes.historical.data_provider import (
            HistoricalDataProvider,
        )
        repo = Repository(tmp_path / "prov.sqlite3", Mode.COLLECT)
        repo.record_contract(self._rich_contract())
        repo.save_orderbook_snapshot(make_book(
            "kalshi", "KX-1", asks=[("0.45", "10")], bids=[("0.40", "10")]))
        # a book with no recorded contract at all
        repo.save_orderbook_snapshot(make_book(
            "polymarket_us", "GHOST", asks=[("0.45", "10")], bids=[("0.40", "10")]))

        provider = HistoricalDataProvider(repo)
        frames = provider.load_frames(None, None)
        assert len(frames) == 2  # exact observation times, never minute buckets
        # only the reconstructable contract reaches the matcher
        assert [c.contract_id for c in frames[-1][1]] == ["KX-1"]
        assert len(provider.under_recorded) == 1
        venue, cid, reason = provider.under_recorded[0]
        assert (venue, cid) == ("polymarket_us", "GHOST")
        assert reason == "not recorded"

    @staticmethod
    def _rich_contract() -> NormalizedContract:
        settle = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)
        return NormalizedContract(
            venue="kalshi", underlying_exchange="KX",
            clearing_entity="KXDCO LLC",
            market_id="KXBTC-26SEP2517-70000-B1", contract_id="KX-1",
            event_description="BTC >= 70,000 at 5pm ET on Sep 25?",
            normalized_event="btc_price_70000_202609251700_et",
            outcome_type=OutcomeType.BINARY, outcome="Yes",
            normalized_outcome="yes", outcome_count=2,
            all_outcomes=("Yes", "No"), settlement_time=settle,
            timezone="America/New_York",
            cutoff_time=settle - timedelta(minutes=5),
            resolution_source="kalshi:rules",
            resolution_method="kalshi_settlement",
            threshold=Decimal("70000"), threshold_operator=">=",
            units="USD", payout=Decimal("1.00"), currency="USD",
            early_resolution_rules="none", cancellation_rules="none",
            contract_version="KX-2026-09", tick_size=Decimal("0.001"),
            min_order_size=Decimal("5"), max_order_size=Decimal("5000"),
            jurisdiction_requirements="US", category="crypto",
            market_microstructure=MarketMicrostructure(
                type=MicrostructureType.CLOB, has_order_book=True,
                has_pricing_function=False, quote_validity_ms=500,
                verified=True, source="kalshi:/trade-api/v2/series/KXBTC"),
            supported_order_types=OrderTypeSupport(
                market=False, limit=True, ioc=False, fok=False, gtc=True,
                verified=True, source="kalshi docs 2026-09"),
            raw={"ticker": "KXBTC-26SEP2517-70000-B1", "yes_bid": 44})


# --------------------------------------------------------------------- D3
class TestRecordedStoreAccess:
    def test_historical_reads_collect_store_read_only(self, tmp_path):
        """The D3 fix: historical replay can finally see what collect
        recorded, and physically cannot write to it."""
        recorded = Repository(tmp_path / "recorded.sqlite3", Mode.COLLECT)
        recorded.record_contract(TestContractRecording._rich_contract())
        recorded.save_orderbook_snapshot(make_book(
            "kalshi", "KX-1", asks=[("0.45", "10")], bids=[("0.40", "10")]))

        reader = Repository(tmp_path / "recorded.sqlite3", Mode.HISTORICAL,
                            read_only=True)
        assert reader.recorded_store_tag == "collect"
        row = reader.get_market_row("kalshi", "KX-1")
        assert row is not None and row.payout == "1.00"

        with pytest.raises(Exception):
            # SQLite itself refuses — not a convention we could bypass
            with Session(reader.engine) as s:
                s.add(MarketRow(mode="historical", market_id="X",
                                venue="kalshi", contract_id="X"))
                s.commit()

    def test_read_only_never_creates_a_missing_store(self, tmp_path):
        """No silent empty database, and no invented history (§92)."""
        missing = tmp_path / "absent.sqlite3"
        with pytest.raises(FileNotFoundError, match="never creates"):
            Repository(missing, Mode.HISTORICAL, read_only=True)
        assert not missing.exists()

    def test_read_only_still_refuses_a_live_tagged_store(self, tmp_path):
        """The narrow D3 allowance is read-only + historical + collect/
        paper only. Live data stays walled off (§49)."""
        live = Repository(tmp_path / "live.sqlite3", Mode.LIVE)
        with pytest.raises(RuntimeError, match="Refusing to mix mode data"):
            Repository(tmp_path / "live.sqlite3", Mode.HISTORICAL,
                       read_only=True)

    def test_read_only_refuses_an_untagged_store(self, tmp_path):
        path = tmp_path / "untagged.sqlite3"
        seed = Repository(path, Mode.COLLECT)
        with Session(seed.engine) as s:
            s.query(__import__(
                "prediction_arb.database.models", fromlist=["MetaRow"]
            ).MetaRow).delete()
            s.commit()
        with pytest.raises(RuntimeError, match="no mode tag"):
            Repository(path, Mode.HISTORICAL, read_only=True)

    def test_config_recorded_store_matches_where_collect_writes(self):
        """Derived from the same constants, so it cannot drift."""
        from prediction_arb.core.config import load_config
        config = load_config("historical")
        collect = load_config("collect")
        assert config.recorded_store_path == collect.database_path

    def test_historical_engine_writes_only_to_its_own_database(self,
                                                              tmp_path):
        """Replay output must not contaminate the evidence it replays."""
        from prediction_arb.modes.historical.engine import HistoricalEngine
        from prediction_arb.matching.llm_matcher import LLMMatcher
        from prediction_arb.fees.venue_fees import load_fee_calculator
        from prediction_arb.core.config import load_config
        from rich.console import Console

        recorded_path = tmp_path / "recorded.sqlite3"
        recorded = Repository(recorded_path, Mode.COLLECT)
        recorded.record_contract(TestContractRecording._rich_contract())
        recorded.save_orderbook_snapshot(make_book(
            "kalshi", "KXBTC-26SEP2517-70000-B1",
            asks=[("0.45", "10")], bids=[("0.40", "10")]))

        own = Repository(tmp_path / "own.sqlite3", Mode.HISTORICAL)
        reader = Repository(recorded_path, Mode.HISTORICAL, read_only=True)
        config = load_config("historical")
        engine = HistoricalEngine(config, own, load_fee_calculator(),
                                  LLMMatcher(client=None), Console(),
                                  recorded=reader)
        summary = asyncio.run(engine.run())

        assert summary["frames"] == 1
        assert summary["contracts_reconstructed"] == 1
        assert summary["contracts_unreconstructable"] == 0
        assert summary["recorded_store"] == str(recorded_path)
        # the recorded store was opened read-only and never written
        with recorded.engine.connect() as conn:
            tagged = conn.execute(text(
                "SELECT value FROM meta WHERE key='mode'")).scalar()
        assert tagged == "collect"


# ------------------------------------------------------- backtest metrics
class TestBacktestMetrics:
    def _opportunity(self, ts, legs, *, status="TRADE",
                     net=Decimal("5"), capital=Decimal("100"),
                     fees=Decimal("1"), gross=Decimal("100")):
        from prediction_arb.core.types import (
            Independence,
            Opportunity,
            OpportunityStatus,
            OpportunityType,
        )
        return Opportunity(
            opportunity_id="x", mode=Mode.HISTORICAL,
            opportunity_type=OpportunityType.TYPE_A_GUARANTEED,
            timestamp=ts, normalized_event="e", legs=legs,
            payout_per_contract=Decimal("1"), quantity=Decimal("10"),
            gross_cost=gross, total_fees=fees, slippage=Decimal("0"),
            settlement_delay_cost=Decimal("0"), net_profit=net,
            net_profit_per_contract=Decimal("0.5"),
            capital_required=capital, return_on_capital=Decimal("0.05"),
            independence=Independence.INDEPENDENT, match_id="m",
            match_confidence=0.9, status=OpportunityStatus(status),
            first_seen=ts, last_seen=ts,
            costs=CostLedger((CostComponent("synthetic_cost", fees),)),
            evaluation_metadata={"execution_data_supported": True,
                                 "costed_trade_before_data_gates": status == "TRADE"})

    def _leg(self, filled, requested):
        from prediction_arb.core.types import (
            Leg,
            OrderAction,
            Side,
            WalkResult,
        )
        return Leg(
            venue="kalshi", contract=make_contract(), side=Side.YES,
            action=OrderAction.BUY, quantity=Decimal(requested),
            limit_price=Decimal("0.45"),
            walk=WalkResult(
                requested_quantity=Decimal(requested),
                filled_quantity=Decimal(filled),
                total_cost=Decimal(filled) * Decimal("0.45"),
                weighted_average_price=Decimal("0.45"),
                levels_consumed=[],
                fully_filled=Decimal(filled) >= Decimal(requested)))

    def test_duration_uses_stable_identity_not_the_per_frame_uuid(self):
        """`opportunity_id` is a fresh uuid4 every frame, so it can never
        express persistence. The leg-pair key can."""
        from prediction_arb.backtesting.metrics import (
            compute_duration_stats,
            opportunity_key,
        )
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        legs = [self._leg("10", "10")]
        frames = [
            (t0, [self._opportunity(t0, legs)]),
            (t0 + timedelta(seconds=60), [self._opportunity(
                t0 + timedelta(seconds=60), legs)]),
            (t0 + timedelta(seconds=120), [self._opportunity(
                t0 + timedelta(seconds=120), legs)]),
        ]
        assert opportunity_key(frames[0][1][0]) == opportunity_key(
            frames[2][1][0])
        stats = compute_duration_stats(frames)
        assert stats.distinct_opportunities == 1
        assert stats.median_seconds == 120.0
        assert stats.single_frame_only == 0

    def test_single_frame_opportunity_is_flagged(self):
        from prediction_arb.backtesting.metrics import compute_duration_stats
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        stats = compute_duration_stats(
            [(t0, [self._opportunity(t0, [self._leg("10", "10")])])])
        assert stats.distinct_opportunities == 1
        assert stats.single_frame_only == 1
        assert stats.max_seconds == 0.0

    def test_depth_stats_measure_recorded_depth(self):
        from prediction_arb.backtesting.metrics import compute_depth_stats
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        ops = [self._opportunity(t0, [
            self._leg("10", "10"),    # fully filled
            self._leg("4", "10"),     # partial
            self._leg("0", "10"),     # nothing
        ])]
        stats = compute_depth_stats(ops)
        assert stats.leg_observations == 3
        assert stats.fully_covered == 1
        assert stats.partially_covered == 1
        assert stats.no_coverage == 1
        assert stats.full_coverage_rate == pytest.approx(1 / 3)
        assert stats.partial_coverage_rate == pytest.approx(1 / 3)
        assert stats.mean_coverage_ratio == pytest.approx((1 + 0.4 + 0) / 3)

    def test_depth_stats_are_null_not_optimistic_without_walks(self):
        """No recorded walk ⇒ null. Assuming 100% fill would be D12 all
        over again."""
        from prediction_arb.backtesting.metrics import compute_depth_stats
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        leg = self._leg("10", "10")
        leg.walk = None
        stats = compute_depth_stats([self._opportunity(t0, [leg])])
        assert stats.leg_observations == 0
        assert stats.full_coverage_rate is None
        assert stats.partial_coverage_rate is None
        assert stats.mean_coverage_ratio is None

    def test_theoretical_net_series_includes_costed_losses_not_equity(self):
        from prediction_arb.backtesting.metrics import compute_theoretical_net_series
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        frames = [
            (t0, [self._opportunity(t0, [], net=Decimal("10"))]),
            (t0 + timedelta(minutes=1), [self._opportunity(
                t0, [], net=Decimal("-4"))]),
            (t0 + timedelta(minutes=2), [self._opportunity(
                t0, [], net=Decimal("2"))]),
        ]
        curve = compute_theoretical_net_series(frames)
        assert curve.net_edge_sum_known_subset == Decimal("8")
        assert curve.costed_observations == 3
        assert len(curve.points) == 3

    def test_costed_block_watch_edges_remain_theoretical_not_fills(self):
        """All costed edge observations can be calculated, but none may
        be credited as trading P&L without dated simulation."""
        from prediction_arb.backtesting.metrics import compute_theoretical_net_series
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        curve = compute_theoretical_net_series([(t0, [
            self._opportunity(t0, [], net=Decimal("10")),
            self._opportunity(t0, [], net=Decimal("99"), status="BLOCK"),
            self._opportunity(t0, [], net=Decimal("99"), status="WATCH"),
        ])])
        assert curve.net_edge_sum_known_subset == Decimal("208")
        assert curve.costed_observations == 3

    def test_fee_drag_and_capital_utilisation(self):
        from prediction_arb.backtesting.metrics import (
            compute_capital_stats,
            compute_fee_drag,
        )
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        frames = [(t0, [
            self._opportunity(t0, [], fees=Decimal("2"),
                              gross=Decimal("100"), capital=Decimal("80")),
            self._opportunity(t0, [], fees=Decimal("1"),
                              gross=Decimal("50"), capital=Decimal("20")),
        ])]
        drag = compute_fee_drag([op for _t, ops in frames for op in ops])
        assert drag.total_fees == Decimal("3")
        assert drag.gross_cost == Decimal("150")
        assert drag.drag_ratio == pytest.approx(0.02)

        cap = compute_capital_stats(frames, Decimal("1000"))
        assert cap.peak_capital_required == Decimal("100")
        assert cap.peak_utilisation == pytest.approx(0.1)

    def test_summarize_frames_records_its_own_assumptions(self):
        from prediction_arb.backtesting.metrics import summarize_frames
        t0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        result = summarize_frames(
            [(t0, [self._opportunity(t0, [self._leg("10", "10")])])],
            max_total_capital=Decimal("1000"))
        assert result.frames_available == 1
        assert result.opportunities_observed == 1
        assert result.executable_observed == 1
        assert any("not MODELLED equity" in a for a in result.assumptions)
        # JSON-safe and lossless for Decimals
        as_dict = result.to_dict()
        assert as_dict["capital"]["peak_capital_required"] == "100"
        assert as_dict["fee_drag"]["total_fees_known_subset"] == "1"

    def test_run_backtest_end_to_end_over_recorded_data(self, tmp_path):
        """The whole Phase 4 chain: collect records → store → read-only
        replay → metrics, with the real fee ledger and the real matcher.

        With no LLM configured the deterministic pair lands at REVIEW, so
        nothing is approved and the honest answer is ZERO opportunities —
        even though the spread looks profitable. That is the pipeline
        working as designed (D9), and this test pins it: replay must not
        manufacture trades the matcher refused.
        """
        from prediction_arb.backtesting.engine import BacktestEngine
        from prediction_arb.core.config import load_config
        from prediction_arb.fees.venue_fees import load_fee_calculator

        recorded = Repository(tmp_path / "rec.sqlite3", Mode.COLLECT)
        yes = make_contract(venue="kalshi", contract_id="KX-YES",
                            threshold=Decimal("25"), threshold_op=">=",
                            units="bps", early_resolution="Fed statement")
        no = make_contract(venue="forecast_ex", contract_id="FX-NO",
                           threshold=Decimal("25"), threshold_op=">=",
                           units="bps", early_resolution="Fed statement")
        no = replace(no, outcome="NO")
        recorded.record_contract(yes)
        recorded.record_contract(no)
        recorded.save_orderbook_snapshot(make_book(
            "kalshi", "KX-YES", asks=[("0.40", "100")], bids=[("0.38", "50")]))
        recorded.save_orderbook_snapshot(make_book(
            "forecast_ex", "FX-NO", asks=[("0.50", "100")],
            bids=[("0.48", "50")]))

        reader = Repository(tmp_path / "rec.sqlite3", Mode.HISTORICAL,
                            read_only=True)
        config = load_config("historical")
        engine = BacktestEngine(reader, fee_calc=load_fee_calculator(),
                                settings=config.settings)
        result = asyncio.run(engine.run_backtest())

        # Recorded data was found and both contracts were rebuilt faithfully.
        assert result.frames_available == 2  # independently observed books
        assert result.first_frame is not None
        assert result.contracts_reconstructed == 3  # per-frame contract references
        assert result.contracts_unreconstructable == 0
        # No LLM ⇒ pair is REVIEW ⇒ nothing approved ⇒ no opportunities
        # reported. A profitable-looking spread is not a trade.
        assert result.opportunities_observed == 0
        assert result.executable_observed == 0
        assert result.theoretical_net.net_edge_sum_known_subset is None
        assert result.simulation["net_simulated_pnl"] is None
        assert result.assumptions  # assumptions are always disclosed

    def test_run_backtest_reports_trades_when_a_pair_is_approved(self,
                                                                tmp_path,
                                                                monkeypatch):
        """Same recorded data, but with the pair approved: now the metrics
        must actually populate. Proves the metric battery is wired to real
        pipeline output and not permanently empty."""
        from prediction_arb.backtesting.engine import BacktestEngine
        from prediction_arb.core.config import load_config
        from prediction_arb.fees.venue_fees import load_fee_calculator
        from prediction_arb.modes import shared

        recorded = Repository(tmp_path / "rec2.sqlite3", Mode.COLLECT)
        yes = make_contract(venue="kalshi", contract_id="KX-YES",
                            threshold=Decimal("25"), threshold_op=">=",
                            units="bps", early_resolution="Fed statement")
        no = make_contract(venue="forecast_ex", contract_id="FX-NO",
                           threshold=Decimal("25"), threshold_op=">=",
                           units="bps", early_resolution="Fed statement")
        no = replace(no, outcome="NO")
        recorded.record_contract(yes)
        recorded.record_contract(no)
        recorded.save_orderbook_snapshot(make_book(
            "kalshi", "KX-YES", asks=[("0.40", "100")], bids=[("0.38", "50")]))
        recorded.save_orderbook_snapshot(make_book(
            "forecast_ex", "FX-NO", asks=[("0.50", "100")],
            bids=[("0.48", "50")]))

        # Stand in for a configured LLM: approve exactly the one pair the
        # deterministic matcher already passed. Nothing is invented — the
        # contracts, books and fees are all the recorded ones.
        from prediction_arb.backtesting import engine as bt_engine

        async def fake_matching(contracts, llm):
            return {("forecast_ex:FX-NO", "kalshi:KX-YES"): ("m1", 0.99)}, \
                shared.ScanResult(markets_scanned=len(contracts),
                                  pairs_considered=1,
                                  pairs_deterministic_pass=1)
        # patch the name the engine actually calls, not shared's
        monkeypatch.setattr(bt_engine, "run_matching", fake_matching)

        reader = Repository(tmp_path / "rec2.sqlite3", Mode.HISTORICAL,
                            read_only=True)
        config = load_config("historical")
        engine = BacktestEngine(reader,
                                fee_calc=load_fee_calculator(config.fees, 30),
                                settings=config.settings)
        result = asyncio.run(engine.run_backtest())

        assert result.frames_available == 2
        assert result.opportunities_observed >= 1
        assert result.executable_observed == 0  # legacy timing/terms are insufficient
        assert result.simulation["attempted_entries"] == 0
        assert result.durations.distinct_opportunities >= 1
        assert result.fee_drag.gross_cost > 0
        assert result.capital.peak_capital_required is not None
        # the assumptions are disclosed on a populated run too
        assert any("MODELLED" in a for a in result.assumptions)
        assert result.to_dict()["capital"]["peak_capital_required"] is not None

    def test_replay_report_carries_the_metric_battery(self, tmp_path):
        """`--mode historical` must REPORT metrics, not just count frames.

        The metric battery was implemented but reachable only from
        `BacktestEngine`, so the phase exit criterion ("replays recorded
        data and reports metrics") was not actually met by the CLI path.
        This pins the wiring: the replay summary itself carries the
        battery, computed over the same opportunities it persisted.
        """
        from prediction_arb.modes.historical.engine import HistoricalEngine
        from prediction_arb.matching.llm_matcher import LLMMatcher
        from prediction_arb.core.config import load_config
        from prediction_arb.fees.venue_fees import load_fee_calculator
        from rich.console import Console

        recorded_path = tmp_path / "recorded.sqlite3"
        recorded = Repository(recorded_path, Mode.COLLECT)
        recorded.record_contract(TestContractRecording._rich_contract())
        recorded.save_orderbook_snapshot(make_book(
            "kalshi", "KXBTC-26SEP2517-70000-B1",
            asks=[("0.45", "10")], bids=[("0.40", "10")]))

        own = Repository(tmp_path / "own.sqlite3", Mode.HISTORICAL)
        reader = Repository(recorded_path, Mode.HISTORICAL, read_only=True)
        config = load_config("historical")
        engine = HistoricalEngine(config, own,
                                  load_fee_calculator(config.fees, 30),
                                  LLMMatcher(client=None), Console(),
                                  recorded=reader)
        summary = asyncio.run(engine.run())

        metrics = summary["metrics"]
        assert metrics["frames_available"] == 1
        # every section of the battery is present in the report payload
        for section in ("theoretical_depth_coverage", "sampled_observation_spans", "capital",
                        "fee_drag", "theoretical_net_edge_observations", "simulation", "observed_data"):
            assert section in metrics, f"missing {section} from replay report"
        assert metrics["assumptions"]
        assert "fills" not in metrics and "modelled_equity" not in metrics
        # the explicit theoretical label survives into the report
        assert any("MODELLED" in a for a in metrics["assumptions"])

    def test_data_inventory_still_reports_recorded_range(self, tmp_path):
        from prediction_arb.backtesting.engine import BacktestEngine
        repo = Repository(tmp_path / "inv.sqlite3", Mode.COLLECT)
        repo.save_orderbook_snapshot(make_book(
            "kalshi", "KX-1", asks=[("0.5", "10")]))
        reader = Repository(tmp_path / "inv.sqlite3", Mode.HISTORICAL,
                            read_only=True)
        inv = BacktestEngine(reader).data_inventory()
        assert inv.frames_available == 1
        assert inv.first_frame is not None
