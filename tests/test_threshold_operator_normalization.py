"""D25: cross-venue threshold-operator vocabulary and units (2026-10-11).

Before this change the deterministic matcher compared the raw venue tokens
with `==`. Kalshi emits its own `strike_type` vocabulary ("greater",
"greater_or_equal", ...) and the Polymarket US adapter cannot emit one at all,
so a pair with the *same* threshold and settlement was reported as a
`threshold_operator` MISMATCH and deleted from the pipeline — the false
negative recorded as D25 and reproduced 2026-10-10.

These tests pin the three-state replacement:

* both legs declare the same canonical operator  -> pass;
* both legs declare canonical operators that differ -> final mismatch;
* anything else (declared vs unknown, or an unmapped venue token) is
  UNVERIFIED: it reaches the certificate as REVIEW with the field named, it
  never spends the LLM budget, and no verdict can make it APPROVED.

Mutation notes (each verified by reverting the production change and
watching these tests fail):

* mapping `strike_type` back to the raw token fails the adapter tests and the
  end-to-end D25 test;
* treating "unverified" as a pass (dropping the `det.unverified` gate in
  `validate_match`) fails `test_unverified_never_approved_even_with_a_confident_llm`;
* treating "unverified" as a mismatch (setting `passed=False`) fails the two
  matcher tri-state tests.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import timedelta
from decimal import Decimal

import pytest

from prediction_arb.adapters.kalshi import KalshiAdapter
from prediction_arb.adapters.polymarket_us import PolymarketUSAdapter
from prediction_arb.matching.deterministic_matcher import (
    MATCHER_VERSION,
    DeterministicResult,
    deterministic_match,
)
from prediction_arb.matching.llm_matcher import LLMMatcher, not_consulted
from prediction_arb.matching.match_certificates import build_certificate
from prediction_arb.matching.rules_validator import validate_match
from prediction_arb.modes.shared import run_matching
from prediction_arb.normalization.thresholds import (
    ABSENT,
    CANONICAL,
    CANONICAL_OPERATORS,
    KALSHI_OPERATOR_VOCABULARY,
    POLYMARKET_US_OPERATOR_VOCABULARY,
    UNSUPPORTED,
    compare_operators,
    compare_units,
    normalize_units,
    read_operator,
)

from .conftest import SETTLEMENT_TEST_NOW, make_contract

# The two legs of the recorded D25 reproduction: same real-world threshold,
# same settlement, one leg with a venue vocabulary the other cannot declare.
SETTLE = SETTLEMENT_TEST_NOW + timedelta(days=2)
THRESHOLD = "4.00"


def kalshi_payload(**overrides) -> dict:
    """A documented `GET /markets` binary market, threshold-style."""
    payload = {
        "ticker": "KXCPI-26NOV-T4.00",
        "event_ticker": "KXCPI-26NOV",
        "market_type": "binary",
        "title": "Will US CPI year-over-year for October 2026 be above 4.00%?",
        "yes_sub_title": "Above 4.00%",
        "strike_type": "greater",
        "floor_strike": 4.0,
        "expected_expiration_time": SETTLE.isoformat(),
        "close_time": (SETTLE - timedelta(hours=1)).isoformat(),
        "rules_primary": "Bureau of Labor Statistics CPI release for October 2026.",
        "updated_time": "2026-10-01T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def polymarket_payload(**overrides) -> dict:
    """A documented public-gateway market. No operator field is documented,
    so the adapter cannot declare one (evidence row P7: no live payload has
    been observed either)."""
    payload = {
        "slug": "us-cpi-october-2026-above-4-00",
        "question": "Will US CPI year-over-year for October 2026 be above 4.00%?",
        "description": ("Resolves YES if the BLS October 2026 CPI "
                        "year-over-year print is above 4.00%."),
        "endDate": SETTLE.isoformat(),
        "closed": False,
        "archived": False,
        "category": "economics",
        "line": 4.0,
        "orderPriceMinTickSize": "0.01",
        "minimumTradeQty": "1",
        "updatedAt": "2026-10-01T00:00:00Z",
    }
    payload.update(overrides)
    return payload


def kalshi_contract(**overrides):
    return KalshiAdapter("https://external-api.kalshi.com/trade-api/v2"
                         )._normalize_market(kalshi_payload(**overrides))


def polymarket_contract(**overrides):
    return PolymarketUSAdapter()._normalize_market(polymarket_payload(**overrides))


def _verdict(match: bool = True, confidence: float = 0.998) -> str:
    return json.dumps({
        "match": match, "confidence": confidence,
        "reasoning_summary": "same real-world event",
        "event_identity": match, "outcome_identity": True,
        "settlement_identity": True, "resolution_identity": True,
        "threshold_identity": True, "time_identity": True,
        "payout_identity": True, "ambiguities": [],
    })


class RecordingLLM:
    """An LLM matcher that records every comparison it was asked to make."""

    def __init__(self):
        self.calls = 0

        async def client(prompt: str) -> str:
            self.calls += 1
            return _verdict()

        self.matcher = LLMMatcher(client=client, model_version="stub-1")


# ------------------------------------------------------------------ vocabulary

class TestOperatorVocabulary:
    @pytest.mark.parametrize("token,canonical", [
        ("greater", ">"),
        ("greater_or_equal", ">="),
        ("less", "<"),
        ("less_or_equal", "<="),
        ("between", "between"),
        ("GREATER", ">"),                     # case-insensitive
        (" greater ", ">"),                   # whitespace
    ])
    def test_documented_kalshi_tokens_map_to_canonical(self, token, canonical):
        read = read_operator("kalshi", token)
        assert read.state == CANONICAL
        assert read.canonical == canonical
        assert read.raw == token.strip()

    @pytest.mark.parametrize("token", ["functional", "custom", "structured",
                                       "some_future_token"])
    def test_unmapped_venue_tokens_stay_unsupported(self, token):
        """Never guessed at, and never silently downgraded to 'absent' — that
        would let a non-numeric strike compare as if it had no operator."""
        read = read_operator("kalshi", token)
        assert read.state == UNSUPPORTED
        assert read.canonical is None
        assert read.raw == token

    @pytest.mark.parametrize("raw", [None, "", "   "])
    def test_no_declared_operator_is_absent(self, raw):
        assert read_operator("kalshi", raw).state == ABSENT
        assert read_operator("polymarket_us", raw).state == ABSENT

    @pytest.mark.parametrize("token", CANONICAL_OPERATORS)
    def test_already_canonical_tokens_pass_through_for_any_venue(self, token):
        for venue in ("kalshi", "polymarket_us", "forecast_ex"):
            read = read_operator(venue, token)
            assert (read.state, read.canonical) == (CANONICAL, token)

    def test_legacy_recorded_kalshi_token_still_reads_canonically(self):
        """Rows recorded before this change hold Kalshi's raw token."""
        assert read_operator("kalshi", "greater_or_equal").canonical == ">="

    def test_polymarket_vocabulary_is_unknown_with_no_invented_mapping(self):
        """Evidence guard: the module must not grow an undocumented
        Polymarket mapping without new evidence (D40)."""
        assert POLYMARKET_US_OPERATOR_VOCABULARY.status == "UNKNOWN"
        assert dict(POLYMARKET_US_OPERATOR_VOCABULARY.operators) == {}
        assert KALSHI_OPERATOR_VOCABULARY.status == "DOCUMENTED"
        assert POLYMARKET_US_OPERATOR_VOCABULARY.reviewed_on == "2026-10-11"


class TestCompareOperators:
    @pytest.mark.parametrize("a,b,expected", [
        (("kalshi", "greater"), ("polymarket_us", ">"), "match"),
        (("kalshi", "greater"), ("forecast_ex", ">"), "match"),
        (("kalshi", "greater"), ("polymarket_us", "<"), "mismatch"),
        (("kalshi", "greater_or_equal"), ("polymarket_us", ">"), "mismatch"),
        (("kalshi", "greater"), ("polymarket_us", None), "unverified"),
        (("kalshi", None), ("polymarket_us", "greater"), "unverified"),
        (("kalshi", None), ("polymarket_us", None), "match"),
        (("kalshi", "structured"), ("polymarket_us", ">"), "unverified"),
        (("kalshi", "structured"), ("polymarket_us", None), "unverified"),
        (("kalshi", "between"), ("polymarket_us", "between"), "match"),
        (("kalshi", "between"), ("polymarket_us", ">="), "mismatch"),
    ])
    def test_three_state_verdicts(self, a, b, expected):
        verdict, _reason = compare_operators(a[0], a[1], b[0], b[1])
        assert verdict == expected

    def test_unverified_reason_names_the_field_and_forbids_approval(self):
        verdict, reason = compare_operators("kalshi", "greater",
                                            "polymarket_us", None)
        assert verdict == "unverified"
        assert "threshold_operator" in reason
        assert "not a mismatch" in reason
        assert "never APPROVED" in reason


class TestCompareUnits:
    def test_case_and_whitespace_are_normalized_not_inferred(self):
        assert normalize_units(" USD ") == "usd"
        assert compare_units("USD", "usd")[0] == "match"

    @pytest.mark.parametrize("a,b,expected", [
        (None, None, "match"),
        ("", "  ", "match"),
        ("USD", "USD", "match"),
        ("USD", "USDC", "mismatch"),
        ("percent", "bps", "mismatch"),
        ("USD", None, "unverified"),
        (None, "USD", "unverified"),
    ])
    def test_three_state_verdicts(self, a, b, expected):
        assert compare_units(a, b)[0] == expected

    def test_different_spellings_are_not_silently_equated(self):
        """"USD" vs "dollars" needs evidence we do not have; it stays a
        declared difference rather than an invented equivalence."""
        assert compare_units("USD", "dollars")[0] == "mismatch"


# --------------------------------------------------------------- adapter layer

class TestKalshiAdapterEmitsCanonicalOperators:
    @pytest.mark.parametrize("strike_type,canonical", [
        ("greater", ">"),
        ("greater_or_equal", ">="),
        ("less", "<"),
        ("less_or_equal", "<="),
        ("between", "between"),
    ])
    def test_documented_tokens_are_translated(self, strike_type, canonical):
        contract = kalshi_contract(strike_type=strike_type)
        assert contract.threshold_operator == canonical
        # The venue's own token stays in the evidence fields.
        assert contract.resolution_method == strike_type
        assert contract.raw["strike_type"] == strike_type

    def test_threshold_is_unchanged_by_the_translation(self):
        contract = kalshi_contract(strike_type="greater", floor_strike=4.0)
        assert contract.threshold == Decimal("4.0")

    @pytest.mark.parametrize("strike_type", ["structured", "functional",
                                             "custom", "unknown_future_token"])
    def test_unmapped_tokens_are_preserved_not_invented(self, strike_type):
        contract = kalshi_contract(strike_type=strike_type,
                                   floor_strike=None)
        assert contract.threshold_operator == strike_type
        assert read_operator("kalshi", contract.threshold_operator).state == \
            UNSUPPORTED

    def test_absent_strike_type_stays_absent(self):
        payload = kalshi_payload()
        payload.pop("strike_type")
        contract = KalshiAdapter(
            "https://external-api.kalshi.com/trade-api/v2"
        )._normalize_market(payload)
        assert contract.threshold_operator is None
        assert read_operator("kalshi", contract.threshold_operator).state == \
            ABSENT

    def test_polymarket_operator_remains_unknown(self):
        contract = polymarket_contract()
        assert contract.threshold_operator is None
        assert contract.threshold == Decimal("4.0")


# ------------------------------------------------------- deterministic matcher

class TestDeterministicTriState:
    def _pair(self):
        """The D25 reproduction: equal threshold, equal settlement, one leg
        that cannot declare an operator."""
        return kalshi_contract(), polymarket_contract()

    def test_same_threshold_declared_vs_unknown_is_unverified(self):
        a, b = self._pair()
        res = deterministic_match(a, b)
        assert res.passed is True                     # NOT a mismatch any more
        assert "threshold_operator" not in res.checks  # and not a pass either
        assert "threshold_operator" in res.unverified
        assert res.unverified["threshold_operator"]
        assert res.confidence < 1.0                   # unverified lowers it
        assert MATCHER_VERSION == res.version

    def test_legacy_raw_token_pair_is_not_a_false_mismatch(self):
        """A recorded row holding Kalshi's raw token must compare equal to a
        freshly normalized one (both read as ">=", the unit is unverified)."""
        a = kalshi_contract(strike_type="greater_or_equal")
        b = dataclasses.replace(kalshi_contract(strike_type="greater_or_equal"),
                                venue="forecast_ex", threshold_operator=">=",
                                units="%")
        res = deterministic_match(a, b)
        assert res.checks.get("threshold_operator") is True
        assert res.checks.get("units") is None        # declared vs absent
        assert "units" in res.unverified

    def test_declared_operators_that_differ_are_still_a_final_mismatch(self):
        a = kalshi_contract(strike_type="greater")
        b = dataclasses.replace(polymarket_contract(), threshold_operator="<")
        res = deterministic_match(a, b)
        assert res.passed is False
        assert res.checks["threshold_operator"] is False
        assert "threshold_operator" not in res.unverified

    def test_both_legs_declaring_the_same_operator_is_a_pass(self):
        a = kalshi_contract(strike_type="greater")
        b = dataclasses.replace(polymarket_contract(), threshold_operator=">")
        res = deterministic_match(a, b)
        assert res.passed is True
        assert res.checks["threshold_operator"] is True
        assert res.unverified == {}

    def test_units_declared_on_one_leg_only_is_unverified_not_rejected(self):
        a = kalshi_contract(strike_type="greater")
        b = dataclasses.replace(polymarket_contract(), units="percent")
        res = deterministic_match(a, b)
        assert res.passed is True
        assert "units" in res.unverified

    def test_no_threshold_on_either_leg_stays_a_vacuous_pass(self):
        a = make_contract(venue="kalshi", threshold=None)
        b = make_contract(venue="polymarket_us", threshold=None)
        res = deterministic_match(a, b)
        assert res.passed is True
        assert res.unverified == {}
        assert res.checks["threshold_operator"] is True

    def test_unverified_never_marks_the_pair_failed(self):
        res = DeterministicResult(passed=True)
        res.unverified["units"] = "why"
        assert res.passed is True
        assert res.confidence == 0.0        # nothing verified, nothing failed


# ------------------------------------------------- validator + certificate

class TestUnverifiedNeverApproved:
    def _det(self):
        a = kalshi_contract()
        b = polymarket_contract()
        return a, b, deterministic_match(a, b)

    def test_confidence_of_one_is_still_review_not_approved(self):
        a, b, det = self._det()
        llm = LLMMatcher(client=None)                       # no LLM configured
        outcome = validate_match(a, b, det, not_consulted("unverified field"))
        assert outcome.status == "REVIEW"
        assert any("threshold_operator" in r for r in outcome.reasons)

    def test_a_confident_valid_llm_verdict_cannot_approve_it(self):
        a, b, det = self._det()
        from prediction_arb.matching.llm_matcher import validate_llm_output
        llm = validate_llm_output(_verdict(match=True, confidence=0.999))
        assert llm.valid is True
        outcome = validate_match(a, b, det, llm)
        assert outcome.status == "REVIEW"
        assert any("never APPROVED" in r for r in outcome.reasons)

    def test_positive_control_same_pair_approves_once_operator_is_known(self):
        """Nothing about the pair blocks approval except the unknown
        operator: declare it and the same evidence approves."""
        a, b, _ = self._det()
        b = dataclasses.replace(b, threshold_operator=">")
        det = deterministic_match(a, b)
        from prediction_arb.matching.llm_matcher import validate_llm_output
        llm = validate_llm_output(_verdict(match=True, confidence=0.999))
        assert validate_match(a, b, det, llm).status == "APPROVED"

    def test_certificate_records_and_renders_the_unverified_field(self):
        a, b, det = self._det()
        llm = not_consulted("threshold_operator unverified")
        outcome = validate_match(a, b, det, llm)
        cert = build_certificate(a, b, det, llm, outcome)
        assert cert.status == "REVIEW"
        assert "threshold_operator" in cert.unverified
        assert cert.checks.get("threshold_operator") is None
        assert "UNVERIFIED" in cert.render()
        assert "not a mismatch" in cert.render()
        assert json.loads(cert.to_json())["unverified"]

    def test_deterministic_reject_still_rejects(self):
        a = kalshi_contract(strike_type="greater")
        b = dataclasses.replace(polymarket_contract(), threshold_operator="<")
        det = deterministic_match(a, b)
        llm = not_consulted("n/a")
        assert validate_match(a, b, det, llm).status == "REJECTED"


# ------------------------------------------------------------------ pipeline

class TestPipelineD25:
    def _run(self, contracts, recorder):
        return asyncio.run(run_matching(contracts, recorder.matcher))

    def test_unverified_pair_is_review_without_spending_llm_budget(self):
        recorder = RecordingLLM()
        approved, result = self._run(
            [kalshi_contract(), polymarket_contract()], recorder)
        assert result.pairs_considered == 1
        assert result.pairs_deterministic_pass == 1
        assert result.deterministic_rejects == {}       # was: {threshold_operator: 1}
        assert result.deterministic_unverified == {"threshold_operator": 1}
        assert [c.status for c in result.certificates] == ["REVIEW"]
        assert approved == {}
        assert result.certificates[0].unverified
        # A pair no verdict can approve must not consume the bounded budget.
        assert recorder.calls == 0
        assert any("semantic stage not consulted" in r
                   for r in result.certificates[0].reasons)

    def test_known_operator_pair_still_approved_and_llm_consulted(self):
        recorder = RecordingLLM()
        b = dataclasses.replace(polymarket_contract(), threshold_operator=">")
        approved, result = self._run([kalshi_contract(), b], recorder)
        assert result.deterministic_unverified == {}
        assert [c.status for c in result.certificates] == ["APPROVED"]
        assert len(approved) == 1
        assert recorder.calls == 1

    def test_declared_mismatch_is_still_rejected_by_the_pipeline(self):
        recorder = RecordingLLM()
        b = dataclasses.replace(polymarket_contract(), threshold_operator="<")
        approved, result = self._run([kalshi_contract(), b], recorder)
        assert result.deterministic_rejects == {"threshold_operator": 1}
        assert result.deterministic_unverified == {}
        # Deterministic rejects never produce a certificate (unchanged): the
        # counter and the empty approved set are the record.
        assert result.certificates == []
        assert approved == {}
        assert recorder.calls == 0
