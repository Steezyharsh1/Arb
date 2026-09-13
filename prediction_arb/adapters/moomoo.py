"""Moomoo adapter — STUB, status NO_API_AVAILABLE.

Research finding (docs/VENUE_RESEARCH.md, re-verified 2026-09-13):
  1. Moomoo event contracts launched June 2026 in partnership with
     Kalshi: Moomoo Financial Inc. is the FCM; the venue is Kalshi's
     exchange (same order book as the Kalshi adapter reads directly).
  2. The Futu OpenAPI / OpenD documentation contains NO event-contract
     endpoints as of the research date.

Both facts independently disqualify Moomoo as an arbitrage leg:
SAME_VENUE with Kalshi, and NO_API_AVAILABLE for event contracts.
If Futu ships event-contract API support, this stub is where it would
be implemented — wrapped in asyncio.to_thread because the Futu SDK is
synchronous — and it must still be modeled as kalshi_dcm in provenance.
"""
from __future__ import annotations

from ..core.types import APIStatus
from .stubs import StubAdapter


class MoomooAdapter(StubAdapter):
    venue = "moomoo"
    api_status = APIStatus.NO_API_AVAILABLE
    reason = ("Futu OpenAPI/OpenD has no documented event-contract "
              "endpoints (re-checked 2026-09-13); Moomoo event contracts are "
              "also Kalshi-routed (SAME_VENUE), so even with an API there "
              "is no independent liquidity to arbitrage.")
