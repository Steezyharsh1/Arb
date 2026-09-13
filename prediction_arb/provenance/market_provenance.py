"""Per-market provenance resolution (§66).

Independence is contract-specific when necessary: a frontend may route
different contract categories to different underlying venues in the
future. This module resolves the underlying exchange for a specific
market, defaulting to the venue-level registry, and returns None
(UNKNOWN) when it cannot.
"""
from __future__ import annotations

from typing import Optional

from .exchange_registry import ProvenanceRecord, get_provenance


def resolve_market_provenance(venue: str, market_id: str) -> Optional[ProvenanceRecord]:
    """Resolve provenance for a specific market on a venue.

    v1: all known venues route every event contract to a single underlying
    exchange, so this delegates to the venue-level registry. The signature
    exists so that per-category routing can be added without touching the
    arbitrage engine. Returns None when provenance is UNKNOWN.
    """
    del market_id  # reserved for per-category routing
    return get_provenance(venue)
