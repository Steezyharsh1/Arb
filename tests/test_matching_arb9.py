"""Arb.9 — deterministic prioritization, budget, cache, dedup, repeated cycles.

Covers:
- deterministic ordering (priority, tie-breakers, no discovery-order dependence)
- budget exhaustion remains eligible and not cached as rejected
- cache invalidation on contract_version and matching implementation versions
- duplicate persistence: unchanged REVIEW does not create unbounded duplicates
- repeated cycles: cache hits avoid LLM calls, dedup avoids DB bloat
"""
from __future__ import annotations

import asyncio
import json
import hashlib
from datetime import timedelta
from pathlib import Path

import pytest

from tests.conftest import SETTLEMENT_TEST_NOW, make_contract

from prediction_arb.matching.deterministic_matcher import MATCHER_VERSION
from prediction_arb.matching.llm_matcher import LLMMatcher, LLM_PROMPT_VERSION
from prediction_arb.modes.shared import run_matching
from prediction_arb.database.repository import Repository
from prediction_arb.core.types import Mode


def _verdict(match: bool = True, confidence: float = 0.998) -> str:
    return json.dumps({
        "match": match, "confidence": confidence,
        "reasoning_summary": "same",
        "event_identity": match, "outcome_identity": True,
        "settlement_identity": True, "resolution_identity": True,
        "threshold_identity": True, "time_identity": True,
        "payout_identity": True, "ambiguities": [],
    })


def _counting_matcher(budget=None, model_version="m1"):
    calls = {"n": 0, "prompts": []}

    async def client(prompt: str) -> str:
        calls["n"] += 1
        calls["prompts"].append(prompt)
        return _verdict(match=True)

    matcher = LLMMatcher(client=client, model_version=model_version,
                         max_comparisons_per_cycle=budget)
    return matcher, calls


class TestDeterministicOrdering:
    def test_priority_independent_first(self):
        """INDEPENDENT pairs should be processed before SAME_VENUE."""
        # Kalshi vs ForecastEx = INDEPENDENT, Kalshi vs Webull = SAME_VENUE
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Fed holds rates at 4.25% September")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Fed holds rates at 4.25% September")
        c = make_contract(venue="webull", contract_id="C",
                          event="Fed holds rates at 4.25% September",
                          underlying="kalshi_dcm")
        # 3 contracts, all cross-venue (different venue strings), so 3 pairs:
        # a-b (INDEPENDENT), a-c (SAME_VENUE), b-c (SAME_VENUE via kalshi_dcm?).
        # With budget=1, only first by priority should get LLM call.
        matcher, calls = _counting_matcher(budget=1)
        approved, result = asyncio.run(run_matching([a, b, c], matcher))
        # Should have 1 LLM call (budget 1) + 2 budget-exhausted REVIEW
        assert calls["n"] == 1
        assert len(result.certificates) == 3
        # Certificates are now in priority order, not discovery order.
        # First cert should be INDEPENDENT (kalshi/forecast_ex) if prioritization works.
        first = result.certificates[0]
        assert first.independence == "INDEPENDENT"
        # Remaining should be SAME_VENUE REVIEWs with budget error
        for cert in result.certificates[1:]:
            assert cert.independence == "SAME_VENUE" or cert.status == "REVIEW"

    def test_ordering_deterministic_under_reordering(self):
        """Same contracts in different input order should give same priority order."""
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Will Fed hold rates at 4.25% September")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Will Fed hold rates at 4.25% September",
                          settlement=SETTLEMENT_TEST_NOW + timedelta(days=1, hours=1))
        c = make_contract(venue="polymarket_us", contract_id="C",
                          event="Will Fed hold rates at 4.25% September",
                          settlement=SETTLEMENT_TEST_NOW + timedelta(days=1, hours=2))

        matcher1, _ = _counting_matcher(budget=10)
        approved1, result1 = asyncio.run(run_matching([a, b, c], matcher1))
        order1 = [(cert.contract_a, cert.contract_b) for cert in result1.certificates]

        matcher2, _ = _counting_matcher(budget=10)
        approved2, result2 = asyncio.run(run_matching([c, b, a], matcher2))
        order2 = [(cert.contract_a, cert.contract_b) for cert in result2.certificates]

        assert order1 == order2, f"order should be deterministic, got {order1} vs {order2}"

    def test_settlement_proximity_priority(self):
        """Closer settlement times should be prioritized."""
        # All settle within 6h window so deterministic passes
        base_time = SETTLEMENT_TEST_NOW + timedelta(days=1)
        base = make_contract(venue="kalshi", contract_id="BASE",
                             event="Fed holds at 4.25% Sep",
                             settlement=base_time)
        close = make_contract(venue="forecast_ex", contract_id="CLOSE",
                              event="Fed holds at 4.25% Sep",
                              settlement=base_time + timedelta(minutes=10))
        far = make_contract(venue="polymarket_us", contract_id="FAR",
                            event="Fed holds at 4.25% Sep",
                            settlement=base_time + timedelta(hours=5))

        matcher, calls = _counting_matcher(budget=1)
        # Both pairs share same keys, same independence, but different delta
        # Pairs: BASE-CLOSE (10m), BASE-FAR (5h), CLOSE-FAR (4h50m) = 3 pairs
        # With budget 1, closest should win
        approved, result = asyncio.run(run_matching([base, close, far], matcher))
        assert calls["n"] == 1
        # First certificate should be the closer settlement pair (BASE-CLOSE)
        first = result.certificates[0]
        assert "CLOSE" in first.contract_a or "CLOSE" in first.contract_b
        # And it should have smaller delta than others — check ordering
        # Second cert should have larger delta
        assert len(result.certificates) == 3

    def test_shared_keys_priority(self):
        """Pairs sharing thr keys should be prioritized over tok-only."""
        # Both have same settlement, same independence, but one shares thr
        a_thr = make_contract(venue="kalshi", contract_id="A_THR",
                              event="Will X exceed 10000 USD",
                              threshold=10000, threshold_op=">", units="USD")
        b_thr = make_contract(venue="forecast_ex", contract_id="B_THR",
                              event="Will X exceed 10000 USD",
                              threshold=10000, threshold_op=">", units="USD")
        a_tok = make_contract(venue="kalshi", contract_id="A_TOK",
                              event="Will Fed hold rates")
        b_tok = make_contract(venue="forecast_ex", contract_id="B_TOK",
                              event="Will Fed hold rates")

        matcher, calls = _counting_matcher(budget=1)
        approved, result = asyncio.run(run_matching([a_thr, b_thr, a_tok, b_tok], matcher))
        # With budget 1, thr pair should win because thr=20 vs tok=1
        assert calls["n"] == 1
        first = result.certificates[0]
        # Check that first involves thr contracts
        assert "THR" in first.contract_a or "THR" in first.contract_b


class TestBudgetExhaustion:
    def test_budget_exhaustion_not_cached(self):
        matcher, calls = _counting_matcher(budget=1)
        a = make_contract(venue="kalshi", contract_id="A")
        b = make_contract(venue="forecast_ex", contract_id="B")
        c = make_contract(venue="polymarket_us", contract_id="C")

        first = asyncio.run(matcher.compare(a, b))
        assert first.valid
        assert calls["n"] == 1

        second = asyncio.run(matcher.compare(a, c))
        assert not second.valid
        assert "budget" in second.error
        assert calls["n"] == 1  # no new call

        # After begin_cycle, budget resets and previously blocked pair can be called
        matcher.begin_cycle()
        third = asyncio.run(matcher.compare(a, c))
        assert third.valid
        assert calls["n"] == 2

    def test_budget_exhausted_pair_remains_eligible(self):
        """Budget-exhausted pairs should not be cached as rejected."""
        matcher, calls = _counting_matcher(budget=1)
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Fed holds rates at 4.25% September")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Fed holds rates at 4.25% September")
        c = make_contract(venue="polymarket_us", contract_id="C",
                          event="Fed holds rates at 4.25% September")

        # First cycle: only 1 call allowed, 3 pairs total
        approved, result = asyncio.run(run_matching([a, b, c], matcher))
        # 3 pairs, 1 approved (budget 1), 2 REVIEW with budget reason
        assert len([cert for cert in result.certificates if cert.status == "APPROVED"]) == 1
        reviews = [cert for cert in result.certificates if cert.status == "REVIEW"]
        assert len(reviews) == 2
        assert any("budget" in r for r in reviews[0].reasons)

        # Second cycle with fresh budget should be able to approve the previously blocked pair
        approved2, result2 = asyncio.run(run_matching([a, b, c], matcher))
        # After first cycle, cache has 1 entry, so second cycle: 1 cache hit (no budget),
        # 1 new call (budget 1), 1 budget block.
        # So we should still have REVIEWs, but cache ensures we progress.
        assert calls["n"] == 2  # second cycle did one more new call


class TestCacheInvalidation:
    def test_cache_key_includes_contract_version(self):
        matcher, calls = _counting_matcher()
        a_v1 = make_contract(venue="kalshi", contract_id="A",
                             event="Fed holds rates at 4.25% September")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Fed holds rates at 4.25% September")
        # First call
        asyncio.run(matcher.compare(a_v1, b))
        assert calls["n"] == 1
        # Same versions -> cache hit
        asyncio.run(matcher.compare(a_v1, b))
        assert calls["n"] == 1

        # Change contract version -> cache miss
        a_v2 = make_contract(venue="kalshi", contract_id="A",
                             event="Fed holds rates at 4.25% September")
        # Manually change version
        import dataclasses
        a_v2 = dataclasses.replace(a_v2, contract_version="v2")
        asyncio.run(matcher.compare(a_v2, b))
        assert calls["n"] == 2

    def test_cache_key_includes_model_version(self):
        a = make_contract(venue="kalshi", contract_id="A")
        b = make_contract(venue="forecast_ex", contract_id="B")

        matcher1, calls1 = _counting_matcher(model_version="model-v1")
        asyncio.run(matcher1.compare(a, b))
        assert calls1["n"] == 1
        # Same matcher, same versions -> hit
        asyncio.run(matcher1.compare(a, b))
        assert calls1["n"] == 1

        # Different model version -> should be miss (different cache key)
        matcher2, calls2 = _counting_matcher(model_version="model-v2")
        # Need to share cache? No, new matcher has empty cache, so it will call.
        # To test key sensitivity, we check that cache key includes model version
        # by inspecting _cache_key directly
        key1 = matcher1._cache_key(a, b)
        key2 = matcher2._cache_key(a, b)
        assert key1 != key2
        assert key1[-1] == "model-v1"
        assert key2[-1] == "model-v2"

    def test_cache_key_includes_matcher_and_prompt_versions(self):
        a = make_contract(venue="kalshi", contract_id="A")
        b = make_contract(venue="forecast_ex", contract_id="B")
        matcher, _ = _counting_matcher()
        key = matcher._cache_key(a, b)
        # key = (ka, kb, matcher_version, prompt_version, model_version)
        assert len(key) == 5
        assert key[2] == MATCHER_VERSION
        assert key[3] == LLM_PROMPT_VERSION

    def test_match_id_includes_versions_and_sorted(self):
        from prediction_arb.matching.match_certificates import build_certificate
        from prediction_arb.matching.deterministic_matcher import deterministic_match
        from prediction_arb.matching.llm_matcher import LLMMatchResult
        from prediction_arb.matching.rules_validator import validate_match

        a = make_contract(venue="kalshi", contract_id="A",
                          event="Fed holds rates at 4.25% September")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Fed holds rates at 4.25% September")
        det = deterministic_match(a, b)
        llm_res = LLMMatchResult(match=True, confidence=0.99, reasoning_summary="",
                                 model_version="model-v1", prompt_version=LLM_PROMPT_VERSION,
                                 valid=True)

        from prediction_arb.core.types import Independence
        from prediction_arb.matching.rules_validator import ValidationOutcome
        outcome = ValidationOutcome("APPROVED", Independence.INDEPENDENT, [])

        cert1 = build_certificate(a, b, det, llm_res, outcome)
        cert2 = build_certificate(b, a, det, llm_res, outcome)
        # Sorted, so same match_id regardless of order
        assert cert1.match_id == cert2.match_id
        assert cert1.contract_a <= cert1.contract_b

        # Different contract version -> different match_id
        import dataclasses
        a_v2 = dataclasses.replace(a, contract_version="v2")
        cert_v2 = build_certificate(a_v2, b, det, llm_res, outcome)
        assert cert_v2.match_id != cert1.match_id

        # Different model version -> different match_id
        llm_res_v2 = LLMMatchResult(match=True, confidence=0.99, reasoning_summary="",
                                    model_version="model-v2", prompt_version=LLM_PROMPT_VERSION,
                                    valid=True)
        cert_m2 = build_certificate(a, b, det, llm_res_v2, outcome)
        assert cert_m2.match_id != cert1.match_id


class TestDuplicatePersistence:
    def test_unchanged_review_not_duplicated(self, tmp_path):
        """Same unchanged pair should not create unbounded duplicate REVIEW rows."""
        db_path = tmp_path / "test.sqlite3"
        repo = Repository(db_path, Mode.PAPER)

        # Simulate saving same REVIEW certificate twice
        match_id = "test-match-id-1234"
        repo.save_match_certificate(
            match_id=match_id,
            contract_a="kalshi:A", contract_b="forecast_ex:B",
            score=0.5, status="REVIEW", independence="INDEPENDENT",
            certificate_json='{"status":"REVIEW"}',
            llm_version="model-v1", llm_prompt_version=LLM_PROMPT_VERSION,
            matcher_version=MATCHER_VERSION)

        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from prediction_arb.database.models import ContractMatchRow
        with Session(repo.engine) as s:
            rows = list(s.scalars(select(ContractMatchRow)))
            assert len(rows) == 1

        # Save same again (unchanged) -> should NOT create duplicate, and should skip write
        repo.save_match_certificate(
            match_id=match_id,
            contract_a="kalshi:A", contract_b="forecast_ex:B",
            score=0.5, status="REVIEW", independence="INDEPENDENT",
            certificate_json='{"status":"REVIEW"}',
            llm_version="model-v1", llm_prompt_version=LLM_PROMPT_VERSION,
            matcher_version=MATCHER_VERSION)

        with Session(repo.engine) as s:
            rows = list(s.scalars(select(ContractMatchRow)))
            assert len(rows) == 1, "unchanged REVIEW should not duplicate"

    def test_changed_verdict_updates(self, tmp_path):
        """Meaningful verdict change should update existing row."""
        db_path = tmp_path / "test2.sqlite3"
        repo = Repository(db_path, Mode.PAPER)
        match_id = "test-match-id-5678"

        repo.save_match_certificate(
            match_id=match_id,
            contract_a="kalshi:A", contract_b="forecast_ex:B",
            score=0.5, status="REVIEW", independence="INDEPENDENT",
            certificate_json='{"status":"REVIEW"}',
            llm_version="model-v1", llm_prompt_version=LLM_PROMPT_VERSION,
            matcher_version=MATCHER_VERSION)

        # Change verdict to APPROVED
        repo.save_match_certificate(
            match_id=match_id,
            contract_a="kalshi:A", contract_b="forecast_ex:B",
            score=0.99, status="APPROVED", independence="INDEPENDENT",
            certificate_json='{"status":"APPROVED"}',
            llm_version="model-v1", llm_prompt_version=LLM_PROMPT_VERSION,
            matcher_version=MATCHER_VERSION)

        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from prediction_arb.database.models import ContractMatchRow
        with Session(repo.engine) as s:
            rows = list(s.scalars(select(ContractMatchRow)))
            assert len(rows) == 1
            assert rows[0].validation_status == "APPROVED"

    def test_version_change_creates_new_row(self, tmp_path):
        """Version change should create new row (different match_id)."""
        db_path = tmp_path / "test3.sqlite3"
        repo = Repository(db_path, Mode.PAPER)

        # Two different match_ids (simulating version change)
        repo.save_match_certificate(
            match_id="match-v1",
            contract_a="kalshi:A", contract_b="forecast_ex:B",
            score=0.5, status="REVIEW", independence="INDEPENDENT",
            certificate_json='{"v":"1"}',
            llm_version="model-v1", llm_prompt_version=LLM_PROMPT_VERSION,
            matcher_version=MATCHER_VERSION)

        repo.save_match_certificate(
            match_id="match-v2",
            contract_a="kalshi:A", contract_b="forecast_ex:B",
            score=0.5, status="REVIEW", independence="INDEPENDENT",
            certificate_json='{"v":"2"}',
            llm_version="model-v2", llm_prompt_version=LLM_PROMPT_VERSION,
            matcher_version=MATCHER_VERSION)

        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from prediction_arb.database.models import ContractMatchRow
        with Session(repo.engine) as s:
            rows = list(s.scalars(select(ContractMatchRow)))
            assert len(rows) == 2, "version change should preserve history via new match_id"


class TestRepeatedCycles:
    def test_repeated_cycles_use_cache(self):
        """Second scan cycle should hit cache and not call LLM again for same versions."""
        a = make_contract(venue="kalshi", contract_id="A",
                          event="Fed holds rates at 4.25% September")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Fed holds rates at 4.25% September")

        matcher, calls = _counting_matcher(budget=10)

        # First cycle
        approved1, result1 = asyncio.run(run_matching([a, b], matcher))
        assert calls["n"] == 1
        assert len(result1.certificates) == 1

        # Second cycle with same contracts, same matcher (cache should hit)
        approved2, result2 = asyncio.run(run_matching([a, b], matcher))
        # No new LLM calls, cache hit
        assert calls["n"] == 1
        assert len(result2.certificates) == 1
        # Certificates should be same match_id
        assert result1.certificates[0].match_id == result2.certificates[0].match_id

    def test_repeated_cycles_dedup_db(self, tmp_path):
        """Repeated saves of same certificate should not create duplicates."""
        db_path = tmp_path / "test_repeat.sqlite3"
        repo = Repository(db_path, Mode.PAPER)

        a = make_contract(venue="kalshi", contract_id="A",
                          event="Fed holds rates at 4.25% September")
        b = make_contract(venue="forecast_ex", contract_id="B",
                          event="Fed holds rates at 4.25% September")

        matcher, _ = _counting_matcher(budget=10)

        for _ in range(5):
            approved, result = asyncio.run(run_matching([a, b], matcher))
            for cert in result.certificates:
                repo.save_match_certificate(
                    cert.match_id, cert.contract_a, cert.contract_b,
                    cert.llm_confidence, cert.status, cert.independence,
                    cert.to_json(), cert.llm_model_version,
                    cert.llm_prompt_version, cert.matcher_version)

        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from prediction_arb.database.models import ContractMatchRow
        with Session(repo.engine) as s:
            rows = list(s.scalars(select(ContractMatchRow)))
            # Should be 1 row, not 5, because dedup skips unchanged
            assert len(rows) == 1
