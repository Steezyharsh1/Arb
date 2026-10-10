"""Derived readiness reporting (§58, §98, §99; defects D10 and D19).

`--readiness` used to print three checks, and the third was the literal
``False``:

    ("Two live-capable independent venues", 1, False),  # kalshi verified; …

so the two-venue gate could never pass even after two venues became verified,
and the report said nothing about *why* the system was not ready. Separately,
`fees.max_schedule_age_days: 30` was documented as the live staleness gate
while no production caller ever passed the flag that enforced it (D19).

Every criterion in this module is computed from verified system state instead:

* **adapter verification** — each adapter's own ``get_api_status()`` and
  ``get_supported_order_types().verified`` (a venue whose order behaviour was
  never verified is not live-capable, however good its market data is);
* **venue independence** — :func:`provenance.independence.is_independent` over
  the venues that actually qualify, never a counted constant;
* **data availability** — the recorded store, opened read-only and never
  created by a status report;
* **fee completeness and freshness** — :meth:`FeeCalculator.schedule_check`
  under the mode's policy from :mod:`fees.freshness`;
* **settlement requirements** — the evidence status of each venue's entry in
  :data:`normalization.settlement.PRODUCTION_SETTLEMENT_TIMING`;
* **applicable risk gates** — kill switch, configured risk limits, the
  order-book staleness gate, the compliance eligibility ledger, and (for live)
  the mode-configuration invariants ``core.config`` enforces at startup.

Two rules shape the module, and both are enforced by construction:

1. a criterion always carries the observation behind it — :class:`Criterion`
   refuses to exist without a reason, so an unmet gate names the actual
   blocker instead of a hardcoded status value;
2. the report is advisory (§58) and enables nothing. Live mode still requires
   ``config/live.yaml``, the typed confirmation phrase and every
   ``LiveOrderFirewall`` check; a fully green report does not place an order.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import yaml
from rich.console import Console
from rich.markup import escape
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..adapters.base import PredictionMarketAdapter
from ..compliance.jurisdiction import EligibilityStatus, get_eligibility
from ..core.types import APIStatus, Independence, Mode
from ..database.models import OpportunityRow, OrderBookSnapshotRow
from ..database.repository import Repository
from ..fees.calculator import FeeCalculator
from ..fees.freshness import (
    FeeFreshnessPolicy,
    FeeScheduleCheck,
    policy_for_mode,
    policy_note,
)
from ..normalization.settlement import (
    SettlementTimingSource,
    TimingEvidenceStatus,
    settlement_timing_source,
)
from ..provenance.independence import is_independent
from ..risk.limits import RiskLimits

# --------------------------------------------------------------------- model


class CriterionState(str, Enum):
    #: Requirement met, with the observation that shows it.
    PASS = "PASS"
    #: Not met, but this mode may legitimately continue (research/replay).
    #: Always reported — an advisory is not a silent pass.
    WARN = "WARN"
    #: Not met, and this mode cannot produce trustworthy results without it.
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class Criterion:
    """One derived readiness requirement and the observation behind it."""
    key: str
    label: str
    state: CriterionState
    reason: str
    value: str = ""

    def __post_init__(self) -> None:
        # Requirement: unmet criteria carry explicit reasons, never a bare
        # status value. A criterion with nothing to say cannot be built.
        if not str(self.reason).strip():
            raise ValueError(
                f"criterion {self.key!r} has no reason: readiness must state "
                f"the observation behind every verdict")

    @property
    def blocking(self) -> bool:
        return self.state is CriterionState.BLOCKED


def passed(key: str, label: str, reason: str, value: str = "") -> Criterion:
    return Criterion(key, label, CriterionState.PASS, reason, value)


def warned(key: str, label: str, reason: str, value: str = "") -> Criterion:
    return Criterion(key, label, CriterionState.WARN, reason, value)


def blocked(key: str, label: str, reason: str, value: str = "") -> Criterion:
    return Criterion(key, label, CriterionState.BLOCKED, reason, value)


@dataclass(frozen=True)
class ModeReadiness:
    """Every criterion that applies to one mode, and the verdict they give."""
    mode: Mode
    criteria: tuple[Criterion, ...]

    @property
    def ready(self) -> bool:
        """True only when no applicable criterion is BLOCKED."""
        return not any(c.blocking for c in self.criteria)

    @property
    def blockers(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria if c.blocking)

    @property
    def warnings(self) -> tuple[Criterion, ...]:
        return tuple(c for c in self.criteria
                     if c.state is CriterionState.WARN)

    def get(self, key: str) -> Criterion:
        for c in self.criteria:
            if c.key == key:
                return c
        raise KeyError(f"no criterion {key!r} for mode {self.mode.value}")

    def blocker_summary(self) -> str:
        if not self.blockers:
            return "none"
        return "; ".join(f"{c.label} — {c.reason}" for c in self.blockers)


@dataclass(frozen=True)
class ReadinessReport:
    """Readiness for all four modes, from one set of observations."""
    modes: tuple[ModeReadiness, ...]
    thresholds: "ReadinessThresholds"

    def for_mode(self, mode: Mode) -> ModeReadiness:
        for m in self.modes:
            if m.mode == mode:
                return m
        raise KeyError(f"no readiness evaluated for mode {mode!r}")

    @property
    def live(self) -> ModeReadiness:
        return self.for_mode(Mode.LIVE)


# ------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class RecordedEvidence:
    """What the recorded store holds — measured, never created, by a report."""
    store_path: Optional[Path]
    present: bool
    snapshots: int
    opportunities: int
    note: str


def load_recorded_evidence(path: Optional[Path]) -> RecordedEvidence:
    """Count recorded snapshots/opportunities **without creating a database**.

    The old ``cmd_readiness`` opened the mode database through
    ``Repository``, which creates ``data/paper/prediction_arb_paper.sqlite3``
    as a side effect; a later historical run then reported a "recorded store"
    that held zero rows. A readiness report is read-only, so a missing store
    is reported as missing (§92: never fabricate, never create).
    """
    if path is None:
        return RecordedEvidence(None, False, 0, 0,
                                "no recorded-store path is configured")
    if not Path(path).exists():
        return RecordedEvidence(
            Path(path), False, 0, 0,
            f"no recorded store at {path} — nothing has been collected yet; "
            f"run `python -m prediction_arb.main --mode collect`")
    try:
        repo = Repository(Path(path), Mode.HISTORICAL, read_only=True)
    except Exception as exc:                     # untagged, foreign, corrupt
        return RecordedEvidence(
            Path(path), True, 0, 0,
            f"recorded store at {path} exists but could not be read "
            f"({type(exc).__name__}: {exc})")
    with Session(repo.engine) as s:
        snapshots = s.scalar(select(func.count(OrderBookSnapshotRow.id))) or 0
        opportunities = s.scalar(select(func.count(OpportunityRow.id))) or 0
    return RecordedEvidence(
        Path(path), True, int(snapshots), int(opportunities),
        f"recorded store at {path}")


@dataclass(frozen=True)
class MatcherProbe:
    """Whether the semantic matcher can produce APPROVED certificates."""
    configured: bool
    reason: str


def probe_matcher(env: Mapping[str, str], *, env_label: str,
                  max_comparisons_per_cycle: int = 200) -> MatcherProbe:
    """Derive matcher availability from the mode env file (§97).

    Without a configured matcher every ambiguous pair stays REVIEW, so no
    certificate is APPROVED and ``search_event()`` evaluates nothing — which
    is exactly why paper and live readiness cannot pass without it.
    """
    from ..matching.llm_client import LLMConfigError, build_llm_matcher
    try:
        matcher, model = build_llm_matcher(
            dict(env),
            max_comparisons_per_cycle=max_comparisons_per_cycle)
    except LLMConfigError as exc:
        return MatcherProbe(
            False,
            f"LLM matcher configuration in {env_label} is partial and refuses "
            f"startup: {exc}")
    if matcher.client is None:
        return MatcherProbe(
            False,
            f"no LLM configured in {env_label} (LLM_API_BASE / LLM_API_KEY / "
            f"LLM_MODEL): every ambiguous pair stays REVIEW, so no match "
            f"certificate is ever APPROVED and no opportunity is evaluated")
    return MatcherProbe(
        True,
        f"LLM matcher configured in {env_label}: model={model}, endpoint="
        f"{str(env.get('LLM_API_BASE', '')).strip()}, per-cycle comparison "
        f"budget={matcher.max_comparisons_per_cycle} (the key is never "
        f"reported)")


@dataclass(frozen=True)
class LiveConfiguration:
    """``config/live.yaml`` and the existence (never the contents) of its env."""
    path: Path
    present: bool
    document: Mapping[str, Any]
    env_path: Path
    env_present: bool


def probe_live_configuration(config_dir: Path, root: Path) -> LiveConfiguration:
    """Read live mode's configuration file and check its env file exists.

    §48 forbids loading live credentials outside live mode, so this looks at
    the *path* only. A readiness report must never be the thing that reads
    ``.env.live``.
    """
    path = Path(config_dir) / "live.yaml"
    document: Mapping[str, Any] = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            document = yaml.safe_load(f) or {}
    env_file = str(document.get("env_file") or ".env.live")
    env_path = Path(root) / env_file
    return LiveConfiguration(path=path, present=path.exists(),
                             document=document, env_path=env_path,
                             env_present=env_path.exists())


@dataclass(frozen=True)
class ReadinessThresholds:
    """Evidence thresholds, configured rather than baked into the report."""
    minimum_recorded_snapshots: int = 1000
    minimum_analyzed_opportunities: int = 50
    minimum_live_capable_venues: int = 2
    minimum_data_venues: int = 2

    @classmethod
    def from_settings(cls, settings: Optional[Mapping[str, Any]]
                      ) -> "ReadinessThresholds":
        block = dict((settings or {}).get("readiness", {}) or {})
        defaults = {f.name: getattr(cls(), f.name)
                    for f in dataclasses.fields(cls)}
        values = {}
        for name, default in defaults.items():
            raw = block.get(name, default)
            try:
                values[name] = int(raw)
            except (TypeError, ValueError):
                # A threshold we cannot parse is not a threshold we skip:
                # keep the documented default and let the report say so.
                values[name] = default
        return cls(**values)


@dataclass(frozen=True)
class ReadinessInputs:
    """Every observation the report is allowed to use."""
    adapters: Mapping[str, PredictionMarketAdapter]
    fee_calc: FeeCalculator
    settings: Mapping[str, Any]
    risk_limits: Mapping[str, Any]
    recorded: RecordedEvidence
    matcher: MatcherProbe
    kill_switch_engaged: bool
    jurisdiction: str
    live_configuration: LiveConfiguration
    settlement_sources: Optional[Mapping[str, SettlementTimingSource]] = None
    today: Optional[date] = None


# ------------------------------------------------------- adapter verification


@dataclass(frozen=True)
class VenueCapability:
    """What one adapter's own reporting establishes about a venue."""
    venue: str
    api_status: str
    order_types_verified: bool
    #: ``MARKET_DATA_OBSERVED`` or ``VERIFIED`` — what collect/paper admit.
    data_capable: bool
    #: Full adapter ``VERIFIED`` plus verified order behaviour: live-capable.
    live_capable: bool

    @property
    def data_note(self) -> str:
        """What market-data admission depends on."""
        return f"{self.venue}: API {self.api_status}"

    @property
    def note(self) -> str:
        """The full observation, including order-placement verification."""
        return (f"{self.venue}: API {self.api_status}, order types "
                f"{'VERIFIED' if self.order_types_verified else 'NOT verified'}"
                f" → {'live-capable' if self.live_capable else 'not live-capable'}")


def venue_capabilities(
        adapters: Mapping[str, PredictionMarketAdapter]
) -> tuple[VenueCapability, ...]:
    """Ask each adapter what it verifies — no registry of hardcoded names."""
    out: list[VenueCapability] = []
    for venue, adapter in adapters.items():
        try:
            api_status = adapter.get_api_status()
        except Exception as exc:      # an adapter that cannot answer is not verified
            api_status = APIStatus.UNVERIFIED
            note = f" ({type(exc).__name__}: {exc})"
        else:
            note = ""
        try:
            order_types_verified = bool(
                adapter.get_supported_order_types().verified)
        except Exception:
            order_types_verified = False
        data_capable = api_status.supports_market_data
        out.append(VenueCapability(
            venue=venue, api_status=f"{api_status.value}{note}",
            order_types_verified=order_types_verified,
            data_capable=data_capable,
            live_capable=(api_status == APIStatus.VERIFIED
                          and order_types_verified)))
    return tuple(out)


# ------------------------------------------------------------- criteria


def criterion_data_venues(caps: Sequence[VenueCapability], minimum: int,
                          purpose: str) -> Criterion:
    """Adapters with observed usable market data or full verification."""
    key, label = "data_venues", "Usable market-data adapters"
    capable = [c.venue for c in caps if c.data_capable]
    excluded = [c.data_note for c in caps if not c.data_capable]
    value = f"{len(capable)} of {len(caps)}"
    detail = "; ".join(c.data_note for c in caps) or "no adapters are enabled"
    if len(capable) >= minimum:
        return passed(key, label, f"{purpose} — {detail}", value)
    why = (f"not admitted: {', '.join(excluded)}" if excluded
           else "no adapters are enabled")
    return blocked(
        key, label,
        f"{purpose}. {minimum} venue(s) must report "
        f"MARKET_DATA_OBSERVED or VERIFIED; found {len(capable)} — {why}. "
        f"Connectivity alone is insufficient. Promote only from observed "
        f"market/book payload evidence (scripts/polymarket_us_smoke.py, "
        f"scripts/kalshi_smoke.py)",
        value)


def criterion_live_capable_venues(caps: Sequence[VenueCapability],
                                  minimum: int) -> Criterion:
    """VERIFIED API **and** verified order behaviour: able to trade live."""
    key, label = ("live_capable_venues",
                  f"At least {minimum} live-capable independent venues")
    capable = [c.venue for c in caps if c.live_capable]
    value = f"{len(capable)} of {len(caps)}"
    detail = "; ".join(c.note for c in caps) or "no adapters are enabled"
    if len(capable) >= minimum:
        return passed(
            key, label,
            f"live-capable venues (VERIFIED API and verified order types): "
            f"{', '.join(capable)} — {detail}", value)
    missing = [c.note for c in caps if not c.live_capable]
    return blocked(
        key, label,
        f"live needs {minimum} venues whose adapter reports both "
        f"APIStatus.VERIFIED and verified order-type support; found "
        f"{len(capable)}. Not live-capable — {'; '.join(missing) or 'none enabled'}",
        value)


def criterion_independent_pairs(venues: Sequence[str], scope: str) -> Criterion:
    """Provenance-INDEPENDENT pairs among ``venues`` (§33)."""
    key, label = "independent_pair", "Provenance-independent venue pair"
    if len(venues) < 2:
        return blocked(
            key, label,
            f"{scope}: fewer than two qualifying venues "
            f"({', '.join(venues) or 'none'}), so there is no cross-venue "
            f"pair to classify — guaranteed two-leg arbitrage is impossible",
            f"{len(venues)} qualifying venue(s)")
    pairs: list[str] = []
    independent: list[str] = []
    for i in range(len(venues)):
        for j in range(i + 1, len(venues)):
            a, b = venues[i], venues[j]
            classification = is_independent(a, b)
            pairs.append(f"{a}↔{b}={classification.value}")
            if classification == Independence.INDEPENDENT:
                independent.append(f"{a}↔{b}")
    if independent:
        return passed(key, label,
                      f"{scope}: provenance-independent pair(s) "
                      f"{', '.join(independent)} (all pairs: "
                      f"{', '.join(pairs)})",
                      f"{len(independent)} independent of {len(pairs)} pairs")
    return blocked(
        key, label,
        f"{scope}: no pair is provenance-INDEPENDENT ({', '.join(pairs)}). "
        f"LIKELY_INDEPENDENT and UNKNOWN never authorise a guaranteed "
        f"two-leg trade (§33)",
        f"0 independent of {len(pairs)} pairs")


FEE_EVIDENCE_DOC = "docs/FEE_READINESS.md"
SETTLEMENT_EVIDENCE_DOC = "docs/TRADING_LOGIC.md"


def _short(text: str, limit: int = 200) -> str:
    """The first sentence of a long evidence note, for operator-facing lines.

    The full text stays available programmatically (``FeeScheduleCheck.reasons``,
    ``SettlementTimingSource.gaps``); the report quotes enough to act on and
    names the document that carries the rest.
    """
    flat = " ".join(str(text).split())
    if not flat:
        return ""
    sentence = flat.split(". ")[0].rstrip()
    if sentence and sentence[-1] not in ".!?":
        sentence += "."
    if len(sentence) > limit:
        sentence = sentence[:limit].rstrip(" ,;") + "…"
    return sentence


def _fee_gap_detail(checks: Sequence[FeeScheduleCheck]) -> str:
    """Per-venue summary of what makes a schedule uncalculable."""
    parts: list[str] = []
    for c in checks:
        if c.calculable:
            continue
        states = [r for r in _calculation_reasons(c)
                  if not r.startswith("venue evidence gap:")]
        gap = next((r[len("venue evidence gap: "):]
                    for r in _calculation_reasons(c)
                    if r.startswith("venue evidence gap: ")), "")
        text = f"{c.venue}: {'; '.join(states) or 'not calculable'}"
        if gap:
            text += f" — {_short(gap)} ({FEE_EVIDENCE_DOC})"
        parts.append(text)
    return " | ".join(parts)


def _calculation_reasons(check: FeeScheduleCheck) -> tuple[str, ...]:
    """A check's unmet requirements, excluding the freshness one.

    Freshness is its own criterion, so the completeness criterion must not
    repeat it — an operator reading two lines for one problem cannot tell
    which gate to clear.
    """
    return tuple(r for r in check.reasons if r != check.freshness.reason)


def criterion_fee_completeness(fee_calc: FeeCalculator,
                               venues: Sequence[str],
                               policy: FeeFreshnessPolicy,
                               today: Optional[date] = None) -> Criterion:
    """Trade **and** settlement components calculable for every venue."""
    key, label = "fee_completeness", "Complete all-in fee schedules"
    checks = fee_calc.schedule_checks(venues, policy, today)
    unmet = [c for c in checks if not c.calculable]
    value = f"{len(checks) - len(unmet)} of {len(checks)} venues"
    if not checks:
        return blocked(key, label,
                       "no venues are enabled, so no fee schedule can be "
                       "checked", value)
    if not unmet:
        return passed(
            key, label,
            f"trade and settlement components are COMPLETE, evidence-verified, "
            f"sourced and inside their effective window for "
            f"{', '.join(c.venue for c in checks)}", value)
    return blocked(
        key, label,
        f"mandatory costs cannot be computed for {len(unmet)} of "
        f"{len(checks)} venue(s), so net profit stays n/a and every "
        f"opportunity BLOCKs (§24) — {_fee_gap_detail(unmet)}",
        value)


def criterion_fee_freshness(fee_calc: FeeCalculator,
                            venues: Sequence[str],
                            mode: Mode,
                            today: Optional[date] = None) -> Criterion:
    """The mode's mandatory-freshness policy, reported as it is enforced."""
    key = "fee_freshness"
    policy = policy_for_mode(mode)
    limit = fee_calc.max_schedule_age_days
    label = f"Fee schedules fresh (policy {policy.value}, limit {limit} days)"
    if policy is FeeFreshnessPolicy.NOT_APPLICABLE:
        return passed(
            key, label,
            f"{mode.value} mode records markets and order books and never "
            f"calculates a fee, so no schedule can be stale here (§98)",
            "not applicable")
    checks = fee_calc.schedule_checks(venues, policy, today)
    blocking = [c for c in checks if c.freshness.blocking]
    stale = [c for c in checks if c.stale and not c.freshness.blocking]
    ages = ", ".join(
        f"{c.venue} verified "
        f"{c.freshness.age_days if c.freshness.age_days is not None else 'n/a'}"
        f" days ago" for c in checks) or "no venues enabled"
    value = f"{len(checks) - len(blocking)} of {len(checks)} venues"
    if blocking:
        detail = "; ".join(f"{c.venue}: {c.freshness.reason}" for c in blocking)
        return blocked(
            key, label,
            f"{policy_note(policy, limit)} — {detail}", value)
    if stale:
        detail = "; ".join(f"{c.venue}: {c.freshness.reason}" for c in stale)
        return warned(
            key, label,
            f"{policy_note(policy, limit)} — {detail}. Costs are research "
            f"figures for recorded history, not live-executable prices",
            value)
    return passed(key, label,
                  f"{policy_note(policy, limit)} — {ages}", value)


def criterion_settlement_timing(venues: Sequence[str], mode: Mode,
                                sources: Optional[
                                    Mapping[str, SettlementTimingSource]] = None
                                ) -> Criterion:
    """Capital-release timing evidenced per venue (D18)."""
    key, label = ("settlement_timing",
                  "Evidenced settlement timing (capital lockup)")
    notes: list[str] = []
    unevidenced: list[str] = []
    for venue in venues:
        source = settlement_timing_source(venue, sources)
        if source is None:
            status = "NO ENTRY"
            gap = "no settlement-timing evidence entry exists for this venue"
        else:
            status = source.status.value
            gap = source.gaps
        notes.append(f"{venue}={status}")
        if source is None or source.status is TimingEvidenceStatus.UNKNOWN:
            unevidenced.append(
                f"{venue}: {_short(gap) or 'no evidence'} "
                f"({SETTLEMENT_EVIDENCE_DOC})")
    value = ", ".join(notes) or "no venues enabled"
    if not venues:
        return blocked(key, label,
                       "no venues are enabled, so no settlement timing can be "
                       "checked", value)
    if not unevidenced:
        return passed(
            key, label,
            f"capital-release timing is evidenced for every enabled venue: "
            f"{value} (lockup is costed, not assumed zero)", value)
    detail = "; ".join(unevidenced)
    reason = (f"settlement timing is UNKNOWN, so the capital-lockup cost "
              f"cannot be computed and every leg on those venues BLOCKs "
              f"(§28, D18) — {detail}")
    if mode is Mode.LIVE or mode is Mode.PAPER:
        return blocked(key, label, reason, value)
    return warned(key, label,
                  f"{reason}. Replay still runs: those opportunities are "
                  f"reported with lockup unavailable rather than excluded",
                  value)


def criterion_recorded_evidence(recorded: RecordedEvidence,
                                thresholds: ReadinessThresholds,
                                mode: Mode) -> Criterion:
    """Recorded data volume — measured from the store, never created."""
    key, label = "recorded_evidence", "Recorded data volume"
    value = (f"{recorded.snapshots} snapshots, "
             f"{recorded.opportunities} opportunities")
    if mode is Mode.HISTORICAL:
        if not recorded.present:
            return blocked(key, label, recorded.note, value)
        if recorded.snapshots <= 0:
            return blocked(
                key, label,
                f"the recorded store holds 0 order-book snapshots, so there "
                f"is nothing to replay — {recorded.note}. Historical mode "
                f"never fabricates data or falls back to live data "
                f"(§55, §92)", value)
        return passed(key, label,
                      f"{recorded.note}: {recorded.snapshots} recorded "
                      f"order-book snapshot(s) available to replay", value)

    # live: replay/paper evidence thresholds (§58 progression)
    missing: list[str] = []
    if recorded.snapshots < thresholds.minimum_recorded_snapshots:
        missing.append(
            f"{recorded.snapshots} recorded order-book snapshots, needs "
            f"{thresholds.minimum_recorded_snapshots}")
    if recorded.opportunities < thresholds.minimum_analyzed_opportunities:
        missing.append(
            f"{recorded.opportunities} opportunities analyzed, needs "
            f"{thresholds.minimum_analyzed_opportunities}")
    if missing:
        return blocked(
            key, label,
            f"live readiness requires paper/replay evidence — "
            f"{'; '.join(missing)}. {recorded.note}", value)
    return passed(
        key, label,
        f"{recorded.note}: {recorded.snapshots} snapshots and "
        f"{recorded.opportunities} analyzed opportunities meet the "
        f"configured thresholds "
        f"({thresholds.minimum_recorded_snapshots}/"
        f"{thresholds.minimum_analyzed_opportunities})", value)


def criterion_matcher(matcher: MatcherProbe, mode: Mode) -> Criterion:
    """APPROVED certificates are the only way an opportunity is evaluated."""
    key, label = "matcher", "Semantic matcher configured"
    if matcher.configured:
        return passed(key, label, matcher.reason, "configured")
    if mode in (Mode.PAPER, Mode.LIVE):
        return blocked(key, label, matcher.reason, "not configured")
    return warned(key, label,
                  f"{matcher.reason}. Replay still measures candidates and "
                  f"deterministic outcomes; it cannot produce APPROVED pairs",
                  "not configured")


def criterion_kill_switch(engaged: bool, mode: Mode) -> Criterion:
    """The persistent global stop (§71)."""
    key, label = "kill_switch", "Kill switch disengaged"
    if not engaged:
        return passed(key, label,
                      "the persistent kill switch is disengaged", "disengaged")
    reason = ("the kill switch is ENGAGED, so no new trades may be submitted "
              "in any mode (§71); clear it with "
              "`python -m prediction_arb.main --unkill` once the cause is "
              "understood")
    if mode in (Mode.PAPER, Mode.LIVE):
        return blocked(key, label, reason, "engaged")
    return warned(key, label,
                  f"{reason}. {mode.value} mode places no orders, so it may "
                  f"continue", "engaged")


def criterion_book_staleness(settings: Mapping[str, Any]) -> Criterion:
    """The order-book staleness gate both the scan and the firewall use."""
    key, label = "book_staleness", "Order-book staleness gate configured"
    execution = (settings or {}).get("execution", {}) or {}
    arbitrage = (settings or {}).get("arbitrage", {}) or {}
    scan_ms = execution.get("max_book_age_ms")
    arb_ms = arbitrage.get("maximum_book_age_ms")
    problems: list[str] = []
    for name, value in (("execution.max_book_age_ms", scan_ms),
                        ("arbitrage.maximum_book_age_ms", arb_ms)):
        try:
            numeric = float(value) if value is not None else None
        except (TypeError, ValueError):
            numeric = None
        if numeric is None:
            problems.append(f"{name} is not configured")
        elif numeric <= 0:
            problems.append(f"{name}={value} is not a positive limit")
    value = f"execution={scan_ms}ms, scan={arb_ms}ms"
    if problems:
        return blocked(
            key, label,
            "the paper engine and the live firewall refuse to trade on a book "
            "older than the stricter of these limits (D11), so an unset or "
            "non-positive limit would disable the gate — "
            + "; ".join(problems), value)
    return passed(key, label,
                  f"books older than the stricter limit are excluded from the "
                  f"scan and from execution, and are counted when excluded "
                  f"({value})", value)


def criterion_eligibility(jurisdiction: str, venues: Sequence[str],
                          mode: Mode) -> Criterion:
    """Jurisdiction/category eligibility from the evidence ledger (§35)."""
    key, label = "eligibility", "Account/category eligibility resolved"
    notes: list[str] = []
    unresolved: list[str] = []
    for venue in venues:
        record = get_eligibility(jurisdiction, venue, "")
        notes.append(f"{venue}={record.status.value}")
        if record.status is not EligibilityStatus.ELIGIBLE:
            unresolved.append((venue, record.status.value,
                               _short(record.notes or record.source)))
    value = f"{jurisdiction}: " + (", ".join(notes) or "no venues enabled")
    if not venues:
        return blocked(key, label,
                       "no venues are enabled, so eligibility cannot be "
                       "checked", value)
    if not unresolved:
        return passed(key, label,
                      f"eligibility is ELIGIBLE for every enabled venue in "
                      f"{jurisdiction} per the evidence ledger", value)
    grouped: dict[tuple[str, str], list[str]] = {}
    for venue, status, why in unresolved:
        grouped.setdefault((status, why), []).append(venue)
    detail = "; ".join(
        f"{status} for {', '.join(venues)} — {why}"
        for (status, why), venues in grouped.items()).rstrip(".")
    if mode is Mode.LIVE:
        return blocked(
            key, label,
            f"UNKNOWN and RESTRICTED both block live trading (§35) — {detail}",
            value)
    return warned(
        key, label,
        f"eligibility is not ELIGIBLE for {len(unresolved)} venue(s) — "
        f"{detail}. {mode.value.capitalize()} mode may continue as research; "
        f"live is blocked until owner/legal evidence resolves it", value)


def criterion_risk_limits(risk_limits: Mapping[str, Any]) -> Criterion:
    """Check configuration completeness only, never infer runtime wiring."""
    key, label = "risk_limits", "Risk limits configured"
    cfg = dict(risk_limits or {})
    sections = {
        "limits": dict(cfg.get("limits", cfg) or {}),
        "partial_fill": dict(cfg.get("partial_fill", cfg) or {}),
    }
    problems: list[str] = []
    configured: list[str] = []
    for f in dataclasses.fields(RiskLimits):
        name = f.name
        raw = None
        found = False
        for section in ("limits", "partial_fill"):
            if name in sections[section]:
                raw = sections[section][name]
                found = True
                break
        if not found and name in cfg:
            raw, found = cfg[name], True
        if not found:
            problems.append(
                f"{name} is not configured — the loader would silently "
                f"substitute a default, which is not an operator decision")
            continue
        try:
            numeric = float(raw)
        except (TypeError, ValueError):
            problems.append(f"{name}={raw!r} is not a number")
            continue
        if numeric <= 0:
            problems.append(f"{name}={raw} is not positive, so it would stop "
                            f"every trade or allow none")
            continue
        configured.append(f"{name}={raw}")
    value = f"{len(configured)} limits configured"
    if problems:
        return blocked(key, label,
                       "hard risk limits (§70) must be explicit and positive: "
                       + "; ".join(problems), value)
    return passed(
        key, label,
        "all hard-limit values are configured and positive (configuration "
        "check only; runtime-enforcement evidence is documented in "
        f"docs/RISK_MANAGEMENT.md): {', '.join(configured)}", value)


def criterion_live_configuration(probe: LiveConfiguration) -> Criterion:
    """live.yaml's mode-isolation invariants and its credential file (§48)."""
    key, label = "live_configuration", "Live mode configuration"
    doc = dict(probe.document or {})
    problems: list[str] = []
    if not probe.present:
        problems.append(f"no {probe.path} — core.config refuses to start live "
                        f"mode without it")
    else:
        if str(doc.get("mode", "")).lower() != "live":
            problems.append(f"{probe.path} declares mode={doc.get('mode')!r}, "
                            f"not 'live'")
        if doc.get("execution_engine") != "LiveExecutionEngine":
            problems.append(f"{probe.path} declares execution_engine="
                            f"{doc.get('execution_engine')!r}, not "
                            f"'LiveExecutionEngine'")
        if not bool(doc.get("allow_live_orders", False)):
            problems.append(f"{probe.path} must explicitly set "
                            f"allow_live_orders: true")
        if not bool(doc.get("requires_trading_credentials", False)):
            problems.append(f"{probe.path} does not declare "
                            f"requires_trading_credentials: true")
    if doc.get("requires_trading_credentials") and not probe.env_present:
        problems.append(
            f"no credential file at {probe.env_path} (existence checked only — "
            f"§48 forbids reading live credentials outside live mode)")
    value = f"{probe.path.name}{' present' if probe.present else ' missing'}"
    if problems:
        return blocked(key, label, "; ".join(problems), value)
    return passed(
        key, label,
        f"{probe.path} declares mode=live, LiveExecutionEngine, "
        f"allow_live_orders=true and requires_trading_credentials=true, and "
        f"the credential file {probe.env_path.name} exists (contents never "
        f"read outside live mode, §48)", value)


# ------------------------------------------------------------- evaluation


def evaluate_mode(mode: Mode, inputs: ReadinessInputs) -> ModeReadiness:
    """Every criterion that applies to ``mode``, derived from ``inputs``."""
    caps = venue_capabilities(inputs.adapters)
    all_venues = tuple(c.venue for c in caps)
    data_venues = tuple(c.venue for c in caps if c.data_capable)
    live_venues = tuple(c.venue for c in caps if c.live_capable)
    thresholds = ReadinessThresholds.from_settings(inputs.settings)
    policy = policy_for_mode(mode)
    criteria: list[Criterion] = []

    if mode is Mode.COLLECT:
        # Collect records markets and books; it places no orders, has no
        # execution engine and never calculates a fee (§90).
        criteria.append(criterion_data_venues(
            caps, minimum=1,
            purpose="collect records only adapters with "
                    "MARKET_DATA_OBSERVED or VERIFIED usable-data evidence "
                    "(§90)"))
        criteria.append(criterion_fee_freshness(
            inputs.fee_calc, all_venues, mode, inputs.today))
        criteria.append(criterion_kill_switch(inputs.kill_switch_engaged, mode))

    elif mode is Mode.HISTORICAL:
        # Replay is research: the only hard prerequisite is that there is
        # something recorded to replay. A stale (but present) fee schedule
        # still costs the replay and is labelled, not refused (§98).
        criteria.append(criterion_recorded_evidence(
            inputs.recorded, thresholds, mode))
        criteria.append(criterion_fee_freshness(
            inputs.fee_calc, all_venues, mode, inputs.today))
        fee_policy_note = policy_note(policy, inputs.fee_calc.max_schedule_age_days)
        fee_checks = inputs.fee_calc.schedule_checks(
            all_venues, policy, inputs.today)
        unpriced = [c for c in fee_checks if not c.calculable]
        if unpriced:
            criteria.append(warned(
                "fee_completeness", "Complete all-in fee schedules",
                f"replay runs, but {len(unpriced)} of {len(all_venues)} "
                f"venue(s) cannot be costed, so their opportunities are "
                f"reported with net profit n/a instead of a number (§24, D17) "
                f"— {_fee_gap_detail(unpriced)}",
                f"{len(all_venues) - len(unpriced)} of {len(all_venues)} venues"))
        else:
            criteria.append(passed(
                "fee_completeness", "Complete all-in fee schedules",
                f"every enabled venue's trade and settlement components are "
                f"calculable, so replay can cost what it measures "
                f"({fee_policy_note})",
                f"{len(all_venues)} of {len(all_venues)} venues"))
        criteria.append(criterion_settlement_timing(
            all_venues, mode, inputs.settlement_sources))
        criteria.append(criterion_matcher(inputs.matcher, mode))
        criteria.append(criterion_kill_switch(inputs.kill_switch_engaged, mode))

    elif mode is Mode.PAPER:
        # Paper is the rehearsal whose results are the live-readiness
        # evidence, so it carries the execution-facing gates (§44, §98).
        criteria.append(criterion_data_venues(
            caps, minimum=thresholds.minimum_data_venues,
            purpose="paper REST discovery uses adapters with observed "
                    "market-data evidence, and a cross-venue pair needs "
                    "two of them"))
        criteria.append(criterion_independent_pairs(
            data_venues, "paper evaluates only provenance-INDEPENDENT pairs"))
        criteria.append(criterion_matcher(inputs.matcher, mode))
        criteria.append(criterion_fee_completeness(
            inputs.fee_calc, all_venues, policy, inputs.today))
        criteria.append(criterion_fee_freshness(
            inputs.fee_calc, all_venues, mode, inputs.today))
        criteria.append(criterion_settlement_timing(
            all_venues, mode, inputs.settlement_sources))
        criteria.append(criterion_book_staleness(inputs.settings))
        criteria.append(criterion_kill_switch(inputs.kill_switch_engaged, mode))
        criteria.append(criterion_eligibility(
            inputs.jurisdiction, all_venues, mode))

    elif mode is Mode.LIVE:
        criteria.append(criterion_live_capable_venues(
            caps, thresholds.minimum_live_capable_venues))
        criteria.append(criterion_independent_pairs(
            live_venues, "live may only trade provenance-INDEPENDENT pairs"))
        criteria.append(criterion_matcher(inputs.matcher, mode))
        criteria.append(criterion_fee_completeness(
            inputs.fee_calc, all_venues, policy, inputs.today))
        criteria.append(criterion_fee_freshness(
            inputs.fee_calc, all_venues, mode, inputs.today))
        criteria.append(criterion_settlement_timing(
            all_venues, mode, inputs.settlement_sources))
        criteria.append(criterion_recorded_evidence(
            inputs.recorded, thresholds, mode))
        criteria.append(criterion_book_staleness(inputs.settings))
        criteria.append(criterion_kill_switch(inputs.kill_switch_engaged, mode))
        criteria.append(criterion_eligibility(
            inputs.jurisdiction, all_venues, mode))
        criteria.append(criterion_risk_limits(inputs.risk_limits))
        criteria.append(criterion_live_configuration(inputs.live_configuration))

    else:                                            # pragma: no cover
        raise ValueError(f"no readiness criteria defined for mode {mode!r}")

    return ModeReadiness(mode=mode, criteria=tuple(criteria))


def evaluate_all(inputs: ReadinessInputs) -> ReadinessReport:
    """Collect → historical → paper → live, the §58 progression."""
    modes = (Mode.COLLECT, Mode.HISTORICAL, Mode.PAPER, Mode.LIVE)
    return ReadinessReport(
        modes=tuple(evaluate_mode(m, inputs) for m in modes),
        thresholds=ReadinessThresholds.from_settings(inputs.settings))


# ---------------------------------------------------------------- rendering

_ADVISORY_FOOTER = (
    "Advisory only (§58): this report enables nothing. Live mode still "
    "requires config/live.yaml, the typed phrase I UNDERSTAND LIVE TRADING, "
    "and every LiveOrderFirewall check — and live orders remain prohibited in "
    "this engagement.")

_STATE_STYLE = {
    CriterionState.PASS: "green",
    CriterionState.WARN: "yellow",
    CriterionState.BLOCKED: "red",
}


def print_readiness(report: ReadinessReport, console: Console,
                    *, current_mode: Optional[Mode] = None) -> None:
    """Print every mode's criteria with the observation behind each verdict."""
    thresholds = report.thresholds
    console.print(
        "[bold]READINESS — derived from adapter, provenance, recorded-data, "
        "fee, settlement, compliance and risk state (§58, §98–§99)[/bold]",
        soft_wrap=True)
    policies = ", ".join(
        f"{m.value}={policy_for_mode(m).value}" for m in
        (Mode.LIVE, Mode.PAPER, Mode.HISTORICAL, Mode.COLLECT))
    console.print(f"Fee-freshness policy per mode: {policies}", soft_wrap=True)
    console.print(
        f"Evidence thresholds (config/settings.yaml → readiness): "
        f"{thresholds.minimum_recorded_snapshots} recorded snapshots, "
        f"{thresholds.minimum_analyzed_opportunities} analyzed opportunities, "
        f"{thresholds.minimum_live_capable_venues} live-capable venues",
        soft_wrap=True)

    for readiness in report.modes:
        marker = " (this run's mode)" if readiness.mode == current_mode else ""
        verdict = ("READY" if readiness.ready
                   else f"NOT READY — {len(readiness.blockers)} blocker(s)")
        console.print(
            f"\n[bold]{readiness.mode.value.upper()}[/bold]{marker}: "
            f"[bold]{verdict}[/bold]", soft_wrap=True)
        for c in readiness.criteria:
            style = _STATE_STYLE[c.state]
            value = f" = {escape(c.value)}" if c.value else ""
            console.print(
                f"  [{style}]{c.state.value:<7}[/{style}] "
                f"{escape(c.label)}{value} — {escape(c.reason)}",
                soft_wrap=True)

    live = report.live
    if live.ready:
        console.print("\n[bold green]LIVE MODE: READY (final decision is "
                      "yours)[/bold green]", soft_wrap=True)
    else:
        console.print("\n[bold red]LIVE MODE: NOT RECOMMENDED YET[/bold red]",
                      soft_wrap=True)
        console.print("  blockers (detail above): "
                      + "; ".join(escape(c.label) for c in live.blockers),
                      soft_wrap=True)
    if live.warnings:
        console.print("  warnings (detail above): "
                      + "; ".join(escape(c.label) for c in live.warnings),
                      soft_wrap=True)
    console.print(f"\n[dim]{escape(_ADVISORY_FOOTER)}[/dim]", soft_wrap=True)
