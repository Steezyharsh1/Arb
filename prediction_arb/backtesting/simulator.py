"""One deterministic, dated fill model for backtests AND historical mode.

No adapter, real order, wall-clock sleep, random fill, settlement cash credit
or forward-nearest quote lookup exists here. Fills use the last supported
observation at each logical arrival time, under explicitly reported depth /
carry-forward assumptions. Missing data is UNAVAILABLE, never a full fill.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Optional, TYPE_CHECKING

from ..core.types import FeeDataError, Leg, Mode, Opportunity, PriceLevel
from ..fees.calculator import FeeCalculator, FillSlice
from ..normalization.settlement import assess_lockup, settlement_delay_cost
from ..orderbooks.orderbook import book_for_side, walk_book
from .policy import ReplayPolicy

if TYPE_CHECKING:
    from ..modes.historical.data_provider import HistoricalDataProvider

ZERO = Decimal(0)


@dataclass
class SimulatedOrder:
    venue: str
    contract_id: str
    side: str
    arrival_time: datetime
    requested_quantity: Decimal
    limit_price: Decimal
    status: str
    filled_quantity: Optional[Decimal] = None
    purchase_cost: Optional[Decimal] = None
    trade_fees: Optional[Decimal] = None
    settlement_fees_reserved: Optional[Decimal] = None
    levels: list[PriceLevel] = field(default_factory=list)
    snapshot_id: Optional[int] = None
    quote_time: Optional[datetime] = None
    received_at: Optional[datetime] = None
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "classification": "SIMULATED_ORDER_NOT_VENUE_EXECUTION",
            "venue": self.venue, "contract_id": self.contract_id, "side": self.side,
            "arrival_time": self.arrival_time.isoformat(),
            "requested_quantity": str(self.requested_quantity),
            "limit_price": str(self.limit_price), "status": self.status,
            "filled_quantity": None if self.filled_quantity is None else str(self.filled_quantity),
            "purchase_cost": None if self.purchase_cost is None else str(self.purchase_cost),
            "trade_fees": None if self.trade_fees is None else str(self.trade_fees),
            "settlement_fees_reserved": (str(self.settlement_fees_reserved)
                                         if self.settlement_fees_reserved is not None else None),
            "levels_consumed": [[str(l.price), str(l.quantity)] for l in self.levels],
            "snapshot_id": self.snapshot_id,
            "quote_time": self.quote_time.isoformat() if self.quote_time else None,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "reasons": self.reasons,
        }


@dataclass
class SimulatedPosition:
    opportunity_id: str
    decision_time: datetime
    target_quantity: Decimal
    orders: list[SimulatedOrder]
    paired_quantity: Decimal = ZERO
    completed_pair_net_pnl: Optional[Decimal] = None
    settlement_fees: Optional[Decimal] = None
    capital_delay_cost: Optional[Decimal] = None
    unresolved_exposure: list[dict] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "opportunity_id": self.opportunity_id, "decision_time": self.decision_time.isoformat(),
            "target_quantity": str(self.target_quantity),
            "paired_quantity": str(self.paired_quantity),
            "fully_completed_target": self.paired_quantity == self.target_quantity,
            "completed_pair_net_pnl": (str(self.completed_pair_net_pnl)
                                       if self.completed_pair_net_pnl is not None else None),
            "pnl_basis": "SIMULATED conditional terminal contractual payoff, NOT realized cash",
            "settlement_fees": str(self.settlement_fees) if self.settlement_fees is not None else None,
            "capital_delay_cost": str(self.capital_delay_cost) if self.capital_delay_cost is not None else None,
            "orders": [o.to_dict() for o in self.orders],
            "unresolved_exposure": self.unresolved_exposure,
            "limitations": self.limitations,
        }


class HistoricalFillSimulator:
    simulated = True
    mode = Mode.HISTORICAL

    def __init__(self, provider: HistoricalDataProvider, fee_calc: FeeCalculator,
                 policy: ReplayPolicy, *, annual_rate: Decimal,
                 max_total_capital: Optional[Decimal] = None,
                 max_capital_per_trade: Optional[Decimal] = None):
        self.provider, self.fee_calc, self.policy = provider, fee_calc, policy
        self.annual_rate = annual_rate
        self.max_total_capital = max_total_capital
        self.max_capital_per_trade = max_capital_per_trade
        self.cash = dict(policy.starting_cash)
        self.positions: list[SimulatedPosition] = []
        self.skipped: dict[str, int] = {}
        self._attempted: set[tuple] = set()
        self._remaining: dict[tuple[int, str], list[PriceLevel]] = {}
        self._busy_until: Optional[datetime] = None
        self.capital_committed = ZERO
        self.stopped_for_exposure = False

    def _skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    def execute(self, op: Opportunity, ts: datetime) -> Optional[SimulatedPosition]:
        if not self.policy.enabled:
            self._skip("simulation disabled (no explicit configuration)")
            return None
        if (not op.is_executable or not op.costs_complete
                or not op.evaluation_metadata.get("execution_data_supported")):
            self._skip("opportunity failed cost/decision/data gates")
            return None
        pair = tuple(sorted((l.venue, l.contract.contract_id, l.contract.contract_version)
                            for l in op.legs))
        if pair in self._attempted:
            self._skip("same contract/version pair already attempted")
            return None
        if self.stopped_for_exposure:
            self._skip("new entries stopped for unresolved exposure")
            return None
        if self._busy_until is not None and ts < self._busy_until:
            self._skip("decision occurred while sequential orders were in flight")
            return None
        if len(op.legs) != 2:
            self._skip("only two-leg entries supported")
            return None
        if (self.max_capital_per_trade is not None and op.capital_required > self.max_capital_per_trade
                or self.max_total_capital is not None
                and self.capital_committed + op.capital_required > self.max_total_capital):
            self._skip("configured capital limit")
            return None
        for leg in op.legs:
            needed = leg.walk.total_cost + leg.fees if leg.walk is not None else None
            if leg.venue not in self.cash or needed is None or needed > self.cash[leg.venue]:
                self._skip("insufficient or unconfigured venue-local simulated capital")
                return None
        self._attempted.add(pair)
        first_time = ts + timedelta(milliseconds=self.policy.decision_latency_ms)
        second_time = first_time + timedelta(milliseconds=self.policy.leg_latency_ms)
        entry_start_capital = self.capital_committed
        first = self._fill(op.legs[0], op.quantity, first_time)
        orders = [first]
        # Contingent leg B never buys more than the known fill of leg A.
        if first.filled_quantity is not None and first.filled_quantity > 0:
            orders.append(self._fill(op.legs[1], first.filled_quantity, second_time,
                                     entry_capital_used=self.capital_committed - entry_start_capital))
        self._busy_until = orders[-1].arrival_time
        position = SimulatedPosition(op.opportunity_id, ts, op.quantity, orders)
        self._cost_position(position, op)
        self.positions.append(position)
        if position.unresolved_exposure:
            self.stopped_for_exposure = True
        return position

    def _fill(self, leg: Leg, quantity: Decimal, arrival: datetime,
              *, entry_capital_used: Decimal = ZERO) -> SimulatedOrder:
        # WAP is not a marketable limit for a multi-level walk. Retain the
        # worst decision-time consumed price, with no latency-time concession.
        limit = max((l.price for l in leg.walk.levels_consumed), default=leg.limit_price) \
            if leg.walk is not None else leg.limit_price
        result = SimulatedOrder(leg.venue, leg.contract.contract_id, leg.side.value,
                                arrival, quantity, limit, "UNAVAILABLE")
        book, contract, reasons = self.provider.execution_book(
            (leg.venue, leg.contract.contract_id), arrival, require_horizon=True)
        if contract is not None:
            # Matching normalization may fill these two derived labels. All
            # observed terms (including raw settlement evidence) must agree.
            expected = replace(leg.contract, normalized_event=contract.normalized_event,
                               normalized_outcome=contract.normalized_outcome)
            if contract != expected:
                reasons.append("contract terms/version changed since decision; match no longer applicable")
        if reasons or book is None or contract is None:
            result.reasons = reasons or ["as-of contract unavailable"]
            return result
        result.snapshot_id, result.quote_time, result.received_at = (
            book.snapshot_id, book.timestamp, book.received_at)
        if (quantity < contract.min_order_size
                or contract.max_order_size is not None and quantity > contract.max_order_size
                or limit % contract.tick_size != 0):
            result.status, result.filled_quantity = "REJECTED", ZERO
            result.purchase_cost = result.trade_fees = result.settlement_fees_reserved = ZERO
            result.reasons = ["order quantity/limit violates recorded venue rules"]
            return result
        oriented = book_for_side(book, leg.side, contract.payout)
        depth_key = (book.snapshot_id, leg.side.value)
        if depth_key not in self._remaining:
            self._remaining[depth_key] = [
                PriceLevel(level.price, (level.quantity * self.policy.displayed_depth_fraction)
                           .quantize(Decimal(1), rounding=ROUND_DOWN))
                for level in oriented.asks]
        depth = self._remaining[depth_key]
        walk = walk_book([l for l in depth if l.price <= limit], quantity)
        if walk.filled_quantity <= 0:
            result.status, result.filled_quantity = "UNFILLED", ZERO
            result.purchase_cost = result.trade_fees = result.settlement_fees_reserved = ZERO
            result.reasons = ["no assumed executable depth at the decision-time limit"]
            return result
        try:
            fees = self.fee_calc.trade_fee_fills(
                leg.venue, [FillSlice(l.price, l.quantity) for l in walk.levels_consumed],
                is_taker=True, as_of=arrival).total
            reserved = self.fee_calc.settlement_fee(leg.venue, walk.filled_quantity, as_of=arrival)
        except FeeDataError as exc:
            result.reasons = [str(exc)]
            return result  # unknown fee is not a free fill
        needed = walk.total_cost + fees + reserved
        if (needed > self.cash.get(leg.venue, ZERO)
                or self.max_total_capital is not None
                and self.capital_committed + needed > self.max_total_capital
                or self.max_capital_per_trade is not None
                and entry_capital_used + needed > self.max_capital_per_trade):
            result.status, result.filled_quantity = "REJECTED", ZERO
            result.purchase_cost = result.trade_fees = result.settlement_fees_reserved = ZERO
            result.reasons = ["insufficient simulated cash/capital at arrival (including reserved fees)"]
            return result
        self.cash[leg.venue] -= needed
        self.capital_committed += needed
        # Deduct from this snapshot/outcome once, including across competing
        # opportunities. Identical-price slices are consumed FIFO.
        remaining_sale = walk.filled_quantity
        updated = []
        for level in depth:
            take = min(level.quantity, remaining_sale) if level.price <= limit else ZERO
            updated.append(PriceLevel(level.price, level.quantity - take))
            remaining_sale -= take
        self._remaining[depth_key] = updated
        result.filled_quantity, result.purchase_cost, result.trade_fees = (
            walk.filled_quantity, walk.total_cost, fees)
        result.settlement_fees_reserved = reserved
        result.levels = walk.levels_consumed
        result.status = "FILLED" if walk.fully_filled else "PARTIALLY_FILLED"
        return result

    def _cost_position(self, position: SimulatedPosition, op: Opportunity) -> None:
        quantities = [o.filled_quantity or ZERO for o in position.orders]
        paired = min(quantities) if len(quantities) == 2 else ZERO
        position.paired_quantity = paired
        for order in position.orders:
            filled = order.filled_quantity or ZERO
            extra = filled - paired
            if extra > 0:
                # Pro-rata cost allocation is explicit; residual value is not
                # marked at a midpoint or assumed to recover at entry price.
                fraction = extra / filled
                position.unresolved_exposure.append({
                    "classification": "SIMULATED_UNRESOLVED_EXPOSURE",
                    "venue": order.venue, "contract_id": order.contract_id, "side": order.side,
                    "quantity": str(extra), "entry_time": order.arrival_time.isoformat(),
                    "purchase_cost": str(order.purchase_cost * fraction),
                    "trade_fees": str(order.trade_fees * fraction),
                    "settlement_fees_reserved": str(order.settlement_fees_reserved * fraction),
                    "terminal_value": None, "net_pnl": None,
                })
        if paired <= 0:
            return
        gross_cost = sum((o.purchase_cost * paired / o.filled_quantity
                          for o in position.orders), ZERO)
        trade_fees = sum((o.trade_fees * paired / o.filled_quantity
                          for o in position.orders), ZERO)
        settlement_fees = sum((o.settlement_fees_reserved * paired / o.filled_quantity
                               for o in position.orders), ZERO)
        timing = assess_lockup([l.contract for l in op.legs], position.orders[0].arrival_time)
        if timing.lockup_days is None:
            position.limitations.extend(timing.reasons)
            return
        delay = settlement_delay_cost(gross_cost + trade_fees + settlement_fees,
                                      timing.lockup_days, self.annual_rate)
        position.settlement_fees, position.capital_delay_cost = settlement_fees, delay
        position.completed_pair_net_pnl = (
            paired * op.payout_per_contract - gross_cost - trade_fees - settlement_fees - delay)
        # Entry reserved eventual fees already. No payout cash is invented
        # and the locked capital is never re-used on a later frame.

    def report(self) -> dict:
        orders = [o for p in self.positions for o in p.orders]
        completed = [p for p in self.positions if p.paired_quantity > 0]
        priced = [p for p in completed if p.completed_pair_net_pnl is not None]
        unresolved = [e for p in self.positions for e in p.unresolved_exposure]
        for exposure in unresolved:
            entry = datetime.fromisoformat(exposure["entry_time"])
            end = max(self.provider.last_observation or entry, entry)
            exposure["age_ms_at_recording_end"] = (end - entry).total_seconds() * 1000
        unavailable = sum(o.status == "UNAVAILABLE" for o in orders)
        known_pnl = sum((p.completed_pair_net_pnl for p in priced), ZERO) if priced else None
        incomplete = bool(unresolved or unavailable or len(priced) != len(completed))
        # Zero is valid only for a configured, actually attempted, fully known
        # no-fill run. A missing dataset/configuration has no simulated P&L.
        net = (sum((p.completed_pair_net_pnl for p in priced), ZERO)
               if self.positions and not incomplete else None)
        return {
            "classification": "SIMULATED_RESEARCH_NOT_REAL_EXECUTION",
            "evaluation_status": ("DISABLED" if not self.policy.enabled else
                                  "NO_ENTRIES_SIMULATED" if not self.positions else
                                  "INCOMPLETE" if incomplete else "CONDITIONAL_SIMULATION_COMPLETE"),
            "policy": self.policy.to_dict(),
            "attempted_entries": len(self.positions),
            "orders_simulated": len(orders),
            "filled_orders": sum(o.status == "FILLED" for o in orders),
            "partial_orders": sum(o.status == "PARTIALLY_FILLED" for o in orders),
            "unfilled_or_rejected_orders": sum(o.status in ("UNFILLED", "REJECTED") for o in orders),
            "orders_with_unavailable_data": unavailable,
            "completed_two_leg_positions": len(completed),
            "fully_completed_target_positions": sum(p.paired_quantity == p.target_quantity for p in completed),
            "completed_pair_quantity": str(sum((p.paired_quantity for p in completed), ZERO)),
            "completed_pairs_with_unavailable_costs": len(completed) - len(priced),
            "completed_pair_net_pnl_known_subset": str(known_pnl) if known_pnl is not None else None,
            "net_simulated_pnl": str(net) if net is not None else None,
            "pnl_basis": "CONDITIONAL_TERMINAL_CONTRACTUAL_PAYOFF_NOT_REALIZED; paired quantities only; "
                         "aggregate withheld on unresolved exposure or unavailable execution/cost data",
            "realized_net_simulated_pnl": None,
            "realized_pnl_unavailable_reason": "No resolution/payout-to-cash or position-close data was replayed",
            "unresolved_exposure": unresolved,
            "incomplete_evaluation": incomplete,
            "new_entries_stopped_for_exposure": self.stopped_for_exposure,
            "capital_committed": str(self.capital_committed),
            "remaining_simulated_cash": {v: str(c) for v, c in sorted(self.cash.items())},
            "paired_positions_still_open": len(completed),
            "skipped_opportunities": dict(sorted(self.skipped.items())),
            "entries": [p.to_dict() for p in self.positions],
            "assumptions": [
                "Last supported books at arrival are ASSUMED unchanged between observations, "
                "within the age limit. Polling does not prove that assumption.",
                "Displayed depth is not queue/fill evidence. The configured fraction is a deterministic "
                "haircut, NOT an observed fill probability; no hidden liquidity is inferred.",
                "Cost/timing/matching inputs may be retrospectively supplied; actual contemporaneous "
                "strategy knowledge is not established. Supplied fee versions must cover the period; "
                "date bounds alone do not reconstruct intraday changes.",
                "Each displayed level is treated as a fee slice; real execution fragmentation may differ.",
                "Costs/fees are allocated pro rata between paired quantity and retained exposure.",
                "Paired terminal P&L ASSUMES matched complementary contracts pay their contractual payout. "
                "No settlement cash is credited; cancellations/disputes are not simulated.",
                "No result guarantees or predicts real execution or profitability.",
            ],
        }


BacktestFillSimulator = HistoricalFillSimulator

__all__ = ["BacktestFillSimulator", "HistoricalFillSimulator", "SimulatedOrder", "SimulatedPosition"]
