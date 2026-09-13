"""LiveExecutionEngine — the ONLY class that may transmit real orders
(§44, §54, §60, §61).

Construction requirements (all enforced, not conventions):
  * AppConfig mode must be LIVE with allow_live_orders=true (validated at
    load time by core.config).
  * The interactive confirmation phrase must have been entered (§47);
    the engine takes the confirmation token, not a boolean default.
  * Adapters must be constructed with allow_order_placement=True, which
    main.py only does in live mode.

Order path: write intent (PENDING) → firewall → transmit → SUBMITTED →
poll/confirm → FILLED/REJECTED (§61). On timeout, the venue is queried
before any retry (§72) — never assume a timed-out order failed.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Optional

from ..adapters.base import PredictionMarketAdapter
from ..core.logging import get_logger
from ..core.types import (
    FirewallBlocked,
    Mode,
    OrderRequest,
    OrderResult,
    OrderStatus,
)
from ..database.repository import Repository
from .executor import ExecutionEngine
from .firewall import FirewallContext, LiveOrderFirewall

log = get_logger("execution.live")

LIVE_CONFIRMATION_PHRASE = "I UNDERSTAND LIVE TRADING"


class LiveConfirmationToken:
    """Proof that a human typed the confirmation phrase this session."""

    def __init__(self, typed_phrase: str):
        if typed_phrase != LIVE_CONFIRMATION_PHRASE:
            raise PermissionError(
                "Live confirmation phrase incorrect. Live trading refused.")
        self._confirmed = True

    @property
    def confirmed(self) -> bool:
        return self._confirmed


class LiveExecutionEngine(ExecutionEngine):
    mode = Mode.LIVE
    simulated = False

    def __init__(self, adapters: dict[str, PredictionMarketAdapter],
                 repository: Repository,
                 confirmation: LiveConfirmationToken,
                 allow_live_orders: bool):
        if not allow_live_orders:
            raise PermissionError(
                "LiveExecutionEngine requires allow_live_orders=true from "
                "live.yaml. Refusing construction.")
        if not confirmation.confirmed:
            raise PermissionError("Live confirmation missing.")
        self.adapters = adapters
        self.repo = repository
        self.firewall = LiveOrderFirewall()
        self.confirmation = confirmation

    async def submit_order_with_context(self, request: OrderRequest,
                                        ctx: FirewallContext) -> OrderResult:
        """Full guarded path. `submit_order` (interface method) refuses to
        run without firewall context — there is no unguarded entry."""
        adapter = self.adapters.get(request.venue)
        if adapter is None:
            raise FirewallBlocked(
                f"No live adapter for venue {request.venue!r}")

        # §61 step 1: intent record BEFORE transmission
        self.repo.write_trade_intent(
            trade_id=request.client_order_id,
            opportunity_id="", venue=request.venue,
            client_order_id=request.client_order_id,
            contract_id=request.contract_id, side=request.side.value,
            action=request.action.value, quantity=str(request.quantity),
            requested_price=str(request.limit_price))

        try:
            self.firewall.verify(request, ctx)   # raises FirewallBlocked
        except FirewallBlocked:
            self.repo.update_trade_status(request.client_order_id,
                                          OrderStatus.REJECTED)
            raise

        try:
            result = await adapter.place_order(request)
        except asyncio.TimeoutError:
            # §72: NEVER assume failure on timeout — reconcile first.
            log.warning("order transmission timed out; querying venue "
                        "before any retry",
                        extra={"data": {"client_order_id":
                                        request.client_order_id}})
            result = await self._reconcile_by_client_id(adapter, request)
        except Exception:
            self.repo.update_trade_status(request.client_order_id,
                                          OrderStatus.UNKNOWN)
            raise

        self.repo.update_trade_status(
            request.client_order_id,
            result.status,
            order_id=result.venue_order_id or "",
            fill_price=str(result.average_fill_price or ""),
            filled_quantity=str(result.filled_quantity),
            fees=str(result.fees))
        return result

    async def _reconcile_by_client_id(self, adapter: PredictionMarketAdapter,
                                      request: OrderRequest) -> OrderResult:
        """After a timeout, search venue open orders/fills for our
        client_order_id rather than blindly retrying (§72)."""
        try:
            for o in await adapter.get_open_orders():
                if o.get("client_order_id") == request.client_order_id:
                    return OrderResult(
                        client_order_id=request.client_order_id,
                        venue_order_id=o.get("order_id"),
                        status=OrderStatus.SUBMITTED,
                        filled_quantity=Decimal("0"),
                        average_fill_price=None, fees=Decimal("0"),
                        message="found resting after timeout")
            for f in await adapter.get_fills():
                if f.get("client_order_id") == request.client_order_id:
                    return OrderResult(
                        client_order_id=request.client_order_id,
                        venue_order_id=f.get("order_id"),
                        status=OrderStatus.FILLED,
                        filled_quantity=Decimal(str(f.get("count", 0))),
                        average_fill_price=None, fees=Decimal("0"),
                        message="found filled after timeout")
        except Exception as exc:
            log.error(f"reconciliation query failed: {exc}")
        return OrderResult(
            client_order_id=request.client_order_id, venue_order_id=None,
            status=OrderStatus.UNKNOWN, filled_quantity=Decimal("0"),
            average_fill_price=None, fees=Decimal("0"),
            message="UNKNOWN after timeout — manual reconciliation required")

    # ------------------------------------------------- interface methods
    async def submit_order(self, request: OrderRequest) -> OrderResult:
        raise FirewallBlocked(
            "LiveExecutionEngine.submit_order requires a FirewallContext; "
            "use submit_order_with_context. There is no unguarded path to "
            "a live order.")

    async def cancel_order(self, venue: str, order_id: str) -> bool:
        adapter = self.adapters.get(venue)
        if adapter is None:
            return False
        return await adapter.cancel_order(order_id)

    async def get_order_status(self, venue: str,
                               order_id: str) -> Optional[OrderResult]:
        adapter = self.adapters.get(venue)
        if adapter is None:
            return None
        return await adapter.get_order(order_id)

    async def get_positions(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for venue, adapter in self.adapters.items():
            try:
                for p in await adapter.get_positions():
                    p["venue"] = venue
                    out.append(p)
            except Exception as exc:
                log.error(f"get_positions failed on {venue}: {exc}")
        return out

    async def get_balances(self) -> dict[str, dict[str, Decimal]]:
        out: dict[str, dict[str, Decimal]] = {}
        for venue, adapter in self.adapters.items():
            try:
                out[venue] = await adapter.get_balances()
            except Exception as exc:
                log.error(f"get_balances failed on {venue}: {exc}")
        return out

    # ------------------------------------------------- crash recovery §61
    async def recover_on_startup(self) -> list[str]:
        """Reconcile PENDING/SUBMITTED intents against venue state."""
        notes: list[str] = []
        for row in self.repo.unreconciled_trades():
            adapter = self.adapters.get(row.venue)
            if adapter is None:
                notes.append(f"{row.client_order_id}: no adapter for "
                             f"{row.venue} — manual review required")
                continue
            resolved = False
            try:
                if row.order_id:
                    result = await adapter.get_order(row.order_id)
                    if result is not None:
                        self.repo.update_trade_status(
                            row.client_order_id, result.status,
                            filled_quantity=str(result.filled_quantity))
                        notes.append(f"{row.client_order_id}: venue says "
                                     f"{result.status.value}")
                        resolved = True
                if not resolved:
                    for o in await adapter.get_open_orders():
                        if o.get("client_order_id") == row.client_order_id:
                            self.repo.update_trade_status(
                                row.client_order_id, OrderStatus.SUBMITTED,
                                order_id=o.get("order_id", ""))
                            notes.append(f"{row.client_order_id}: resting "
                                         f"at venue")
                            resolved = True
                            break
                if not resolved:
                    for f in await adapter.get_fills():
                        if f.get("client_order_id") == row.client_order_id:
                            self.repo.update_trade_status(
                                row.client_order_id, OrderStatus.FILLED)
                            notes.append(f"{row.client_order_id}: FILLED at "
                                         f"venue (orphaned leg — review "
                                         f"hedge state)")
                            resolved = True
                            break
                if not resolved:
                    # intent written but venue has no trace → never sent
                    self.repo.update_trade_status(row.client_order_id,
                                                  OrderStatus.REJECTED)
                    notes.append(f"{row.client_order_id}: no venue trace — "
                                 f"marked REJECTED (never transmitted)")
            except Exception as exc:
                notes.append(f"{row.client_order_id}: reconciliation error "
                             f"{exc} — left as-is")
        return notes
