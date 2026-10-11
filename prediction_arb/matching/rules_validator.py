"""Final deterministic rules validation before a match certificate is
issued (§18–§20). Runs AFTER the LLM and can only reject/downgrade —
never approve something the deterministic matcher failed."""
from __future__ import annotations

from dataclasses import dataclass

from ..core.types import Independence, NormalizedContract
from ..provenance.independence import contracts_independent
from .deterministic_matcher import DeterministicResult
from .llm_matcher import NOT_CONSULTED_PREFIX, LLMMatchResult


@dataclass
class ValidationOutcome:
    status: str            # APPROVED / REJECTED / REVIEW
    independence: Independence
    reasons: list[str]


def validate_match(a: NormalizedContract, b: NormalizedContract,
                   det: DeterministicResult,
                   llm: LLMMatchResult,
                   min_llm_confidence: float = 0.97) -> ValidationOutcome:
    reasons: list[str] = []
    independence = contracts_independent(a, b)

    if not det.passed:
        return ValidationOutcome("REJECTED", independence,
                                 ["deterministic filter failed"] + det.reasons)

    # §18 revised 2026-10-11 (D25): a check that could not be evaluated (one
    # leg declares an operator or a unit and the other declares none) is not
    # a mismatch, but it is also not agreement. No LLM verdict — however
    # confident — may approve a pair whose declared economics it cannot
    # compare: the pair is REVIEW only, and the reason names the field.
    if det.unverified:
        reasons.extend(
            f"unverified deterministic field: {check}: {why}"
            for check, why in det.unverified.items())
        if not llm.valid and llm.error.startswith(NOT_CONSULTED_PREFIX):
            # run_matching deliberately skips the semantic budget for a pair
            # no verdict can approve; say so instead of implying a verdict.
            reasons.append(f"semantic stage not consulted: {llm.error}")
        reasons.append("a pair with an unverifiable deterministic field is "
                       "never APPROVED; human review required")
        return ValidationOutcome("REVIEW", independence, reasons)

    if not llm.valid:
        reasons.append(f"LLM result invalid/unavailable: {llm.error}")
        return ValidationOutcome("REVIEW", independence, reasons)

    if not llm.match:
        return ValidationOutcome("REJECTED", independence,
                                 ["LLM: not equivalent"] + llm.ambiguities)

    if llm.confidence < min_llm_confidence:
        reasons.append(
            f"LLM confidence {llm.confidence:.3f} < {min_llm_confidence}")
        return ValidationOutcome("REVIEW", independence, reasons)

    # LLM may claim match, but if any deterministic field disagrees the
    # deterministic result wins (spec test case 5 generalization).
    if not all(det.checks.values()):
        failed = [k for k, v in det.checks.items() if not v]
        return ValidationOutcome("REJECTED", independence,
                                 [f"deterministic checks failed: {failed}"])

    # Provenance gate (§33, defect D15): ONLY a proven-INDEPENDENT
    # relationship may be APPROVED. Same-venue frontends (e.g. Kalshi
    # contracts reached through Webull) are not arbitrage at all, and an
    # APPROVED certificate saying otherwise would poison the human match
    # review — they go to REVIEW like every other unresolved relationship.
    # `search_event` additionally refuses to evaluate non-INDEPENDENT
    # pairs, so this closes the certificate side of the same hole.
    if independence != Independence.INDEPENDENT:
        reasons.append(f"venue relationship {independence.value} is not "
                       f"proven INDEPENDENT → REVIEW, never APPROVED")
        return ValidationOutcome("REVIEW", independence, reasons)

    return ValidationOutcome("APPROVED", independence, reasons)
