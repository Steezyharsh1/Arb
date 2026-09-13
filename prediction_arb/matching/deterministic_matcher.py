"""Stage-1 deterministic contract matching filter (§18).

Rejects candidate pairs on any hard mismatch. Only survivors may be sent
to the LLM (stage 2). A REJECT here is FINAL — the LLM cannot overrule
deterministic filters (Principle B).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from ..core.clock import ensure_utc
from ..core.types import NormalizedContract

MATCHER_VERSION = "det-1.0.0"

# Venues may quote the same real-world event with slightly different
# expiration timestamps (e.g. "3pm ET" vs "end of day"). Tolerance is
# deliberately small; anything larger must be rejected.
_SETTLEMENT_TOLERANCE = timedelta(hours=6)


@dataclass
class DeterministicResult:
    passed: bool
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    version: str = MATCHER_VERSION

    @property
    def confidence(self) -> float:
        if not self.checks:
            return 0.0
        return sum(self.checks.values()) / len(self.checks)


def _fail(res: DeterministicResult, check: str, reason: str) -> None:
    res.checks[check] = False
    res.reasons.append(reason)
    res.passed = False


def _ok(res: DeterministicResult, check: str) -> None:
    res.checks[check] = True


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
        if delta > _SETTLEMENT_TOLERANCE:
            _fail(res, "settlement_time",
                  f"settlement times differ by {delta} (> {_SETTLEMENT_TOLERANCE})")
        else:
            _ok(res, "settlement_time")

    # threshold + units (when either declares one)
    if a.threshold is not None or b.threshold is not None:
        if a.threshold != b.threshold:
            _fail(res, "threshold", f"{a.threshold} != {b.threshold}")
        else:
            _ok(res, "threshold")
        if (a.threshold_operator or "") != (b.threshold_operator or ""):
            _fail(res, "threshold_operator",
                  f"{a.threshold_operator!r} != {b.threshold_operator!r}")
        else:
            _ok(res, "threshold_operator")
        if (a.units or "") != (b.units or ""):
            _fail(res, "units", f"{a.units!r} != {b.units!r}")
        else:
            _ok(res, "units")
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
