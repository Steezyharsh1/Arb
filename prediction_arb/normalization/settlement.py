"""Settlement timing and capital lockup (§28, defect D18).

Capital is locked until the LATEST leg's money is usable again. That
moment is **not** the event's resolution time: a contract first resolves
(the outcome is determined), then the exchange/clearinghouse settles it,
and only then is the payout credited to the account. This module keeps
those three things apart and costs capital only against an explicitly
evidenced *capital-release time*.

Vocabulary (used by every registry entry below):

* **resolution basis** — the venue-documented timestamp the estimate is
  anchored to (for example Kalshi's ``latest_expiration_time``). It is
  never the matching-oriented ``NormalizedContract.settlement_time`` unless
  an entry says so explicitly.
* **post-resolution delay** — the venue-documented time from that basis
  until settlement credits the payout.
* **capital-release time** — basis + delay: the earliest moment the
  evaluation may assume the leg's capital is back in the venue account.
  It is *venue-account* availability; bank withdrawal timing is a
  different, slower thing and is not modelled.

Each venue entry carries an explicit meaning, an evidence status, source
URLs and a review date. A venue without an entry, an entry whose status
is ``UNKNOWN``, a contract that lacks the documented fields, or a release
time that is not in the future all yield ``None`` with a reason — the
caller must BLOCK. Unknown timing is never zero, and a long-dated contract
is never treated as settling immediately.

Evidence was re-read from official sources on 2026-10-10; no venue
account, settlement statement, or live API payload was available, so no
entry is ``OBSERVED``. Details: ``docs/TRADING_LOGIC.md`` ("Settlement
timing and capital lockup").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_UP, Decimal
from enum import Enum
from typing import Callable, Mapping, Optional, Sequence

from ..core.clock import ensure_utc, parse_iso_utc, utcnow
from ..core.types import NormalizedContract

_SECONDS_PER_DAY = Decimal("86400")
_DAYS_PER_YEAR = Decimal("365")
_COST_QUANTUM = Decimal("0.0001")


class TimingEvidenceStatus(str, Enum):
    """How well a venue's capital-release estimate is supported."""
    #: Official documentation defines every input; not yet reconciled
    #: against an actual settlement statement or observed payload.
    DOCUMENTED = "DOCUMENTED"
    #: Reconciled against real settlement records (none exist yet).
    OBSERVED = "OBSERVED"
    #: No defensible estimate. Lockup is unknown and the opportunity blocks.
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CapitalRelease:
    """When one leg's capital is expected back in the venue account."""
    venue: str
    contract_id: str
    #: The documented timestamp the estimate is anchored to, if any.
    resolution_basis: Optional[datetime]
    #: The documented delay from that basis to settlement credit, if any.
    post_resolution_delay: Optional[timedelta]
    #: basis + delay. ``None`` ⇒ unknown, with ``unknown_reason`` set.
    release_time: Optional[datetime]
    unknown_reason: Optional[str] = None

    @property
    def known(self) -> bool:
        return self.release_time is not None


Estimator = Callable[[NormalizedContract], CapitalRelease]


@dataclass(frozen=True)
class SettlementTimingSource:
    """One venue's settlement-timing evidence record.

    ``estimator`` is ``None`` exactly when ``status`` is ``UNKNOWN``: an
    entry without evidence cannot produce a number.
    """
    venue: str
    status: TimingEvidenceStatus
    #: What the produced release time means, in one sentence.
    release_meaning: str
    #: Which documented timestamp anchors the estimate.
    resolution_basis: str
    #: Which documented delay is added to the basis.
    post_resolution_delay: str
    sources: tuple[str, ...]
    reviewed_on: str
    #: What the sources do and do not establish.
    evidence: str
    #: Known ways the real release can be later than the estimate, or the
    #: evidence that is missing for an UNKNOWN entry.
    gaps: str
    estimator: Optional[Estimator] = None

    def __post_init__(self) -> None:
        if (self.estimator is None) != (
                self.status is TimingEvidenceStatus.UNKNOWN):
            raise ValueError(
                f"{self.venue}: an estimator is allowed only for a "
                f"non-UNKNOWN evidence status")


def _unknown(contract: NormalizedContract, reason: str) -> CapitalRelease:
    return CapitalRelease(contract.venue, contract.contract_id, None, None,
                          None, reason)


def _kalshi_release(contract: NormalizedContract) -> CapitalRelease:
    """Kalshi: ``latest_expiration_time`` + ``settlement_timer_seconds``.

    Both are required fields of the documented Market object and are read
    from the preserved venue payload (``contract.raw``), which the
    recorded store round-trips, so replay sees the same evidence.
    """
    raw = contract.raw if isinstance(contract.raw, dict) else {}
    latest_text = raw.get("latest_expiration_time")
    latest = parse_iso_utc(latest_text) if isinstance(latest_text, str) else None
    if latest is None:
        return _unknown(contract, "kalshi market payload has no parseable "
                                  "latest_expiration_time")
    timer = raw.get("settlement_timer_seconds")
    if isinstance(timer, bool) or not isinstance(timer, int) or timer < 0:
        return _unknown(contract, "kalshi market payload has no valid "
                                  "non-negative integer "
                                  "settlement_timer_seconds")
    expected = ensure_utc(contract.settlement_time)
    if expected is not None and expected > latest:
        return _unknown(
            contract,
            f"kalshi expected expiration {expected.isoformat()} is after the "
            f"documented latest expiration {latest.isoformat()} — "
            f"inconsistent payload")
    delay = timedelta(seconds=timer)
    return CapitalRelease(contract.venue, contract.contract_id, latest, delay,
                          latest + delay)


#: Evidence-backed settlement timing, keyed by venue. Venues absent from
#: this mapping have no evidence and block like an UNKNOWN entry.
PRODUCTION_SETTLEMENT_TIMING: Mapping[str, SettlementTimingSource] = {
    "kalshi": SettlementTimingSource(
        venue="kalshi",
        status=TimingEvidenceStatus.DOCUMENTED,
        release_meaning=(
            "conservative per-market bound on when the payout is credited to "
            "the Kalshi account: the latest possible expiration plus the "
            "market's documented post-determination settlement timer"),
        resolution_basis=(
            "Market.latest_expiration_time — 'Latest possible time for this "
            "market to expire' (required field). Deliberately NOT "
            "expected_expiration_time ('Time when this market is expected to "
            "expire'), which is an expectation, not a bound, and is what the "
            "adapter maps to NormalizedContract.settlement_time for matching"),
        post_resolution_delay=(
            "Market.settlement_timer_seconds — 'The amount of time after "
            "determination that the market settles' (required field); the "
            "settlement page says settlement transfers funds"),
        sources=(
            "https://docs.kalshi.com/api-reference/market/get-market.md",
            "https://docs.kalshi.com/getting_started/market_settlement",
        ),
        reviewed_on="2026-10-10",
        evidence=(
            "Field definitions and 'Positions are automatically resolved and "
            "funds transferred' are documented. The settlement page also says "
            "'Markets typically settle shortly after expiration, but timing "
            "can vary based on market type, data source availability, and "
            "manual review requirements', so a venue-wide zero-day constant "
            "is not supported; the former 0-day value was removed"),
        gaps=(
            "Documented only: no observed payload or settlement statement "
            "(K9 open). Treats determination as occurring no later than "
            "latest_expiration_time; disputed/amended markets and manual "
            "review can settle later and are not modelled. Bank withdrawal "
            "time is excluded"),
        estimator=_kalshi_release,
    ),
    "polymarket_us": SettlementTimingSource(
        venue="polymarket_us",
        status=TimingEvidenceStatus.UNKNOWN,
        release_meaning="none — no defensible estimate",
        resolution_basis=(
            "none. The adapter maps Market.endDate (documented only as "
            "'Market end date') to settlement_time, but the docs say 'Markets "
            "stay open until the outcome is known', so endDate is neither a "
            "resolution time nor a bound"),
        post_resolution_delay=(
            "none. Settlement 'begins once a market's outcome is finalized' "
            "and the Portfolio 'updates automatically when settlement "
            "completes'; no duration is published. Polymarket Clearing's "
            "Settlement Date is 'established under the terms of a Contract', "
            "which the adapter does not carry"),
        sources=(
            "https://docs.polymarket.us/learn/markets/contract-settlement",
            "https://docs.polymarket.us/learn/markets/market-resolution.md",
            "https://docs.polymarket.us/api-reference/markets/get-market-by-slug.md",
            "https://docs.polymarket.us/learn/get-started/cash.md",
            "https://www.cftc.gov/filings/orgrules/rules12312536031.pdf",
        ),
        reviewed_on="2026-10-10",
        evidence=(
            "Resolution timing 'varies by event'; proceeds from resolved "
            "markets 'return automatically to cash'. Neither a resolution "
            "bound nor a settlement delay is stated. The current exchange "
            "rulebook's contract chapter (10) and clearing Rule 5.3 were not "
            "readable with the available tooling (30-page PDF limit)"),
        gaps=(
            "Needs a per-contract documented resolution bound and settlement "
            "date (contract terms or an API field), or redacted settlement "
            "records showing resolution→cash timing. Until then lockup is "
            "UNKNOWN and every Polymarket US leg blocks"),
    ),
    "forecast_ex": SettlementTimingSource(
        venue="forecast_ex",
        status=TimingEvidenceStatus.UNKNOWN,
        release_meaning="none — no defensible customer-account estimate",
        resolution_basis=(
            "none. The adapter is an untested stub and does not establish "
            "which ForecastEx time it would map to settlement_time; contract "
            "terms allow the Resolution Time to be delayed when the source "
            "agency delays data or an event review is opened"),
        post_resolution_delay=(
            "exchange level only: contract terms say settlement occurs 'no "
            "later than the day following Expiration Time' unless under "
            "review (Rule 415), and Rule 603 settles daily at 13:00 CST, "
            "crediting the Member's Collateral Account. Crediting of an "
            "IBKR customer account is not documented"),
        sources=(
            "https://data.forecastex.com/regulatory/ForecastEx_LLC_Rulebook.pdf",
            "https://data.forecastex.com/regulatory/FESTermsandConditions.pdf",
        ),
        reviewed_on="2026-10-10",
        evidence=(
            "The former 0-day 'credits on resolution' value had no source and "
            "contradicts the documented next-day settlement; it was removed. "
            "The documented schedule is an exchange→Member bound with "
            "unbounded review/source-delay exceptions, not customer cash "
            "availability"),
        gaps=(
            "Needs the adapter's resolution-time mapping plus FCM/customer "
            "crediting evidence (IBKR statement) before any number is used"),
    ),
}

# The registry consulted by default. Tests may substitute a clearly
# labelled synthetic registry (tests/conftest.py), exactly as they use
# synthetic fee schedules; production code never mutates it.
_ACTIVE_TIMING: Mapping[str, SettlementTimingSource] = PRODUCTION_SETTLEMENT_TIMING


def settlement_timing_source(
        venue: str,
        sources: Optional[Mapping[str, SettlementTimingSource]] = None,
) -> Optional[SettlementTimingSource]:
    """The evidence record for ``venue`` (``None`` if there is none)."""
    return (sources if sources is not None else _ACTIVE_TIMING).get(venue)


def capital_release(
        contract: NormalizedContract,
        sources: Optional[Mapping[str, SettlementTimingSource]] = None,
) -> CapitalRelease:
    """Estimate when ``contract``'s capital is back in the venue account."""
    source = settlement_timing_source(contract.venue, sources)
    if source is None:
        return _unknown(contract, f"{contract.venue}: no settlement-timing "
                                  f"evidence entry")
    if source.estimator is None:
        return _unknown(contract, f"{contract.venue}: settlement timing "
                                  f"{source.status.value} — {source.gaps}")
    return source.estimator(contract)


def timedelta_days(delta: timedelta) -> Decimal:
    """Days in ``delta`` computed in Decimal from integer microseconds
    (no binary-float round-trip)."""
    micros = (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds
    return Decimal(micros) / (_SECONDS_PER_DAY * Decimal(1_000_000))


@dataclass(frozen=True)
class LockupAssessment:
    """Capital lockup for a set of legs evaluated at ``now``."""
    now: datetime
    releases: tuple[CapitalRelease, ...]
    #: Days from ``now`` until the LAST leg's capital is released.
    lockup_days: Optional[Decimal]
    #: Days between the earliest and latest leg release.
    spread_days: Optional[Decimal]
    #: Why ``lockup_days`` is unknown (empty when it is known).
    reasons: tuple[str, ...]


def assess_lockup(
        contracts: Sequence[NormalizedContract],
        now: Optional[datetime] = None,
        sources: Optional[Mapping[str, SettlementTimingSource]] = None,
) -> LockupAssessment:
    """Lockup until the latest leg's capital release, or unknown with reasons.

    A release time at or before ``now`` makes the lockup unknown rather
    than zero: the legs are being priced off live books, so they have not
    settled, and a passed bound means the documented timing has already
    failed for this contract. Zero would silently treat it as settled.
    """
    now = ensure_utc(now) if now is not None else utcnow()
    releases = tuple(capital_release(c, sources) for c in contracts)
    reasons = [r.unknown_reason for r in releases if r.unknown_reason]
    if not releases:
        reasons.append("no legs supplied")

    known_times = [r.release_time for r in releases if r.release_time]
    spread: Optional[Decimal] = None
    if releases and len(known_times) == len(releases):
        spread = timedelta_days(max(known_times) - min(known_times))

    for r in releases:
        if r.release_time is not None and r.release_time <= now:
            reasons.append(
                f"{r.venue}:{r.contract_id} capital-release time "
                f"{r.release_time.isoformat()} is not after evaluation time "
                f"{now.isoformat()} — the contract is still being priced, so "
                f"its remaining lockup is unknown")

    lockup = None
    if not reasons:
        lockup = timedelta_days(max(known_times) - now)
    return LockupAssessment(now=now, releases=releases, lockup_days=lockup,
                            spread_days=spread, reasons=tuple(reasons))


def capital_lockup_days(contracts: Sequence[NormalizedContract],
                        now: Optional[datetime] = None) -> Optional[Decimal]:
    """Days until the LAST leg's capital is released. ``None`` if unknown."""
    return assess_lockup(contracts, now).lockup_days


def settlement_delay_spread_days(contracts: Sequence[NormalizedContract]
                                 ) -> Optional[Decimal]:
    """Max difference in capital-release time between legs (T+0 vs T+2
    risk). ``None`` if any leg's release time is unknown."""
    return assess_lockup(contracts).spread_days


def settlement_delay_cost(capital: Decimal, lockup_days: Optional[Decimal],
                          annual_rate: Decimal) -> Optional[Decimal]:
    """Simple (non-compounded) cost of capital for the lockup period:
    ``capital × annual_rate × lockup_days / 365``, rounded UP to $0.0001 so a
    cost is never understated.

    Returns None when lockup is unknown — callers must treat that as a
    blocking condition, not as zero. Negative inputs are rejected.
    """
    if lockup_days is None:
        return None
    if capital < 0 or annual_rate < 0 or lockup_days < 0:
        raise ValueError("capital, annual_rate and lockup_days must be "
                         "non-negative")
    return (capital * annual_rate * lockup_days / _DAYS_PER_YEAR
            ).quantize(_COST_QUANTUM, rounding=ROUND_UP)
