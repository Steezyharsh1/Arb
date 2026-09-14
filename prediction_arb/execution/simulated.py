"""Simulated execution engines: Historical and Paper (§44, §46, §50).

Both are SIMULATION ONLY:
  * They fill orders against provided order-book snapshots using the
    order-book walker — never against a venue.
  * They maintain their own simulated cash/positions, initialized from
    config, never from a real account.
  * They contain no adapter reference capable of placing orders; the
    class simply has no such attribute. There is nothing to toggle.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Any, Callable, Optional

from ..core.types import (
    FeeDataError,
    Mode,
    OrderBook,
    OrderRequest,
    OrderResult,
    OrderStatus,
    PriceLevel,
)
from ..fees.calculator import FeeCalculator, FillSlice
from ..orderbooks.orderbook import walk_buy, walk_sell
from .executor import ExecutionEngine

# Callable that returns the current (or historical, in replay) book for a
# venue/contract, or None when unavailable.
BookLookup = Callable[[str, str], Optional[OrderBook]]


@dataclass
class SimPosition:
    venue: str
    contract_id: str
    side: str
    quantity: Decimal
    entry_price: Decimal


@dataclass
class SimState:
    cash: dict[str, Decimal] = field(default_factory=dict)
    positions: list[SimPosition] = field(default_factory=list)
    orders: dict[str, OrderResult] = field(default_factory=dict)
    fills: list[dict[str, Any]] = field(default_factory=list)


class _SimulatedExecutionEngine(ExecutionEngine):
    """Shared simulation logic. Not exported; use the mode subclasses."""

    simulated = True
    mode = Mode.PAPER  # overridden

    def __init__(self, starting_cash: dict[str, Decimal],
                 fee_calc: FeeCalculator,
                 book_lookup: BookLookup,
                 fill_probability_at_touch: float = 1.0,
                 simulated_latency_ms: int = 0):
        self.state = SimState(cash=dict(starting_cash))
        self.fee_calc = fee_calc
        self.book_lookup = book_lookup
        self.fill_probability_at_touch = fill_probability_at_touch
        self.simulated_latency_ms = simulated_latency_ms
        self._lock = asyncio.Lock()

    async def submit_order(self, request: OrderRequest) -> OrderResult:
        if request.mode != self.mode:
            return OrderResult(
                client_order_id=request.client_order_id,
                venue_order_id=None, status=OrderStatus.REJECTED,
                filled_quantity=Decimal("0"), average_fill_price=None,
                fees=Decimal("0"),
                message=f"mode mismatch: engine={self.mode.value} "
                        f"request={request.mode.value}")
        if self.simulated_latency_ms:
            await asyncio.sleep(self.simulated_latency_ms / 1000.0)

        book = self.book_lookup(request.venue, request.contract_id)
        if book is None:
            return self._reject(request, "no order book available")

        async with self._lock:
            walk = (walk_buy if request.action.value == "buy" else walk_sell)(
                book, request.quantity)
            # respect limit price: only levels at-or-better fill
            if request.limit_price is not None and walk.levels_consumed:
                ok_levels = [l for l in walk.levels_consumed
                             if (l.price <= request.limit_price
                                 if request.action.value == "buy"
                                 else l.price >= request.limit_price)]
                filled = sum((l.quantity for l in ok_levels), Decimal("0"))
                cost = sum((l.quantity * l.price for l in ok_levels),
                           Decimal("0"))
                filled_levels = ok_levels
            else:
                filled = walk.filled_quantity
                cost = walk.total_cost
                filled_levels = walk.levels_consumed

            # D12: queue risk at the touch. The portion of an order that
            # executes AT the best displayed price competes with resting
            # orders ahead of it, so it does not fill reliably. Levels
            # consumed *beyond* the touch are taker executions against
            # displayed liquidity and fill in full.
            #
            # This is an expected-fill model, not a coin flip, so paper
            # stays reproducible run to run. Before this, `paper.yaml`'s
            # 0.9 was read, stored, and ignored — every paper fill was
            # 100% at the touch, which flattered the strategy.
            if self.fill_probability_at_touch < 1.0:
                touch = (book.best_ask() if request.action.value == "buy"
                         else book.best_bid())
                if touch is not None:
                    scaled: list[PriceLevel] = []
                    for level in filled_levels:
                        if level.price != touch.price:
                            scaled.append(level)
                            continue
                        prob = Decimal(str(self.fill_probability_at_touch))
                        # Round DOWN: rounding up would overstate the fill,
                        # which is the exact error this fix removes.
                        qty = (level.quantity * prob).quantize(
                            level.quantity, rounding=ROUND_DOWN)
                        if qty > 0:
                            scaled.append(PriceLevel(level.price, qty))
                    filled_levels = scaled
                    filled = sum((l.quantity for l in filled_levels),
                                 Decimal("0"))
                    cost = sum((l.quantity * l.price for l in filled_levels),
                               Decimal("0"))

            if filled <= 0:
                return self._reject(request, "no executable liquidity at limit")

            avg = cost / filled
            try:
                # Per FILL, from the levels that actually filled — a venue
                # bills each fill of a walking order separately, rounds each
                # one, and may cap the order total. One fee priced at `avg`
                # is not what the venue charges (defect D13).
                fees = self.fee_calc.trade_fee_fills(
                    request.venue,
                    [FillSlice(price=l.price, quantity=l.quantity)
                     for l in filled_levels],
                    is_taker=True).total
            except FeeDataError as exc:
                # Missing contextual fees block even simulated fills: otherwise
                # paper results would silently overstate executable profit.
                return self._reject(request, str(exc))
            total = cost + fees
            venue_cash = self.state.cash.get(request.venue, Decimal("0"))
            if request.action.value == "buy" and total > venue_cash:
                return self._reject(
                    request, f"insufficient simulated cash on "
                             f"{request.venue}: need {total}, have {venue_cash}")

            if request.action.value == "buy":
                self.state.cash[request.venue] = venue_cash - total
                self.state.positions.append(SimPosition(
                    venue=request.venue, contract_id=request.contract_id,
                    side=request.side.value, quantity=filled,
                    entry_price=avg))
            else:
                self.state.cash[request.venue] = venue_cash + cost - fees

            status = (OrderStatus.FILLED if filled >= request.quantity
                      else OrderStatus.PARTIALLY_FILLED)
            result = OrderResult(
                client_order_id=request.client_order_id,
                venue_order_id=f"sim-{uuid.uuid4().hex[:12]}",
                status=status, filled_quantity=filled,
                average_fill_price=avg, fees=fees,
                message="simulated fill")
            self.state.orders[result.venue_order_id or ""] = result
            self.state.fills.append({
                "venue": request.venue, "contract_id": request.contract_id,
                "side": request.side.value, "quantity": str(filled),
                "price": str(avg), "fees": str(fees), "simulated": True})
            return result

    def _reject(self, request: OrderRequest, why: str) -> OrderResult:
        return OrderResult(
            client_order_id=request.client_order_id, venue_order_id=None,
            status=OrderStatus.REJECTED, filled_quantity=Decimal("0"),
            average_fill_price=None, fees=Decimal("0"), message=why)

    async def cancel_order(self, venue: str, order_id: str) -> bool:
        async with self._lock:
            r = self.state.orders.get(order_id)
            if r and r.status == OrderStatus.SUBMITTED:
                self.state.orders[order_id] = OrderResult(
                    client_order_id=r.client_order_id,
                    venue_order_id=order_id, status=OrderStatus.CANCELED,
                    filled_quantity=r.filled_quantity,
                    average_fill_price=r.average_fill_price, fees=r.fees)
                return True
        return False

    async def get_order_status(self, venue: str,
                               order_id: str) -> Optional[OrderResult]:
        async with self._lock:
            return self.state.orders.get(order_id)

    async def get_positions(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [{"venue": p.venue, "contract_id": p.contract_id,
                     "side": p.side, "quantity": str(p.quantity),
                     "entry_price": str(p.entry_price), "simulated": True}
                    for p in self.state.positions]

    async def get_balances(self) -> dict[str, dict[str, Decimal]]:
        async with self._lock:
            return {v: {"USD": c} for v, c in self.state.cash.items()}


class PaperExecutionEngine(_SimulatedExecutionEngine):
    """Real-time data, simulated fills. ABSOLUTELY NO REAL ORDER MAY BE
    SENT: this class holds no live-capable adapter and no firewall."""
    mode = Mode.PAPER


class HistoricalExecutionEngine(_SimulatedExecutionEngine):
    """Replay data, simulated fills, fully reproducible (no randomness in
    fill logic; latency is deterministic)."""
    mode = Mode.HISTORICAL

    def __init__(self, starting_cash: dict[str, Decimal],
                 fee_calc: FeeCalculator, book_lookup: BookLookup):
        super().__init__(starting_cash, fee_calc, book_lookup,
                         fill_probability_at_touch=1.0,
                         simulated_latency_ms=0)
