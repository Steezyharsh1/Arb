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
from .evidence import AdapterEvidence, EvidenceClaim, EvidenceState
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

    def get_evidence(self) -> AdapterEvidence:
        return AdapterEvidence(
            documentation=EvidenceClaim(
                EvidenceState.DOCUMENTED,
                as_of="2026-09-13",
                source="docs/VENUE_RESEARCH.md V1 and F1-F4",
                detail="Exchange identity and route documentation reviewed; retail API requires an authenticated IBKR session."),
            connectivity=EvidenceClaim(
                EvidenceState.NOT_OBSERVED,
                as_of="2026-10-10",
                detail="No authorized IBKR account/session connectivity test is recorded."),
            usable_market_data=EvidenceClaim(
                EvidenceState.NOT_IMPLEMENTED,
                as_of="2026-10-10",
                detail="This adapter is a structured stub and does not fetch ForecastEx market data."),
            full_adapter=EvidenceClaim(
                EvidenceState.NOT_IMPLEMENTED,
                as_of="2026-10-10",
                detail="Market-data and order surfaces are not implemented."),
            latest_smoke=EvidenceClaim(EvidenceState.NOT_APPLICABLE),
            surfaces=("IBKR Client Portal/TWS/FIX route (stub; not connected)",),
        )
