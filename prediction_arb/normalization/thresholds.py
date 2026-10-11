"""Canonical threshold-operator vocabulary across venues (§18; defect D25).

The deterministic matcher (§18) compares the comparison operator each leg
declares. Venues spell those operators differently (`greater` vs `>`), and a
leg may not declare one at all. Comparing the raw venue tokens reports a
MISMATCH where the honest answer is "cannot verify" — a false negative that
permanently removes a possibly-equivalent cross-venue pair from the pipeline
(D25, reproduced 2026-10-10). "UNKNOWN is never permission" (§18, D25) does
not mean "UNKNOWN is a mismatch": neither an unmapped token nor a leg that
never declared an operator is *evidence* that the operators differ.

This module owns the single mapping from each venue's own vocabulary to the
shared canonical tokens, and a three-state read of one contract's operator:

* ``ABSENT``      — nothing declared (a plain yes/no market, or a venue with
                    no operator field at all);
* ``CANONICAL``   — mapped to a shared token;
* ``UNSUPPORTED`` — the venue declared a token this module cannot translate
                    (for example a strike defined by a payout mapping rather
                    than a numeric comparison).

``UNSUPPORTED`` is not "absent" and is not "the same as the other leg": it is
unverifiable. Only ``CANONICAL`` vs ``CANONICAL`` can be a match or a final
mismatch; every other combination is *unverified*, which the matcher routes to
REVIEW and which can never be APPROVED.

Nothing here invents a mapping. A token is translated only when the venue's
own documented vocabulary says what it means; otherwise it stays unknown.

Evidence (retrieved 2026-10-11, UTC):

* Kalshi `strike_type` is a real response field on `GET /markets` /
  `GET /markets/{ticker}` with a numeric strike (`floor_strike` /
  `cap_strike`) or a mapping (`functional_strike` / `custom_strike`) —
  https://docs.kalshi.com/api-reference/market/get-markets and
  https://docs.kalshi.com/getting_started/targets_and_milestones.
* `strike_type: "structured"` selects a structured target through
  `custom_strike` (same targets page): not a numeric comparison at all, so it
  maps to nothing here.
* The comparison tokens distinguish strictness in their own names
  (`greater` vs `greater_or_equal`, `less` vs `less_or_equal`), which is what
  this module translates. The payout *boundary* of a market whose settlement
  variable can equal the strike is a rule-level question for the semantic
  stage and the human review, not something this module decides.
* `between` markets carry both `floor_strike` and `cap_strike`, and `less`
  markets are cap-only (Kalshi API changelog, docs.kalshi.com/changelog).
  This module maps `between` to a distinct canonical token so it can only
  ever compare equal to another `between`; the cap is NOT modelled on
  `NormalizedContract`, so two `between` legs with equal floors but different
  caps are not distinguished here (recorded as a remaining gap, D40).
* Polymarket US declares no comparison-operator field in its documented
  market object (https://docs.polymarket.us/api-reference/market/overview),
  and no live payload has been observed (evidence row P7). The documented
  typed crypto terms (`assetPriceTerms.marketType` ABOVE_BELOW / RANGE, and
  `direction` for ONE_TOUCH) are not read by the adapter and their payout
  inclusivity for ABOVE_BELOW is not established, so no mapping is claimed;
  the adapter keeps ``threshold_operator=None`` and D40 tracks that gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

#: The shared vocabulary. A token means the same thing whichever venue
#: declared it. `between` is deliberately a single opaque token: an
#: inclusive range is not a one-sided inequality and must never compare equal
#: to `>` or `>=`.
CANONICAL_OPERATORS: tuple[str, ...] = (">", ">=", "<", "<=", "=", "between")

# Three-state read of one declared operator.
ABSENT = "absent"
CANONICAL = "canonical"
UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class OperatorVocabulary:
    """One venue's declared vocabulary and the evidence behind it."""
    venue: str
    status: str                       # DOCUMENTED / UNKNOWN
    operators: Mapping[str, str]      # venue token -> canonical operator
    sources: tuple[str, ...]
    reviewed_on: str
    evidence: str
    gaps: str


KALSHI_OPERATOR_VOCABULARY = OperatorVocabulary(
    venue="kalshi",
    status="DOCUMENTED",
    operators={
        "greater": ">",
        "greater_or_equal": ">=",
        "less": "<",
        "less_or_equal": "<=",
        "between": "between",
        # `functional`, `custom` and `structured` strikes define the YES
        # payoff through a mapping or a named target, not through a single
        # numeric comparison. They are deliberately NOT mapped: an unmapped
        # token stays UNSUPPORTED (never guessed, never silently treated as
        # "no operator").
    },
    sources=(
        "https://docs.kalshi.com/api-reference/market/get-markets",
        "https://docs.kalshi.com/getting_started/targets_and_milestones",
        "https://docs.kalshi.com/changelog",
    ),
    reviewed_on="2026-10-11",
    evidence=(
        "strike_type/floor_strike/cap_strike are documented response fields; "
        "the numeric tokens name their own strictness (greater vs "
        "greater_or_equal, less vs less_or_equal); between is documented as "
        "floor+cap and less as cap-only; structured/functional/custom use "
        "custom_strike or functional_strike instead of a numeric strike."
    ),
    gaps=(
        "cap_strike is not carried on NormalizedContract, so two `between` "
        "legs with different caps are not distinguished (D40). The payout "
        "boundary when the settlement variable equals the strike is not "
        "decided here."
    ),
)

POLYMARKET_US_OPERATOR_VOCABULARY = OperatorVocabulary(
    venue="polymarket_us",
    status="UNKNOWN",
    operators={},
    sources=("https://docs.polymarket.us/api-reference/market/overview",),
    reviewed_on="2026-10-11",
    evidence=(
        "The documented market object names no comparison-operator field. The "
        "typed crypto terms (assetPriceTerms.marketType, .strike, "
        ".rangeLower/.rangeUpper, .direction) are documented but are not read "
        "by the adapter, and ABOVE_BELOW payout inclusivity is not "
        "established from the documentation reviewed. No live payload has "
        "been observed (P7)."
    ),
    gaps=(
        "Keep threshold_operator=None (UNKNOWN) until an observed payload and "
        "an explicit inclusivity rule are reviewed. D40 tracks the adapter "
        "side; the pair is now visible as REVIEW instead of being silently "
        "rejected (D25)."
    ),
)

#: Venues with a mapping. A venue absent from this table can still emit
#: already-canonical tokens (synthetic/recorded data); it simply has no
#: venue-specific vocabulary to translate.
PRODUCTION_OPERATOR_VOCABULARIES: Mapping[str, OperatorVocabulary] = {
    KALSHI_OPERATOR_VOCABULARY.venue: KALSHI_OPERATOR_VOCABULARY,
    POLYMARKET_US_OPERATOR_VOCABULARY.venue: POLYMARKET_US_OPERATOR_VOCABULARY,
}


@dataclass(frozen=True)
class OperatorRead:
    """What one contract's ``threshold_operator`` declares, after mapping."""
    state: str
    raw: Optional[str] = None
    canonical: Optional[str] = None


def canonical_or_none(token: Optional[str]) -> Optional[str]:
    """Map one venue-native token to the shared vocabulary, or None.

    A token that is already canonical passes through unchanged, so recorded
    or synthetic contracts that already use the shared vocabulary keep
    comparing correctly. Anything else returns None: this function never
    guesses.
    """
    if token is None:
        return None
    text = str(token).strip().lower()
    if not text:
        return None
    if text in CANONICAL_OPERATORS:
        return text
    return None


def read_operator(venue: str, raw: Optional[str]) -> OperatorRead:
    """Read one leg's declared operator as ABSENT / CANONICAL / UNSUPPORTED.

    A raw token is first translated through the venue's own vocabulary and
    then checked against the shared one, so both a freshly normalized
    contract and a legacy row that still holds the venue's raw token read the
    same way.
    """
    text = None if raw is None else str(raw).strip()
    if not text:
        return OperatorRead(ABSENT)
    vocabulary = PRODUCTION_OPERATOR_VOCABULARIES.get(venue)
    if vocabulary is not None:
        mapped = vocabulary.operators.get(text.lower())
        if mapped is not None:
            return OperatorRead(CANONICAL, raw=text, canonical=mapped)
    already = canonical_or_none(text)
    if already is not None:
        return OperatorRead(CANONICAL, raw=text, canonical=already)
    return OperatorRead(UNSUPPORTED, raw=text)


def _describe(read: OperatorRead) -> str:
    if read.state == CANONICAL:
        return repr(read.canonical)
    if read.state == UNSUPPORTED:
        return f"unsupported token {read.raw!r}"
    return "no operator"


def compare_operators(venue_a: str, operator_a: Optional[str],
                      venue_b: str, operator_b: Optional[str]) -> tuple[str, str]:
    """Compare two legs' operators.

    Returns ``(verdict, reason)`` with verdict one of:

    * ``"match"``      — both legs declare the same canonical operator, or
                         neither declares one (the check is vacuous, as §18
                         specifies for contracts with no threshold);
    * ``"mismatch"``   — both legs declare a canonical operator and they
                         differ. This stays a FINAL deterministic reject;
    * ``"unverified"`` — anything else (one leg declares and the other does
                         not, or a token cannot be translated). Not evidence
                         of disagreement: review-only, never APPROVED.
    """
    read_a = read_operator(venue_a, operator_a)
    read_b = read_operator(venue_b, operator_b)

    if read_a.state == ABSENT and read_b.state == ABSENT:
        return "match", ""
    if read_a.state == CANONICAL and read_b.state == CANONICAL:
        if read_a.canonical == read_b.canonical:
            return "match", ""
        return "mismatch", (
            f"declared operators differ: {read_a.canonical!r} != "
            f"{read_b.canonical!r}")
    return "unverified", (
        "threshold_operator unverified: "
        f"{venue_a} declares {_describe(read_a)} and {venue_b} declares "
        f"{_describe(read_b)} — the two cannot be compared, so this is not a "
        "mismatch; REVIEW only, never APPROVED")


def normalize_units(units: Optional[str]) -> Optional[str]:
    """Normalize a declared unit for comparison.

    Only whitespace and letter case are normalized. Different spellings of
    the same unit ("USD" vs "dollars") are NOT treated as equal: that would
    be an inference the evidence does not support, so they remain a declared
    difference.
    """
    if units is None:
        return None
    text = str(units).strip().casefold()
    return text or None


def compare_units(units_a: Optional[str],
                  units_b: Optional[str]) -> tuple[str, str]:
    """Compare two legs' declared units, with the same three verdicts.

    Declared-vs-declared equality is a match; declared-vs-declared difference
    is a final mismatch; a value on one leg and nothing on the other is
    unverified (review-only), never a mismatch.
    """
    norm_a = normalize_units(units_a)
    norm_b = normalize_units(units_b)

    if norm_a is None and norm_b is None:
        return "match", ""
    if norm_a is not None and norm_b is not None:
        if norm_a == norm_b:
            return "match", ""
        return "mismatch", f"declared units differ: {norm_a!r} != {norm_b!r}"
    declared = norm_a if norm_a is not None else norm_b
    return "unverified", (
        f"units unverified: one leg declares {declared!r} and the other "
        "declares no units — the two cannot be compared, so this is not a "
        "mismatch; REVIEW only, never APPROVED")
