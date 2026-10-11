"""Stage-1 deterministic contract matching filter (§18).

Rejects candidate pairs on any hard mismatch. Only survivors may be sent
to the LLM (stage 2). A REJECT here is FINAL — the LLM cannot overrule
deterministic filters (Principle B).

A check can also come back **unverified** (2026-10-11, D25): the two legs
declare values that cannot be compared (one leg declares an operator or a
unit and the other declares none, or a venue token has no documented
translation). Unverified is NOT a mismatch and NOT a pass: it is recorded in
:attr:`DeterministicResult.unverified` with its reason, the pair survives to
the certificate as REVIEW, and no LLM verdict may ever turn it into an
APPROVED match. Treating "cannot verify" as "does not match" silently deleted
possibly-equivalent cross-venue pairs from the pipeline — exactly the
false-negative D25 documents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ..core.clock import ensure_utc
from ..core.types import NormalizedContract
from ..normalization.thresholds import compare_operators, compare_units

MATCHER_VERSION = "det-1.1.0"

# Venues may quote the same real-world event with slightly different
# expiration timestamps (e.g. "3pm ET" vs "end of day"). Tolerance is
# deliberately small; anything larger must be rejected.
SETTLEMENT_TOLERANCE = timedelta(hours=6)


@dataclass
class DeterministicResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    #: Checks that could not be evaluated (declared-vs-unknown operator or
    #: units). Maps check name -> the reason it is unverifiable. A non-empty
    #: map means REVIEW only: an unverified field can never be APPROVED.
    unverified: dict[str, str] = field(default_factory=dict)
    version: str = MATCHER_VERSION

    @property
    def confidence(self) -> float:
        """Share of checks that passed. Unverified checks lower it: they are
        not evidence of agreement."""
        total = len(self.checks) + len(self.unverified)
        if not total:
            return 0.0
        return sum(1 for ok in self.checks.values() if ok) / total


def _fail(res: DeterministicResult, check: str, reason: str) -> None:
    res.checks[check] = False
    res.reasons.append(reason)
    res.passed = False


def _ok(res: DeterministicResult, check: str) -> None:
    res.checks[check] = True


def _unverified(res: DeterministicResult, check: str, reason: str) -> None:
    """Record a check that neither passed nor failed — it could not be
    evaluated against evidence. Never sets ``passed=False`` (it is not a
    mismatch) and never sets ``checks[check]`` (it is not a pass either)."""
    res.unverified[check] = reason
    res.reasons.append(reason)


def _apply_verdict(res: DeterministicResult, check: str,
                   verdict: str, reason: str) -> None:
    if verdict == "match":
        _ok(res, check)
    elif verdict == "mismatch":
        _fail(res, check, reason)
    else:
        _unverified(res, check, reason)


def deterministic_match(a: NormalizedContract,
                        b: NormalizedContract) -> DeterministicResult:
    res = DeterministicResult(passed=True)

    # outcome type
    if a.outcome_type != b.outcome_type:
        _fail(res, "outcome_type", f"{a.outcome_type} != {b.outcome_type}")
    else:
        _ok(res, "outcome_type")

    # payout & currency
    if a.payout != b.payout or a.currency != b.currency:
        _fail(res, "payout", f"{a.payout}{a.currency} != {b.payout}{b.currency}")
    else:
        _ok(res, "payout")

    # settlement date/time
    if a.settlement_time is None or b.settlement_time is None:
        _fail(res, "settlement_time", "missing settlement time on a leg")
    else:
        delta = abs(ensure_utc(a.settlement_time) - ensure_utc(b.settlement_time))
        if delta > SETTLEMENT_TOLERANCE:
            _fail(res, "settlement_time",
                  f"settlement times differ by {delta} (> {SETTLEMENT_TOLERANCE})")
        else:
            _ok(res, "settlement_time")

    # threshold + units (when either declares one)
    # §18 revised 2026-10-11 (D25): the operator and units checks are
    # three-state. A declared-vs-declared difference is still a final
    # mismatch; a declared-vs-unknown field is unverifiable, not a mismatch.
    if a.threshold is not None or b.threshold is not None:
        if a.threshold != b.threshold:
            _fail(res, "threshold", f"{a.threshold} != {b.threshold}")
        else:
            _ok(res, "threshold")
        _apply_verdict(res, "threshold_operator", *compare_operators(
            a.venue, a.threshold_operator, b.venue, b.threshold_operator))
        _apply_verdict(res, "units", *compare_units(a.units, b.units))
    else:
        _ok(res, "threshold")
        _ok(res, "threshold_operator")
        _ok(res, "units")

    # resolution source: both must be present; textual identity is NOT
    # required (venues phrase rules differently) but emptiness is fatal
    # and gross length mismatch is suspicious → reject, let humans review.
    if not a.resolution_source or not b.resolution_source:
        _fail(res, "resolution_source", "missing resolution source text")
    else:
        _ok(res, "resolution_source")

    # settlement rules compatibility: early-resolution asymmetry is a
    # material rule difference (one contract can settle early, the other
    # cannot → payoffs can diverge in time).
    if bool(a.early_resolution_rules) != bool(b.early_resolution_rules):
        _fail(res, "early_resolution",
              "early-resolution rules differ between contracts")
    else:
        _ok(res, "early_resolution")

    return res
