"""Phase 5 / D9: real LLM client wiring (§19, §97).

`main.py` used to hardcode `LLMMatcher(client=None)`, so every ambiguous
pair landed in REVIEW and `search_event` — which requires APPROVED
certificates — never evaluated anything, even after D6 produced
candidates. These tests pin the wiring: config comes only from the mode
env dict; absence is conservative (no LLM ⇒ REVIEW); partial
configuration refuses startup; the transport raises on anything that is
not a well-formed 200 response (which the matcher turns into a
conservative no-match); and a per-cycle budget bounds new LLM calls
without poisoning the cache.

Mutation note: `test_run_matching_resets_the_budget_each_cycle` fails if
`run_matching` stops calling `begin_cycle()`; the config-refusal tests
fail if partial configuration is silently ignored.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from .conftest import make_contract

from prediction_arb.matching.llm_client import (
    LLMConfigError,
    LLMTransportError,
    build_llm_config,
    build_llm_matcher,
    chat_completion,
)
from prediction_arb.matching.llm_matcher import LLMMatcher
from prediction_arb.modes.shared import run_matching

FULL_ENV = {
    "LLM_API_BASE": "https://api.example.test/v1",
    "LLM_API_KEY": "sk-test-not-a-real-key",
    "LLM_MODEL": "test-model-1",
}


def _verdict_json(match: bool = True, confidence: float = 0.998) -> str:
    return json.dumps({
        "match": match, "confidence": confidence,
        "reasoning_summary": "same real-world event",
        "event_identity": match, "outcome_identity": True,
        "settlement_identity": True, "resolution_identity": True,
        "threshold_identity": True, "time_identity": True,
        "payout_identity": True, "ambiguities": [],
    })


def _chat_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": content}}]},
        request=httpx.Request("POST", "https://api.example.test/v1/"
                                     "chat/completions"))


class TestBuildLLMConfig:
    def test_absent_config_is_none_not_error(self):
        assert build_llm_config({}) is None
        assert build_llm_config({"UNRELATED": "x"}) is None

    def test_full_config_builds(self):
        cfg = build_llm_config(FULL_ENV)
        assert cfg is not None
        assert cfg.api_base == FULL_ENV["LLM_API_BASE"]
        assert cfg.api_key == FULL_ENV["LLM_API_KEY"]
        assert cfg.model == FULL_ENV["LLM_MODEL"]
        assert cfg.timeout_seconds == 60.0

    @pytest.mark.parametrize("missing", ["LLM_API_BASE", "LLM_API_KEY",
                                         "LLM_MODEL"])
    def test_partial_config_refuses_and_names_the_gap(self, missing):
        env = {k: v for k, v in FULL_ENV.items() if k != missing}
        with pytest.raises(LLMConfigError) as excinfo:
            build_llm_config(env)
        assert missing in str(excinfo.value)

    def test_bad_base_url_refused(self):
        env = dict(FULL_ENV, LLM_API_BASE="api.example.test/v1")
        with pytest.raises(LLMConfigError):
            build_llm_config(env)

    def test_bad_timeout_refused(self):
        with pytest.raises(LLMConfigError):
            build_llm_config(dict(FULL_ENV, LLM_TIMEOUT_SECONDS="soon"))
        with pytest.raises(LLMConfigError):
            build_llm_config(dict(FULL_ENV, LLM_TIMEOUT_SECONDS="0"))

    def test_timeout_override(self):
        cfg = build_llm_config(dict(FULL_ENV, LLM_TIMEOUT_SECONDS="12.5"))
        assert cfg.timeout_seconds == 12.5


class TestChatCompletion:
    def _transport(self, responder):
        return httpx.MockTransport(
            lambda request: responder(request))

    def test_success_returns_content_and_sends_auth_and_model(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = json.loads(request.content)
            return _chat_response(_verdict_json())

        out = asyncio.run(chat_completion(
            build_llm_config(FULL_ENV), "PROMPT",
            transport=self._transport(handler)))
        assert out == _verdict_json()
        assert seen["auth"] == f"Bearer {FULL_ENV['LLM_API_KEY']}"
        assert seen["body"]["model"] == FULL_ENV["LLM_MODEL"]
        assert seen["body"]["messages"][0]["content"] == "PROMPT"

    def test_full_endpoint_url_is_not_double_suffixed(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return _chat_response(_verdict_json())

        cfg = build_llm_config(dict(
            FULL_ENV, LLM_API_BASE="https://api.example.test/v1/chat/completions"))
        asyncio.run(chat_completion(cfg, "P", transport=self._transport(handler)))
        assert seen["url"].rstrip("/") == \
            "https://api.example.test/v1/chat/completions"

    def test_http_error_raises_transport_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text='{"error": "bad key"}',
                                  request=httpx.Request("POST", "https://x"))
        with pytest.raises(LLMTransportError, match="HTTP 401"):
            asyncio.run(chat_completion(
                build_llm_config(FULL_ENV), "P",
                transport=self._transport(handler)))

    def test_non_json_body_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>gateway</html>",
                                  request=httpx.Request("POST", "https://x"))
        with pytest.raises(LLMTransportError, match="non-JSON"):
            asyncio.run(chat_completion(
                build_llm_config(FULL_ENV), "P",
                transport=self._transport(handler)))

    def test_unexpected_shape_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"nope": True},
                                  request=httpx.Request("POST", "https://x"))
        with pytest.raises(LLMTransportError, match="unexpected shape"):
            asyncio.run(chat_completion(
                build_llm_config(FULL_ENV), "P",
                transport=self._transport(handler)))


class TestMatcherWiring:
    def test_no_config_conservative_default(self):
        matcher, model = build_llm_matcher({})
        assert matcher.client is None
        assert model == "none"
        a = make_contract(venue="kalshi")
        b = make_contract(venue="forecast_ex", contract_id="B")
        res = asyncio.run(matcher.compare(a, b))
        assert not res.valid                      # REVIEW, never APPROVED
        assert "NO_LLM_CONFIGURED" in res.error

    def test_partial_config_refuses(self):
        with pytest.raises(LLMConfigError):
            build_llm_matcher({"LLM_API_KEY": "sk-only-key"})

    def test_wired_matcher_reaches_a_real_verdict(self):
        """The whole D9 chain: env → client → transport → schema-validated
        verdict, with the model label recorded for certificates."""
        def handler(request: httpx.Request) -> httpx.Response:
            return _chat_response(_verdict_json(match=True))
        matcher, model = build_llm_matcher(
            FULL_ENV, transport=httpx.MockTransport(handler))
        assert model == FULL_ENV["LLM_MODEL"]
        assert matcher.model_version == FULL_ENV["LLM_MODEL"]
        a = make_contract(venue="kalshi")
        b = make_contract(venue="forecast_ex", contract_id="B")
        res = asyncio.run(matcher.compare(a, b))
        assert res.valid and res.match and res.confidence == 0.998

    def test_garbage_verdict_is_no_match(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return _chat_response("I think they match, probably!")
        matcher, _ = build_llm_matcher(
            FULL_ENV, transport=httpx.MockTransport(handler))
        a = make_contract(venue="kalshi")
        b = make_contract(venue="forecast_ex", contract_id="B")
        res = asyncio.run(matcher.compare(a, b))
        assert not res.valid                      # malformed ⇒ no match (§88)


class TestComparisonBudget:
    def _counting_matcher(self, budget):
        calls = {"n": 0}

        async def client(prompt: str) -> str:
            calls["n"] += 1
            return _verdict_json(match=True)

        return LLMMatcher(client=client, model_version="m",
                          max_comparisons_per_cycle=budget), calls

    def _trio(self):
        return (make_contract(venue="kalshi", contract_id="A"),
                make_contract(venue="forecast_ex", contract_id="B"),
                make_contract(venue="polymarket_us", contract_id="C"))

    def test_budget_blocks_new_calls_but_not_cache_hits(self):
        matcher, calls = self._counting_matcher(budget=1)
        a, b, c = self._trio()
        first = asyncio.run(matcher.compare(a, b))
        assert first.valid and calls["n"] == 1
        # a different pair over the budget → fail closed to REVIEW
        second = asyncio.run(matcher.compare(a, c))
        assert not second.valid and "budget" in second.error
        assert calls["n"] == 1
        # but the cached pair is still served without a new call
        again = asyncio.run(matcher.compare(a, b))
        assert again.valid and calls["n"] == 1

    def test_budget_exhaustion_is_not_cached(self):
        matcher, calls = self._counting_matcher(budget=1)
        a, b, c = self._trio()
        asyncio.run(matcher.compare(a, b))
        blocked = asyncio.run(matcher.compare(a, c))
        assert not blocked.valid
        matcher.begin_cycle()                     # new cycle, fresh budget
        now = asyncio.run(matcher.compare(a, c))
        assert now.valid and calls["n"] == 2      # it really called again

    def test_run_matching_resets_the_budget_each_cycle(self):
        """run_matching opens a fresh budget window per cycle (§97). With
        budget=1 and a warmed cache, exactly one further new comparison
        is allowed; the third pair fails closed to REVIEW."""
        matcher, calls = self._counting_matcher(budget=1)
        a, b, c = self._trio()
        asyncio.run(matcher.compare(a, b))        # warm cache, budget spent
        approved, result = asyncio.run(run_matching([a, b, c], matcher))
        assert calls["n"] == 2      # (a,b) cache hit; (a,c) compared; (b,c) not
        assert len(approved) == 2
        reviews = [cert for cert in result.certificates if cert.status == "REVIEW"]
        assert len(reviews) == 1
        assert any("budget" in reason for reason in reviews[0].reasons)
