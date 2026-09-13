"""ForecastEx (IBKR) adapter — structured stub, status API_DOCUMENTED_UNTESTED.

Research finding (docs/VENUE_RESEARCH.md, verified 2026-08-30):
  * ForecastEx LLC is a CFTC-registered DCM **and** DCO, wholly owned by
    Interactive Brokers Group — genuinely INDEPENDENT of Kalshi. It is the
    only independent counterpart venue available to this system.
  * Access is via IBKR's API surfaces (Client Portal Web API, TWS API,
    FIX). Forecast contracts route like other IBKR instruments.
  * Microstructure is PAIRED_BID: there are no asks. Only YES bids and
    NO bids exist; a trade occurs when YES_bid + NO_bid = $1.01 ($0.01 is
    the exchange fee). The synthetic YES ask = 1.01 - best NO bid.

Why this is a stub in v1:
  IBKR's API requires an authenticated brokerage session (running IB
  Gateway / TWS, or a Client Portal session). No IBKR account is
  available in this build environment, so every capability would be
  UNVERIFIED code pretending to work. Per spec §5/§84, this adapter is a
  properly structured stub: importable, typed, honest about status, and
  raising NotImplementedError with exact instructions.

To activate:
  1. Open an IBKR account (paper account first), enable ForecastEx.
  2. Run IB Gateway locally; set cp_gateway_base in config/venues.yaml.
  3. Implement the marked methods against the Client Portal Web API
     (contract search: /iserver/secdef/search with secType filtering;
     books: /iserver/marketdata/snapshot; orders: /iserver/account/orders).
  4. Verify against the paper account before flipping api_status.
"""
from __future__ import annotations

from ..core.clock import utcnow
from ..core.types import (
    APIStatus,
    MarketMicrostructure,
    MicrostructureType,
    OrderTypeSupport,
)
from .stubs import StubAdapter


class ForecastExAdapter(StubAdapter):
    venue = "forecast_ex"
    api_status = APIStatus.API_DOCUMENTED_UNTESTED
    reason = ("ForecastEx is accessible only through an authenticated IBKR "
              "session (IB Gateway / Client Portal API). No IBKR account is "
              "configured, so this adapter is a structured stub. Follow the "
              "activation steps in adapters/forecast_ex.py.")

    def __init__(self, cp_gateway_base: str | None = None):
        self.cp_gateway_base = cp_gateway_base

    def get_supported_order_types(self) -> OrderTypeSupport:
        # From IBKR/ForecastEx documentation: limit-style bids only
        # (paired-bid mechanism). Market orders do not exist on ForecastEx.
        return OrderTypeSupport(
            market=False, limit=True, ioc=False, fok=False, gtc=True,
            verified=False,
            source="IBKR ForecastTrader docs (paired-bid; docs only, "
                   "not live-tested)",
            verified_at=utcnow(),
        )

    def get_market_microstructure(self) -> MarketMicrostructure:
        return MarketMicrostructure(
            type=MicrostructureType.PAIRED_BID,
            has_order_book=True,      # bid-only book, both sides
            has_pricing_function=False,
            quote_validity_ms=None,
            verified=False,
            source="ForecastEx launch documentation (PR Newswire "
                   "2024-07-31); YES_bid + NO_bid = $1.01 pairing",
        )
