"""Venue → underlying exchange / clearing / issuer registry (§14).

Every record carries evidence: source URLs and verification dates.
Records were populated ONLY from official announcements, official API
documentation, and CFTC registration status — never from assumption.
See docs/VENUE_RESEARCH.md for the full research trail.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ProvenanceRecord:
    frontend: str
    underlying_exchange: str      # "" == UNKNOWN
    clearing_entity: str
    contract_issuer: str
    matching_engine: str
    liquidity_source: str
    settlement_authority: str
    order_routing: str
    confidence: str               # HIGH / MEDIUM / LOW
    version: int
    sources: tuple[str, ...] = field(default_factory=tuple)
    verified_at: str = ""         # ISO date


_REGISTRY: dict[str, ProvenanceRecord] = {
    "kalshi": ProvenanceRecord(
        frontend="kalshi",
        underlying_exchange="kalshi_dcm",
        clearing_entity="kalshi_klear_dco",
        contract_issuer="kalshi_dcm",
        matching_engine="kalshi_clob",
        liquidity_source="kalshi_clob",
        settlement_authority="kalshi_dcm",
        order_routing="direct",
        confidence="HIGH",
        version=1,
        sources=(
            "https://kalshi.com (CFTC DCM approved Nov 2020)",
            "CFTC DCO registration: Kalshi Klear LLC (Aug 2024)",
            "https://api.elections.kalshi.com/trade-api/v2 (verified live 2026-08-30)",
        ),
        verified_at="2026-08-30",
    ),
    "webull": ProvenanceRecord(
        frontend="webull",
        underlying_exchange="kalshi_dcm",
        clearing_entity="kalshi_klear_dco",
        contract_issuer="kalshi_dcm",
        matching_engine="kalshi_clob",
        liquidity_source="kalshi_clob",
        settlement_authority="kalshi_dcm",
        order_routing="broker_fcm_into_kalshi",
        confidence="HIGH",
        version=1,
        sources=(
            "Bloomberg 2025-02-12: Webull partners with Kalshi for event contracts",
            "PR Newswire 2025-02-12: 'event contracts ... provided by Kalshi'; "
            "'Webull plans to become a clearing member of Kalshi'",
        ),
        verified_at="2026-08-30",
    ),
    "moomoo": ProvenanceRecord(
        frontend="moomoo",
        underlying_exchange="kalshi_dcm",
        clearing_entity="kalshi_klear_dco",
        contract_issuer="kalshi_dcm",
        matching_engine="kalshi_clob",
        liquidity_source="kalshi_clob",
        settlement_authority="kalshi_dcm",
        order_routing="broker_fcm_into_kalshi",
        confidence="HIGH",
        version=1,
        sources=(
            "GlobeNewswire 2026-06-04: Moomoo partners with Kalshi; Moomoo "
            "Financial Inc. is the FCM, contracts trade on Kalshi's exchange",
        ),
        verified_at="2026-08-30",
    ),
    "forecast_ex": ProvenanceRecord(
        frontend="forecast_ex",
        underlying_exchange="forecastex_dcm",
        clearing_entity="forecastex_dco",
        contract_issuer="forecastex_dcm",
        matching_engine="forecastex_paired_bid",
        liquidity_source="forecastex_book",
        settlement_authority="forecastex_dcm",
        order_routing="ibkr_fcm_into_forecastex",
        confidence="HIGH",
        version=1,
        sources=(
            "PR Newswire 2024-07-31: ForecastEx LLC launch; CFTC-registered "
            "DCM and DCO; wholly owned subsidiary of Interactive Brokers Group",
            "https://forecastex.com/about",
        ),
        verified_at="2026-08-30",
    ),
}


def get_provenance(venue: str) -> ProvenanceRecord | None:
    """Return provenance for a venue, or None if UNKNOWN (never guess)."""
    return _REGISTRY.get(venue)


def all_venues() -> list[str]:
    return sorted(_REGISTRY)
