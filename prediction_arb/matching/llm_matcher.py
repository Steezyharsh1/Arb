"""Stage-2 LLM semantic matcher (§19, §87–§89).

The LLM is UNTRUSTED input:
  * it only sees pairs that survived deterministic filtering;
  * its output is schema-validated (§88) — malformed output == NO MATCH;
  * it can only say "not equivalent" or "plausibly equivalent"; the final
    authority is deterministic code + the match certificate;
  * results are cached by (contract_version_a, contract_version_b) and
    only re-run when contract rules change (§87).

When no client is injected, the matcher returns an invalid result with
reason NO_LLM_CONFIGURED — the conservative default (false negatives
preferred, §19): the pair goes to human REVIEW, never auto-APPROVED.
A real client is wired from the mode env file via
`matching/llm_client.py` (§97, docs/API_SETUP.md).

A per-cycle comparison budget (`max_comparisons_per_cycle`, reset by
`begin_cycle()` once per scan cycle) bounds how many NEW LLM calls a
single cycle can spend; pairs beyond the budget stay un-cached and end
in REVIEW rather than running an unbounded bill. Cache hits never
consume budget.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..core.types import NormalizedContract

LLM_PROMPT_VERSION = "match-prompt-1.0.0"

PROMPT_TEMPLATE = """You are comparing two prediction-market contracts to decide whether they are ECONOMICALLY EQUIVALENT: identical event, identical outcome direction, identical settlement terms, so that holding YES on one and NO on the other is a perfect hedge.

Rules:
- When uncertain, answer match=false. False negatives are preferable to false positives.
- Do not infer facts not present in the structured data.
- Respond with ONLY a JSON object matching the schema shown.

CONTRACT A:
{contract_a}

CONTRACT B:
{contract_b}

Respond with JSON:
{{"match": bool, "confidence": float 0..1, "reasoning_summary": str,
"event_identity": bool, "outcome_identity": bool, "settlement_identity": bool,
"resolution_identity": bool, "threshold_identity": bool, "time_identity": bool,
"payout_identity": bool, "ambiguities": [str]}}
"""

_REQUIRED_FIELDS = {
    "match": bool, "confidence": float, "reasoning_summary": str,
    "event_identity": bool, "outcome_identity": bool,
    "settlement_identity": bool, "resolution_identity": bool,
    "threshold_identity": bool, "time_identity": bool,
    "payout_identity": bool, "ambiguities": list,
}

# Async callable: prompt -> raw model text
LLMClient = Callable[[str], Awaitable[str]]


@dataclass
class LLMMatchResult:
    match: bool
    confidence: float
    reasoning_summary: str
    field_identities: dict[str, bool] = field(default_factory=dict)
    ambiguities: list[str] = field(default_factory=list)
    model_version: str = "none"
    prompt_version: str = LLM_PROMPT_VERSION
    valid: bool = False
    error: str = ""


#: Marker on the error of a result that was deliberately not produced. The
#: certificate must say "not consulted" rather than imply a verdict existed.
NOT_CONSULTED_PREFIX = "NOT_CONSULTED"


def not_consulted(reason: str) -> LLMMatchResult:
    """A clearly-labelled "no verdict was requested" result.

    Used when the deterministic stage has already decided the pair cannot be
    approved (an unverified field, D25): spending the bounded per-cycle
    comparison budget on a pair that no verdict could approve would only
    starve candidates that can be approved. The certificate records that the
    semantic stage was deliberately not consulted, rather than inventing a
    verdict.
    """
    return LLMMatchResult(match=False, confidence=0.0, reasoning_summary="",
                          valid=False,
                          error=f"{NOT_CONSULTED_PREFIX}: {reason}")


def _contract_payload(c: NormalizedContract) -> dict[str, Any]:
    return {
        "venue": c.venue,
        "event_description": c.event_description,
        "outcome": c.outcome,
        "outcome_type": c.outcome_type.value,
        "settlement_time_utc": c.settlement_time.isoformat() if c.settlement_time else None,
        "resolution_source": c.resolution_source,
        "resolution_method": c.resolution_method,
        "threshold": str(c.threshold) if c.threshold is not None else None,
        "threshold_operator": c.threshold_operator,
        "units": c.units,
        "payout": str(c.payout),
        "currency": c.currency,
        "early_resolution_rules": c.early_resolution_rules,
        "cancellation_rules": c.cancellation_rules,
    }


def validate_llm_output(raw: str) -> LLMMatchResult:
    """Schema-validate untrusted LLM output (§88). Any defect → no match."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return LLMMatchResult(False, 0.0, "", valid=False,
                              error="malformed JSON")
    if not isinstance(data, dict):
        return LLMMatchResult(False, 0.0, "", valid=False,
                              error="not a JSON object")
    for key, typ in _REQUIRED_FIELDS.items():
        if key not in data:
            return LLMMatchResult(False, 0.0, "", valid=False,
                                  error=f"missing field {key!r}")
        if typ is float:
            if not isinstance(data[key], (int, float)) or isinstance(data[key], bool):
                return LLMMatchResult(False, 0.0, "", valid=False,
                                      error=f"field {key!r} not numeric")
        elif not isinstance(data[key], typ):
            return LLMMatchResult(False, 0.0, "", valid=False,
                                  error=f"field {key!r} wrong type")
    conf = float(data["confidence"])
    if not (0.0 <= conf <= 1.0):
        return LLMMatchResult(False, 0.0, "", valid=False,
                              error="confidence outside [0,1]")
    identities = {k: bool(data[k]) for k in (
        "event_identity", "outcome_identity", "settlement_identity",
        "resolution_identity", "threshold_identity", "time_identity",
        "payout_identity")}
    match = bool(data["match"])
    # contradiction check (§88): match=true requires all identities true
    if match and not all(identities.values()):
        return LLMMatchResult(False, 0.0, data.get("reasoning_summary", ""),
                              field_identities=identities, valid=False,
                              error="contradictory: match=true with a "
                                    "failed identity field")
    return LLMMatchResult(
        match=match, confidence=conf,
        reasoning_summary=str(data["reasoning_summary"])[:2000],
        field_identities=identities,
        ambiguities=[str(a)[:500] for a in data["ambiguities"]][:20],
        valid=True,
    )


class LLMMatcher:
    def __init__(self, client: Optional[LLMClient] = None,
                 model_version: str = "none",
                 max_comparisons_per_cycle: Optional[int] = None):
        self.client = client
        self.model_version = model_version
        self.max_comparisons_per_cycle = max_comparisons_per_cycle
        self._cycle_comparisons = 0
        # Cache key includes contract versions + matcher/prompt/model
        # versions for correctness (§87, Arb.9).
        self._cache: dict[tuple[str, str, str, str, str], LLMMatchResult] = {}

    def begin_cycle(self) -> None:
        """Reset the per-cycle comparison budget. `run_matching` calls
        this once per scan cycle so paper/historical cycles cannot spend
        an unbounded number of LLM calls; cache hits never count."""
        self._cycle_comparisons = 0

    def _cache_key(self, a: NormalizedContract,
                   b: NormalizedContract) -> tuple[str, str, str, str, str]:
        from .deterministic_matcher import MATCHER_VERSION as _DET_VER

        ka = f"{a.venue}:{a.contract_id}:{a.contract_version}"
        kb = f"{b.venue}:{b.contract_id}:{b.contract_version}"
        if ka <= kb:
            first, second = ka, kb
        else:
            first, second = kb, ka
        return (first, second, _DET_VER, LLM_PROMPT_VERSION, self.model_version)

    async def compare(self, a: NormalizedContract,
                      b: NormalizedContract) -> LLMMatchResult:
        key = self._cache_key(a, b)
        if key in self._cache:
            return self._cache[key]

        if self.client is None:
            result = LLMMatchResult(
                match=False, confidence=0.0,
                reasoning_summary="",
                model_version="none", valid=False,
                error="NO_LLM_CONFIGURED: conservative default is no-match. "
                      "Configure an LLM client to enable semantic matching "
                      "(LLM_API_BASE / LLM_API_KEY / LLM_MODEL in the mode "
                      "env file — see docs/API_SETUP.md).")
        elif (self.max_comparisons_per_cycle is not None
                and self._cycle_comparisons >= self.max_comparisons_per_cycle):
            # Fail closed, and deliberately do NOT cache: a later cycle
            # with budget available must be able to look at this pair.
            return LLMMatchResult(
                match=False, confidence=0.0, reasoning_summary="",
                model_version=self.model_version, valid=False,
                error=f"LLM comparison budget exhausted for this cycle "
                      f"({self.max_comparisons_per_cycle}); pair left for "
                      f"human REVIEW (raise llm."
                      f"max_comparisons_per_cycle in settings.yaml if this "
                      f"is expected).")
        else:
            self._cycle_comparisons += 1
            prompt = PROMPT_TEMPLATE.format(
                contract_a=json.dumps(_contract_payload(a), indent=2),
                contract_b=json.dumps(_contract_payload(b), indent=2))
            try:
                raw = await self.client(prompt)
            except Exception as exc:  # LLM outage == no match, never crash
                result = LLMMatchResult(False, 0.0, "", valid=False,
                                        error=f"LLM call failed: {exc}")
            else:
                result = validate_llm_output(raw)
            result.model_version = self.model_version

        self._cache[key] = result
        return result
