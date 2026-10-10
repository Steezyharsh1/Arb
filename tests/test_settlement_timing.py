"""D18 regression tests: settlement timing and capital-lockup cost.

The inherited review reported that Polymarket US had no entry in the
settlement-delay table, so its opportunities blocked on unknown lockup.
Re-verification found the table itself was the problem: it held bare
0-day constants for Kalshi and ForecastEx with no stated meaning and no
source, added them to a matching-oriented ``settlement_time`` (Kalshi's
*expected* expiration), clamped any passed time to an immediate (zero)
lockup, and measured replayed frames against the wall clock.

These tests pin the repaired contract:

* every production registry entry states its meaning, evidence status,
  sources and review date; UNKNOWN entries cannot produce a number;
* Polymarket US is present and explicitly UNKNOWN (no published timing),
  so it still blocks — it is not turned into zero to pass the pipeline;
* Kalshi uses the documented per-market bound
  ``latest_expiration_time + settlement_timer_seconds``;
* missing timing, passed release times and inconsistent payloads block;
* long-dated contracts are costed for their full lockup;
* lockup follows the last leg, the spread compares release times;
* the simple annual capital cost is exact, linear and rounded up.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from prediction_arb.arbitrage.two_leg import evaluate_two_leg
from prediction_arb.core.types import Independence, Mode, OpportunityStatus
from prediction_arb.normalization.settlement import (
    PRODUCTION_SETTLEMENT_TIMING,
    SettlementTimingSource,
    TimingEvidenceStatus,
    assess_lockup,
    capital_lockup_days,
    capital_release,
    settlement_delay_cost,
    settlement_delay_spread_days,
    timedelta_days,
)

from .conftest import SETTLE, SETTLEMENT_TEST_NOW, make_book, make_contract

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROD = PRODUCTION_SETTLEMENT_TIMING


def _kalshi(latest: datetime, timer: int = 0,
            expected: datetime | None = None, contract_id: str = "K"):
    return make_contract(venue="kalshi", contract_id=contract_id,
                         settlement=expected or latest,
                         kalshi_latest_expiration=latest,
                         kalshi_settlement_timer_seconds=timer)


def _evaluate(yes_c, no_c, fee_calc, **kw):
    return evaluate_two_leg(
        yes_c, no_c,
        make_book(yes_c.venue, yes_c.contract_id, asks=[("0.40", "100")]),
        make_book(no_c.venue, no_c.contract_id, asks=[("0.50", "100")]),
        fee_calc, Mode.PAPER, independence=Independence.INDEPENDENT, **kw)


# ------------------------------------------------------------- the registry

class TestProductionRegistryEvidence:
    def test_every_entry_states_meaning_status_and_sources(self):
        assert set(PROD) >= {"kalshi", "polymarket_us", "forecast_ex"}
        for venue, src in PROD.items():
            assert src.venue == venue
            assert isinstance(src.status, TimingEvidenceStatus)
            for text in (src.release_meaning, src.resolution_basis,
                         src.post_resolution_delay, src.evidence, src.gaps):
                assert text.strip(), f"{venue}: empty evidence field"
            assert src.sources and all(s.startswith("https://")
                                       for s in src.sources)
            assert src.reviewed_on == "2026-10-10"

    def test_no_production_entry_is_observed_yet(self):
        """No settlement statement or live payload has been reviewed."""
        assert all(s.status is not TimingEvidenceStatus.OBSERVED
                   for s in PROD.values())

    def test_unknown_entries_cannot_carry_a_number(self):
        for src in PROD.values():
            assert (src.estimator is None) == (
                src.status is TimingEvidenceStatus.UNKNOWN)
        with pytest.raises(ValueError):
            SettlementTimingSource(
                venue="x", status=TimingEvidenceStatus.UNKNOWN,
                release_meaning="m", resolution_basis="b",
                post_resolution_delay="d", sources=("https://x",),
                reviewed_on="2026-10-10", evidence="e", gaps="g",
                estimator=lambda c: None)
        with pytest.raises(ValueError):
            SettlementTimingSource(
                venue="x", status=TimingEvidenceStatus.DOCUMENTED,
                release_meaning="m", resolution_basis="b",
                post_resolution_delay="d", sources=("https://x",),
                reviewed_on="2026-10-10", evidence="e", gaps="g")

    def test_polymarket_us_is_present_and_explicitly_unknown(self):
        """The reported missing entry now exists — as UNKNOWN, because no
        official source publishes a resolution bound or settlement delay."""
        src = PROD["polymarket_us"]
        assert src.status is TimingEvidenceStatus.UNKNOWN
        assert "endDate" in src.resolution_basis
        c = make_contract(venue="polymarket_us", settlement=NOW + timedelta(1))
        release = capital_release(c, PROD)
        assert release.release_time is None
        assert "UNKNOWN" in release.unknown_reason
        assert capital_lockup_days([c], now=NOW) is None

    def test_forecast_ex_zero_day_constant_was_withdrawn(self):
        src = PROD["forecast_ex"]
        assert src.status is TimingEvidenceStatus.UNKNOWN
        c = make_contract(venue="forecast_ex", settlement=NOW + timedelta(1))
        assert capital_release(c, PROD).release_time is None

    def test_kalshi_is_documented_per_market_not_a_venue_constant(self):
        src = PROD["kalshi"]
        assert src.status is TimingEvidenceStatus.DOCUMENTED
        assert "latest_expiration_time" in src.resolution_basis
        assert "settlement_timer_seconds" in src.post_resolution_delay

    def test_engine_with_production_registry_blocks_unknown_venues(
            self, production_settlement_timing, complete_fee_calc):
        """Under the production registry a Kalshi↔ForecastEx pair blocks on
        lockup even with complete (synthetic) fees."""
        op = _evaluate(make_contract(venue="kalshi", contract_id="Y"),
                       make_contract(venue="forecast_ex", contract_id="N"),
                       complete_fee_calc)
        assert op.status == OpportunityStatus.BLOCK
        assert op.net_profit is None and op.settlement_delay_cost is None
        assert op.costs.missing_names() == ("settlement_delay",)
        assert any("settlement lockup UNKNOWN" in r and "forecast_ex" in r
                   for r in op.block_reasons)


# ------------------------------------------------------------ missing timing

class TestMissingTimingBlocks:
    def test_venue_without_an_entry(self):
        c = make_contract(venue="mystery", settlement=NOW + timedelta(1))
        a = assess_lockup([c], now=NOW, sources=PROD)
        assert a.lockup_days is None and a.spread_days is None
        assert any("no settlement-timing evidence entry" in r
                   for r in a.reasons)

    def test_no_legs(self):
        a = assess_lockup([], now=NOW)
        assert a.lockup_days is None and a.reasons

    @pytest.mark.parametrize("raw, fragment", [
        ({}, "latest_expiration_time"),
        ({"latest_expiration_time": "not a date",
          "settlement_timer_seconds": 0}, "latest_expiration_time"),
        ({"latest_expiration_time": "2026-01-05T00:00:00Z"},
         "settlement_timer_seconds"),
        ({"latest_expiration_time": "2026-01-05T00:00:00Z",
          "settlement_timer_seconds": -1}, "settlement_timer_seconds"),
        ({"latest_expiration_time": "2026-01-05T00:00:00Z",
          "settlement_timer_seconds": True}, "settlement_timer_seconds"),
        ({"latest_expiration_time": "2026-01-05T00:00:00Z",
          "settlement_timer_seconds": "3600"}, "settlement_timer_seconds"),
    ])
    def test_kalshi_payload_without_documented_fields(self, raw, fragment):
        c = make_contract(venue="kalshi", settlement=NOW + timedelta(2),
                          raw=raw)
        a = assess_lockup([c], now=NOW, sources=PROD)
        assert a.lockup_days is None
        assert any(fragment in r for r in a.reasons)

    def test_kalshi_expected_after_latest_is_inconsistent(self):
        c = _kalshi(latest=NOW + timedelta(days=2),
                    expected=NOW + timedelta(days=3))
        a = assess_lockup([c], now=NOW, sources=PROD)
        assert a.lockup_days is None
        assert any("inconsistent" in r for r in a.reasons)

    def test_one_unknown_leg_makes_the_whole_lockup_unknown(self):
        known = _kalshi(latest=NOW + timedelta(days=2))
        unknown = make_contract(venue="polymarket_us",
                                settlement=NOW + timedelta(days=2))
        a = assess_lockup([known, unknown], now=NOW, sources=PROD)
        assert a.lockup_days is None and a.spread_days is None
        assert a.releases[0].known and not a.releases[1].known

    def test_unknown_lockup_is_never_costed(self):
        assert settlement_delay_cost(Decimal("100"), None,
                                     Decimal("0.05")) is None


# ------------------------------------------------- past settlement timestamps

class TestPastTimestamps:
    def test_passed_release_is_unknown_not_immediate(self):
        c = _kalshi(latest=NOW - timedelta(hours=1))
        a = assess_lockup([c], now=NOW, sources=PROD)
        assert a.lockup_days is None
        assert any("not after evaluation time" in r for r in a.reasons)

    def test_release_exactly_at_now_is_unknown(self):
        c = _kalshi(latest=NOW)
        assert capital_lockup_days([c], now=NOW) is None

    def test_passed_expected_time_but_future_bound_is_costed_to_the_bound(
            self):
        """Kalshi's *expected* expiration passing does not end the lockup;
        the documented latest expiration + timer still governs."""
        c = _kalshi(latest=NOW + timedelta(days=2), timer=3600,
                    expected=NOW - timedelta(hours=6))
        lockup = capital_lockup_days([c], now=NOW)
        assert lockup == Decimal(2) + Decimal(3600) / Decimal(86400)

    def test_settlement_timer_can_keep_a_passed_expiration_locked(self):
        c = _kalshi(latest=NOW - timedelta(hours=1), timer=3 * 3600)
        assert capital_lockup_days([c], now=NOW) == Decimal(2) / Decimal(24)

    def test_engine_blocks_a_passed_leg_instead_of_charging_zero(
            self, complete_fee_calc):
        past = SETTLEMENT_TEST_NOW - timedelta(days=1)
        op = _evaluate(
            make_contract(venue="kalshi", contract_id="Y", settlement=past),
            make_contract(venue="forecast_ex", contract_id="N",
                          settlement=past),
            complete_fee_calc)
        assert op.status == OpportunityStatus.BLOCK
        assert op.settlement_delay_cost is None and op.net_profit is None
        assert any("not after evaluation time" in r for r in op.block_reasons)

    def test_replay_measures_lockup_from_the_recorded_frame(
            self, complete_fee_calc):
        """Historical/backtest pass the frame time: a pair that was three
        days from settlement when recorded is costed for three days, not
        blocked (or zeroed) because the wall clock has moved on."""
        frame = datetime(2025, 3, 1, tzinfo=timezone.utc)
        settle = frame + timedelta(days=3)
        yes_c = make_contract(venue="kalshi", contract_id="Y",
                              settlement=settle)
        no_c = make_contract(venue="forecast_ex", contract_id="N",
                             settlement=settle)
        replayed = _evaluate(yes_c, no_c, complete_fee_calc,
                             evaluation_time=frame)
        assert replayed.settlement_delay_cost == Decimal("0.0381")
        assert replayed.status == OpportunityStatus.TRADE
        wall_clock = _evaluate(yes_c, no_c, complete_fee_calc)
        assert wall_clock.status == OpportunityStatus.BLOCK
        assert wall_clock.settlement_delay_cost is None


    def test_scan_threads_the_frame_time_to_the_evaluator(
            self, complete_fee_calc):
        """`run_arbitrage_scan(evaluation_time=…)` — the entry point the
        historical engine and backtest call with each frame's timestamp."""
        import asyncio

        from prediction_arb.modes.shared import run_arbitrage_scan
        frame = datetime(2025, 3, 1, tzinfo=timezone.utc)
        settle = frame + timedelta(days=3)
        a = make_contract(venue="kalshi", contract_id="K1", settlement=settle)
        b = make_contract(venue="forecast_ex", contract_id="F1",
                          settlement=settle)
        books = {("kalshi", "K1"): make_book("kalshi", "K1",
                                             asks=[("0.40", "100")]),
                 ("forecast_ex", "F1"): make_book("forecast_ex", "F1",
                                                  asks=[("0.50", "100")])}
        approved = {("forecast_ex:F1", "kalshi:K1"): ("m-1", 0.99)}

        def scan(**kw):
            return asyncio.run(run_arbitrage_scan(
                [a, b], books, approved, complete_fee_calc, Mode.HISTORICAL,
                minimum_net_profit=Decimal("2"),
                minimum_net_return=Decimal("0.005"),
                maximum_settlement_delay_days=Decimal("5"),
                cost_of_capital_annual=Decimal("0.05"), **kw))

        replayed = [o for o in scan(evaluation_time=frame)
                    if o.legs[0].venue == "kalshi"]
        assert replayed and replayed[0].settlement_delay_cost == Decimal("0.0381")
        wall = [o for o in scan() if o.legs[0].venue == "kalshi"]
        assert wall and wall[0].settlement_delay_cost is None

# ---------------------------------------------------- long-duration lockups

class TestLongDurationLockups:
    def test_long_bound_is_costed_in_full(self):
        """Expected tomorrow, but documented latest expiration 400 days
        out: the conservative lockup is 400 days, not one."""
        c = _kalshi(latest=NOW + timedelta(days=400),
                    expected=NOW + timedelta(days=1))
        lockup = capital_lockup_days([c], now=NOW)
        assert lockup == Decimal("400")
        # 100 × 0.05 × 400 / 365 = 5.479452… → rounded up to 5.4795
        assert settlement_delay_cost(Decimal("100"), lockup,
                                     Decimal("0.05")) == Decimal("5.4795")

    def test_long_dated_pair_loses_its_trade_verdict(self, complete_fee_calc):
        """The same books are TRADE with a three-day lockup and BLOCK when
        both legs release capital three years out — the lockup cost
        exceeds the edge. A long contract is never treated as immediate."""
        short = _evaluate(make_contract(venue="kalshi", contract_id="Y"),
                          make_contract(venue="forecast_ex", contract_id="N"),
                          complete_fee_calc)
        assert short.status == OpportunityStatus.TRADE

        far = SETTLEMENT_TEST_NOW + timedelta(days=3 * 365)
        yes_c = make_contract(venue="kalshi", contract_id="Y",
                              settlement=SETTLE,
                              kalshi_latest_expiration=far)
        no_c = make_contract(venue="forecast_ex", contract_id="N",
                             settlement=far)
        op = _evaluate(yes_c, no_c, complete_fee_calc)
        # 92.68 × 0.05 × 1095 / 365 = 13.902 exactly
        assert op.settlement_delay_cost == Decimal("13.9020")
        assert op.net_profit == Decimal("10.00") - Decimal("2.68") \
            - Decimal("13.9020")
        assert op.status == OpportunityStatus.BLOCK
        assert any("net profit not positive" in r for r in op.block_reasons)

    def test_implausible_lockup_blocks(self, complete_fee_calc):
        far = SETTLEMENT_TEST_NOW + timedelta(days=40000)
        op = _evaluate(
            make_contract(venue="kalshi", contract_id="Y", settlement=far),
            make_contract(venue="forecast_ex", contract_id="N",
                          settlement=far),
            complete_fee_calc)
        assert op.status == OpportunityStatus.BLOCK
        assert "implausible settlement lockup" in op.block_reasons


# ----------------------------------------- different settlement times by leg

class TestLegsReleasingAtDifferentTimes:
    def test_lockup_is_the_last_release_and_spread_the_gap(self):
        early = _kalshi(latest=NOW + timedelta(days=1), timer=7200,
                        contract_id="A")
        late = _kalshi(latest=NOW + timedelta(days=4), timer=0,
                       contract_id="B")
        a = assess_lockup([early, late], now=NOW, sources=PROD)
        assert a.lockup_days == Decimal("4")
        # 4 d − (1 d + 2 h) = 2 d 22 h
        assert a.spread_days == Decimal(70) / Decimal(24)
        # order of legs does not matter
        b = assess_lockup([late, early], now=NOW, sources=PROD)
        assert (b.lockup_days, b.spread_days) == (a.lockup_days, a.spread_days)

    def test_settlement_timer_is_part_of_the_release(self):
        c = _kalshi(latest=NOW + timedelta(days=1), timer=86400)
        assert capital_lockup_days([c], now=NOW) == Decimal("2")

    def test_spread_beyond_limit_blocks(self, complete_fee_calc):
        yes_c = make_contract(venue="kalshi", contract_id="Y",
                              settlement=SETTLE)
        no_c = make_contract(venue="forecast_ex", contract_id="N",
                             settlement=SETTLE + timedelta(days=6))
        op = _evaluate(yes_c, no_c, complete_fee_calc)
        assert op.status == OpportunityStatus.BLOCK
        assert any("settlement delay spread 6 days exceeds limit 5" in r
                   for r in op.block_reasons)
        # lockup (and so its cost) follows the LATER leg: 3 + 6 = 9 days
        assert op.settlement_delay_cost == settlement_delay_cost(
            Decimal("92.68"), Decimal("9"), Decimal("0.05"))

    def test_spread_function_matches_assessment(self):
        a = make_contract(venue="kalshi", contract_id="A", settlement=SETTLE)
        b = make_contract(venue="kalshi", contract_id="B",
                          settlement=SETTLE + timedelta(hours=36))
        assert settlement_delay_spread_days([a, b]) == Decimal("1.5")


# ------------------------------------------------------ capital-cost formula

class TestSimpleAnnualCapitalCost:
    def test_formula_is_capital_rate_days_over_365(self):
        assert settlement_delay_cost(Decimal("365"), Decimal("1"),
                                     Decimal("0.10")) == Decimal("0.1000")
        assert settlement_delay_cost(Decimal("1000"), Decimal("365"),
                                     Decimal("0.05")) == Decimal("50.0000")

    def test_simple_not_compounded_and_linear_in_days(self):
        one = settlement_delay_cost(Decimal("1000"), Decimal("365"),
                                    Decimal("0.05"))
        two = settlement_delay_cost(Decimal("1000"), Decimal("730"),
                                    Decimal("0.05"))
        assert two == 2 * one == Decimal("100.0000")  # compounding ⇒ 102.50

    def test_rounds_up_so_cost_is_never_understated(self):
        # 100 × 0.05 × 1 / 365 = 0.01369863… → 0.0137
        assert settlement_delay_cost(Decimal("100"), Decimal("1"),
                                     Decimal("0.05")) == Decimal("0.0137")
        # 1 × 0.05 × 0.001 / 365 is tiny but positive → one quantum
        assert settlement_delay_cost(Decimal("1"), Decimal("0.001"),
                                     Decimal("0.05")) == Decimal("0.0001")

    def test_explicit_zero_lockup_costs_zero(self):
        assert settlement_delay_cost(Decimal("100"), Decimal("0"),
                                     Decimal("0.05")) == Decimal("0")

    @pytest.mark.parametrize("capital, days, rate", [
        ("-1", "1", "0.05"), ("1", "-1", "0.05"), ("1", "1", "-0.05")])
    def test_negative_inputs_are_rejected(self, capital, days, rate):
        with pytest.raises(ValueError):
            settlement_delay_cost(Decimal(capital), Decimal(days),
                                  Decimal(rate))

    def test_engine_cost_base_is_capital_including_fees(
            self, complete_fee_calc):
        op = _evaluate(make_contract(venue="kalshi", contract_id="Y"),
                       make_contract(venue="forecast_ex", contract_id="N"),
                       complete_fee_calc, cost_of_capital_annual=Decimal("0.10"))
        assert op.capital_required == Decimal("92.68")
        assert op.settlement_delay_cost == settlement_delay_cost(
            Decimal("92.68"), Decimal("3"), Decimal("0.10")) == Decimal("0.0762")

    def test_day_arithmetic_is_decimal_not_float(self):
        assert timedelta_days(timedelta(hours=36)) == Decimal("1.5")
        assert timedelta_days(timedelta(days=3)) == Decimal("3")
        # Decimal division at context precision, not a float round-trip
        # (the former Decimal(str(float)) path gave 0.000011574074074074073)
        assert timedelta_days(timedelta(seconds=1)) == \
            Decimal(1) / Decimal(86400)
        assert timedelta_days(timedelta(days=-1)) == Decimal("-1")


# ------------------------------------------- adapter + recorded-store evidence

class TestKalshiEvidenceSurvivesAdapterAndReplay:
    PAYLOAD = {
        "ticker": "KXTEST-26JAN05", "market_type": "binary",
        "title": "Test market", "yes_sub_title": "Yes",
        "expected_expiration_time": "2026-01-03T00:00:00Z",
        "latest_expiration_time": "2026-01-05T00:00:00Z",
        "settlement_timer_seconds": 1800,
        "close_time": "2026-01-02T23:00:00Z",
        "rules_primary": "r", "rules_secondary": "s",
        "updated_time": "2025-12-01T00:00:00Z",
    }

    def _contract(self):
        from prediction_arb.adapters.kalshi import KalshiAdapter
        a = KalshiAdapter(rest_base="https://api.elections.kalshi.com/trade-api/v2")
        return a._normalize_market(dict(self.PAYLOAD))

    def test_adapter_contract_uses_the_documented_bound(self):
        c = self._contract()
        # matching still sees the EXPECTED expiration …
        assert c.settlement_time == datetime(2026, 1, 3, tzinfo=timezone.utc)
        # … but capital is costed to latest expiration + settlement timer
        r = capital_release(c, PROD)
        assert r.resolution_basis == datetime(2026, 1, 5, tzinfo=timezone.utc)
        assert r.post_resolution_delay == timedelta(seconds=1800)
        assert r.release_time == datetime(2026, 1, 5, 0, 30,
                                          tzinfo=timezone.utc)
        assert capital_lockup_days([c], now=NOW) == \
            Decimal(4) + Decimal(1800) / Decimal(86400)

    def test_recorded_store_round_trip_keeps_the_evidence(self):
        from prediction_arb.database.contract_codec import (
            decode_contract,
            encode_contract,
        )
        c = self._contract()
        row = SimpleNamespace(**encode_contract(c), venue=c.venue,
                              market_id=c.market_id,
                              contract_id=c.contract_id)
        decoded = decode_contract(row)
        assert decoded is not None
        assert capital_release(decoded, PROD) == capital_release(c, PROD)

    def test_payload_without_the_fields_blocks(self):
        c = dataclasses.replace(self._contract(), raw={"ticker": "X"})
        assert capital_release(c, PROD).release_time is None
