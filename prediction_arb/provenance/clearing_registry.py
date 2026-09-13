"""Clearing entity registry (§14). Records the known CFTC-registered
clearing organizations relevant to this system, with evidence."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClearingEntity:
    entity_id: str
    legal_name: str
    regulator: str
    registration: str
    parent: str
    sources: tuple[str, ...]
    verified_at: str


_CLEARING: dict[str, ClearingEntity] = {
    "kalshi_klear_dco": ClearingEntity(
        entity_id="kalshi_klear_dco",
        legal_name="Kalshi Klear LLC",
        regulator="CFTC",
        registration="DCO (approved Aug 2024)",
        parent="Kalshi Inc.",
        sources=("CFTC DCO registry", "kalshi.com"),
        verified_at="2026-08-30",
    ),
    "forecastex_dco": ClearingEntity(
        entity_id="forecastex_dco",
        legal_name="ForecastEx LLC",
        regulator="CFTC",
        registration="DCM + DCO (launched 2024-08-01)",
        parent="Interactive Brokers Group, Inc.",
        sources=("PR Newswire 2024-07-31", "forecastex.com/about"),
        verified_at="2026-08-30",
    ),
}


def get_clearing_entity(entity_id: str) -> ClearingEntity | None:
    return _CLEARING.get(entity_id)
