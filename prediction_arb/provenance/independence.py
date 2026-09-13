"""Venue-independence classifier.

Guaranteed arbitrage requires independent contract issuance and liquidity.  A
shared frontend is not decisive, and a shared third-party DCO alone is *not*
decisive: independent DCMs can clear through the same DCO while maintaining
separate contracts and books.  Missing or conflicting critical provenance fails
closed to UNKNOWN.
"""
from __future__ import annotations

from ..core.types import Independence, NormalizedContract
from .exchange_registry import ExchangeRecord, get_exchange

# Equal values in any of these fields collapse two routes into the same venue.
# Clearing is deliberately excluded; a DCO manages clearing risk, not order
# matching or contract issuance.
_CRITICAL_FIELDS = (
    "underlying_exchange",
    "contract_issuer",
    "order_matching_system",
    "settlement_authority",
)
_VERIFIED_CONFIDENCE = {"VERIFIED", "HIGH"}


def _critical_values(record: ExchangeRecord) -> tuple[str, ...]:
    return tuple(getattr(record, field) for field in _CRITICAL_FIELDS)


def is_independent(venue_a: str, venue_b: str) -> Independence:
    """Classify two route-qualified venue IDs conservatively.

    Use route-qualified frontend IDs for multi-venue apps.  A generic frontend
    such as ``robinhood`` intentionally returns UNKNOWN.
    """
    if venue_a == venue_b:
        return Independence.SAME_VENUE
    a = get_exchange(venue_a)
    b = get_exchange(venue_b)
    if a is None or b is None:
        return Independence.UNKNOWN

    a_values = _critical_values(a)
    b_values = _critical_values(b)
    if any(not value for value in (*a_values, *b_values)):
        return Independence.UNKNOWN

    for field in _CRITICAL_FIELDS:
        av = getattr(a, field)
        bv = getattr(b, field)
        if av == bv:
            return Independence.SAME_VENUE

    a_conf = a.independence_confidence.upper()
    b_conf = b.independence_confidence.upper()
    if a_conf in _VERIFIED_CONFIDENCE and b_conf in _VERIFIED_CONFIDENCE:
        return Independence.INDEPENDENT
    if a_conf == "UNKNOWN" or b_conf == "UNKNOWN":
        return Independence.UNKNOWN
    return Independence.LIKELY_INDEPENDENT


def contracts_independent(a: NormalizedContract,
                          b: NormalizedContract) -> Independence:
    """Contract-aware wrapper that detects registry/payload conflicts.

    Clearing-entity equality is intentionally non-dispositive.  Contract
    payloads that disagree with an evidence-backed route are treated as
    UNKNOWN rather than allowing a potentially false independence result.
    """
    venue_level = is_independent(a.venue, b.venue)
    if venue_level in (
        Independence.SAME_VENUE,
        Independence.LIKELY_SAME_VENUE,
        Independence.UNKNOWN,
    ):
        return venue_level

    if (a.underlying_exchange and b.underlying_exchange
            and a.underlying_exchange == b.underlying_exchange):
        return Independence.SAME_VENUE

    for contract in (a, b):
        record = get_exchange(contract.venue)
        if (record is not None and contract.underlying_exchange
                and record.underlying_exchange
                and contract.underlying_exchange != record.underlying_exchange):
            return Independence.UNKNOWN

    return venue_level


def eligible_for_guaranteed_arb(cls: Independence) -> bool:
    """Only fully VERIFIED INDEPENDENT pairs feed guaranteed arbitrage."""
    return cls == Independence.INDEPENDENT
