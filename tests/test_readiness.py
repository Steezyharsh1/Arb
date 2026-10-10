"""D10/D19 regression tests: readiness and fee freshness are derived, not asserted.

Two inherited claims were false at runtime:

* ``main.py::cmd_readiness`` hardcoded the two-venue gate to the literal
  ``False`` (D10), so the report could never pass even with two verified
  independent venues, and it never said what the real blocker was;
* ``fees.max_schedule_age_days: 30`` was documented as the live staleness
  gate, but ``strict_fee_freshness`` defaulted to ``False`` in both
  ``evaluate_two_leg()`` and ``run_arbitrage_scan()`` and no production
  caller passed ``True`` (D19) — the documented gate did not exist.

These tests pin the replacement:

* one freshness policy, derived from the mode (§98): live and paper refuse a
  stale schedule, historical replay still costs it and is labelled RESEARCH
  USE ONLY, collect never calculates fees;
* structured schedule checks for fresh, stale, missing, unverified,
  not-yet-effective, expired and incomplete schedules — the same rule the
  engine enforces by raising;
* enforcement at every production call site (two-leg evaluation, the scan,
  and the simulated execution engines);
* every readiness gate proven to both PASS and BLOCK from synthetic state,
  so no verdict can be a constant;
* the current repository state reported honestly, with the actual blocker
  named and no database created by a status report.
"""
from __future__ import annotations

import asyncio
import io
import shutil
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from rich.console import Console

from prediction_arb import main as main_module
from prediction_arb.core import config as core_config
from prediction_arb.core.types import (
    APIStatus,
    Independence,
    Mode,
    OrderAction,
    OrderRequest,
    OrderStatus,
    OrderType,
    OrderTypeSupport,
    Side,
)
from prediction_arb.database.repository import Repository
from prediction_arb.execution.simulated import (
    HistoricalExecutionEngine,
    PaperExecutionEngine,
)
from prediction_arb.fees.calculator import FeeCalculator
from prediction_arb.fees.freshness import (
    FALLBACK_POLICY,
    FeeFreshnessPolicy,
    check_schedule,
    freshness_enforced,
    policy_for_mode,
    policy_note,
    research_fee_label,
    schedule_freshness,
    summarize_checks,
)
from prediction_arb.monitoring.readiness import (
    Criterion,
    CriterionState,
    MatcherProbe,
    ReadinessInputs,
    ReadinessThresholds,
    RecordedEvidence,
    criterion_book_staleness,
    criterion_data_venues,
    criterion_eligibility,
    criterion_fee_completeness,
    criterion_fee_freshness,
    criterion_independent_pairs,
    criterion_kill_switch,
    criterion_live_capable_venues,
    criterion_live_configuration,
    criterion_matcher,
    criterion_recorded_evidence,
    criterion_risk_limits,
    criterion_settlement_timing,
    evaluate_all,
    evaluate_mode,
    load_recorded_evidence,
    print_readiness,
    probe_live_configuration,
    probe_matcher,
    venue_capabilities,
)
from prediction_arb.normalization.settlement import (
    PRODUCTION_SETTLEMENT_TIMING,
    CapitalRelease,
    SettlementTimingSource,
    TimingEvidenceStatus,
)
from prediction_arb.arbitrage.two_leg import evaluate_two_leg
from prediction_arb.modes.shared import run_arbitrage_scan

from .conftest import (
    SETTLE,
    SETTLEMENT_TEST_NOW,
    fresh_verification_date,
    make_book,
    make_contract,
)

# --------------------------------------------------------------------- helpers

#: A second venue the provenance registry classifies INDEPENDENT of kalshi.
SECOND = "forecast_ex"
PAIR = ("kalshi", SECOND)

RISK_LIMITS = {
    "limits": {
        "max_total_capital": 1000, "max_capital_per_venue": 500,
        "max_capital_per_event": 200, "max_capital_per_trade": 100,
        "max_unhedged_exposure": 25, "max_daily_loss": 50,
        "max_open_positions": 20, "max_concurrent_orders": 4,
    },
    "partial_fill": {
        "max_unhedged_seconds": 30, "max_unhedged_contracts": 100,
        "max_unhedged_dollars": 25, "max_emergency_loss": 10,
    },
}

SETTINGS = {
    "execution": {"max_book_age_ms": 1000},
    "arbitrage": {"maximum_book_age_ms": 1000},
    "readiness": {
        "minimum_recorded_snapshots": 10,
        "minimum_analyzed_opportunities": 2,
        "minimum_live_capable_venues": 2,
        "minimum_data_venues": 2,
    },
}


def _fee_today() -> date:
    """The date the fee engine itself uses for age arithmetic.

    Reading the same clock the engine reads keeps these tests correct under
    the real wall clock *and* under a frozen-clock date-independence sweep:
    "45 days ago" means 45 days before whatever the engine thinks today is.
    """
    from prediction_arb.fees import calculator
    return calculator.utcnow().date()


def _days_ago(days: int) -> str:
    return (_fee_today() - timedelta(days=days)).isoformat()


def _fee_calc(venues=PAIR, *, max_age_days: int = 30,
              overrides: dict | None = None) -> FeeCalculator:
    """Synthetic COMPLETE schedules; per-venue overrides degrade them."""
    overrides = overrides or {}
    schedules = []
    for venue in venues:
        cfg = {
            "venue": venue,
            "version": "test-readiness-v1",
            "effective_date": "2020-01-01",
            "verified_at": fresh_verification_date(),
            "verification_status": "VERIFIED",
            "documentation_tested": True,
            "source": "synthetic deterministic test fixture",
            "trade_calculation_status": "COMPLETE",
            "settlement_calculation_status": "COMPLETE",
            "fees": [
                {"fee_type": "taker_quadratic", "multiplier": "0.07",
                 "applies_to": "taker"},
                {"fee_type": "maker_quadratic", "multiplier": "0.0175",
                 "applies_to": "maker"},
                {"fee_type": "settlement", "fixed_amount": "0",
                 "applies_to": "settlement"},
            ],
        }
        cfg.update(overrides.get(venue, {}))
        schedules.append(cfg)
    return FeeCalculator.from_config({"schedules": schedules}, max_age_days)


class StubAdapter:
    """Duck-typed adapter: readiness reads only what an adapter self-reports."""

    def __init__(self, venue: str,
                 api_status: APIStatus = APIStatus.VERIFIED,
                 order_types_verified: bool = True):
        self.venue = venue
        self._api_status = api_status
        self._order_types_verified = order_types_verified

    def get_api_status(self) -> APIStatus:
        return self._api_status

    def get_supported_order_types(self) -> OrderTypeSupport:
        return OrderTypeSupport(limit=True, gtc=True,
                                verified=self._order_types_verified,
                                source="synthetic test adapter")


def _adapters(*specs) -> dict:
    """``_adapters(("kalshi", APIStatus.VERIFIED, True), ...)``."""
    out = {}
    for spec in specs:
        venue, status, orders = spec
        out[venue] = StubAdapter(venue, status, orders)
    return out


def _documented_timing(venues=PAIR) -> dict:
    """TEST-ONLY registry: every named venue has DOCUMENTED release timing.

    Production timing for Polymarket US and ForecastEx is UNKNOWN (D18), so a
    green settlement gate can only be built from a labelled synthetic entry —
    which is the point: the criterion reads the registry, not a constant.
    """
    def release(contract):
        return CapitalRelease(contract.venue, contract.contract_id,
                              contract.settlement_time, timedelta(0),
                              contract.settlement_time)

    return {
        venue: SettlementTimingSource(
            venue=venue, status=TimingEvidenceStatus.DOCUMENTED,
            release_meaning="TEST-ONLY synthetic: release at settlement_time",
            resolution_basis="TEST-ONLY synthetic",
            post_resolution_delay="TEST-ONLY synthetic: zero",
            sources=("tests/test_readiness.py synthetic fixture",),
            reviewed_on="n/a",
            evidence="none — production evidence lives in normalization/settlement.py",
            gaps="not evidence; never used outside tests",
            estimator=release)
        for venue in venues
    }


def _live_config_dir(tmp_path: Path, *, create_env: bool = True,
                     env_file: str = ".env.live", **overrides) -> Path:
    """A synthetic config/ holding live.yaml (and optionally its env file)."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "mode": "live",
        "execution_engine": "LiveExecutionEngine",
        "database": "prediction_arb_live",
        "allow_live_orders": True,
        "env_file": env_file,
        "requires_trading_credentials": True,
    }
    doc.update(overrides)
    (config_dir / "live.yaml").write_text(yaml.safe_dump(doc), encoding="utf-8")
    if create_env:
        (tmp_path / env_file).write_text(
            "# synthetic placeholder — holds no credentials\n", encoding="utf-8")
    return config_dir


def _ready_inputs(tmp_path: Path, **overrides) -> ReadinessInputs:
    """A synthetic state in which **every** live gate genuinely passes.

    It exists to prove the report is computed: if any criterion were a
    hardcoded ``False`` this state could not be ready.
    """
    config_dir = _live_config_dir(tmp_path)
    values = dict(
        adapters=_adapters(("kalshi", APIStatus.VERIFIED, True),
                           (SECOND, APIStatus.VERIFIED, True)),
        fee_calc=_fee_calc(),
        settings=SETTINGS,
        risk_limits=RISK_LIMITS,
        recorded=RecordedEvidence(tmp_path / "recorded.sqlite3", True, 500, 60,
                                  "synthetic recorded store"),
        matcher=MatcherProbe(True, "synthetic: matcher configured"),
        kill_switch_engaged=False,
        jurisdiction="US-TEST",
        live_configuration=probe_live_configuration(config_dir, tmp_path),
        settlement_sources=_documented_timing(),
    )
    values.update(overrides)
    return ReadinessInputs(**values)


@pytest.fixture
def eligible_jurisdiction(monkeypatch) -> str:
    """TEST-ONLY ledger entry making every venue ELIGIBLE in ``US-TEST``.

    Production US-UT eligibility is UNKNOWN for non-sports categories
    (docs/COMPLIANCE.md, evidence row R9), so the live eligibility gate
    cannot pass without injecting a labelled synthetic record.
    """
    from prediction_arb.compliance import jurisdiction as jur
    record = jur.EligibilityRecord(
        jurisdiction="US-TEST", venue="*", contract_category="*",
        status=jur.EligibilityStatus.ELIGIBLE, effective_date="2026-01-01",
        source="tests/test_readiness.py synthetic ledger entry",
        verified_at="n/a", notes="TEST-ONLY; not eligibility evidence")
    monkeypatch.setattr(jur, "_RECORDS", [record, *jur._RECORDS])
    return "US-TEST"


@pytest.fixture
def isolated_root(tmp_path, monkeypatch) -> Path:
    """A project root holding a copy of the real config and no data/."""
    shutil.copytree(core_config.CONFIG_DIR, tmp_path / "config")
    monkeypatch.setattr(core_config, "ROOT", tmp_path)
    monkeypatch.setattr(core_config, "CONFIG_DIR", tmp_path / "config")
    from prediction_arb.risk import kill_switch
    monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KILL_SWITCH")
    return tmp_path


# --------------------------------------------------------------- the policy


class TestFreshnessPolicyIsOneRule:
    def test_live_and_paper_are_execution_facing(self):
        assert policy_for_mode(Mode.LIVE) is FeeFreshnessPolicy.MANDATORY
        assert policy_for_mode(Mode.PAPER) is FeeFreshnessPolicy.MANDATORY
        assert policy_for_mode(Mode.LIVE).enforces_freshness
        assert policy_for_mode(Mode.PAPER).enforces_freshness

    def test_historical_is_labelled_research(self):
        assert policy_for_mode(Mode.HISTORICAL) is FeeFreshnessPolicy.RESEARCH
        assert not policy_for_mode(Mode.HISTORICAL).enforces_freshness

    def test_collect_never_calculates_fees(self):
        assert (policy_for_mode(Mode.COLLECT)
                is FeeFreshnessPolicy.NOT_APPLICABLE)
        assert not policy_for_mode(Mode.COLLECT).enforces_freshness

    def test_mode_value_strings_are_accepted(self):
        """Callers legitimately pass ``"paper"`` (see the scan's own tests)."""
        assert policy_for_mode("paper") is FeeFreshnessPolicy.MANDATORY
        assert policy_for_mode("historical") is FeeFreshnessPolicy.RESEARCH
        assert policy_for_mode("collect") is FeeFreshnessPolicy.NOT_APPLICABLE

    @pytest.mark.parametrize("mode", ["nonsense", "", None, 42, object()])
    def test_an_unrecognized_mode_fails_closed(self, mode):
        """UNKNOWN never means "skip the check"."""
        assert policy_for_mode(mode) is FALLBACK_POLICY
        assert FALLBACK_POLICY is FeeFreshnessPolicy.MANDATORY
        assert freshness_enforced(mode) is True

    def test_an_explicit_override_still_wins_both_ways(self):
        assert freshness_enforced(Mode.LIVE, override=False) is False
        assert freshness_enforced(Mode.HISTORICAL, override=True) is True
        assert freshness_enforced(Mode.PAPER, override=None) is True

    def test_the_policy_note_names_the_limit(self):
        note = policy_note(FeeFreshnessPolicy.MANDATORY, 30)
        assert "30" in note and "refused" in note
        assert "RESEARCH USE ONLY" in policy_note(
            FeeFreshnessPolicy.RESEARCH, 30)
        assert "never calculates fees" in policy_note(
            FeeFreshnessPolicy.NOT_APPLICABLE, 30)


# ------------------------------------------------- fresh/stale/missing/unverified


class TestScheduleChecks:
    def test_a_fresh_complete_schedule_has_no_unmet_requirement(self):
        check = _fee_calc().schedule_check("kalshi",
                                          FeeFreshnessPolicy.MANDATORY)
        assert check.present and check.verified and check.calculable
        assert check.usable and not check.stale and not check.research_only
        assert check.reasons == ()
        assert check.freshness.fresh is True
        assert check.freshness.age_days == 0

    def test_a_stale_schedule_blocks_under_the_mandatory_policy(self):
        calc = _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})
        check = calc.schedule_check("kalshi", FeeFreshnessPolicy.MANDATORY)
        assert check.calculable, "staleness is not incompleteness"
        assert check.freshness.stale and check.freshness.blocking
        assert not check.usable
        assert check.freshness.age_days == 45
        reason = check.freshness.reason
        assert "45 days ago" in reason and "30-day limit" in reason
        assert "Re-verify before live execution" in reason
        assert reason in check.reasons

    def test_a_stale_schedule_is_reported_but_usable_for_research(self):
        calc = _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})
        check = calc.schedule_check("kalshi", FeeFreshnessPolicy.RESEARCH)
        assert check.stale and not check.freshness.blocking
        assert check.usable and check.research_only
        assert "research only" in check.freshness.reason
        assert "never for live execution" in check.freshness.reason
        # reported, never silent
        assert check.freshness.reason in check.reasons

    def test_a_schedule_with_no_verification_date_is_unknown_and_blocks(self):
        """`verified_at: null` cannot be shown to be current, so it is not
        treated as fresh — UNKNOWN never means yes (§98)."""
        calc = _fee_calc(overrides={"kalshi": {"verified_at": None}})
        check = calc.schedule_check("kalshi", FeeFreshnessPolicy.MANDATORY)
        assert check.freshness.fresh is None
        assert check.freshness.age_days is None
        assert check.freshness.blocking and not check.freshness.stale
        assert "no verification date" in check.freshness.reason
        # research still costs it — there is no age to refuse — but the
        # unknown age is recorded and named in the run label, never silent
        research = calc.schedule_check("kalshi", FeeFreshnessPolicy.RESEARCH)
        assert research.freshness.fresh is None
        assert research.usable and research.research_only
        assert any("no verification date" in r for r in research.reasons)
        label = research_fee_label([research])
        assert label and "kalshi has no verification date" in label
        assert "RESEARCH USE ONLY: fee schedule(s) older than" in label

        from prediction_arb.core.types import FeeDataError
        with pytest.raises(FeeDataError) as exc:
            calc.schedule_for("kalshi", strict_freshness=True)
        assert "no verification date" in str(exc.value)

    def test_the_refusal_names_the_venue_exactly_once(self):
        """Operator-facing readability: reasons are venue-free and the caller
        prefixes the venue, so the message never reads "kalshi fee schedule
        kalshi …" (§98, §100)."""
        from prediction_arb.core.types import FeeDataError
        calc = _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})
        with pytest.raises(FeeDataError) as exc:
            calc.schedule_for("kalshi", strict_freshness=True)
        message = str(exc.value)
        assert message.startswith("FEE_DATA_INCOMPLETE: kalshi:")
        assert message.count("kalshi") == 1
        assert "45 days ago" in message
        assert "Re-verify before live execution" in message

    def test_a_missing_schedule_is_never_fresh(self):
        check = _fee_calc(venues=("kalshi",)).schedule_check(
            "polymarket_us", FeeFreshnessPolicy.MANDATORY)
        assert not check.present and not check.calculable and not check.usable
        assert check.freshness.fresh is None
        assert check.freshness.age_days is None
        assert check.freshness.blocking, "unknown age fails closed"
        assert any("no fee schedule on file" in r for r in check.reasons)

    def test_a_missing_schedule_is_still_not_fresh_for_research(self):
        check = _fee_calc(venues=("kalshi",)).schedule_check(
            "polymarket_us", FeeFreshnessPolicy.RESEARCH)
        assert not check.usable
        assert not check.research_only, "research tolerance is for age, not absence"

    def test_an_unverified_schedule_is_not_calculable_even_if_complete(self):
        calc = _fee_calc(overrides={
            "kalshi": {"verification_status": "UNVERIFIED"}})
        check = calc.schedule_check("kalshi", FeeFreshnessPolicy.MANDATORY)
        assert not check.verified and not check.calculable
        # the same downgrade `_require_calculable` applies at calculation time
        assert check.trade_calculation_status == "INCOMPLETE"
        assert check.settlement_calculation_status == "INCOMPLETE"
        assert any("verification_status=UNVERIFIED" in r for r in check.reasons)

    @pytest.mark.parametrize("override", [
        {"documentation_tested": False},
        {"source": "   "},
    ])
    def test_untested_or_unsourced_evidence_is_not_calculable(self, override):
        check = _fee_calc(overrides={"kalshi": override}).schedule_check(
            "kalshi", FeeFreshnessPolicy.MANDATORY)
        assert not check.verified and not check.calculable
        assert any("schedule evidence incomplete" in r for r in check.reasons)

    def test_a_missing_effective_date_is_reported(self):
        check = _fee_calc(overrides={"kalshi": {"effective_date": None}}
                          ).schedule_check("kalshi",
                                           FeeFreshnessPolicy.MANDATORY)
        assert not check.currently_effective and not check.calculable
        assert "no verified effective date" in check.reasons

    def test_a_future_schedule_is_not_yet_usable(self):
        check = _fee_calc(overrides={"kalshi": {"effective_date": "2099-01-01"}}
                          ).schedule_check("kalshi",
                                           FeeFreshnessPolicy.MANDATORY)
        assert not check.currently_effective
        assert any("not effective until 2099-01-01" in r for r in check.reasons)

    def test_an_expired_schedule_is_reported(self):
        check = _fee_calc(overrides={
            "kalshi": {"effective_date": "2020-01-01",
                       "expiration_date": "2021-01-01"}}).schedule_check(
                           "kalshi", FeeFreshnessPolicy.MANDATORY)
        assert not check.currently_effective
        assert any("expired on 2021-01-01" in r for r in check.reasons)

    def test_incomplete_components_are_named_separately(self):
        calc = _fee_calc(overrides={
            "kalshi": {"settlement_calculation_status": "INCOMPLETE",
                       "blocking_reason": "customer settlement treatment "
                                           "not established"}})
        check = calc.schedule_check("kalshi", FeeFreshnessPolicy.MANDATORY)
        assert check.trade_calculation_status == "COMPLETE"
        assert not check.calculable
        assert "settlement fee component is INCOMPLETE" in check.reasons
        assert any(r.startswith("venue evidence gap:") for r in check.reasons)
        # the venue's own gap statement appears once, not once per component
        assert sum(1 for r in check.reasons
                   if "customer settlement treatment" in r) == 1

    def test_context_required_is_not_unconditionally_calculable(self):
        check = _fee_calc(overrides={
            "kalshi": {"trade_calculation_status": "CONTEXT_REQUIRED",
                       "required_context": ["series_ticker"]}}).schedule_check(
                           "kalshi", FeeFreshnessPolicy.MANDATORY)
        assert not check.calculable
        assert "trade fee component is CONTEXT_REQUIRED" in check.reasons

    def test_the_age_boundary_is_the_configured_limit(self):
        """`> max_schedule_age_days` is stale; exactly at it is still fresh.

        Explicit ``today=`` and ``verified_at``: a boundary test must not
        depend on the host clock (that is how D16 happened).
        """
        calc = _fee_calc(overrides={"kalshi": {"verified_at": "2026-09-01"}})
        for today, age, fresh in ((date(2026, 9, 30), 29, True),
                                  (date(2026, 10, 1), 30, True),
                                  (date(2026, 10, 2), 31, False)):
            check = calc.schedule_check("kalshi",
                                        FeeFreshnessPolicy.MANDATORY,
                                        today=today)
            assert check.freshness.age_days == age, today
            assert check.freshness.fresh is fresh, today
            assert check.usable is fresh, today

    def test_a_stricter_configured_limit_is_applied(self):
        calc = _fee_calc(max_age_days=7,
                         overrides={"kalshi": {"verified_at": _days_ago(10)}})
        check = calc.schedule_check("kalshi", FeeFreshnessPolicy.MANDATORY)
        assert check.freshness.max_age_days == 7
        assert check.freshness.blocking and not check.usable
        assert "7-day limit" in check.freshness.reason

    def test_the_engine_raises_exactly_what_the_check_reports(self):
        """Report and enforcement cannot drift: one implementation of age."""
        from prediction_arb.core.types import FeeDataError
        calc = _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})
        check = calc.schedule_check("kalshi", FeeFreshnessPolicy.MANDATORY)
        with pytest.raises(FeeDataError) as exc:
            calc.schedule_for("kalshi", strict_freshness=True)
        assert check.freshness.reason in str(exc.value)
        assert "FEE_DATA_INCOMPLETE" in str(exc.value)

    def test_the_engine_does_not_raise_for_research_freshness(self):
        calc = _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})
        assert calc.schedule_for("kalshi", strict_freshness=False).venue == "kalshi"

    def test_checks_cover_every_venue_in_order(self):
        checks = _fee_calc().schedule_checks((SECOND, "kalshi"),
                                            FeeFreshnessPolicy.MANDATORY)
        assert [c.venue for c in checks] == [SECOND, "kalshi"]

    def test_the_summary_is_json_safe_and_states_the_policy(self):
        calc = _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})
        summary = summarize_checks(
            calc.schedule_checks(PAIR, FeeFreshnessPolicy.RESEARCH))
        assert summary["policy"] == "RESEARCH"
        kalshi = summary["venues"]["kalshi"]
        assert kalshi["age_days"] == 45 and kalshi["fresh"] is False
        assert kalshi["research_only"] is True
        assert kalshi["usable_in_this_mode"] is True
        assert isinstance(kalshi["verified_at"], str)

    def test_the_research_label_names_the_stale_schedule(self):
        calc = _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})
        checks = calc.schedule_checks(PAIR, FeeFreshnessPolicy.RESEARCH)
        label = research_fee_label(checks)
        assert label is not None
        assert "RESEARCH USE ONLY" in label
        assert "kalshi verified 45 days ago" in label
        assert "not live-executable prices" in label
        assert SECOND not in label, "a fresh schedule is not labelled"

    def test_no_label_when_everything_is_fresh(self):
        checks = _fee_calc().schedule_checks(PAIR,
                                            FeeFreshnessPolicy.RESEARCH)
        assert research_fee_label(checks) is None


# ------------------------------------------------- enforcement in production


def _pair_and_books():
    yes_c = make_contract(venue="kalshi", contract_id="Y")
    no_c = make_contract(venue=SECOND, contract_id="N")
    return (yes_c, no_c,
            make_book("kalshi", "Y", asks=[("0.40", "100")]),
            make_book(SECOND, "N", asks=[("0.50", "100")]))


def _evaluate(calc, mode, **kwargs):
    yes_c, no_c, yes_book, no_book = _pair_and_books()
    op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, calc, mode,
                          independence=Independence.INDEPENDENT, **kwargs)
    assert op is not None, "these books have a gross edge; sizing must run"
    return op


def _stale_calc():
    return _fee_calc(overrides={"kalshi": {"verified_at": _days_ago(45)}})


class TestEvaluationEnforcesTheModePolicy:
    def test_a_fresh_schedule_is_costed_in_paper(self):
        op = _evaluate(_fee_calc(), Mode.PAPER)
        assert op.net_profit is not None
        assert op.total_fees is not None

    def test_paper_refuses_a_stale_schedule_without_being_told(self):
        """D19: no production caller passes the flag, so the mode must."""
        op = _evaluate(_stale_calc(), Mode.PAPER)
        assert op.net_profit is None and op.total_fees is None
        assert op.status.value == "BLOCK"
        assert any("Re-verify before live execution" in r
                   for r in op.block_reasons)
        assert op.costs.missing_names() == ("kalshi:trade_fee",
                                            "kalshi:settlement_fee",
                                            "settlement_delay")
        # the pre-cost research figure survives; it is still not profit
        assert op.gross_edge == Decimal("10.00")
        assert not op.is_executable

    def test_live_refuses_a_stale_schedule(self):
        op = _evaluate(_stale_calc(), Mode.LIVE)
        assert op.net_profit is None
        assert any("Re-verify before live execution" in r
                   for r in op.block_reasons)

    def test_historical_replay_still_costs_a_stale_schedule(self):
        """Requirement: research replay is not blocked by an old schedule."""
        stale = _evaluate(_stale_calc(), Mode.HISTORICAL)
        assert stale.net_profit is not None
        assert stale.total_fees is not None
        fresh = _evaluate(_fee_calc(), Mode.HISTORICAL)
        assert stale.net_profit == fresh.net_profit

    def test_an_explicit_override_still_wins_both_ways(self):
        """A caller may still decide explicitly — and both directions work."""
        # research mode forced to enforce the limit
        forced = _evaluate(_stale_calc(), Mode.HISTORICAL,
                           strict_fee_freshness=True)
        assert forced.net_profit is None
        assert any("Re-verify before live execution" in r
                   for r in forced.block_reasons)
        # execution-facing mode forced to tolerate it (tests, never production)
        relaxed = _evaluate(_stale_calc(), Mode.PAPER,
                            strict_fee_freshness=False)
        assert relaxed.net_profit is not None
        # a fresh schedule is unaffected by the override
        assert _evaluate(_fee_calc(), Mode.HISTORICAL,
                         strict_fee_freshness=True).net_profit is not None

    def test_a_missing_schedule_blocks_in_every_mode(self):
        """Research tolerance is for age, never for absence."""
        calc = _fee_calc(venues=("kalshi",))
        for mode in (Mode.PAPER, Mode.HISTORICAL, Mode.LIVE):
            op = _evaluate(calc, mode)
            assert op.net_profit is None, mode
            assert any("no fee schedule on file" in r for r in op.block_reasons)


class TestScanEnforcesTheModePolicy:
    def _scan(self, calc, mode, **kwargs):
        yes_c, no_c, yes_book, no_book = _pair_and_books()
        approved = {(f"{SECOND}:N", "kalshi:Y"): ("m-1", 0.99)}
        books = {("kalshi", "Y"): yes_book, (SECOND, "N"): no_book}
        return asyncio.run(run_arbitrage_scan(
            [yes_c, no_c], books, approved, calc, mode,
            minimum_net_profit=Decimal("2"),
            minimum_net_return=Decimal("0.005"),
            maximum_settlement_delay_days=Decimal("5"),
            cost_of_capital_annual=Decimal("0.05"), **kwargs))

    def test_a_paper_scan_blocks_on_a_stale_schedule(self):
        ops = self._scan(_stale_calc(), "paper")
        assert ops and ops[0].net_profit is None
        assert any("Re-verify before live execution" in r
                   for r in ops[0].block_reasons)

    def test_a_historical_scan_prices_the_same_stale_schedule(self):
        ops = self._scan(_stale_calc(), Mode.HISTORICAL)
        assert ops and ops[0].net_profit is not None

    def test_a_fresh_scan_prices_in_both_modes(self):
        for mode in ("paper", Mode.HISTORICAL):
            ops = self._scan(_fee_calc(), mode)
            assert ops and ops[0].net_profit is not None, mode


class TestSimulatedExecutionEnforcesTheModePolicy:
    def _request(self, mode):
        return OrderRequest(
            venue="kalshi", contract_id="K1", side=Side.YES,
            action=OrderAction.BUY, quantity=Decimal("10"),
            order_type=OrderType.LIMIT, limit_price=Decimal("0.50"),
            client_order_id=str(uuid.uuid4()), mode=mode)

    def _book(self, _venue, _contract):
        return make_book("kalshi", "K1", asks=[("0.50", "100")])

    def test_paper_execution_refuses_a_stale_schedule(self):
        engine = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("1000")}, fee_calc=_stale_calc(),
            book_lookup=self._book)
        result = asyncio.run(engine.submit_order(self._request(Mode.PAPER)))
        assert result.status is OrderStatus.REJECTED
        assert "Re-verify before live execution" in result.message

    def test_paper_execution_fills_on_a_fresh_schedule(self):
        engine = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("1000")}, fee_calc=_fee_calc(),
            book_lookup=self._book)
        result = asyncio.run(engine.submit_order(self._request(Mode.PAPER)))
        assert result.status is OrderStatus.FILLED
        assert result.fees > 0

    def test_historical_execution_still_costs_a_stale_schedule(self):
        engine = HistoricalExecutionEngine(
            starting_cash={"kalshi": Decimal("1000")}, fee_calc=_stale_calc(),
            book_lookup=self._book)
        result = asyncio.run(
            engine.submit_order(self._request(Mode.HISTORICAL)))
        assert result.status is OrderStatus.FILLED


class TestHistoricalRunIsLabelledResearch:
    def _engine(self, tmp_path, isolated_root, fee_calc):
        from prediction_arb.matching.llm_matcher import LLMMatcher
        from prediction_arb.modes.historical.engine import HistoricalEngine
        config = core_config.load_config("historical")
        repo = Repository(config.database_path, config.mode)
        console = Console(file=io.StringIO(), force_terminal=False, width=200)
        engine = HistoricalEngine(config, repo, fee_calc,
                                  LLMMatcher(client=None), console)
        return engine, console

    def _one_frame(self):
        contracts = [make_contract(venue="kalshi", contract_id="Y"),
                     make_contract(venue=SECOND, contract_id="N")]
        books = {("kalshi", "Y"): make_book("kalshi", "Y",
                                            asks=[("0.40", "100")]),
                 (SECOND, "N"): make_book(SECOND, "N", asks=[("0.50", "100")])}
        return [(SETTLEMENT_TEST_NOW, contracts, books)]

    def test_a_stale_schedule_is_labelled_not_refused(self, tmp_path,
                                                      isolated_root,
                                                      monkeypatch):
        engine, console = self._engine(tmp_path, isolated_root, _stale_calc())
        monkeypatch.setattr(engine.provider, "load_frames",
                            lambda start, end: self._one_frame())
        summary = asyncio.run(engine.run(single_date=date(2026, 1, 1)))
        out = console.file.getvalue()
        assert "RESEARCH USE ONLY" in out
        assert "kalshi verified 45 days ago" in out
        assert summary["fee_freshness"]["policy"] == "RESEARCH"
        label = summary["fee_freshness"]["research_only_label"]
        assert label and "RESEARCH USE ONLY" in label
        assert summary["fee_freshness"]["schedules"]["kalshi"]["age_days"] == 45

    def test_a_fresh_schedule_needs_no_research_label(self, tmp_path,
                                                      isolated_root,
                                                      monkeypatch):
        engine, console = self._engine(tmp_path, isolated_root, _fee_calc())
        monkeypatch.setattr(engine.provider, "load_frames",
                            lambda start, end: self._one_frame())
        summary = asyncio.run(engine.run(single_date=date(2026, 1, 1)))
        out = console.file.getvalue()
        assert summary["fee_freshness"]["research_only_label"] is None
        # the policy itself is always stated, so silence is never ambiguous…
        assert "research fee freshness" in out
        # …but the research label line is printed only when it is true
        assert "RESEARCH USE ONLY: fee schedule(s) older than" not in out


# ------------------------------------------------------------ criterion model


class TestCriterionModel:
    def test_a_criterion_cannot_exist_without_a_reason(self):
        """Requirement: explicit reasons, never a bare status value."""
        for state in (CriterionState.PASS, CriterionState.WARN,
                      CriterionState.BLOCKED):
            with pytest.raises(ValueError, match="no reason"):
                Criterion("k", "label", state, "   ")

    def test_only_blocked_criteria_stop_a_mode(self, tmp_path):
        """A WARN is reported and does not stop the mode (research)."""
        inputs = _ready_inputs(
            tmp_path, fee_calc=_stale_calc(),
            matcher=MatcherProbe(False, "no LLM configured"))
        readiness = evaluate_mode(Mode.HISTORICAL, inputs)
        assert any(c.state is CriterionState.WARN for c in readiness.criteria)
        assert readiness.ready is True
        assert readiness.blockers == ()
        assert readiness.warnings

    def test_the_blocker_summary_quotes_every_blocker(self, tmp_path):
        report = evaluate_all(_mutate(tmp_path, "fees_stale"))
        summary = report.live.blocker_summary()
        assert "Fee schedules fresh" in summary and "45 days ago" in summary
        assert evaluate_all(_ready_inputs(tmp_path)).for_mode(
            Mode.COLLECT).blocker_summary() == "none"


# --------------------------------------------------------------- each gate


class TestVenueGates:
    def test_capabilities_are_read_from_the_adapters(self):
        caps = venue_capabilities(_adapters(
            ("kalshi", APIStatus.VERIFIED, True),
            (SECOND, APIStatus.API_DOCUMENTED_UNTESTED, False)))
        by_venue = {c.venue: c for c in caps}
        assert by_venue["kalshi"].data_capable and by_venue["kalshi"].live_capable
        assert not by_venue[SECOND].data_capable
        assert not by_venue[SECOND].live_capable
        assert "API_DOCUMENTED_UNTESTED" in by_venue[SECOND].note

    def test_a_verified_api_without_verified_orders_is_not_live_capable(self):
        caps = venue_capabilities(
            _adapters((SECOND, APIStatus.VERIFIED, False)))
        assert caps[0].data_capable and not caps[0].live_capable

    def test_observed_market_data_is_not_full_or_live_verification(self):
        caps = venue_capabilities(_adapters((
            "kalshi", APIStatus.MARKET_DATA_OBSERVED, True)))
        assert caps[0].data_capable
        assert not caps[0].live_capable
        assert "MARKET_DATA_OBSERVED" in caps[0].data_note
        assert criterion_live_capable_venues(caps, 1).state is CriterionState.BLOCKED

    def test_data_venues_pass_and_fail_on_adapter_status(self):
        caps = venue_capabilities(_adapters(
            ("kalshi", APIStatus.VERIFIED, True),
            (SECOND, APIStatus.VERIFIED, True)))
        ok = criterion_data_venues(caps, 2, "paper needs a pair")
        assert ok.state is CriterionState.PASS and ok.value == "2 of 2"
        one = criterion_data_venues(
            venue_capabilities(_adapters(
                ("kalshi", APIStatus.VERIFIED, True),
                (SECOND, APIStatus.API_DOCUMENTED_UNTESTED, False))),
            2, "paper needs a pair")
        assert one.state is CriterionState.BLOCKED
        assert "API_DOCUMENTED_UNTESTED" in one.reason
        assert "polymarket_us_smoke.py" in one.reason, "names the way to clear it"

    def test_live_capable_venues_pass_and_fail(self):
        caps = venue_capabilities(_adapters(
            ("kalshi", APIStatus.VERIFIED, True),
            (SECOND, APIStatus.VERIFIED, True)))
        assert criterion_live_capable_venues(caps, 2).state is CriterionState.PASS
        short = criterion_live_capable_venues(
            venue_capabilities(_adapters(
                ("kalshi", APIStatus.VERIFIED, True),
                (SECOND, APIStatus.VERIFIED, False))), 2)
        assert short.state is CriterionState.BLOCKED
        assert "order types NOT verified" in short.reason

    def test_independence_passes_for_a_proven_independent_pair(self):
        criterion = criterion_independent_pairs(PAIR, "live")
        assert criterion.state is CriterionState.PASS
        assert "INDEPENDENT" in criterion.reason

    def test_independence_fails_for_a_same_venue_route(self):
        criterion = criterion_independent_pairs(("kalshi", "webull"), "live")
        assert criterion.state is CriterionState.BLOCKED
        assert "SAME_VENUE" in criterion.reason

    def test_independence_fails_for_unknown_provenance(self):
        criterion = criterion_independent_pairs(
            ("kalshi", "mystery_venue"), "live")
        assert criterion.state is CriterionState.BLOCKED
        assert "UNKNOWN" in criterion.reason
        assert "never authorise" in criterion.reason

    def test_independence_fails_when_there_is_no_pair(self):
        criterion = criterion_independent_pairs(("kalshi",), "live")
        assert criterion.state is CriterionState.BLOCKED
        assert "fewer than two qualifying venues" in criterion.reason
        assert criterion.value == "1 qualifying venue(s)"


class TestFeeGates:
    def test_completeness_passes_for_complete_schedules(self):
        criterion = criterion_fee_completeness(
            _fee_calc(), PAIR, FeeFreshnessPolicy.MANDATORY)
        assert criterion.state is CriterionState.PASS
        assert criterion.value == "2 of 2 venues"

    def test_completeness_fails_and_names_the_component(self):
        calc = _fee_calc(overrides={
            "kalshi": {"settlement_calculation_status": "INCOMPLETE",
                       "blocking_reason": "customer settlement treatment "
                                           "not established"}})
        criterion = criterion_fee_completeness(
            calc, PAIR, FeeFreshnessPolicy.MANDATORY)
        assert criterion.state is CriterionState.BLOCKED
        assert "settlement fee component is INCOMPLETE" in criterion.reason
        assert "customer settlement treatment" in criterion.reason
        assert "docs/FEE_READINESS.md" in criterion.reason
        assert criterion.value == "1 of 2 venues"

    def test_completeness_fails_for_a_missing_schedule(self):
        criterion = criterion_fee_completeness(
            _fee_calc(venues=("kalshi",)), PAIR, FeeFreshnessPolicy.MANDATORY)
        assert criterion.state is CriterionState.BLOCKED
        assert "no fee schedule on file" in criterion.reason

    def test_freshness_passes_when_every_schedule_is_current(self):
        criterion = criterion_fee_freshness(_fee_calc(), PAIR, Mode.LIVE)
        assert criterion.state is CriterionState.PASS
        assert "mandatory fee freshness" in criterion.reason

    def test_freshness_blocks_live_and_paper_on_a_stale_schedule(self):
        calc = _stale_calc()
        for mode in (Mode.LIVE, Mode.PAPER):
            criterion = criterion_fee_freshness(calc, PAIR, mode)
            assert criterion.state is CriterionState.BLOCKED, mode
            assert "45 days ago" in criterion.reason
            assert "Re-verify before live execution" in criterion.reason

    def test_freshness_warns_instead_of_blocking_research(self):
        criterion = criterion_fee_freshness(_stale_calc(), PAIR, Mode.HISTORICAL)
        assert criterion.state is CriterionState.WARN
        assert "research" in criterion.reason
        assert "not live-executable prices" in criterion.reason

    def test_freshness_is_not_applicable_to_collect(self):
        criterion = criterion_fee_freshness(_stale_calc(), PAIR, Mode.COLLECT)
        assert criterion.state is CriterionState.PASS
        assert "never calculates a fee" in criterion.reason

    def test_freshness_blocks_when_a_schedule_is_missing(self):
        criterion = criterion_fee_freshness(
            _fee_calc(venues=("kalshi",)), PAIR, Mode.PAPER)
        assert criterion.state is CriterionState.BLOCKED
        assert "no fee schedule on file" in criterion.reason


class TestSettlementGate:
    def test_evidenced_timing_passes(self):
        criterion = criterion_settlement_timing(PAIR, Mode.LIVE,
                                               _documented_timing())
        assert criterion.state is CriterionState.PASS
        assert "kalshi=DOCUMENTED" in criterion.value

    def test_unknown_timing_blocks_live_and_paper(self):
        for mode in (Mode.LIVE, Mode.PAPER):
            criterion = criterion_settlement_timing(
                ("kalshi", "polymarket_us"), mode, PRODUCTION_SETTLEMENT_TIMING)
            assert criterion.state is CriterionState.BLOCKED, mode
            assert "polymarket_us" in criterion.reason
            assert "docs/TRADING_LOGIC.md" in criterion.reason

    def test_unknown_timing_only_warns_for_replay(self):
        criterion = criterion_settlement_timing(
            ("kalshi", "polymarket_us"), Mode.HISTORICAL,
            PRODUCTION_SETTLEMENT_TIMING)
        assert criterion.state is CriterionState.WARN
        assert "Replay still runs" in criterion.reason

    def test_a_venue_with_no_entry_is_not_evidenced(self):
        criterion = criterion_settlement_timing(("kalshi", "invented_venue"),
                                               Mode.LIVE, {})
        assert criterion.state is CriterionState.BLOCKED
        assert "invented_venue=NO ENTRY" in criterion.value


class TestDataAndConfigurationGates:
    def test_recorded_evidence_passes_above_the_thresholds(self):
        criterion = criterion_recorded_evidence(
            RecordedEvidence(Path("/tmp/x.sqlite3"), True, 500, 60, "store"),
            ReadinessThresholds.from_settings(SETTINGS), Mode.LIVE)
        assert criterion.state is CriterionState.PASS

    def test_recorded_evidence_fails_below_either_threshold(self):
        thresholds = ReadinessThresholds.from_settings(SETTINGS)
        few_snapshots = criterion_recorded_evidence(
            RecordedEvidence(Path("/tmp/x.sqlite3"), True, 5, 60, "store"),
            thresholds, Mode.LIVE)
        assert few_snapshots.state is CriterionState.BLOCKED
        assert "5 recorded order-book snapshots, needs 10" in few_snapshots.reason
        few_ops = criterion_recorded_evidence(
            RecordedEvidence(Path("/tmp/x.sqlite3"), True, 500, 1, "store"),
            thresholds, Mode.LIVE)
        assert few_ops.state is CriterionState.BLOCKED
        assert "1 opportunities analyzed, needs 2" in few_ops.reason

    def test_replay_needs_something_to_replay(self):
        thresholds = ReadinessThresholds.from_settings(SETTINGS)
        empty = criterion_recorded_evidence(
            RecordedEvidence(Path("/tmp/x.sqlite3"), True, 0, 0, "store"),
            thresholds, Mode.HISTORICAL)
        assert empty.state is CriterionState.BLOCKED
        assert "never fabricates data" in empty.reason
        one = criterion_recorded_evidence(
            RecordedEvidence(Path("/tmp/x.sqlite3"), True, 1, 0, "store"),
            thresholds, Mode.HISTORICAL)
        assert one.state is CriterionState.PASS, "replay has no volume threshold"

    def test_thresholds_come_from_settings_not_from_the_report(self):
        thresholds = ReadinessThresholds.from_settings(
            {"readiness": {"minimum_recorded_snapshots": 7}})
        assert thresholds.minimum_recorded_snapshots == 7
        assert thresholds.minimum_live_capable_venues == 2, "documented default"
        broken = ReadinessThresholds.from_settings(
            {"readiness": {"minimum_recorded_snapshots": "many"}})
        assert broken.minimum_recorded_snapshots == 1000

    def test_the_matcher_gate_blocks_execution_facing_modes_only(self):
        probe = MatcherProbe(False, "no LLM configured in .env.paper")
        assert criterion_matcher(probe, Mode.PAPER).state is CriterionState.BLOCKED
        assert criterion_matcher(probe, Mode.LIVE).state is CriterionState.BLOCKED
        assert criterion_matcher(probe, Mode.HISTORICAL).state is CriterionState.WARN
        assert criterion_matcher(
            MatcherProbe(True, "configured"), Mode.LIVE
        ).state is CriterionState.PASS

    def test_the_kill_switch_blocks_trading_modes_and_warns_others(self):
        assert criterion_kill_switch(True, Mode.LIVE).state is CriterionState.BLOCKED
        assert criterion_kill_switch(True, Mode.PAPER).state is CriterionState.BLOCKED
        assert criterion_kill_switch(True, Mode.COLLECT).state is CriterionState.WARN
        assert "--unkill" in criterion_kill_switch(True, Mode.LIVE).reason
        assert criterion_kill_switch(False, Mode.LIVE).state is CriterionState.PASS

    def test_the_book_staleness_gate_needs_a_positive_limit(self):
        assert criterion_book_staleness(SETTINGS).state is CriterionState.PASS
        missing = criterion_book_staleness({"arbitrage": {}})
        assert missing.state is CriterionState.BLOCKED
        assert "execution.max_book_age_ms is not configured" in missing.reason
        zero = criterion_book_staleness(
            {"execution": {"max_book_age_ms": 0},
             "arbitrage": {"maximum_book_age_ms": 1000}})
        assert zero.state is CriterionState.BLOCKED
        assert "not a positive limit" in zero.reason

    def test_risk_limits_pass_with_the_checked_in_configuration(self):
        real = yaml.safe_load(
            (core_config.CONFIG_DIR / "risk_limits.yaml").read_text())
        assert criterion_risk_limits(real).state is CriterionState.PASS

    def test_an_unstated_limit_is_a_blocker_not_a_default(self):
        cfg = {"limits": dict(RISK_LIMITS["limits"]),
               "partial_fill": dict(RISK_LIMITS["partial_fill"])}
        del cfg["limits"]["max_daily_loss"]
        criterion = criterion_risk_limits(cfg)
        assert criterion.state is CriterionState.BLOCKED
        assert "max_daily_loss is not configured" in criterion.reason
        assert "silently substitute a default" in criterion.reason

    @pytest.mark.parametrize("value", [0, -5, "not-a-number"])
    def test_a_non_positive_or_unparsable_limit_is_a_blocker(self, value):
        cfg = {"limits": {**RISK_LIMITS["limits"], "max_total_capital": value},
               "partial_fill": dict(RISK_LIMITS["partial_fill"])}
        assert criterion_risk_limits(cfg).state is CriterionState.BLOCKED

    def test_eligibility_blocks_live_and_warns_in_research(self):
        criterion = criterion_eligibility("US-UT", PAIR, Mode.LIVE)
        assert criterion.state is CriterionState.BLOCKED
        assert "UNKNOWN and RESTRICTED both block live trading" in criterion.reason
        warn = criterion_eligibility("US-UT", PAIR, Mode.PAPER)
        assert warn.state is CriterionState.WARN
        assert "may continue as research" in warn.reason

    def test_eligibility_passes_on_an_eligible_ledger_entry(
            self, eligible_jurisdiction):
        criterion = criterion_eligibility(eligible_jurisdiction, PAIR, Mode.LIVE)
        assert criterion.state is CriterionState.PASS

    def test_restricted_sports_eligibility_blocks_live(self):
        criterion = criterion_eligibility("US-UT", ("kalshi",), Mode.LIVE)
        assert criterion.state is CriterionState.BLOCKED


class TestLiveConfigurationGate:
    def test_a_complete_live_configuration_passes(self, tmp_path):
        config_dir = _live_config_dir(tmp_path)
        criterion = criterion_live_configuration(
            probe_live_configuration(config_dir, tmp_path))
        assert criterion.state is CriterionState.PASS
        assert "contents never read outside live mode" in criterion.reason

    def test_a_missing_credential_file_blocks(self, tmp_path):
        """§48: the report checks existence and never reads live secrets."""
        config_dir = _live_config_dir(tmp_path, create_env=False)
        probe = probe_live_configuration(config_dir, tmp_path)
        assert probe.env_present is False
        criterion = criterion_live_configuration(probe)
        assert criterion.state is CriterionState.BLOCKED
        assert "no credential file" in criterion.reason
        assert "§48" in criterion.reason

    def test_a_missing_live_yaml_blocks(self, tmp_path):
        (tmp_path / "config").mkdir()
        criterion = criterion_live_configuration(
            probe_live_configuration(tmp_path / "config", tmp_path))
        assert criterion.state is CriterionState.BLOCKED
        assert "refuses to start live mode without it" in criterion.reason

    @pytest.mark.parametrize("override,fragment", [
        ({"allow_live_orders": False}, "allow_live_orders: true"),
        ({"execution_engine": "PaperExecutionEngine"}, "LiveExecutionEngine"),
        ({"mode": "paper"}, "not 'live'"),
        ({"requires_trading_credentials": False},
         "requires_trading_credentials"),
    ])
    def test_each_mode_isolation_invariant_is_checked(self, tmp_path,
                                                      override, fragment):
        config_dir = _live_config_dir(tmp_path, **override)
        criterion = criterion_live_configuration(
            probe_live_configuration(config_dir, tmp_path))
        assert criterion.state is CriterionState.BLOCKED
        assert fragment in criterion.reason


# --------------------------------------------------------- whole-mode verdicts


class TestModeVerdictsAreComputed:
    def test_the_report_covers_the_mode_progression(self, tmp_path,
                                                    eligible_jurisdiction):
        report = evaluate_all(_ready_inputs(tmp_path))
        assert [m.mode for m in report.modes] == [
            Mode.COLLECT, Mode.HISTORICAL, Mode.PAPER, Mode.LIVE]

    def test_live_is_ready_when_every_gate_genuinely_passes(
            self, tmp_path, eligible_jurisdiction):
        """The D10 proof: the verdict is not a constant ``False``."""
        report = evaluate_all(_ready_inputs(tmp_path))
        assert report.live.ready is True
        assert report.live.blockers == ()
        assert len(report.live.criteria) >= 10, "every gate was evaluated"

    @pytest.mark.parametrize("mutate,expected", [
        ("second_adapter_untested",
         ("live_capable_venues", "independent_pair")),
        ("order_types_unverified",
         ("live_capable_venues", "independent_pair")),
        ("same_venue_pair", ("independent_pair",)),
        ("unknown_provenance", ("independent_pair",)),
        ("matcher_unconfigured", ("matcher",)),
        ("fees_incomplete", ("fee_completeness",)),
        ("fees_missing", ("fee_completeness", "fee_freshness")),
        ("fees_stale", ("fee_freshness",)),
        ("settlement_unknown", ("settlement_timing",)),
        ("no_recorded_store", ("recorded_evidence",)),
        ("too_few_opportunities", ("recorded_evidence",)),
        ("kill_switch_engaged", ("kill_switch",)),
        ("eligibility_unknown", ("eligibility",)),
        ("risk_limit_unstated", ("risk_limits",)),
        ("book_gate_unset", ("book_staleness",)),
        ("live_env_missing", ("live_configuration",)),
    ])
    def test_each_live_gate_can_fail_on_its_own(self, tmp_path,
                                                eligible_jurisdiction,
                                                mutate, expected):
        inputs = _mutate(tmp_path, mutate)
        report = evaluate_all(inputs)
        assert report.live.ready is False
        assert tuple(c.key for c in report.live.blockers) == expected
        for criterion in report.live.blockers:
            assert criterion.reason.strip()

    def test_paper_is_ready_when_its_gates_pass(self, tmp_path,
                                                eligible_jurisdiction):
        report = evaluate_all(_ready_inputs(tmp_path))
        assert report.for_mode(Mode.PAPER).ready is True

    def test_paper_is_not_ready_with_one_verified_venue(self, tmp_path,
                                                        eligible_jurisdiction):
        report = evaluate_all(_mutate(tmp_path, "second_adapter_untested"))
        paper = report.for_mode(Mode.PAPER)
        assert paper.ready is False
        assert "data_venues" in [c.key for c in paper.blockers]

    def test_collect_needs_one_verified_adapter(self, tmp_path):
        ready = evaluate_mode(Mode.COLLECT, _ready_inputs(tmp_path))
        assert ready.ready is True
        empty = evaluate_mode(Mode.COLLECT, _ready_inputs(tmp_path, adapters={
            "kalshi": StubAdapter("kalshi", APIStatus.API_DOCUMENTED_UNTESTED)}))
        assert empty.ready is False
        assert empty.blockers[0].key == "data_venues"

    def test_replay_is_ready_with_data_even_when_fees_are_stale(
            self, tmp_path):
        """Requirement 4: research replay is not blocked by an old schedule."""
        inputs = _ready_inputs(
            tmp_path, fee_calc=_stale_calc(),
            settlement_sources=PRODUCTION_SETTLEMENT_TIMING,
            matcher=MatcherProbe(False, "no LLM configured"))
        historical = evaluate_mode(Mode.HISTORICAL, inputs)
        assert historical.ready is True
        assert {c.key for c in historical.warnings} >= {
            "fee_freshness", "settlement_timing", "matcher"}
        # the same state is NOT ready for live
        assert evaluate_mode(Mode.LIVE, inputs).ready is False

    def test_replay_is_not_ready_without_anything_to_replay(self, tmp_path):
        inputs = _ready_inputs(
            tmp_path,
            recorded=RecordedEvidence(tmp_path / "missing.sqlite3", False, 0, 0,
                                      "no recorded store"))
        assert evaluate_mode(Mode.HISTORICAL, inputs).ready is False


def _mutate(tmp_path: Path, name: str) -> ReadinessInputs:
    """One broken gate at a time, from an otherwise fully-ready state."""
    if name == "second_adapter_untested":
        return _ready_inputs(tmp_path, adapters=_adapters(
            ("kalshi", APIStatus.VERIFIED, True),
            (SECOND, APIStatus.API_DOCUMENTED_UNTESTED, True)))
    if name == "order_types_unverified":
        return _ready_inputs(tmp_path, adapters=_adapters(
            ("kalshi", APIStatus.VERIFIED, True),
            (SECOND, APIStatus.VERIFIED, False)))
    if name == "same_venue_pair":
        return _ready_inputs(
            tmp_path,
            adapters=_adapters(("kalshi", APIStatus.VERIFIED, True),
                               ("webull", APIStatus.VERIFIED, True)),
            fee_calc=_fee_calc(venues=("kalshi", "webull")),
            settlement_sources=_documented_timing(("kalshi", "webull")))
    if name == "unknown_provenance":
        return _ready_inputs(
            tmp_path,
            adapters=_adapters(("kalshi", APIStatus.VERIFIED, True),
                               ("mystery_venue", APIStatus.VERIFIED, True)),
            fee_calc=_fee_calc(venues=("kalshi", "mystery_venue")),
            settlement_sources=_documented_timing(("kalshi", "mystery_venue")))
    if name == "matcher_unconfigured":
        return _ready_inputs(
            tmp_path,
            matcher=MatcherProbe(False, "no LLM configured in .env.paper"))
    if name == "fees_incomplete":
        return _ready_inputs(tmp_path, fee_calc=_fee_calc(overrides={
            "kalshi": {"trade_calculation_status": "INCOMPLETE",
                       "blocking_reason": "series fee state not supplied"}}))
    if name == "fees_missing":
        return _ready_inputs(tmp_path, fee_calc=_fee_calc(venues=("kalshi",)))
    if name == "fees_stale":
        return _ready_inputs(tmp_path, fee_calc=_stale_calc())
    if name == "settlement_unknown":
        return _ready_inputs(
            tmp_path, settlement_sources=PRODUCTION_SETTLEMENT_TIMING)
    if name == "no_recorded_store":
        return _ready_inputs(
            tmp_path,
            recorded=RecordedEvidence(tmp_path / "missing.sqlite3", False, 0, 0,
                                      "no recorded store at "
                                      f"{tmp_path / 'missing.sqlite3'}"))
    if name == "too_few_opportunities":
        return _ready_inputs(
            tmp_path,
            recorded=RecordedEvidence(tmp_path / "recorded.sqlite3", True, 500,
                                      1, "synthetic recorded store"))
    if name == "kill_switch_engaged":
        return _ready_inputs(tmp_path, kill_switch_engaged=True)
    if name == "eligibility_unknown":
        return _ready_inputs(tmp_path, jurisdiction="US-UT")
    if name == "risk_limit_unstated":
        limits = {"limits": dict(RISK_LIMITS["limits"]),
                  "partial_fill": dict(RISK_LIMITS["partial_fill"])}
        del limits["limits"]["max_daily_loss"]
        return _ready_inputs(tmp_path, risk_limits=limits)
    if name == "book_gate_unset":
        return _ready_inputs(
            tmp_path,
            settings={**SETTINGS, "execution": {}, "arbitrage": {}})
    if name == "live_env_missing":
        config_dir = _live_config_dir(tmp_path / "no-env", create_env=False)
        return _ready_inputs(tmp_path, live_configuration=(
            probe_live_configuration(config_dir, tmp_path / "no-env")))
    raise AssertionError(f"unknown mutation {name!r}")


# ------------------------------------------------- the current repository state


class TestCurrentStateIsReportedHonestly:
    def _inputs(self, isolated_root) -> ReadinessInputs:
        config = core_config.load_config("paper")
        from prediction_arb.fees.venue_fees import load_fee_calculator
        return ReadinessInputs(
            adapters=main_module.build_adapters(config),
            fee_calc=load_fee_calculator(
                config.fees,
                int(config.settings["fees"]["max_schedule_age_days"])),
            settings=config.settings,
            risk_limits=config.risk_limits,
            recorded=load_recorded_evidence(config.recorded_store_path),
            matcher=probe_matcher(config.env, env_label=".env.paper"),
            kill_switch_engaged=False,
            jurisdiction=str(config.settings["compliance"]["jurisdiction"]),
            live_configuration=probe_live_configuration(core_config.CONFIG_DIR,
                                                        core_config.ROOT),
            settlement_sources=PRODUCTION_SETTLEMENT_TIMING,
        )

    def test_live_is_not_ready_and_names_the_real_blockers(self, isolated_root):
        report = evaluate_all(self._inputs(isolated_root))
        live = report.live
        assert live.ready is False
        keys = {c.key for c in live.blockers}
        # exactly the gates the current evidence leaves open
        assert {"live_capable_venues", "independent_pair", "matcher",
                "fee_completeness", "settlement_timing", "recorded_evidence",
                "eligibility", "live_configuration"} <= keys
        by_key = {c.key: c for c in live.blockers}
        assert "0 of 3" in by_key["live_capable_venues"].value
        assert "API_DOCUMENTED_UNTESTED" in by_key["live_capable_venues"].reason
        assert "INCOMPLETE" in by_key["fee_completeness"].reason
        assert "polymarket_us" in by_key["settlement_timing"].reason
        assert "no credential file" in by_key["live_configuration"].reason
        # ... and the gates that genuinely do pass today are reported as such
        passing = {c.key for c in live.criteria
                   if c.state is CriterionState.PASS}
        assert {"kill_switch", "risk_limits", "book_staleness"} <= passing

    def test_the_freshness_verdict_follows_the_ledger_dates(self,
                                                            isolated_root):
        """The one date-dependent gate: assert the observation, not a verdict.

        The production ledger's ``verified_at`` dates are fixed, so whether
        they are inside ``fees.max_schedule_age_days`` depends on when the
        report runs. The criterion must agree with the ledger either way —
        which is what makes it derived rather than asserted.
        """
        from prediction_arb.fees import calculator as fee_clock
        inputs = self._inputs(isolated_root)
        live = evaluate_all(inputs).live
        criterion = live.get("fee_freshness")
        today = fee_clock.utcnow().date()
        limit = inputs.fee_calc.max_schedule_age_days
        ages = {
            venue: (today - inputs.fee_calc.schedules[venue].verified_at).days
            for venue in inputs.adapters
            if venue in inputs.fee_calc.schedules
        }
        stale = [v for v, age in ages.items() if age > limit]
        expected = CriterionState.BLOCKED if stale else CriterionState.PASS
        assert criterion.state is expected
        assert f"limit {limit} days" in criterion.label
        # a PASS lists every venue's age; a BLOCK quotes the stale ones
        for venue, age in ages.items():
            if stale and venue not in stale:
                continue
            assert venue in criterion.reason
            assert f"{age} days ago" in criterion.reason, venue
        if stale:
            assert "Re-verify before live execution" in criterion.reason
        else:
            assert criterion.state is CriterionState.PASS

    def test_paper_is_not_ready_for_the_same_underlying_reasons(
            self, isolated_root):
        paper = evaluate_all(self._inputs(isolated_root)).for_mode(Mode.PAPER)
        assert paper.ready is False
        assert {c.key for c in paper.blockers} >= {
            "data_venues", "independent_pair", "matcher", "fee_completeness",
            "settlement_timing"}

    def test_collect_is_ready_with_one_verified_adapter(self, isolated_root):
        collect = evaluate_all(self._inputs(isolated_root)).for_mode(Mode.COLLECT)
        assert collect.ready is True

    def test_replay_reports_the_missing_store_without_creating_it(
            self, isolated_root):
        inputs = self._inputs(isolated_root)
        historical = evaluate_all(inputs).for_mode(Mode.HISTORICAL)
        assert historical.ready is False
        assert historical.blockers[0].key == "recorded_evidence"
        assert "--mode collect" in historical.blockers[0].reason
        # D10's side effect: the old report opened the mode database and so
        # created data/paper/prediction_arb_paper.sqlite3, after which a
        # later historical run reported a "recorded store" holding zero rows.
        assert not Path(inputs.recorded.store_path).exists()
        assert not (isolated_root / "data").exists()


class TestRecordedEvidenceProbe:
    def test_a_missing_store_is_reported_not_created(self, tmp_path):
        path = tmp_path / "data" / "paper" / "prediction_arb_paper.sqlite3"
        evidence = load_recorded_evidence(path)
        assert evidence.present is False
        assert evidence.snapshots == 0 and evidence.opportunities == 0
        assert "--mode collect" in evidence.note
        assert not path.exists() and not (tmp_path / "data").exists()

    def test_rows_are_counted_from_an_existing_store(self, tmp_path):
        path = tmp_path / "recorded.sqlite3"
        repo = Repository(path, Mode.PAPER)
        repo.save_orderbook_snapshot(make_book("kalshi", "K1",
                                               asks=[("0.50", "10")]))
        repo.save_orderbook_snapshot(make_book("kalshi", "K2",
                                               asks=[("0.60", "10")]))
        evidence = load_recorded_evidence(path)
        assert evidence.present is True
        assert evidence.snapshots == 2 and evidence.opportunities == 0

    def test_an_unreadable_store_says_so(self, tmp_path):
        path = tmp_path / "garbage.sqlite3"
        path.write_bytes(b"this is not a sqlite database")
        evidence = load_recorded_evidence(path)
        assert evidence.present is True and evidence.snapshots == 0
        assert "could not be read" in evidence.note


class TestMatcherProbe:
    def test_no_configuration_is_reported_as_the_blocker_it_is(self):
        probe = probe_matcher({}, env_label=".env.paper")
        assert probe.configured is False
        assert "no LLM configured in .env.paper" in probe.reason
        assert "ever APPROVED" in probe.reason

    def test_a_configured_matcher_is_reported_without_its_key(self):
        probe = probe_matcher(
            {"LLM_API_BASE": "https://llm.example/v1",
             "LLM_API_KEY": "super-secret-value",
             "LLM_MODEL": "test-model"},
            env_label=".env.paper", max_comparisons_per_cycle=42)
        assert probe.configured is True
        assert "test-model" in probe.reason
        assert "budget=42" in probe.reason
        assert "super-secret-value" not in probe.reason

    def test_a_partial_configuration_is_refused_not_assumed(self):
        probe = probe_matcher({"LLM_API_KEY": "k"}, env_label=".env.paper")
        assert probe.configured is False
        assert "partial" in probe.reason


class TestBlockReasonsAreVisible:
    """The verdict table shows *that* a row blocked; this shows *why*."""

    def _render(self, opportunities, **kwargs) -> str:
        from prediction_arb.monitoring.dashboard import print_block_reasons
        buffer = io.StringIO()
        console = Console(file=buffer, force_terminal=False, width=220)
        self.returned = print_block_reasons(console, opportunities, **kwargs)
        return buffer.getvalue()

    def test_a_stale_schedule_blocker_is_printed_with_its_reason(self):
        ops = [_evaluate(_stale_calc(), Mode.PAPER)]
        out = self._render(ops, label="cycle 1:")
        assert self.returned == 1
        assert "1 opportunity(ies) BLOCKed" in out
        assert "cycle 1:" in out
        assert "Re-verify before live execution" in out

    def test_identical_reasons_are_counted_not_repeated(self):
        calc = _stale_calc()
        ops = [_evaluate(calc, Mode.PAPER), _evaluate(calc, Mode.PAPER)]
        out = self._render(ops)
        assert "2 opportunity(ies) BLOCKed" in out
        assert "(2x) FEE_DATA_INCOMPLETE" in out
        assert out.count("Re-verify before live execution") == 1

    def test_the_reason_list_is_capped_and_says_so(self):
        calc = _stale_calc()
        ops = [_evaluate(calc, Mode.PAPER)] * 12
        out = self._render(ops, limit=3)
        assert "further distinct reason(s)" not in out, "one distinct reason"
        capped = self._render(ops, limit=1)
        # a stale schedule blocks both of its components and the delay cost
        # that needs them, so more than one distinct reason exists
        assert "further distinct reason(s)" in capped
        assert capped.count("Re-verify before live execution") == 1

    def test_nothing_is_printed_when_nothing_blocked(self):
        out = self._render([_evaluate(_fee_calc(), Mode.PAPER)])
        assert out == "" and self.returned == 0

    def test_reasons_are_escaped_not_interpreted_as_markup(self):
        from prediction_arb.core.types import Opportunity
        op = _evaluate(_stale_calc(), Mode.PAPER)
        op.block_reasons.append("[bold]not markup[/bold]")
        out = self._render([op])
        assert "[bold]not markup[/bold]" in out


# ------------------------------------------------------------------- the CLI


def _capture(fn, *args, **kwargs) -> str:
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=220)
    original = main_module.console
    main_module.console = console
    try:
        exit_code = fn(*args, **kwargs)
    finally:
        main_module.console = original
    return exit_code, buffer.getvalue()


class TestAdapterStatusCLI:
    def test_status_keeps_evidence_layers_and_unavailability_separate(
            self, isolated_root):
        config = core_config.load_config("paper")
        exit_code, out = _capture(main_module.cmd_status, config)
        assert exit_code == 0
        assert "ADAPTER EVIDENCE" in out
        assert "MARKET_DATA_OBSERVED" in out
        assert "Documentation" in out and "Connectivity" in out
        assert "Usable market data" in out and "Full adapter" in out
        assert "UNAVAILABLE (2026-10-10T06:13:37.919175+00:00)" in out
        assert "UNAVAILABLE describes an attempt" in out


class TestReadinessCLI:
    def test_the_report_explains_the_actual_blocker(self, isolated_root):
        config = core_config.load_config("paper")
        exit_code, out = _capture(main_module.cmd_readiness, config)
        assert exit_code == 0
        assert "LIVE MODE: NOT RECOMMENDED YET" in out
        # the policy is stated, so the operator can see which rule applied
        assert ("live=MANDATORY, paper=MANDATORY, historical=RESEARCH, "
                "collect=NOT_APPLICABLE") in out
        # real blockers, in the operator's own terms
        assert "API_DOCUMENTED_UNTESTED" in out
        assert "INCOMPLETE" in out
        assert "polymarket_us=UNKNOWN" in out
        assert "no credential file" in out
        assert "blockers (detail above)" in out
        assert "Advisory only" in out and "enables nothing" in out

    def test_the_cli_delegates_to_the_derived_evaluator(self, isolated_root,
                                                        monkeypatch):
        """D10: the verdict comes from the evaluator, not from a literal."""
        calls: list = []

        def spy(inputs):
            calls.append(inputs)
            return evaluate_all(inputs)

        monkeypatch.setattr(main_module, "evaluate_all", spy)
        config = core_config.load_config("paper")
        _capture(main_module.cmd_readiness, config)
        assert len(calls) == 1
        inputs = calls[0]
        assert set(inputs.adapters) == {"kalshi", "polymarket_us", "forecast_ex"}
        assert inputs.fee_calc.max_schedule_age_days == 30
        assert inputs.jurisdiction == "US-UT"
        assert inputs.recorded.present is False
        assert inputs.kill_switch_engaged is False
        assert inputs.matcher.configured is False

    def test_the_hardcoded_criterion_is_gone(self):
        source = Path(main_module.__file__).read_text(encoding="utf-8")
        # the old report's literal label and its hardcoded comparisons
        assert "Two live-capable independent venues" not in source
        assert "snaps > 1000" not in source
        assert "ops > 50" not in source

    def test_the_report_creates_no_database_and_places_no_order(
            self, isolated_root):
        config = core_config.load_config("paper")
        _capture(main_module.cmd_readiness, config)
        assert not (isolated_root / "data").exists()
        # readiness is advisory: building adapters for a non-live mode still
        # leaves order placement disabled, and the config is untouched.
        adapters = main_module.build_adapters(config)
        assert adapters["kalshi"].allow_order_placement is False
        assert config.allow_live_orders is False

    def test_a_synthetic_ready_state_prints_ready(self, tmp_path,
                                                  eligible_jurisdiction):
        console = Console(file=io.StringIO(), force_terminal=False, width=220)
        print_readiness(evaluate_all(_ready_inputs(tmp_path)), console,
                        current_mode=Mode.LIVE)
        out = console.file.getvalue()
        assert "LIVE MODE: READY (final decision is yours)" in out
        assert "LIVE (this run's mode): READY" in out
        assert "Advisory only" in out

    def test_the_kill_switch_state_is_read_not_assumed(self, isolated_root,
                                                       monkeypatch):
        from prediction_arb.risk import kill_switch
        config = core_config.load_config("paper")
        kill_switch.engage("test")
        try:
            _exit_code, out = _capture(main_module.cmd_readiness, config)
        finally:
            kill_switch.disengage()
        assert "kill switch is ENGAGED" in out
        assert "--unkill" in out
