"""Conservative venue-name canonicalization.

Brand aliases map to generic frontend IDs when the brand can route to multiple
DCMs. Only an explicitly route-qualified input may select a frontend route.
"""
from __future__ import annotations

_ALIASES = {
    # Direct exchanges and true aliases.
    "kalshi": "kalshi",
    "kalshiex": "kalshi",
    "forecastex": "forecast_ex",
    "forecast_ex": "forecast_ex",
    "polymarket_us": "polymarket_us",
    "qcx": "polymarket_us",
    "gemini": "gemini",
    "gemini_predictions": "gemini",
    "cdna": "cdna",
    "og": "og",
    "og_prediction_markets": "og",
    "cme": "cme",
    "railbird": "railbird",
    "dkex": "dkex",
    "ludlow": "ludlow",
    "novig": "novig",
    "rothera": "rothera",

    # Single-route frontend aliases represented by current evidence.
    "webull": "webull",
    "moomoo": "moomoo",
    "futu": "moomoo",
    "coinbase": "coinbase",

    # Generic multi-route frontends. These intentionally remain UNKNOWN in
    # independence checks until a route-qualified ID is supplied.
    "ibkr": "ibkr",
    "interactive_brokers": "ibkr",
    "forecasttrader": "ibkr",
    "robinhood": "robinhood",
    "fanduel": "fanduel",
    "fanduel_predicts": "fanduel",
    "draftkings": "draftkings",
    "draftkings_predictions": "draftkings",

    # Explicit route-qualified forms; no brand-only input reaches these.
    "ibkr_kalshi": "ibkr_kalshi",
    "ibkr_forecastex": "ibkr_forecastex",
    "ibkr_forecast_ex": "ibkr_forecastex",
    "ibkr_cme": "ibkr_cme",
    "robinhood_kalshi": "robinhood_kalshi",
    "robinhood_forecastex": "robinhood_forecastex",
    "robinhood_rothera": "robinhood_rothera",
    "robinhood_cdna": "robinhood_cdna",
    "fanduel_cme": "fanduel_cme",
    "fanduel_cdna": "fanduel_cdna",
    "draftkings_cme": "draftkings_cme",
    "draftkings_cdna": "draftkings_cdna",
    "draftkings_dkex": "draftkings_dkex",
}


def canonical_venue(name: str) -> str | None:
    return _ALIASES.get((name or "").strip().lower().replace(" ", "_"))
