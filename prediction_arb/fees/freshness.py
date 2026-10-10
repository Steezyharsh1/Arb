"""The one mandatory fee-freshness policy (§24, §58, §98; defects D10/D19).

`config/settings.yaml` has always declared `fees.max_schedule_age_days: 30`
with the comment "refuse live trading on stale fee data", and
`FeeCalculator.schedule_for()` has always been able to enforce it — but only
behind a `strict_freshness` flag that **no production caller passed**. Both
`evaluate_two_leg()` and `run_arbitrage_scan()` defaulted it to `False`, so
the documented live gate did not exist at runtime and the fee ledger's
`verified_at` dates were provenance only.

This module removes the ambiguity by making freshness a property of the
**mode**, decided in exactly one place:

=================  ================  ======================================
Mode               Policy            Effect on a schedule older than the limit
=================  ================  =======================================
``live``           ``MANDATORY``     refused, exactly like a missing schedule
``paper``          ``MANDATORY``     refused — paper is the rehearsal whose
                                     results are the live-readiness evidence,
                                     so it may not be costed on a schedule
                                     nobody has re-verified
``historical``     ``RESEARCH``      still costed (replaying recorded history
                                     with the schedule that was current then is
                                     more useful than refusing to measure it),
                                     but the run and its report are labelled
                                     RESEARCH USE ONLY
``collect``        ``NOT_APPLICABLE``collect records markets and books; it
                                     never calculates a fee
=================  ================  =======================================

An unrecognized mode fails closed to ``MANDATORY``: UNKNOWN never means "skip
the check". Callers may still pass an explicit ``strict_fee_freshness``
override (tests do, to prove both directions); production callers pass
``None`` and let the mode decide.

The structured checks here (:class:`FeeFreshness`, :class:`FeeScheduleCheck`)
are also what `--readiness` reports, so the operator sees the same rule the
engine applies, with the observation that failed and what would satisfy it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, Iterable, Optional, Sequence

from ..core.clock import utcnow
from ..core.types import Mode


class FeeFreshnessPolicy(str, Enum):
    """How one mode must treat a fee schedule whose verification is old."""

    #: Execution-facing. The schedule's own verification date is a hard
    #: expiry: past it the schedule is refused like a missing one.
    MANDATORY = "MANDATORY"
    #: Explicitly labelled research replay. A stale schedule is still costed
    #: and the staleness is reported, never silently ignored.
    RESEARCH = "RESEARCH"
    #: The mode never calculates fees, so there is nothing to expire.
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def enforces_freshness(self) -> bool:
        """True only for :attr:`MANDATORY`."""
        return self is FeeFreshnessPolicy.MANDATORY


_POLICY_BY_MODE = {
    Mode.LIVE: FeeFreshnessPolicy.MANDATORY,
    Mode.PAPER: FeeFreshnessPolicy.MANDATORY,
    Mode.HISTORICAL: FeeFreshnessPolicy.RESEARCH,
    Mode.COLLECT: FeeFreshnessPolicy.NOT_APPLICABLE,
}

#: An unrecognized mode fails closed. UNKNOWN never means "no check".
FALLBACK_POLICY = FeeFreshnessPolicy.MANDATORY


def policy_for_mode(mode: Any) -> FeeFreshnessPolicy:
    """The single freshness policy for ``mode``.

    Accepts a :class:`~prediction_arb.core.types.Mode` or its string value
    (callers legitimately pass ``"paper"``); anything unrecognized returns
    :data:`FALLBACK_POLICY` rather than the permissive one.
    """
    try:
        resolved = Mode(mode)
    except (ValueError, TypeError):
        return FALLBACK_POLICY
    return _POLICY_BY_MODE.get(resolved, FALLBACK_POLICY)


def freshness_enforced(mode: Any, override: Optional[bool] = None) -> bool:
    """Resolve the ``strict_freshness`` argument for one evaluation.

    ``override`` is an explicit caller decision (tests use it to prove both
    directions). ``None`` — what every production caller passes — means the
    mode decides, through :func:`policy_for_mode`.
    """
    if override is not None:
        return bool(override)
    return policy_for_mode(mode).enforces_freshness


def policy_note(policy: FeeFreshnessPolicy, max_age_days: int) -> str:
    """Operator-facing one-liner describing a policy (§58 reporting)."""
    if policy is FeeFreshnessPolicy.MANDATORY:
        return (f"mandatory fee freshness: a schedule verified more than "
                f"{max_age_days} days ago is refused, like a missing one")
    if policy is FeeFreshnessPolicy.RESEARCH:
        return (f"research fee freshness: a schedule verified more than "
                f"{max_age_days} days ago is still costed, and the run is "
                f"labelled RESEARCH USE ONLY")
    return ("fee freshness not applicable: this mode never calculates fees")


@dataclass(frozen=True)
class FeeFreshness:
    """One venue's schedule age against the mode's policy.

    ``fresh`` is ``None`` when age cannot be established at all (no schedule,
    no verification date) — unknown is not fresh.
    """
    venue: str
    policy: FeeFreshnessPolicy
    max_age_days: int
    verified_at: Optional[date]
    age_days: Optional[int]
    fresh: Optional[bool]
    reason: str

    @property
    def enforced(self) -> bool:
        return self.policy.enforces_freshness

    @property
    def stale(self) -> bool:
        return self.fresh is False

    @property
    def unknown_age(self) -> bool:
        """No schedule or no verification date: the age cannot be established.

        Unknown is not fresh, so under a MANDATORY policy this blocks; under
        RESEARCH it is still costed, but it must be named in the label — an
        undated schedule is exactly the case an operator has to see.
        """
        return self.fresh is None

    @property
    def blocking(self) -> bool:
        """True when this mode must refuse the schedule for its age.

        An unestablished age blocks too: a schedule with no verification date
        cannot be shown to be current.
        """
        return self.enforced and self.fresh is not True


def schedule_freshness(venue: str, schedule: Any, *,
                       policy: FeeFreshnessPolicy,
                       max_age_days: int,
                       today: Optional[date] = None) -> FeeFreshness:
    """Age one venue's schedule against ``policy`` (no exceptions raised)."""
    today = today or utcnow().date()
    if schedule is None:
        return FeeFreshness(
            venue=venue, policy=policy, max_age_days=max_age_days,
            verified_at=None, age_days=None, fresh=None,
            reason=("no fee schedule on file, so its verification age is "
                    "unknown"))
    verified_at = getattr(schedule, "verified_at", None)
    if verified_at is None:
        return FeeFreshness(
            venue=venue, policy=policy, max_age_days=max_age_days,
            verified_at=None, age_days=None, fresh=None,
            reason=("fee schedule has no verification date, so its age is "
                    "unknown — it cannot be shown to be current"))
    age = (today - verified_at).days
    if age <= max_age_days:
        return FeeFreshness(
            venue=venue, policy=policy, max_age_days=max_age_days,
            verified_at=verified_at, age_days=age, fresh=True,
            reason=(f"verified {age} days ago ({verified_at}), within the "
                    f"{max_age_days}-day limit"))
    detail = f"verified {age} days ago ({verified_at}), beyond the {max_age_days}-day limit"
    if policy is FeeFreshnessPolicy.MANDATORY:
        reason = (f"{detail}: mandatory freshness applies in this mode — "
                  f"Re-verify before live execution.")
    elif policy is FeeFreshnessPolicy.RESEARCH:
        reason = (f"{detail}: allowed for explicitly labelled research only, "
                  f"never for live execution")
    else:
        reason = f"{detail}: this mode does not calculate fees"
    return FeeFreshness(
        venue=venue, policy=policy, max_age_days=max_age_days,
        verified_at=verified_at, age_days=age, fresh=False, reason=reason)


@dataclass(frozen=True)
class FeeScheduleCheck:
    """Everything the fail-closed engine requires of one venue's schedule.

    ``reasons`` lists **every** unmet requirement with the observation behind
    it, so a report can name the actual blocker instead of a status word. An
    empty ``reasons`` means the schedule is usable in this mode right now.
    Every reason (here and in :class:`FeeFreshness`) is venue-free: the
    consumers that print them prefix the venue, so the name appears once.
    """
    venue: str
    policy: FeeFreshnessPolicy
    present: bool
    version: str
    verification_status: str
    documentation_tested: bool
    has_source: bool
    effective_date: Optional[date]
    expiration_date: Optional[date]
    currently_effective: bool
    trade_calculation_status: str
    settlement_calculation_status: str
    blocking_reason: str
    freshness: FeeFreshness
    reasons: tuple[str, ...]

    @property
    def verified(self) -> bool:
        """The evidence gate `_require_calculable` applies: a schedule that is
        not VERIFIED, not documentation-tested or has no source is treated as
        INCOMPLETE however it is labelled."""
        return (self.verification_status == "VERIFIED"
                and self.documentation_tested and self.has_source)

    @property
    def components_complete(self) -> bool:
        return (self.trade_calculation_status == "COMPLETE"
                and self.settlement_calculation_status == "COMPLETE")

    @property
    def calculable(self) -> bool:
        """Would the engine return a number for both components right now?"""
        return (self.present and self.verified and self.currently_effective
                and self.components_complete)

    @property
    def stale(self) -> bool:
        return self.freshness.stale

    @property
    def usable(self) -> bool:
        """Calculable, and not refused for its age under this mode's policy."""
        return self.calculable and not self.freshness.blocking

    @property
    def research_only(self) -> bool:
        """Usable *because* the mode is research: costed on an old or undated
        schedule that a MANDATORY mode would have refused."""
        return (self.usable and (self.stale or self.freshness.unknown_age)
                and self.policy is FeeFreshnessPolicy.RESEARCH)


def check_schedule(venue: str, schedule: Any, *,
                   policy: FeeFreshnessPolicy,
                   max_age_days: int,
                   today: Optional[date] = None) -> FeeScheduleCheck:
    """Structured, exception-free view of one venue's fee schedule."""
    today = today or utcnow().date()
    freshness = schedule_freshness(venue, schedule, policy=policy,
                                   max_age_days=max_age_days, today=today)
    if schedule is None:
        return FeeScheduleCheck(
            venue=venue, policy=policy, present=False, version="",
            verification_status="UNKNOWN", documentation_tested=False,
            has_source=False, effective_date=None, expiration_date=None,
            currently_effective=False,
            trade_calculation_status="INCOMPLETE",
            settlement_calculation_status="INCOMPLETE",
            blocking_reason="", freshness=freshness,
            reasons=(f"no fee schedule on file for {venue} — fees cannot be "
                     f"computed and are never assumed zero",))

    verification_status = str(
        getattr(schedule, "verification_status", "UNKNOWN")).upper()
    documentation_tested = bool(getattr(schedule, "documentation_tested", False))
    has_source = bool(str(getattr(schedule, "source", "")).strip())
    effective_date = getattr(schedule, "effective_date", None)
    expiration_date = getattr(schedule, "expiration_date", None)
    blocking_reason = str(getattr(schedule, "blocking_reason", "")).strip()
    trade_status = str(
        getattr(schedule, "trade_calculation_status", "INCOMPLETE")).upper()
    settlement_status = str(
        getattr(schedule, "settlement_calculation_status", "INCOMPLETE")).upper()
    # The same downgrade `_require_calculable` applies at calculation time:
    # an unverified or unsourced schedule is INCOMPLETE whatever it claims.
    verified = (verification_status == "VERIFIED" and documentation_tested
                and has_source)
    if not verified:
        trade_status = "INCOMPLETE"
        settlement_status = "INCOMPLETE"

    if effective_date is None:
        currently_effective = False
    elif expiration_date is not None and today > expiration_date:
        currently_effective = False
    else:
        currently_effective = today >= effective_date

    reasons: list[str] = []
    if not verified:
        reasons.append(
            f"schedule evidence incomplete (verification_status="
            f"{verification_status}, documentation_tested="
            f"{str(documentation_tested).lower()}, source="
            f"{'present' if has_source else 'missing'}) — treated as "
            f"INCOMPLETE")
    if effective_date is None:
        reasons.append("no verified effective date")
    elif today < effective_date:
        reasons.append(f"not effective until {effective_date}")
    elif expiration_date is not None and today > expiration_date:
        reasons.append(f"expired on {expiration_date}")
    incomplete_components = [
        name for name, status in (("trade", trade_status),
                                  ("settlement", settlement_status))
        if status != "COMPLETE"
    ]
    for name in incomplete_components:
        status = (trade_status if name == "trade" else settlement_status)
        reasons.append(f"{name} fee component is {status}")
    # The venue's own statement of what is missing is recorded once, in full:
    # repeating it per component buried the other failures (D17's lesson).
    if incomplete_components and blocking_reason:
        reasons.append(f"venue evidence gap: {blocking_reason}")
    if freshness.fresh is not True:
        # Stale, undated or missing: recorded in every policy, so a research
        # run that costs it still carries the observation (never silent).
        reasons.append(freshness.reason)

    return FeeScheduleCheck(
        venue=venue, policy=policy, present=True,
        version=str(getattr(schedule, "version", "")),
        verification_status=verification_status,
        documentation_tested=documentation_tested, has_source=has_source,
        effective_date=effective_date, expiration_date=expiration_date,
        currently_effective=currently_effective,
        trade_calculation_status=trade_status,
        settlement_calculation_status=settlement_status,
        blocking_reason=blocking_reason, freshness=freshness,
        reasons=tuple(reasons))


def research_fee_label(checks: Sequence[FeeScheduleCheck]) -> Optional[str]:
    """The RESEARCH USE ONLY label for a run that costed stale schedules.

    ``None`` when every schedule that was costed has a known, in-limit
    verification date, so callers print the label only when it is true.
    Requirement: research replay is not blocked by an old — or undated —
    schedule, but it must never be mistaken for live-executable pricing, and
    silence must never hide an unknown age.
    """
    present = [c for c in checks if c.present]
    stale = [c for c in present if c.stale]
    undated = [c for c in present if c.freshness.unknown_age]
    if not stale and not undated:
        return None
    limit = (stale + undated)[0].freshness.max_age_days
    scope = (f"older than the {limit}-day live limit" if not undated else
             f"older than the {limit}-day live limit or without a "
             f"verification date")
    parts = ", ".join(
        (f"{c.venue} verified {c.freshness.age_days} days ago "
         f"({c.freshness.verified_at})") if c.stale
        else f"{c.venue} has no verification date"
        for c in stale + undated)
    return (f"RESEARCH USE ONLY: fee schedule(s) {scope} — {parts}. Costs in "
            f"this run are research figures for recorded history, not "
            f"live-executable prices (docs/FEE_READINESS.md).")


def summarize_checks(checks: Iterable[FeeScheduleCheck]) -> dict[str, Any]:
    """JSON-safe summary of a set of schedule checks (reports, readiness)."""
    checks = tuple(checks)
    return {
        "policy": checks[0].policy.value if checks else "",
        "venues": {
            c.venue: {
                "present": c.present,
                "version": c.version,
                "verified_at": (c.freshness.verified_at.isoformat()
                                if c.freshness.verified_at else None),
                "age_days": c.freshness.age_days,
                "max_age_days": c.freshness.max_age_days,
                "fresh": c.freshness.fresh,
                "freshness_enforced": c.freshness.enforced,
                "trade_calculation_status": c.trade_calculation_status,
                "settlement_calculation_status": c.settlement_calculation_status,
                "calculable": c.calculable,
                "usable_in_this_mode": c.usable,
                "research_only": c.research_only,
                "reasons": list(c.reasons),
            }
            for c in checks
        },
    }
