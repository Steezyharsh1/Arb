"""Webull adapter — STUB, status SAME_VENUE_ROUTE.

Research finding (docs/VENUE_RESEARCH.md, verified 2026-08-30):
Webull event contracts are PROVIDED BY KALSHI (official announcements,
Feb 2025). Webull is an FCM routing customer flow into Kalshi's order
book — the same book the Kalshi adapter already reads directly.

Consequently Webull has ZERO independent arbitrage value: any apparent
Kalshi/Webull price difference is the same liquidity pool seen through
two frontends, plus possible broker-layer fees (UNKNOWN → the fee engine
marks Webull FEE_DATA_INCOMPLETE and blocks execution regardless).

Webull DOES have an official OpenAPI with documented event-contract
support (developer.webull.com). If a future use case emerges (fee/route
optimization into Kalshi), this stub is the place to implement it. It
must still be modeled as the kalshi_dcm venue in provenance — which it
already is (see provenance/exchange_registry.py).
"""
from __future__ import annotations

from ..core.types import APIStatus
from .stubs import StubAdapter


class WebullAdapter(StubAdapter):
    venue = "webull"
    api_status = APIStatus.SAME_VENUE_ROUTE
    reason = ("Webull event contracts route to Kalshi's exchange (same order "
              "book, same clearing). No independent liquidity; broker-layer "
              "fees unverified. Implementing this adapter cannot create "
              "arbitrage opportunities against Kalshi.")
