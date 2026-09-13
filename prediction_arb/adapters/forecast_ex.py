"""ForecastEx (IBKR) adapter — structured stub, API_DOCUMENTED_UNTESTED.

Phase 1 finding (docs/VENUE_RESEARCH.md, documentation re-verified
2026-09-13; live account test UNAVAILABLE):
  * ForecastEx LLC is a CFTC-registered DCM and DCO, independent of
    Kalshi's DCM/book. It is one of several now-known independent venues.
  * Retail access evaluated here is via IBKR's Client Portal, TWS, or FIX
    surfaces and must remain route-qualified as IBKR -> ForecastEx.
  * Microstructure is PAIRED_BID. A YES bid and opposite NO bid match when
    they sum to $1.00. Therefore synthetic YES ask = 1.00 - best NO bid
    (and vice versa). ForecastEx separately charges $0.01 per executed
    contract side; the fee is not embedded in a $1.01 pairing threshold.

Why this remains a stub:
  IBKR APIs require an authenticated brokerage session (IB Gateway/TWS or
  Client Portal). No owner-authorized IBKR account was available during
  Phase 1. Implementing calls without an authenticated test would violate
  the repository's honesty and fail-closed requirements.

Owner-approved activation steps (Phase 2/3 only):
  1. Use an IBKR paper account and enable prediction-market permissions.
  2. Run IB Gateway; set ``cp_gateway_base`` in ``config/venues.yaml``.
  3. Implement contract discovery, bid-side book normalization, and orders.
  4. Reconcile displayed and reported fees/fills before enabling paper use.
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
    reason = ("ForecastEx access requires an authenticated IBKR session. "
              "Documentation was verified, but no owner-authorized IBKR "
              "account was available for live testing; owner selection is "
              "also required at the Phase 1 checkpoint.")

    def __init__(self, cp_gateway_base: str | None = None):
        self.cp_gateway_base = cp_gateway_base

    def get_supported_order_types(self) -> OrderTypeSupport:
        # Limit-style opposite-side bids are documented; authenticated order
        # behavior has not been tested in this repository.
        return OrderTypeSupport(
            market=False, limit=True, ioc=False, fok=False, gtc=True,
            verified=False,
            source="IBKR ForecastTrader and ForecastEx rules (documentation "
                   "only; not live-tested)",
            verified_at=utcnow(),
        )

    def get_market_microstructure(self) -> MarketMicrostructure:
        return MarketMicrostructure(
            type=MicrostructureType.PAIRED_BID,
            has_order_book=True,
            has_pricing_function=False,
            quote_validity_ms=None,
            verified=False,
            source="CFTC ForecastEx rules filed 2026-03-02 and 2026-03-24: "
                   "YES_bid + NO_bid = $1.00; separate $0.01 fee per side",
        )
