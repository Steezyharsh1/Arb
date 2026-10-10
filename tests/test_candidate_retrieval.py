"""§96 retrieval regressions (revised 2026-10-10; see docs/CANDIDATE_RETRIEVAL.md).

Retrieval proposes cross-venue pairs for the deterministic matcher. It must:
  * never propose a pair the matcher could pass but for a settlement gap
    larger than the matcher's own tolerance (and the tolerance is shared, so
    the two cannot drift apart);
  * link genuinely equivalent listings that are worded differently whenever
    they share a discriminative key (threshold, number, content word);
  * NOT link unrelated contracts merely because they share generic words at a
    busy settlement moment, and NOT chain a whole market into one group;
  * never turn a proposal into an approval.

The reference numbers pinned here come from `benchmarks/candidate_retrieval`.
They describe that dataset only; they are not a claim of perfect recall.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from .conftest import SETTLEMENT_TEST_NOW, make_contract

from benchmarks.candidate_retrieval import legacy_contracts as legacy
from benchmarks.candidate_retrieval.run import (
    exhaustive_truth,
    legacy_generator,
    listings_to_contracts,
    load_reference,
    retrieval_generator,
    score,
)
from benchmarks.candidate_retrieval.synthetic import build_dataset
from prediction_arb.matching.deterministic_matcher import (
    SETTLEMENT_TOLERANCE,
    deterministic_match,
)
from prediction_arb.matching.llm_matcher import LLMMatcher
from prediction_arb.modes.shared import run_matching
from prediction_arb.normalization.contracts import (
    MAX_LOCAL_DENSITY,
    RETRIEVAL_SETTLEMENT_WINDOW,
    blocking_keys,
    candidate_pairs,
    group_by_event,
)

SETTLE = SETTLEMENT_TEST_NOW + timedelta(days=1)

KALSHI_FED = ("Will the Federal Reserve hold its target range at "
              "4.25-4.50 percent in September 2026?")
POLY_FED = "Fed keeps rates at 4.25% to 4.5% at the September meeting?"


def _pairs(contracts):
    return set(candidate_pairs(contracts))


def _llm(match: bool):
    import json as _json

    async def client(prompt: str) -> str:
        return _json.dumps({
            "match": match, "confidence": 0.99 if match else 0.05,
            "reasoning_summary": "test", "event_identity": match,
            "outcome_identity": match, "settlement_identity": match,
            "resolution_identity": match, "threshold_identity": match,
            "time_identity": match, "payout_identity": match,
            "ambiguities": []})
    return LLMMatcher(client=client, model_version="test-1")


class TestSettlementWindow:
    def test_window_is_the_matcher_tolerance(self):
        """Retrieval and the deterministic settlement check share one constant."""
        assert RETRIEVAL_SETTLEMENT_WINDOW == SETTLEMENT_TOLERANCE == timedelta(hours=6)

    def test_five_hours_fifty_nine_minutes_apart_is_proposed(self):
        a = make_contract(venue="kalshi", contract_id="A", event=KALSHI_FED,
                          settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="B", event=POLY_FED,
                          settlement=SETTLE + timedelta(hours=5, minutes=59))
        assert deterministic_match(a, b).passed
        assert candidate_pairs([a, b]) == [(0, 1)]

    def test_exactly_six_hours_is_proposed_and_passes_matcher(self):
        """Boundary is inclusive on both sides: the matcher accepts exactly 6h."""
        a = make_contract(venue="kalshi", contract_id="A", event=KALSHI_FED,
                          settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="B", event=POLY_FED,
                          settlement=SETTLE + timedelta(hours=6))
        assert deterministic_match(a, b).passed
        assert candidate_pairs([a, b]) == [(0, 1)]

    def test_six_hours_one_minute_is_not_proposed_and_matcher_rejects(self):
        """Retrieval must never remove a pair the matcher could pass; here the
        matcher rejects the pair too, so proposing nothing is consistent."""
        a = make_contract(venue="kalshi", contract_id="A", event=KALSHI_FED,
                          settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="B", event=POLY_FED,
                          settlement=SETTLE + timedelta(hours=6, minutes=1))
        assert not deterministic_match(a, b).passed
        assert candidate_pairs([a, b]) == []

    def test_day_boundary_two_hours_apart_is_proposed(self):
        """23:00 UTC vs 01:00 UTC next day: different UTC dates, 2h apart."""
        a = make_contract(venue="kalshi", contract_id="A", event=KALSHI_FED,
                          settlement=SETTLE.replace(hour=23, minute=0))
        b = make_contract(venue="forecast_ex", contract_id="B", event=POLY_FED,
                          settlement=SETTLE.replace(hour=23, minute=0) + timedelta(hours=2))
        assert a.settlement_time.date() != b.settlement_time.date()
        assert candidate_pairs([a, b]) == [(0, 1)]

    def test_missing_settlement_is_never_proposed(self):
        a = make_contract(venue="kalshi", contract_id="A", event=KALSHI_FED)
        b = make_contract(venue="forecast_ex", contract_id="B", event=POLY_FED)
        b = dataclasses.replace(b, settlement_time=None)
        assert candidate_pairs([a, b]) == []

    def test_same_venue_is_never_proposed(self):
        a = make_contract(venue="kalshi", contract_id="A", event=KALSHI_FED)
        b = make_contract(venue="kalshi", contract_id="B", event=KALSHI_FED)
        assert candidate_pairs([a, b]) == []


class TestNumericNormalization:
    def test_k_suffix_and_full_number_share_a_candidate(self):
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Will Bitcoin reach $100k by year end?", settlement=SETTLE)
        b = make_contract(venue="polymarket_us", contract_id="B",
                          event="Bitcoin above $100,000 at year end?", settlement=SETTLE)
        assert candidate_pairs([a, b]) == [(0, 1)]

    def test_threshold_spellings_share_candidate_and_matcher_agrees(self):
        a = make_contract(venue="kalshi", contract_id="A", event="Gas above target",
                          threshold=Decimal("4.50"), threshold_op=">", units="USD",
                          settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="B", event="Gas over the line",
                          threshold=Decimal("4.5"), threshold_op=">", units="USD",
                          settlement=SETTLE)
        assert "thr:>:4.5:usd" in blocking_keys(a) & blocking_keys(b)
        assert candidate_pairs([a, b]) == [(0, 1)]
        assert deterministic_match(a, b).passed

    def test_different_threshold_is_proposed_only_for_shared_words_and_is_rejected(self):
        """A number mismatch is a matcher rejection. Retrieval may still propose the
        pair because the wording overlaps; that proposal approves nothing."""
        a = make_contract(venue="kalshi", contract_id="A", event=KALSHI_FED,
                          threshold=Decimal("4.25"), threshold_op="=", units="percent",
                          settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="B", event=POLY_FED,
                          threshold=Decimal("4.75"), threshold_op="=", units="percent",
                          settlement=SETTLE)
        assert not deterministic_match(a, b).passed
        approved, result = asyncio.run(run_matching([a, b], _llm(match=True)))
        assert approved == {}
        assert result.pairs_deterministic_pass == 0

    def test_k_and_plain_thousands_match_the_matcher_too(self):
        a = make_contract(venue="kalshi", contract_id="A", event="Bitcoin above $100k",
                          threshold=Decimal("100000"), threshold_op=">", units="USD",
                          settlement=SETTLE)
        b = make_contract(venue="polymarket_us", contract_id="B", event="BTC over 100000",
                          threshold=Decimal("1E+5"), threshold_op=">", units="USD",
                          settlement=SETTLE)
        assert candidate_pairs([a, b]) == [(0, 1)]
        assert deterministic_match(a, b).passed


class TestDifferentWording:
    def test_fed_rephrasing_is_proposed(self):
        a = make_contract(venue="kalshi", contract_id="K", event=KALSHI_FED, settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="F", event=POLY_FED,
                          settlement=SETTLE + timedelta(hours=1))
        assert deterministic_match(a, b).passed
        assert candidate_pairs([a, b]) == [(0, 1)]

    def test_threshold_less_paraphrase_sharing_subject_is_proposed(self):
        a = make_contract(venue="kalshi", contract_id="K",
                          event="Will Republicans control the Senate after the 2026 midterms?",
                          settlement=SETTLE)
        b = make_contract(venue="polymarket_us", contract_id="P",
                          event="Republican Senate majority after 2026 midterms?",
                          settlement=SETTLE)
        assert candidate_pairs([a, b]) == [(0, 1)]

    def test_known_miss_paraphrase_without_shared_key_is_not_proposed(self):
        """PINNED LIMITATION. A genuine paraphrase with no shared content word or
        number has no shared key, so retrieval cannot propose it. The reference set
        keeps it on purpose (ref-paraphrase-no-shared-token-oct1). If a future change
        makes this test fail by proposing it, update the docs and the reference
        numbers together."""
        a = make_contract(venue="kalshi", contract_id="K",
                          event="Will Congress enact a spending bill before October 1, 2026?",
                          settlement=SETTLE)
        b = make_contract(venue="polymarket_us", contract_id="P",
                          event="Federal funds law signed by Sep 30?", settlement=SETTLE)
        assert deterministic_match(a, b).passed
        assert candidate_pairs([a, b]) == []


class TestGenericWordsDoNotLink:
    def test_unrelated_pair_sharing_generic_words_is_rejected_and_never_approved(self):
        a = make_contract(venue="kalshi", contract_id="L",
                          event="Will the Lakers win their game on Oct 28, 2026?",
                          settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="Y",
                          event="Will the Yankees win the World Series in 2026?",
                          settlement=SETTLE)
        # Shared word "win"/"2026" can propose; the matcher's economics agree, so the
        # decision rests on the LLM. A declining LLM must leave the pair unapproved.
        approved, result = asyncio.run(run_matching([a, b], _llm(match=False)))
        assert approved == {}
        assert all(c.status != "APPROVED" for c in result.certificates)

    def test_generic_words_at_one_busy_moment_propose_nothing(self):
        """More contracts than the density limit share 'total', 'number' and '2026'
        and all settle at the same moment: that key is generic there, so no pair is
        proposed through it. Identifiers are alphabetic on purpose: digits in titles
        would create numeric keys that collide for unrelated reasons."""
        n = 25
        assert n > MAX_LOCAL_DENSITY   # the fixture must exceed the limit it tests
        contracts = [
            make_contract(venue=("kalshi", "polymarket_us", "forecast_ex")[i % 3],
                          contract_id=f"G{i}",
                          event=(f"Will the total number of zq{chr(97 + i % 26)}"
                                 f"{chr(97 + i // 26)} be above 2026 in 2026?"),
                          settlement=SETTLE)
            for i in range(n)
        ]
        assert candidate_pairs(contracts) == []

    def test_shared_word_spread_over_the_calendar_still_proposes_per_moment(self):
        """Density is local, not global. 'senate' and 'republican' are held by 50
        contracts across the calendar, but each pair settles at its own moment, so
        every pair is still proposed. The titles are identical across rounds on
        purpose: the pair must be linked by those two words alone, not by a unique
        token of its own."""
        rounds = 25
        assert rounds * 2 > MAX_LOCAL_DENSITY  # the word is globally common
        contracts = []
        for k in range(rounds):
            moment = SETTLE + timedelta(days=3 * k)
            contracts.append(make_contract(
                venue="kalshi", contract_id=f"S{k}a", settlement=moment,
                event="Will Republicans control the Senate?"))
            contracts.append(make_contract(
                venue="polymarket_us", contract_id=f"S{k}b", settlement=moment,
                event="Republican Senate majority?"))
        assert len(candidate_pairs(contracts)) == rounds

    def test_generic_decoys_do_not_chain_into_one_group_as_before(self):
        """Regression for the inherited giant component. The frozen legacy design
        joins 300 unrelated decoys into one component over shared settlement buckets
        and numbers; the new design leaves them in many small groups."""
        listings = [x for x in build_dataset(300, seed=7) if x.eq_id.startswith("dc-")]
        contracts = listings_to_contracts(listings)
        legacy_groups = legacy.group_by_event(
            [legacy.with_normalized_keys(c) for c in contracts])
        assert max(len(g) for g in legacy_groups.values()) > 100
        new_groups = group_by_event(contracts)
        assert max(len(g) for g in new_groups.values()) < 40
        assert len(new_groups) > 20

    def test_density_bound_limits_work_per_contract(self):
        """Each contract is paired with at most one dense-window's worth of
        holders per key, so the total pair count stays linear in the number of
        contracts for a fixed key family."""
        n = 200
        contracts = [make_contract(venue=("kalshi", "polymarket_us")[i % 2],
                                   contract_id=f"D{i}", settlement=SETTLE,
                                   event=f"Event {i} widget{i % 40} number {i}")
                     for i in range(n)]
        pairs = candidate_pairs(contracts)
        assert len(pairs) <= n * MAX_LOCAL_DENSITY


class TestNonTransitive:
    def test_chain_neighbour_does_not_join_its_two_ends(self):
        """A~B and B~C share keys; A and C share nothing and are 4h apart. The
        proposal set must contain A-B and B-C but not A-C."""
        a = make_contract(venue="kalshi", contract_id="A",
                          event="alpha lighthouse keeper", settlement=SETTLE)
        b = make_contract(venue="polymarket_us", contract_id="B",
                          event="alpha beta harbour", settlement=SETTLE + timedelta(hours=2))
        c = make_contract(venue="forecast_ex", contract_id="C",
                          event="beta quarry rules", settlement=SETTLE + timedelta(hours=4))
        assert set(candidate_pairs([a, b, c])) == {(0, 1), (1, 2)}

    def test_grouping_joins_a_chain_but_proposal_does_not(self):
        """Groups are components for the scan; they are not claims of equivalence."""
        a = make_contract(venue="kalshi", contract_id="A",
                          event="alpha lighthouse keeper", settlement=SETTLE)
        b = make_contract(venue="polymarket_us", contract_id="B",
                          event="alpha beta harbour", settlement=SETTLE + timedelta(hours=2))
        c = make_contract(venue="forecast_ex", contract_id="C",
                          event="beta quarry rules", settlement=SETTLE + timedelta(hours=4))
        assert len(group_by_event([a, b, c])) == 1
        assert (0, 2) not in candidate_pairs([a, b, c])


class TestProposalIsNotApproval:
    def test_proposed_pair_with_declining_llm_is_not_approved(self):
        a = make_contract(venue="kalshi", contract_id="K", event=KALSHI_FED, settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="F", event=POLY_FED, settlement=SETTLE)
        approved, result = asyncio.run(run_matching([a, b], _llm(match=False)))
        assert result.pairs_considered == 1
        assert result.pairs_deterministic_pass == 1
        assert approved == {}

    def test_retrieval_order_does_not_change_the_approved_set(self):
        a = make_contract(venue="kalshi", contract_id="K", event=KALSHI_FED, settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="F", event=POLY_FED, settlement=SETTLE)
        fwd, _ = asyncio.run(run_matching([a, b], _llm(match=True)))
        rev, _ = asyncio.run(run_matching([b, a], _llm(match=True)))
        assert set(fwd) == set(rev) and len(fwd) == 1


@pytest.fixture(scope="module")
def reference():
    """The hand-labelled exhaustive reference set (benchmarks/candidate_retrieval)."""
    listings, _ = load_reference()
    contracts = listings_to_contracts(listings)
    truth = exhaustive_truth(contracts, [x.eq_id for x in listings])
    return listings, contracts, truth


class TestReferenceSet:

    def test_frozen_baseline_proposes_every_cross_venue_pair(self, reference):
        _, contracts, truth = reference
        before = legacy_generator(contracts)
        assert before.pairs == truth.cross_pairs
        assert len(before.components) == 1

    def test_new_design_keeps_every_matchable_equivalent_except_the_known_miss(self, reference):
        listings, contracts, truth = reference
        after = retrieval_generator(contracts)
        lost = truth.eq_det - after.pairs
        assert len(truth.eq_det) == 16
        assert len(lost) == 1
        (i, j), = lost
        assert {listings[i].eq_id, listings[j].eq_id} == {
            "ref-paraphrase-no-shared-token-oct1"}

    def test_new_design_reduces_candidates_by_over_ninety_percent(self, reference):
        _, contracts, truth = reference
        after = retrieval_generator(contracts)
        assert len(after.pairs) == 36
        assert len(after.pairs) / len(truth.cross_pairs) < 0.10

    def test_new_design_has_no_giant_component(self, reference):
        _, contracts, _ = reference
        after = retrieval_generator(contracts)
        assert max(len(c) for c in after.components) <= 10

    def test_scored_summary_matches_the_documented_numbers(self, reference):
        _, contracts, truth = reference
        before = legacy_generator(contracts)
        after = retrieval_generator(contracts)
        s = score("retrieval", after, truth, current=before)
        assert s["recall_equivalent_and_det_pass"] == pytest.approx(15 / 16, abs=1e-4)
        assert s["recall_det_pass"] == pytest.approx(26 / 29, abs=1e-4)
        assert s["candidate_pairs"] == 36


class TestSyntheticRecall:
    def test_no_matchable_equivalent_lost_on_seeded_dataset(self):
        """300 seeded contracts, exhaustive ground truth: every equivalent pair that
        the deterministic matcher can pass is proposed. Seeded, so the claim is about
        this dataset only."""
        listings = build_dataset(300, seed=20261010)
        contracts = listings_to_contracts(listings)
        truth = exhaustive_truth(contracts, [x.eq_id for x in listings])
        after = retrieval_generator(contracts)
        assert truth.eq_det
        assert truth.eq_det <= after.pairs
        assert len(after.pairs) < 0.05 * len(truth.cross_pairs)

    def test_legacy_single_component_reproduces_on_1000_contracts(self):
        """The inherited symptom, reproduced: one component holding all 1,000
        contracts and every cross-venue pair proposed. Mutation check: if the frozen
        baseline ever changes, this fails."""
        listings = build_dataset(1000, seed=20261010)
        contracts = listings_to_contracts(listings)
        before = legacy_generator(contracts)
        assert len(before.components) == 1
        assert len(before.components[0]) == 1000
        assert len(before.pairs) == 333296


def test_reference_numbers_file_is_valid_json():
    from benchmarks.candidate_retrieval.run import REFERENCE_PATH
    data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    assert len(data["contracts"]) == 50
    assert {c["venue"] for c in data["contracts"]} == {
        "kalshi", "polymarket_us", "forecast_ex"}
