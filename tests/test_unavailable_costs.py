"""D17 regression tests: an unavailable mandatory cost is never a zero.

The inherited review reported a BLOCKed opportunity carrying a *positive*
net profit. It was real: `evaluate_two_leg` caught `FeeDataError` and
substituted `fees = Decimal("0")`, and did the same for an unknown
settlement lockup, so the profit test ran on fabricated free costs and the
dashboard printed a profitable-looking net next to a BLOCK verdict.

These tests pin the repaired contract at both layers:

* calculation layer — every mandatory cost is a
  :class:`~prediction_arb.core.types.CostComponent`; an unavailable one has
  `amount=None` and a reason, the ledger has no total, and `net_profit`,
  `net_profit_per_contract`, `return_on_capital` and `capital_required` are
  `None` (UNVERIFIED), never a number;
* reporting layer — the dashboard renders `n/a`, the database stores NULL
  plus the names of the missing components, and the reports count unpriced
  opportunities instead of implying they are profitable.

Gross edge stays available throughout: it is research data, and it is never
allowed to stand in for a verified net profit.
"""
from __future__ import annotations

import dataclasses
import io
import json
from decimal import Decimal

import pytest
from rich.console import Console
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from prediction_arb.arbitrage.optimizer import rank, score, search_event
from prediction_arb.arbitrage.two_leg import evaluate_two_leg
from prediction_arb.core.types import (
    CostComponent,
    CostLedger,
    Independence,
    Mode,
    OpportunityStatus,
)
from prediction_arb.database.models import OpportunityRow
from prediction_arb.database.repository import Repository
from prediction_arb.fees.calculator import FeeCalculator
from prediction_arb.monitoring.dashboard import opportunities_table
from prediction_arb.monitoring.formatting import format_amount

from .conftest import fresh_verification_date, make_book, make_contract


# --------------------------------------------------------------------- helpers
def _fee_calc(overrides: dict[str, dict] | None = None,
              venues: tuple[str, ...] = ("kalshi", "forecast_ex"),
              max_age_days: int = 30) -> FeeCalculator:
    """Synthetic schedules with per-venue/per-component status control.

    Same shape as `conftest.complete_fee_calc`, but each venue's trade and
    settlement calculation status (and freshness) can be degraded
    independently, which is what the mandatory-cost failures need.
    """
    overrides = overrides or {}
    schedules = []
    for venue in venues:
        if venue == "forecast_ex":
            # ForecastEx microstructure: a flat $0.01 per executed side.
            fees = [
                {"fee_type": "taker_fixed", "fixed_amount": "0.01",
                 "applies_to": "taker"},
                {"fee_type": "maker_fixed", "fixed_amount": "0.01",
                 "applies_to": "maker"},
            ]
        else:
            # Kalshi-style quadratic taker schedule.
            fees = [
                {"fee_type": "taker_quadratic", "multiplier": "0.07",
                 "applies_to": "taker"},
                {"fee_type": "maker_quadratic", "multiplier": "0.0175",
                 "applies_to": "maker"},
            ]
        fees.append({"fee_type": "settlement", "fixed_amount": "0",
                     "applies_to": "settlement"})
        cfg = {
            "venue": venue,
            "version": "test-static-v1",
            "effective_date": "2020-01-01",
            # Fresh by construction: these tests are about incomplete
            # components, not about schedule age (see conftest).
            "verified_at": fresh_verification_date(),
            "verification_status": "VERIFIED",
            "documentation_tested": True,
            "source": "synthetic deterministic test fixture",
            "trade_calculation_status": "COMPLETE",
            "settlement_calculation_status": "COMPLETE",
            "fees": fees,
        }
        cfg.update(overrides.get(venue, {}))
        schedules.append(cfg)
    return FeeCalculator.from_config({"schedules": schedules}, max_age_days)


#: sentinel: leave `make_contract`'s default (frozen-clock) settlement alone
_DEFAULT_SETTLEMENT = object()


def _pair(yes_venue="kalshi", no_venue="forecast_ex",
          settlement=_DEFAULT_SETTLEMENT):
    """An independent YES/NO contract pair. Passing `settlement=None` gives
    a contract with genuinely no known settlement time."""
    yes_c = make_contract(venue=yes_venue, contract_id="Y")
    no_c = make_contract(venue=no_venue, contract_id="N")
    if settlement is not _DEFAULT_SETTLEMENT:
        yes_c = dataclasses.replace(yes_c, settlement_time=settlement)
        no_c = dataclasses.replace(no_c, settlement_time=settlement)
    return yes_c, no_c


def _books():
    return (make_book("kalshi", "Y", asks=[("0.40", "100")]),
            make_book("forecast_ex", "N", asks=[("0.50", "100")]))


def _evaluate(fee_calc, yes_c=None, no_c=None,
              independence=Independence.INDEPENDENT, **kwargs):
    yes_book, no_book = _books()
    if yes_c is None or no_c is None:
        yes_c, no_c = _pair()
    op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, fee_calc,
                          Mode.PAPER, independence=independence, **kwargs)
    assert op is not None, "these books have a gross edge; sizing must run"
    return op


def _render(op) -> str:
    console = Console(file=io.StringIO(), force_terminal=False, width=250)
    console.print(opportunities_table([op], Mode.PAPER))
    return console.file.getvalue()


# ------------------------------------------------------- the repaired contract
class TestUnavailableNetProfitIsNotANumber:
    def test_incomplete_trade_fee_blocks_with_no_net_profit(self):
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE",
                                     "blocking_reason": "series override "
                                                        "not supplied"}})
        op = _evaluate(calc)
        assert op.status == OpportunityStatus.BLOCK
        # The whole point of D17: no verified profit figure at all.
        assert op.net_profit is None
        assert op.net_profit_per_contract is None
        assert op.return_on_capital is None
        # ... and no fabricated cost figures either.
        assert op.total_fees is None
        assert op.capital_required is None
        assert op.settlement_delay_cost is None
        assert not op.costs_complete
        assert not op.net_profit_is_verified
        assert not op.profitable_after_all_costs
        assert not op.is_executable
        assert any("FEE_DATA_INCOMPLETE" in r for r in op.block_reasons)
        assert any("series override not supplied" in r
                   for r in op.block_reasons)

    def test_gross_edge_survives_and_is_not_profit(self):
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        op = _evaluate(calc)
        # payout 1.00 x 100 - walked cost 90.00
        assert op.gross_edge == Decimal("10.00")
        assert op.gross_cost == Decimal("90.00")
        assert op.gross_edge > 0
        # A large positive gross edge is still not a profit and still not
        # executable — the two must stay semantically distinct.
        assert op.net_profit is None
        assert op.profitable_after_all_costs is False
        assert op.is_executable is False

    def test_every_mandatory_failure_is_reported_not_just_the_first(self):
        """One missing schedule used to abort the whole fee sum, so the
        second venue's missing schedule was never reported."""
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"},
                          "forecast_ex": {"trade_calculation_status":
                                          "INCOMPLETE"}})
        op = _evaluate(calc)
        # both missing schedules are named, plus the delay cost that cannot
        # be computed without a capital base
        assert op.costs.missing_names() == (
            "kalshi:trade_fee", "forecast_ex:trade_fee", "settlement_delay")
        kalshi_reported = any("kalshi trade fee" in r for r in op.block_reasons)
        fx_reported = any("forecast_ex trade fee" in r
                          for r in op.block_reasons)
        assert kalshi_reported and fx_reported

    def test_known_costs_stay_available_for_research(self):
        """Only the unavailable component is withheld; the fee that could be
        computed is still reported, separately from net profit."""
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        op = _evaluate(calc)
        known = {c.name: c.amount for c in op.costs.components if c.available}
        # forecast_ex taker fee is $0.01 x 100; both settlement fees are 0.
        assert known["forecast_ex:trade_fee"] == Decimal("1.00")
        assert known["kalshi:settlement_fee"] == Decimal("0.00")
        assert known["forecast_ex:settlement_fee"] == Decimal("0.00")
        assert op.known_costs == Decimal("1.00")
        # known_costs is a partial cost, so it must not be presented as the
        # fee total or turned into a profit.
        assert op.total_fees is None
        assert op.net_profit is None


class TestIncompleteSettlementFees:
    def test_incomplete_settlement_fee_blocks_with_no_net_profit(self):
        calc = _fee_calc({"forecast_ex": {
            "settlement_calculation_status": "INCOMPLETE",
            "blocking_reason": "withdrawal/settlement fee unpublished"}})
        op = _evaluate(calc)
        assert op.status == OpportunityStatus.BLOCK
        assert op.net_profit is None
        assert op.total_fees is None
        assert op.costs.missing_names() == ("forecast_ex:settlement_fee",
                                            "settlement_delay")
        # trade fees on both legs were computable and stay available
        known = {c.name: c.amount for c in op.costs.components if c.available}
        assert known["kalshi:trade_fee"] == Decimal("1.68")
        assert known["kalshi:settlement_fee"] == Decimal("0.00")
        assert any("withdrawal/settlement fee unpublished" in r
                   for r in op.block_reasons)

    def test_context_required_without_context_is_unavailable(self):
        calc = _fee_calc({"kalshi": {
            "trade_calculation_status": "CONTEXT_REQUIRED",
            "required_context": ["series", "fee_multiplier"]}})
        op = _evaluate(calc)
        assert op.status == OpportunityStatus.BLOCK
        assert op.net_profit is None
        assert op.costs.missing_names() == ("kalshi:trade_fee",
                                            "settlement_delay")
        assert any("requires explicit contract/order context" in r
                   for r in op.block_reasons)


class TestUnknownSettlementTiming:
    def test_missing_settlement_time_leaves_net_profit_unavailable(self):
        yes_c, no_c = _pair(settlement=None)
        op = _evaluate(_fee_calc(), yes_c, no_c)
        assert op.status == OpportunityStatus.BLOCK
        assert op.net_profit is None
        assert op.settlement_delay_cost is None
        # fees WERE computable, and they stay reported as known costs
        assert op.total_fees == Decimal("1.68") + Decimal("1.00")
        assert op.costs.missing_names() == ("settlement_delay",)
        assert any("settlement lockup UNKNOWN" in r for r in op.block_reasons)
        assert any("settlement delay spread UNKNOWN" in r
                   for r in op.block_reasons)
        # capital is known here (fees were computable), so it is reported
        assert op.capital_required == Decimal("90.00") + Decimal("2.68")

    def test_venue_without_a_settlement_delay_model_is_unavailable(self):
        """`normalization.settlement` has no post-resolution delay model for
        polymarket_us, so the lockup is unknown and must block."""
        calc = _fee_calc(venues=("kalshi", "polymarket_us"))
        yes_c, no_c = _pair(yes_venue="kalshi", no_venue="polymarket_us")
        no_book = make_book("polymarket_us", "N", asks=[("0.50", "100")])
        op = evaluate_two_leg(
            yes_c, no_c, make_book("kalshi", "Y", asks=[("0.40", "100")]),
            no_book, calc, Mode.PAPER,
            independence=Independence.INDEPENDENT)
        assert op is not None
        assert op.status == OpportunityStatus.BLOCK
        assert op.net_profit is None
        assert op.costs.missing_names() == ("settlement_delay",)

    def test_delay_cost_is_not_computed_on_a_fee_free_capital_base(self):
        """When fees are unknown the capital base is unknown, so the cost of
        capital is unavailable too — it is not charged on gross cost alone,
        which would understate it."""
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        op = _evaluate(calc)
        delay = next(c for c in op.costs.components
                     if c.name == "settlement_delay")
        assert delay.amount is None
        assert "cost-of-capital base unknown" in delay.reason
        assert op.capital_required is None


class TestOtherMandatoryCostFailures:
    def test_no_schedule_on_file_for_a_venue(self, fee_calc):
        """Production ledger minus one venue (the inherited reproduction)."""
        import copy
        crippled = copy.copy(fee_calc)
        crippled.schedules = {"kalshi": fee_calc.schedules["kalshi"]}
        op = _evaluate(crippled)
        assert op.status == OpportunityStatus.BLOCK
        assert op.net_profit is None
        assert op.total_fees is None
        assert op.gross_edge == Decimal("10.00")

    def test_expired_schedule_is_unavailable(self):
        calc = _fee_calc({"kalshi": {"effective_date": "2019-01-01",
                                     "expiration_date": "2020-01-01"}})
        op = _evaluate(calc)
        assert op.net_profit is None
        assert any("expired" in r for r in op.block_reasons)

    def test_not_yet_effective_schedule_is_unavailable(self):
        calc = _fee_calc({"kalshi": {"effective_date": "2099-01-01"}})
        op = _evaluate(calc)
        assert op.net_profit is None
        assert any("not effective until" in r for r in op.block_reasons)

    def test_stale_schedule_under_strict_freshness_is_unavailable(self):
        calc = _fee_calc({"kalshi": {"verified_at": "2020-01-01"}})
        op = _evaluate(calc, strict_fee_freshness=True)
        assert op.status == OpportunityStatus.BLOCK
        assert op.net_profit is None
        # strict freshness rejects the schedule outright, so BOTH kalshi
        # components are unavailable, and the delay cost with them
        assert op.costs.missing_names() == ("kalshi:trade_fee",
                                            "kalshi:settlement_fee",
                                            "settlement_delay")
        assert any("Re-verify before live execution" in r
                   for r in op.block_reasons)
        # ... while non-strict evaluation of the same schedule still costs it
        relaxed = _evaluate(calc, strict_fee_freshness=False)
        assert relaxed.net_profit is not None

    def test_unverified_schedule_is_unavailable_even_if_marked_complete(self):
        calc = _fee_calc({"kalshi": {"verification_status": "UNVERIFIED"}})
        op = _evaluate(calc)
        assert op.net_profit is None
        assert op.status == OpportunityStatus.BLOCK


class TestLedgerSemantics:
    def test_ledger_has_no_total_while_a_component_is_missing(self):
        ledger = CostLedger((
            CostComponent("a", Decimal("1")),
            CostComponent("b", None, "missing"),
        ))
        assert ledger.complete is False
        assert ledger.total is None
        assert ledger.known_total == Decimal("1")
        assert ledger.missing_names() == ("b",)

    def test_empty_ledger_is_not_complete(self):
        """An opportunity that was never costed has not been shown to be
        free of mandatory costs."""
        assert CostLedger().complete is False
        assert CostLedger().total is None

    def test_complete_ledger_totals_every_component(self):
        ledger = CostLedger((CostComponent("a", Decimal("1")),
                             CostComponent("b", Decimal("2.5"))))
        assert ledger.complete is True
        assert ledger.total == Decimal("3.5")
        assert ledger.known_total == Decimal("3.5")


class TestValidCalculationsStillCorrect:
    def test_complete_costs_produce_the_exact_verified_net_profit(self):
        op = _evaluate(_fee_calc())
        assert op.status == OpportunityStatus.TRADE
        assert op.costs_complete
        assert op.costs.missing_names() == ()
        # walked cost, payout and gross edge
        assert op.quantity == Decimal("100")
        assert op.gross_cost == Decimal("90.00")
        assert op.gross_edge == Decimal("10.00")
        # kalshi 0.07*100*0.40*0.60 = 1.68 ; forecast_ex 0.01*100 = 1.00
        assert op.total_fees == Decimal("2.68")
        assert op.capital_required == Decimal("92.68")
        # 3-day lockup at 5% annual on 92.68 → 0.0381
        assert op.settlement_delay_cost == Decimal("0.0381")
        assert op.net_profit == Decimal("10.00") - Decimal("2.68") \
            - Decimal("0.0381")
        assert op.net_profit == Decimal("7.2819")
        assert op.net_profit_per_contract == op.net_profit / Decimal("100")
        assert op.return_on_capital == op.net_profit / Decimal("92.68")
        # gross edge and net profit are different numbers, on purpose
        assert op.gross_edge != op.net_profit
        assert op.is_executable
        assert op.profitable_after_all_costs

    def test_negative_verified_net_profit_still_blocks_with_a_number(self):
        yes_book = make_book("kalshi", "Y", asks=[("0.45", "100")])
        no_book = make_book("forecast_ex", "N", asks=[("0.54", "100")])
        yes_c, no_c = _pair()
        op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, _fee_calc(),
                              Mode.PAPER,
                              independence=Independence.INDEPENDENT)
        assert op is not None
        assert op.status == OpportunityStatus.BLOCK
        # costs were known, so the (negative) net profit IS reported — a
        # verified loss is a number, an unverifiable profit is not
        assert op.net_profit is not None and op.net_profit <= 0
        assert any("net profit not positive after all costs" in r
                   for r in op.block_reasons)

    def test_a_block_for_independence_keeps_its_verified_net_profit(self):
        """BLOCK is not synonymous with UNVERIFIED: when every cost was
        computed the number is real and stays visible."""
        op = _evaluate(_fee_calc(), independence=Independence.UNKNOWN)
        assert op.status == OpportunityStatus.BLOCK
        assert op.costs_complete
        assert op.net_profit == Decimal("7.2819")
        assert op.is_executable is False
        assert any("not INDEPENDENT" in r for r in op.block_reasons)

    def test_no_gross_edge_still_returns_no_opportunity(self):
        yes_book = make_book("kalshi", "Y", asks=[("0.60", "100")])
        no_book = make_book("forecast_ex", "N", asks=[("0.45", "100")])
        yes_c, no_c = _pair()
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        assert evaluate_two_leg(yes_c, no_c, yes_book, no_book, calc,
                                Mode.PAPER,
                                independence=Independence.INDEPENDENT) is None


class TestGrossEdgeIsNeverExecutable:
    def test_score_refuses_to_rank_an_unpriced_opportunity(self):
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        op = _evaluate(calc)
        with pytest.raises(ValueError, match="no verified net profit"):
            score(op)

    def test_rank_puts_a_huge_unpriced_gross_edge_last(self):
        big_unpriced = _evaluate(
            _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}}))
        small_priced = _evaluate(_fee_calc(), max_quantity=Decimal("2"))
        assert big_unpriced.gross_edge > small_priced.gross_edge
        assert small_priced.net_profit is not None
        ordered = rank([big_unpriced, small_priced])
        assert ordered[0] is small_priced
        assert ordered[-1] is big_unpriced

    def test_search_event_never_promotes_an_unpriced_opportunity(self):
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        yes_c, no_c = _pair()
        books = {("kalshi", "Y"): make_book("kalshi", "Y",
                                           asks=[("0.40", "100")]),
                 ("forecast_ex", "N"): make_book("forecast_ex", "N",
                                                 asks=[("0.50", "100")])}
        approved = {("forecast_ex:N", "kalshi:Y"): ("m1", 0.99)}
        # both independent orderings of the pair are evaluated
        ops = search_event([yes_c, no_c], books, calc, Mode.PAPER, approved)
        assert len(ops) == 2
        for op in ops:
            assert op.status == OpportunityStatus.BLOCK
            assert op.net_profit is None
            assert op.is_executable is False


class TestDashboardReportsUnavailable:
    def test_unavailable_amounts_render_as_na(self):
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        out = _render(_evaluate(calc))
        assert out.count("n/a") >= 3          # Fees, Net, Capital
        assert "$10.00" in out                # gross edge is still a number
        assert "INCOMPLETE (2)" in out
        assert "no verified net profit" in out

    def test_a_verified_opportunity_renders_its_numbers(self):
        out = _render(_evaluate(_fee_calc()))
        assert "$7.28" in out                 # net profit
        assert "$2.68" in out                 # fees
        assert "$92.68" in out                # capital
        assert "complete" in out
        assert "no verified net profit" not in out

    def test_format_amount_never_turns_unknown_into_zero(self):
        assert format_amount(None) == "n/a"
        assert format_amount(Decimal("0")) == "$0.00"
        assert format_amount(Decimal("9.9630")) == "$9.96"


class TestPersistenceKeepsUnavailableCosts:
    def _rows(self, tmp_path, op):
        repo = Repository(tmp_path / "paper.sqlite3", Mode.PAPER)
        repo.save_opportunity(op)
        with Session(repo.engine) as s:
            return list(s.scalars(select(OpportunityRow)))

    def test_blocked_opportunity_stores_null_net_profit(self, tmp_path):
        calc = _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}})
        op = _evaluate(calc)
        row = self._rows(tmp_path, op)[0]
        assert row.status == "BLOCK"
        assert row.net_profit is None          # never "0", never "9.96"
        assert row.fees is None
        assert row.capital_required is None
        # the research figure survives, clearly labelled as gross
        assert row.gross_profit == "10.00"
        assert json.loads(row.unavailable_costs) == [
            "kalshi:trade_fee", "settlement_delay"]
        assert "FEE_DATA_INCOMPLETE" in row.block_reasons

    def test_verified_opportunity_stores_its_numbers(self, tmp_path):
        row = self._rows(tmp_path, _evaluate(_fee_calc()))[0]
        assert row.status == "TRADE"
        assert row.net_profit == "7.2819"
        assert row.fees == "2.68"
        assert row.capital_required == "92.68"
        assert row.gross_profit == "10.00"
        assert row.unavailable_costs is None

    def test_migration_003_relaxes_the_cost_columns(self, tmp_path):
        """A database created by the v2 schema (NOT NULL cost columns) must
        upgrade, keep its rows, and then accept NULLs."""
        from prediction_arb.database.migrations.runner import (
            MIGRATIONS,
            run_migrations,
        )
        from sqlalchemy import create_engine

        assert [v for v, _d, _f in MIGRATIONS] == [1, 2, 3]
        path = tmp_path / "legacy.sqlite3"
        engine = create_engine(f"sqlite:///{path}", future=True)
        with engine.begin() as conn:
            conn.execute(text(
                "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,"
                " description TEXT, applied_at TEXT)"))
            conn.execute(text(
                "INSERT INTO schema_migrations VALUES "
                "(1, 'create initial schema', datetime('now')),"
                "(2, 'markets fields', datetime('now'))"))
            conn.execute(text("""
                CREATE TABLE opportunities (
                    id INTEGER NOT NULL PRIMARY KEY,
                    opportunity_id VARCHAR(64) NOT NULL,
                    mode VARCHAR(16) NOT NULL,
                    opportunity_type VARCHAR(32) NOT NULL,
                    timestamp DATETIME NOT NULL,
                    normalized_event TEXT NOT NULL,
                    contracts TEXT NOT NULL,
                    venues VARCHAR(128) NOT NULL,
                    gross_profit VARCHAR(32) NOT NULL,
                    fees VARCHAR(32) NOT NULL,
                    slippage VARCHAR(32) NOT NULL,
                    net_profit VARCHAR(32) NOT NULL,
                    quantity VARCHAR(32) NOT NULL,
                    capital_required VARCHAR(32) NOT NULL,
                    independence VARCHAR(24) NOT NULL,
                    status VARCHAR(16) NOT NULL,
                    block_reasons TEXT NOT NULL,
                    first_seen DATETIME,
                    last_seen DATETIME)"""))
            conn.execute(text(
                "INSERT INTO opportunities (opportunity_id, mode, "
                "opportunity_type, timestamp, normalized_event, contracts, "
                "venues, gross_profit, fees, slippage, net_profit, quantity, "
                "capital_required, independence, status, block_reasons) "
                "VALUES ('old-1', 'paper', 'TYPE_A_GUARANTEED', "
                "datetime('now'), 'e', '[]', 'kalshi', '10.00', '0', '0.00', "
                "'9.9630', '100', '90.00', 'INDEPENDENT', 'BLOCK', '[]')"))
        run_migrations(engine)
        with engine.begin() as conn:
            info = conn.execute(
                text("PRAGMA table_info(opportunities)")).fetchall()
            notnull = {row[1]: row[3] for row in info}
            assert notnull["fees"] == 0
            assert notnull["net_profit"] == 0
            assert notnull["capital_required"] == 0
            assert "unavailable_costs" in notnull
            versions = [row[0] for row in conn.execute(
                text("SELECT version FROM schema_migrations"))]
            assert versions == [1, 2, 3]
            # the recorded row survived the rebuild unchanged
            kept = conn.execute(text(
                "SELECT opportunity_id, net_profit, fees FROM opportunities"
            )).fetchall()
            assert kept == [("old-1", "9.9630", "0")]
            # and a NULL is now accepted
            conn.execute(text(
                "INSERT INTO opportunities (opportunity_id, mode, "
                "opportunity_type, timestamp, normalized_event, contracts, "
                "venues, gross_profit, fees, slippage, net_profit, quantity, "
                "capital_required, independence, status, block_reasons, "
                "unavailable_costs) VALUES ('new-1', 'paper', "
                "'TYPE_A_GUARANTEED', datetime('now'), 'e', '[]', 'kalshi', "
                "'10.00', NULL, '0.00', NULL, '100', NULL, 'INDEPENDENT', "
                "'BLOCK', '[]', '[\"kalshi:trade_fee\"]')"))
        engine.dispose()

    def test_migration_003_is_idempotent_on_a_fresh_database(self, tmp_path):
        repo = Repository(tmp_path / "fresh.sqlite3", Mode.PAPER)
        from prediction_arb.database.migrations.runner import run_migrations
        run_migrations(repo.engine)
        run_migrations(repo.engine)
        with Session(repo.engine) as s:
            assert s.scalar(select(OpportunityRow)) is None


class TestReportSurfacesUnpricedOpportunities:
    def test_paper_daily_report_counts_unpriced_separately(self, tmp_path,
                                                           monkeypatch):
        from prediction_arb.modes.paper import reports as paper_reports

        monkeypatch.setattr(paper_reports, "_REPORT_DIR", tmp_path)
        repo = Repository(tmp_path / "paper.sqlite3", Mode.PAPER)
        repo.save_opportunity(_evaluate(_fee_calc()))
        repo.save_opportunity(_evaluate(_fee_calc({
            "kalshi": {"trade_calculation_status": "INCOMPLETE"}})))
        path = paper_reports.generate_daily_report(repo)
        report = json.loads(path.read_text())
        assert report["opportunities_detected"] == 2
        assert report["opportunities_tradeable"] == 1
        assert report["opportunities_without_verified_net_profit"] == 1
        assert report["trades_executed"] == 0

    def test_historical_summary_counts_unpriced_not_profitable(self, tmp_path,
                                                               monkeypatch):
        """`surviving_fees` must count VERIFIED positive net profit only."""
        from prediction_arb.backtesting.metrics import summarize_frames
        from datetime import datetime, timezone

        ts = datetime(2026, 10, 9, tzinfo=timezone.utc)
        priced = _evaluate(_fee_calc())
        unpriced = _evaluate(
            _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}}))
        assert [o.profitable_after_all_costs
                for o in (priced, unpriced)] == [True, False]
        frames = [(ts, [priced, unpriced])]
        result = summarize_frames(frames, max_total_capital=Decimal("1000"))
        # the unpriced opportunity is counted as observed, not as executable
        assert result.opportunities_observed == 2
        assert result.executable_observed == 1
        assert result.fee_drag.uncosted_opportunities == 1
        # a fee-drag ratio over a partially costed set is withheld, not
        # averaged down
        assert result.fee_drag.drag_ratio is None
        assert result.to_dict()["fee_drag"]["uncosted_opportunities"] == 1
        # and it contributes nothing to the modelled equity curve
        assert result.equity.final_equity == priced.net_profit

    def test_historical_replay_end_to_end_reports_unpriced(self, tmp_path,
                                                           monkeypatch):
        """The real replay engine: one approved pair, one incomplete fee
        schedule. The summary and the console must both say the net profit
        is unavailable rather than implying a profitable opportunity."""
        import asyncio

        from prediction_arb.core.config import load_config
        from prediction_arb.matching.llm_matcher import LLMMatcher
        from prediction_arb.modes import shared
        from prediction_arb.modes.historical import engine as hist_engine
        from prediction_arb.modes.historical.engine import HistoricalEngine

        recorded_path = tmp_path / "recorded.sqlite3"
        recorded = Repository(recorded_path, Mode.COLLECT)
        yes = make_contract(venue="kalshi", contract_id="KX-YES",
                            threshold=Decimal("25"), threshold_op=">=",
                            units="bps", early_resolution="Fed statement")
        no = dataclasses.replace(
            make_contract(venue="forecast_ex", contract_id="FX-NO",
                          threshold=Decimal("25"), threshold_op=">=",
                          units="bps", early_resolution="Fed statement"),
            outcome="NO")
        recorded.record_contract(yes)
        recorded.record_contract(no)
        recorded.save_orderbook_snapshot(make_book(
            "kalshi", "KX-YES", asks=[("0.40", "100")], bids=[("0.38", "50")]))
        recorded.save_orderbook_snapshot(make_book(
            "forecast_ex", "FX-NO", asks=[("0.50", "100")],
            bids=[("0.48", "50")]))

        async def fake_matching(contracts, llm):
            return {("forecast_ex:FX-NO", "kalshi:KX-YES"): ("m1", 0.99)}, \
                shared.ScanResult(markets_scanned=len(contracts),
                                  pairs_considered=1,
                                  pairs_deterministic_pass=1)
        monkeypatch.setattr(hist_engine, "run_matching", fake_matching)

        console = Console(file=io.StringIO(), force_terminal=False, width=200)
        own = Repository(tmp_path / "own.sqlite3", Mode.HISTORICAL)
        reader = Repository(recorded_path, Mode.HISTORICAL, read_only=True)
        config = load_config("historical")
        engine = HistoricalEngine(
            config, own,
            _fee_calc({"kalshi": {"trade_calculation_status": "INCOMPLETE"}}),
            LLMMatcher(client=None), console, recorded=reader)
        summary = asyncio.run(engine.run())

        assert summary["opportunities"] >= 1
        assert summary["net_profit_unavailable"] == summary["opportunities"]
        assert summary["surviving_fees"] == 0
        assert summary["executable"] == 0
        # the fee-drag ratio is withheld, not averaged over a partial set
        assert summary["metrics"]["fee_drag"]["drag_ratio"] is None
        assert summary["metrics"]["fee_drag"]["uncosted_opportunities"] >= 1
        out = console.file.getvalue()
        assert "no verified net profit" in out
        # and the persisted row carries NULL, not a fabricated profit
        with Session(own.engine) as sess:
            rows = list(sess.scalars(select(OpportunityRow)))
        assert rows and all(r.net_profit is None for r in rows)
        assert all(r.status == "BLOCK" for r in rows)

    def test_fee_drag_ratio_is_reported_when_everything_is_costed(self):
        from prediction_arb.backtesting.metrics import compute_fee_drag

        ops = [_evaluate(_fee_calc()), _evaluate(_fee_calc(),
                                                max_quantity=Decimal("2"))]
        drag = compute_fee_drag(ops)
        assert drag.uncosted_opportunities == 0
        assert drag.drag_ratio is not None
        assert drag.total_fees > 0
