"""Deterministic venue-independence engine (§15, §66).

UNKNOWN is never treated as independent. Cross-venue guaranteed arbitrage
requires INDEPENDENT (not even LIKELY_INDEPENDENT is auto-tradable when
`block_unknown_venue_relationship` is on, which is the default).

Independence can be evaluated at venue level and refined at contract level
(§66): if two contracts share a contract identifier namespace or the same
underlying order book, they are SAME_VENUE regardless of frontend.
"""
from __future__ import annotations

from ..core.types import Independence, NormalizedContract
from .exchange_registry import get_provenance


def is_independent(venue_a: str, venue_b: str) -> Independence:
    if venue_a == venue_b:
        return Independence.SAME_VENUE

    pa, pb = get_provenance(venue_a), get_provenance(venue_b)
    if pa is None or pb is None:
        return Independence.UNKNOWN
    if not pa.underlying_exchange or not pb.underlying_exchange:
        return Independence.UNKNOWN

    same_checks = [
        pa.underlying_exchange == pb.underlying_exchange,
        pa.matching_engine == pb.matching_engine,
        pa.clearing_entity == pb.clearing_entity,
        pa.liquidity_source == pb.liquidity_source,
        pa.contract_issuer == pb.contract_issuer,
        pa.settlement_authority == pb.settlement_authority,
    ]
    if any(same_checks):
        # Sharing ANY of exchange/matching/clearing/liquidity/issuer/
        # settlement collapses the pair.
        if all(same_checks):
            return Independence.SAME_VENUE
        return Independence.LIKELY_SAME_VENUE

    if pa.confidence == "HIGH" and pb.confidence == "HIGH":
        return Independence.INDEPENDENT
    return Independence.LIKELY_INDEPENDENT


def contracts_independent(a: NormalizedContract, b: NormalizedContract) -> Independence:
    """Contract-level refinement (§66). Same contract identifier on the
    same underlying exchange == same contract, full stop."""
    venue_level = is_independent(a.venue, b.venue)
    if venue_level in (Independence.SAME_VENUE, Independence.LIKELY_SAME_VENUE,
                       Independence.UNKNOWN):
        return venue_level
    if (a.underlying_exchange and a.underlying_exchange == b.underlying_exchange):
        return Independence.SAME_VENUE
    return venue_level


def eligible_for_guaranteed_arb(cls: Independence) -> bool:
    """Only INDEPENDENT pairs may feed the guaranteed-arbitrage engine."""
    return cls == Independence.INDEPENDENT
