"""Jurisdiction/eligibility tracking (§35). Nothing is hard-coded as
legal; every entry carries status, source, and verification date, and
UNKNOWN blocks live trading (paper/research may continue)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EligibilityRecord:
    jurisdiction: str
    venue: str
    contract_category: str     # "*" = default for venue
    status: EligibilityStatus
    effective_date: str
    source: str
    verified_at: str
    notes: str = ""


# Populated from research (docs/VENUE_RESEARCH.md). Categories not listed
# fall back to the venue's "*" record; venues not listed are UNKNOWN.
_RECORDS: list[EligibilityRecord] = [
    EligibilityRecord(
        jurisdiction="US-UT", venue="kalshi", contract_category="*",
        status=EligibilityStatus.ELIGIBLE,
        effective_date="2020-11-01",
        source="Kalshi is a CFTC DCM available across US states for "
               "non-sports categories; API trading documented/permitted.",
        verified_at="2026-08-30"),
    EligibilityRecord(
        jurisdiction="US-UT", venue="kalshi", contract_category="sports",
        status=EligibilityStatus.UNKNOWN,
        effective_date="2025-02-01",
        source="State gaming regulators have contested sports event "
               "contracts in multiple states; Utah status unresolved.",
        verified_at="2026-08-30",
        notes="Blocked for live trading until verified."),
    EligibilityRecord(
        jurisdiction="US-UT", venue="forecast_ex", contract_category="*",
        status=EligibilityStatus.ELIGIBLE,
        effective_date="2024-08-01",
        source="ForecastEx via IBKR available to US customers; "
               "economics/climate/politics categories.",
        verified_at="2026-08-30"),
    EligibilityRecord(
        jurisdiction="US-UT", venue="forecast_ex", contract_category="sports",
        status=EligibilityStatus.UNKNOWN,
        effective_date="2025-11-01",
        source="Sports contracts self-certified late 2025; state-level "
               "treatment unresolved.",
        verified_at="2026-08-30"),
    EligibilityRecord(
        jurisdiction="US-UT", venue="webull", contract_category="*",
        status=EligibilityStatus.UNKNOWN,
        effective_date="2025-02-12",
        source="Webull automated-trading terms for event contracts "
               "unverified.",
        verified_at="2026-08-30"),
    EligibilityRecord(
        jurisdiction="US-UT", venue="moomoo", contract_category="*",
        status=EligibilityStatus.UNKNOWN,
        effective_date="2026-06-04",
        source="Moomoo state-availability disclosure: 'certain contracts "
               "unavailable in select U.S. states' — unverified for UT.",
        verified_at="2026-08-30"),
]

_SPORTS_CATEGORY_PREFIXES = (
    "KXMLB", "KXNFL", "KXNBA", "KXNHL", "KXEPL", "KXMLS", "KXNCAA",
    "KXUFC", "KXPGA", "KXATP", "KXWTA", "KXLIGA", "KXBRASIL", "KXSERIE",
    "KXBUND", "KXCHAMP", "KXFIFA", "KXWORLDCUP",
)


def category_for(venue: str, category_hint: str) -> str:
    hint = (category_hint or "").upper()
    if any(hint.startswith(p) for p in _SPORTS_CATEGORY_PREFIXES):
        return "sports"
    return "*"


def get_eligibility(jurisdiction: str, venue: str,
                    contract_category: str) -> EligibilityRecord:
    cat = category_for(venue, contract_category)
    best: EligibilityRecord | None = None
    for r in _RECORDS:
        if r.jurisdiction != jurisdiction or r.venue != venue:
            continue
        if r.contract_category == cat:
            return r
        if r.contract_category == "*":
            best = r
    if best is not None:
        return best
    return EligibilityRecord(
        jurisdiction=jurisdiction, venue=venue, contract_category=cat,
        status=EligibilityStatus.UNKNOWN, effective_date="",
        source="no record on file", verified_at="")
