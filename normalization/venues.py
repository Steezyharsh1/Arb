"""Venue name canonicalization. One place maps external spellings to the
internal venue ids used across provenance, fees, and adapters."""
from __future__ import annotations

_ALIASES = {
    "kalshi": "kalshi",
    "kalshiex": "kalshi",
    "webull": "webull",
    "moomoo": "moomoo",
    "futu": "moomoo",
    "forecastex": "forecast_ex",
    "forecast_ex": "forecast_ex",
    "ibkr": "forecast_ex",
    "interactive_brokers": "forecast_ex",
    "forecasttrader": "forecast_ex",
}


def canonical_venue(name: str) -> str | None:
    return _ALIASES.get((name or "").strip().lower().replace(" ", "_"))
