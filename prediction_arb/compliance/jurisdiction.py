"""Evidence ledger for jurisdiction/category eligibility.

Nothing is inferred to be legal merely because a venue is federally regulated or
available elsewhere. UNKNOWN and RESTRICTED both block live trading. Utah sports
contracts are explicitly RESTRICTED; non-sports categories remain UNKNOWN until
venue- and category-specific access is verified.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class EligibilityStatus(str, Enum):
    ELIGIBLE = "ELIGIBLE"
    RESTRICTED = "RESTRICTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EligibilityRecord:
    jurisdiction: str
    venue: str                    # "*" = all venues
    contract_category: str        # "*" = category default
    status: EligibilityStatus
    effective_date: str
    source: str
    verified_at: str
    notes: str = ""


# Utah Code includes proposition bets in gambling, and the final district
# judgment in KalshiEX LLC v. Cox rejected CEA preemption for Kalshi sports
# contracts. The statute is venue-neutral, so the restriction is a wildcard.
_RECORDS: list[EligibilityRecord] = [
    EligibilityRecord(
        jurisdiction="US-UT",
        venue="*",
        contract_category="sports",
        status=EligibilityStatus.RESTRICTED,
        effective_date="2026-05-06",
        source="https://le.utah.gov/xcode/Title76/Chapter9/C76-9-S1401_2026050620260506.pdf; https://www.courthousenews.com/wp-content/uploads/2026/08/kalshi-utah-shelby-decision.pdf",
        verified_at="2026-09-13",
        notes="Utah's statute expressly includes proposition bets. On "
              "2026-08-04 the district court entered final judgment for Utah "
              "on Kalshi's sports-contract preemption claim. This is an "
              "execution restriction, not legal advice."),
]

_SPORTS_CATEGORY_PREFIXES = (
    "KXMLB", "KXNFL", "KXNBA", "KXWNBA", "KXNHL", "KXEPL", "KXMLS",
    "KXNCAA", "KXUFC", "KXPGA", "KXATP", "KXWTA", "KXLIGA",
    "KXBRASIL", "KXSERIE", "KXBUND", "KXCHAMP", "KXFIFA",
    "KXWORLDCUP",
)
_SPORTS_WORDS = (
    "SPORT", "SPORTS", "BASEBALL", "BASKETBALL", "FOOTBALL", "SOCCER", "HOCKEY",
    "TENNIS", "GOLF", "BOXING", "MMA", "CRICKET", "RUGBY", "RACING",
    "NASCAR", "FORMULA 1", "OLYMPIC", "WORLD CUP", "NFL", "MLB", "NBA",
    "WNBA", "NHL", "NCAA", "MLS", "EPL", "UEFA", "FIFA", "UFC", "PGA",
    "ATP", "WTA", "MATCH WINNER", "GAME WINNER", "PLAYER PROP",
    "POINT SPREAD", "RUN LINE", "MONEYLINE", "OVER UNDER",
    "TOTAL POINTS", "TOTAL GOALS", "FINAL SCORE", "HOME TEAM", "AWAY TEAM",
    "TOUCHDOWN", "PASSING YARDS", "RUSHING YARDS", "RECEIVING YARDS",
    "HOME RUN", "STRIKEOUT", "REBOUNDS", "ASSISTS",
)


def category_for(venue: str, category_hint: str) -> str:
    """Map clear sports hints to the restricted category.

    Unrecognized hints remain the default category, which is UNKNOWN (and
    therefore still fails closed for live use).
    """
    del venue  # reserved for future venue-specific category maps
    hint = (category_hint or "").upper()
    if any(hint.startswith(prefix) for prefix in _SPORTS_CATEGORY_PREFIXES):
        return "sports"
    # Match whole normalized tokens/phrases so short league names such as EPL
    # do not accidentally match unrelated words such as "replacement".
    normalized = f" {re.sub(r'[^A-Z0-9]+', ' ', hint).strip()} "
    if any(f" {word} " in normalized for word in _SPORTS_WORDS):
        return "sports"
    return "*"


def get_eligibility(jurisdiction: str, venue: str,
                    contract_category: str) -> EligibilityRecord:
    """Return the most-specific ledger record, otherwise explicit UNKNOWN."""
    category = category_for(venue, contract_category)
    matches = [
        record for record in _RECORDS
        if record.jurisdiction == jurisdiction
        and record.venue in (venue, "*")
        and record.contract_category in (category, "*")
    ]
    if matches:
        # exact venue/category wins over wildcard venue/category
        matches.sort(
            key=lambda record: (
                record.venue == venue,
                record.contract_category == category,
            ),
            reverse=True,
        )
        return matches[0]

    return EligibilityRecord(
        jurisdiction=jurisdiction,
        venue=venue,
        contract_category=category,
        status=EligibilityStatus.UNKNOWN,
        effective_date="",
        source="No authoritative venue/category eligibility evidence was "
               "verified in the Phase 1 ledger.",
        verified_at="2026-09-13",
        notes="Live execution blocked; authenticated account verification and "
              "owner/legal review are required.")
