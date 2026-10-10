"""Phase 5 / D6: cross-venue candidate generation by blocking (§96).

The old grouping keyed on *exact canonical title + settlement date*, so two
venues describing the same real-world event in different words never became
candidates and the pipeline could not find a single cross-venue trade. These
tests pin the replacement: recall-oriented deterministic blocking whose only
job is to put plausible pairs in front of the deterministic matcher — never
to decide equivalence, and never to promote a pair past that matcher.

Mutation note: the core grouping tests below were verified against the
previous exact-key `group_by_event` — reverting the fix makes
`test_differently_phrased_same_event_group_together`,
`test_settlement_across_day_boundary_still_groups` and
`test_pipeline_finds_cross_venue_opportunity` fail.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import timedelta
from decimal import Decimal

import pytest

from .conftest import SETTLEMENT_TEST_NOW, make_book, make_contract

from prediction_arb.matching.deterministic_matcher import deterministic_match
from prediction_arb.matching.llm_matcher import LLMMatcher
from prediction_arb.modes.shared import run_arbitrage_scan, run_matching
from prediction_arb.normalization.contracts import (
    blocking_keys,
    canonical_number,
    content_tokens,
    group_by_event,
    numeric_tokens,
)

# Keep all relative settlement examples after conftest's frozen settlement
# clock. No fixture should age into a settled contract as the calendar moves.
SETTLE = SETTLEMENT_TEST_NOW + timedelta(days=1)

# Same real-world event, the way two venues actually phrase it.
KALSHI_TITLE = ("Will the Federal Reserve hold its target range at "
                "4.25-4.50 percent in September 2026?")
POLY_TITLE = "Fed keeps rates at 4.25% to 4.5% at the September meeting?"


def _llm_verdict(match: bool = True, confidence: float = 0.998) -> str:
    import json
    return json.dumps({
        "match": match, "confidence": confidence,
        "reasoning_summary": "same real-world event",
        "event_identity": match, "outcome_identity": True,
        "settlement_identity": True, "resolution_identity": True,
        "threshold_identity": True, "time_identity": True,
        "payout_identity": True, "ambiguities": [],
    })


@pytest.fixture
def stub_llm():
    async def client(prompt: str) -> str:
        return _llm_verdict(match=True)
    return LLMMatcher(client=client, model_version="stub-1")


class TestCanonicalNumbers:
    def test_thousands_separators_and_trailing_zeros(self):
        assert canonical_number("100,000.00") == "100000"
        assert canonical_number("4.50") == "4.5"
        assert canonical_number("0.500") == "0.5"

    def test_exponent_form_never_leaks_into_keys(self):
        assert canonical_number("1E+4") == "10000"

    def test_non_numbers_are_dropped_not_guessed(self):
        assert canonical_number("abc") is None
        assert canonical_number("") is None

    def test_negative_and_zero(self):
        assert canonical_number("-0") == "0"
        assert canonical_number("-2.50") == "-2.5"


class TestNumericTokens:
    def test_percent_range(self):
        assert numeric_tokens("4.25%-4.50%") == {"4.25", "4.5"}

    def test_dollars_and_commas(self):
        assert numeric_tokens("Will it close above $10,000?") == {"10000"}

    def test_k_suffix_scales_so_both_spellings_block_together(self):
        toks = numeric_tokens("Will Bitcoin reach $100k in 2026?")
        assert "100000" in toks
        assert "100" in toks          # unscaled reading kept: recall-first
        assert "2026" in toks

    def test_no_numbers(self):
        assert numeric_tokens("Will the Fed hold rates?") == set()


class TestContentTokens:
    def test_plural_folding_shares_tokens(self):
        assert content_tokens("Fed raises rates in September") == \
            content_tokens("fed raise rate september")

    def test_stopwords_removed_domain_words_kept(self):
        toks = content_tokens("Will the Federal Reserve hold in September?")
        assert "federal" in toks and "reserve" in toks
        assert "september" in toks
        assert "will" not in toks and "the" not in toks and "in" not in toks

    def test_ies_folding(self):
        assert content_tokens("commodities") == {"commodity"}

    def test_ss_not_folded(self):
        assert content_tokens("business") == {"business"}


class TestBlockingKeys:
    def test_settlement_is_not_a_blocking_key(self):
        """§96 revised 2026-10-10: settlement is a pairwise window in
        `candidate_pairs`, never a shared bucket. Shared buckets chained clustered
        expiries into one giant component (1,000 contracts → 1 group)."""
        c = make_contract()
        assert not [k for k in blocking_keys(c) if k.startswith(("settle:", "cat:"))]

    def test_no_settlement_no_settle_key(self):
        c = make_contract(settlement=SETTLE - timedelta(days=400))
        c = dataclasses.replace(c, settlement_time=None)
        assert not [k for k in blocking_keys(c) if k.startswith("settle:")]

    def test_threshold_operator_units_key(self):
        c = make_contract(threshold=Decimal("10000"), threshold_op=">",
                          units="USD")
        assert "thr:>:10000:usd" in blocking_keys(c)

    def test_threshold_equivalent_spellings_share_key(self):
        a = make_contract(threshold=Decimal("10000.00"), threshold_op=">=",
                          units="USD")
        b = make_contract(threshold=Decimal("1E+4"), threshold_op=">=",
                          units="USD")
        assert blocking_keys(a) & blocking_keys(b)


class TestGrouping:
    def test_differently_phrased_same_event_group_together(self):
        a = make_contract(venue="kalshi", event=KALSHI_TITLE)
        b = make_contract(venue="polymarket_us", event=POLY_TITLE)
        groups = group_by_event([a, b])
        assert len(groups) == 1

    def test_settlement_across_day_boundary_still_groups(self):
        """23:00 UTC vs 01:00 UTC next day: same event, different UTC dates, 2h
        apart. The matcher's 6h window, not the UTC date, decides adjacency."""
        a = make_contract(venue="kalshi", event=POLY_TITLE,
                          settlement=SETTLE - timedelta(hours=1))
        b = make_contract(venue="forecast_ex", event=POLY_TITLE,
                          settlement=SETTLE + timedelta(hours=1))
        assert a.settlement_time.date() != b.settlement_time.date()
        assert len(group_by_event([a, b])) == 1

    def test_settlement_24h_apart_does_not_group(self):
        """Replaces the earlier 'day boundary' case, which asserted a pair 24h apart.
        The matcher rejects that pair (> 6h), so retrieval must not propose it."""
        a = make_contract(venue="kalshi", event=POLY_TITLE,
                          settlement=SETTLE - timedelta(hours=21))
        b = make_contract(venue="forecast_ex", event=POLY_TITLE,
                          settlement=SETTLE + timedelta(hours=3))
        assert not deterministic_match(a, b).passed
        assert len(group_by_event([a, b])) == 2

    def test_truly_unrelated_contracts_stay_apart(self):
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Will Amazon report quarterly revenue above 200 billion dollars",
                          category="AMZN", settlement=SETTLE)
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Will the UK Chancellor resign before March",
                          category="UKPOL", settlement=SETTLE + timedelta(days=120))
        assert len(group_by_event([a, b])) == 2

    def test_threshold_alone_is_enough_to_become_candidates_within_the_window(self):
        """No shared words, numbers, category: the threshold family alone proposes
        the pair when both settle together. The deterministic matcher then rejects it
        (different events); grouping only widens and never approves."""
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Will the pipeline project finish on schedule",
                          category="INFRA", threshold=Decimal("10000"),
                          threshold_op=">", units="USD")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Zebra population exceeds nine thousand nine hundred",
                          category="WILDLIFE", threshold=Decimal("10000"),
                          threshold_op=">", units="USD")
        assert len(group_by_event([a, b])) == 1

    def test_threshold_alone_does_not_propose_across_the_window(self):
        """The same threshold 90 days apart is no longer a candidate (§96 revised):
        it is outside the matcher's settlement tolerance and would be rejected."""
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Will the pipeline project finish on schedule",
                          category="INFRA", threshold=Decimal("10000"),
                          threshold_op=">", units="USD")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Zebra population exceeds nine thousand nine hundred",
                          category="WILDLIFE", threshold=Decimal("10000"),
                          threshold_op=">", units="USD",
                          settlement=SETTLE + timedelta(days=90))
        assert len(group_by_event([a, b])) == 2

    def test_grouping_is_deterministic_under_reordering(self):
        contracts = [
            make_contract(venue="kalshi", contract_id="A", event=KALSHI_TITLE),
            make_contract(venue="polymarket_us", contract_id="P", event=POLY_TITLE),
            make_contract(venue="forecast_ex", contract_id="F",
                          event="Will the UK Chancellor resign before March",
                          category="UKPOL",
                          settlement=SETTLE + timedelta(days=120)),
        ]
        forward = group_by_event(contracts)
        backward = group_by_event(list(reversed(contracts)))
        as_member_sets = lambda g: sorted(
            sorted(f"{c.venue}:{c.contract_id}" for c in members)
            for members in g.values())
        assert as_member_sets(forward) == as_member_sets(backward)


class TestSameVenueNeverApproved:
    """D15: the certificate gate must never stamp APPROVED on a pair whose
    venue relationship is not proven INDEPENDENT.

    Kalshi contracts reached through Webull are the SAME VENUE (§33 requires
    an INDEPENDENT relationship before any trade). `search_event` already
    refuses to evaluate non-INDEPENDENT pairs; this pins the certificate
    side, because a persisted APPROVED same-venue certificate would poison
    the human false-match review."""

    def _same_venue_pair(self):
        # Distinct titles so the pair exercises real blocking, identical
        # economics so the deterministic filter passes.
        a = make_contract(venue="kalshi", contract_id="K1",
                          event="Will the Fed hold rates at 4.25% in September?",
                          underlying="kalshi_dcm")
        b = make_contract(venue="webull", contract_id="W1",
                          event="Fed rate decision September: holds at 4.25 percent",
                          underlying="kalshi_dcm")
        return a, b

    def _stub_llm(self):
        async def client(prompt: str) -> str:
            return _llm_verdict(match=True, confidence=0.999)
        return LLMMatcher(client=client, model_version="stub-1")

    def test_same_venue_with_perfect_llm_verdict_is_review_not_approved(self):
        from prediction_arb.matching.deterministic_matcher import (
            deterministic_match,
        )
        from prediction_arb.matching.rules_validator import validate_match
        a, b = self._same_venue_pair()
        det = deterministic_match(a, b)
        assert det.passed                      # economics identical
        llm = asyncio.run(self._stub_llm().compare(a, b))
        outcome = validate_match(a, b, det, llm)
        assert outcome.status == "REVIEW"
        assert any("not proven INDEPENDENT" in r for r in outcome.reasons)

    def test_same_venue_certificate_via_pipeline_is_review(self):
        a, b = self._same_venue_pair()
        approved, result = asyncio.run(
            run_matching([a, b], self._stub_llm()))
        assert approved == {}
        assert all(c.status == "REVIEW" for c in result.certificates)

    def test_unknown_relationship_stays_review(self):
        from prediction_arb.matching.deterministic_matcher import (
            deterministic_match,
        )
        from prediction_arb.matching.rules_validator import validate_match
        a = make_contract(venue="kalshi", event=POLY_TITLE)
        b = make_contract(venue="mystery_venue", event=POLY_TITLE,
                          underlying="???")
        det = deterministic_match(a, b)
        llm = asyncio.run(self._stub_llm().compare(a, b))
        assert validate_match(a, b, det, llm).status == "REVIEW"


class TestPipelineEmitsCandidates:
    """End-to-end: blocking → deterministic filter → LLM → certificate →
    optimizer. This is the chain D6 silently severed."""

    def _pair(self):
        a = make_contract(venue="kalshi", contract_id="K-FED",
                          event=KALSHI_TITLE,
                          settlement=SETTLE - timedelta(hours=1))
        b = make_contract(venue="forecast_ex", contract_id="FX-FED",
                          event=POLY_TITLE,
                          settlement=SETTLE + timedelta(hours=1))
        return a, b

    def test_pipeline_finds_cross_venue_opportunity(self, stub_llm,
                                                    complete_fee_calc):
        a, b = self._pair()
        approved, result = asyncio.run(run_matching([a, b], stub_llm))
        # D6: the differently-phrased pair is now considered at all…
        assert result.pairs_considered == 1
        assert result.pairs_deterministic_pass == 1
        # …and D9 (stub LLM): the certificate is APPROVED, not stuck in REVIEW
        assert [c.status for c in result.certificates] == ["APPROVED"]
        assert len(approved) == 1

        books = {
            ("kalshi", "K-FED"): make_book("kalshi", "K-FED",
                                           asks=[("0.40", "100")]),
            ("forecast_ex", "FX-FED"): make_book("forecast_ex", "FX-FED",
                                                 asks=[("0.50", "100")]),
        }
        ops = asyncio.run(run_arbitrage_scan(
            [a, b], books, approved, complete_fee_calc, "paper",
            minimum_net_profit=Decimal("2"),
            minimum_net_return=Decimal("0.005"),
            maximum_settlement_delay_days=Decimal("5"),
            cost_of_capital_annual=Decimal("0.05")))
        assert len(ops) >= 1
        assert ops[0].settlement_delay_cost > 0  # future lockup is costed
        assert ops[0].match_id == result.certificates[0].match_id

    def test_blocking_never_approves_a_deterministic_reject(self, stub_llm):
        """Shared blocking keys must not smuggle a bad pair through: a
        payout mismatch is REJECTED by the deterministic filter no matter
        how the candidates were generated (Principle B)."""
        a, b = self._pair()
        b = make_contract(venue="forecast_ex", contract_id="FX-FED",
                          event=POLY_TITLE, payout=Decimal("0.50"),
                          settlement=SETTLE + timedelta(hours=1))
        approved, result = asyncio.run(run_matching([a, b], stub_llm))
        assert result.pairs_considered == 1          # still a candidate
        assert result.pairs_deterministic_pass == 0  # …and rejected
        assert approved == {}
        assert all(c.status == "REJECTED" for c in result.certificates)

    def test_without_llm_candidates_end_in_review_not_approval(self):
        """D9 guard at pipeline level: no LLM configured ⇒ certificate
        REVIEW, approved set empty, so the optimizer evaluates nothing."""
        a, b = self._pair()
        approved, result = asyncio.run(
            run_matching([a, b], LLMMatcher(client=None)))
        assert result.pairs_considered == 1
        assert result.pairs_deterministic_pass == 1
        assert all(c.status == "REVIEW" for c in result.certificates)
        assert approved == {}
