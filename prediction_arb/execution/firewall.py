"""LiveOrderFirewall (§54) — the final gate before any real order.

Every check must pass or the order raises FirewallBlocked. The firewall
is constructed only by LiveExecutionEngine; simulation engines have no
reference to it or to any live-capable adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from ..core.clock import age_ms, utcnow
from ..core.types import FirewallBlocked, Mode, OrderRequest
from ..risk import kill_switch


@dataclass
class FirewallContext:
    """Everything the firewall needs to verify one order."""
    app_mode: Mode
    allow_live_orders: bool
    live_confirmation_given: bool
    credentials_loaded: bool
    account_verified: bool
    contract_eligible: bool
    eligibility_reason: str
    independence_verified: bool
    match_approved: bool
    fees_verified: bool
    risk_violations: list[str]
    book_timestamp: Optional[datetime]
    max_book_age_ms: float
    opportunity_still_valid: Callable[[], bool]
    expected_price: Decimal
    max_price_deviation: Decimal
    quantity_limit: Decimal


class LiveOrderFirewall:
    def verify(self, request: OrderRequest, ctx: FirewallContext) -> None:
        checks: list[tuple[str, bool]] = [
            ("application in LIVE mode",
             ctx.app_mode == Mode.LIVE and request.mode == Mode.LIVE),
            ("live configuration loaded (allow_live_orders)",
             ctx.allow_live_orders),
            ("live credentials loaded", ctx.credentials_loaded),
            ("live account verified", ctx.account_verified),
            ("user explicitly confirmed live mode",
             ctx.live_confirmation_given),
            (f"contract eligibility ({ctx.eligibility_reason})",
             ctx.contract_eligible),
            ("venue relationship verified INDEPENDENT",
             ctx.independence_verified),
            ("contract match certificate APPROVED", ctx.match_approved),
            ("fee schedule verified current", ctx.fees_verified),
            ("risk limits passed", not ctx.risk_violations),
            ("order quantity within limit",
             request.quantity <= ctx.quantity_limit),
            ("no kill switch", not kill_switch.is_engaged()),
        ]
        # order book freshness
        book_age = age_ms(ctx.book_timestamp)
        checks.append((
            f"order book fresh (age={book_age if book_age is not None else 'None'}ms)",
            book_age is not None and book_age <= ctx.max_book_age_ms))
        # price sanity
        if request.limit_price is None:
            checks.append(("limit price present (market orders on illiquid "
                           "books prohibited)", False))
        else:
            deviation = abs(request.limit_price - ctx.expected_price)
            checks.append((
                "price within expected range",
                deviation <= ctx.max_price_deviation))
        # opportunity still exists at transmission time
        checks.append(("arbitrage opportunity still exists",
                       ctx.opportunity_still_valid()))

        failed = [name for name, ok in checks if not ok]
        if failed:
            raise FirewallBlocked(
                "ORDER BLOCKED by LiveOrderFirewall: "
                + "; ".join(failed))
