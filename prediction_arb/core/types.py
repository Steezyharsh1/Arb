"""Core shared types for the prediction-market arbitrage engine.

All prices are Decimal dollars (Kalshi's current API is dollar-denominated
strings; ForecastEx prices are dollars). All timestamps are timezone-aware
UTC datetimes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional


class Mode(str, Enum):
    HISTORICAL = "historical"
    PAPER = "paper"
    LIVE = "live"
    COLLECT = "collect"


class APIStatus(str, Enum):
    """Strongest adapter evidence currently recorded.

    These states are deliberately cumulative but not interchangeable:
    documentation is not a connection, connectivity is not usable market
    data, and observed public market data is not full adapter verification.
    """
    VERIFIED = "VERIFIED"  # full adapter evidence, including required surfaces
    MARKET_DATA_OBSERVED = "MARKET_DATA_OBSERVED"
    CONNECTIVITY_OBSERVED = "CONNECTIVITY_OBSERVED"
    UNVERIFIED = "UNVERIFIED"
    API_DOCUMENTED_UNTESTED = "API_DOCUMENTED_UNTESTED"
    SAME_VENUE_ROUTE = "SAME_VENUE_ROUTE"
    NO_API_AVAILABLE = "NO_API_AVAILABLE"

    @property
    def supports_market_data(self) -> bool:
        """Whether public market data is admitted for collect/paper reads.

        A connection check alone is insufficient. Full adapter verification
        remains a separate, higher bar (and the only state admitted by live
        order paths).
        """
        return self in (APIStatus.MARKET_DATA_OBSERVED, APIStatus.VERIFIED)


class Independence(str, Enum):
    INDEPENDENT = "INDEPENDENT"
    LIKELY_INDEPENDENT = "LIKELY_INDEPENDENT"
    UNKNOWN = "UNKNOWN"
    LIKELY_SAME_VENUE = "LIKELY_SAME_VENUE"
    SAME_VENUE = "SAME_VENUE"


class OutcomeType(str, Enum):
    BINARY = "BINARY"
    MULTI_OUTCOME = "MULTI_OUTCOME"
    RANGED = "RANGED"
    BRACKET = "BRACKET"


class Side(str, Enum):
    YES = "yes"
    NO = "no"


class OrderAction(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(str, Enum):
    PENDING = "PENDING"          # intent written, not yet transmitted
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class OpportunityType(str, Enum):
    TYPE_A_GUARANTEED = "TYPE_A_GUARANTEED"
    TYPE_B_LOGICAL = "TYPE_B_LOGICAL"
    TYPE_C_RELATIVE_VALUE = "TYPE_C_RELATIVE_VALUE"
    TYPE_D_STATISTICAL = "TYPE_D_STATISTICAL"


class OpportunityStatus(str, Enum):
    TRADE = "TRADE"
    WATCH = "WATCH"
    BLOCK = "BLOCK"


class MicrostructureType(str, Enum):
    CLOB = "CLOB"
    PAIRED_BID = "PAIRED_BID"    # ForecastEx: opposite bids pair at $1.00
    AMM = "AMM"
    RFQ = "RFQ"
    DEALER = "DEALER"
    HYBRID = "HYBRID"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class OrderTypeSupport:
    market: bool = False
    limit: bool = False
    ioc: bool = False
    fok: bool = False
    gtc: bool = False
    verified: bool = False
    source: str = ""
    verified_at: Optional[datetime] = None


@dataclass(frozen=True)
class MarketMicrostructure:
    type: MicrostructureType = MicrostructureType.UNKNOWN
    has_order_book: bool = False
    has_pricing_function: bool = False
    quote_validity_ms: Optional[int] = None
    verified: bool = False
    source: str = ""


@dataclass(frozen=True)
class PriceLevel:
    price: Decimal
    quantity: Decimal


@dataclass
class OrderBook:
    venue: str
    contract_id: str
    bids: list[PriceLevel] = field(default_factory=list)   # best first (desc)
    asks: list[PriceLevel] = field(default_factory=list)   # best first (asc)
    timestamp: Optional[datetime] = None
    sequence_number: Optional[int] = None
    microstructure_type: MicrostructureType = MicrostructureType.CLOB
    raw: Any = None  # original venue payload, preserved

    # Historical availability is NOT the venue's quote time. These fields
    # are observations, not defaults: legacy/unstamped books keep None.
    received_at: Optional[datetime] = None
    timestamp_source: Optional[str] = None  # "venue" or "local_receipt"
    outcome_side: Optional[Side] = None     # which outcome these ladders price
    snapshot_id: Optional[int] = None      # recorded-row provenance in replay

    def best_bid(self) -> Optional[PriceLevel]:
        return self.bids[0] if self.bids else None

    def best_ask(self) -> Optional[PriceLevel]:
        return self.asks[0] if self.asks else None


@dataclass(frozen=True)
class NormalizedContract:
    venue: str
    underlying_exchange: str
    clearing_entity: str
    market_id: str
    contract_id: str

    event_description: str
    normalized_event: str

    outcome_type: OutcomeType
    outcome: str
    normalized_outcome: str
    outcome_count: int
    all_outcomes: tuple[str, ...]

    settlement_time: Optional[datetime]
    timezone: str
    cutoff_time: Optional[datetime]

    resolution_source: str
    resolution_method: str

    threshold: Optional[Decimal]
    threshold_operator: Optional[str]
    units: Optional[str]

    payout: Decimal
    currency: str

    early_resolution_rules: str
    cancellation_rules: str

    contract_version: str

    # Unknown venue rules stay None; adapters must not publish defaults as
    # observed market metadata (§92).
    tick_size: Optional[Decimal]
    min_order_size: Optional[Decimal]
    max_order_size: Optional[Decimal]

    jurisdiction_requirements: str
    category: str

    market_microstructure: MarketMicrostructure
    supported_order_types: OrderTypeSupport

    raw: Any = None


@dataclass
class WalkResult:
    """Result of walking one side of an order book for a target quantity."""
    requested_quantity: Decimal
    filled_quantity: Decimal
    total_cost: Decimal
    weighted_average_price: Decimal
    levels_consumed: list[PriceLevel]
    fully_filled: bool


@dataclass(frozen=True)
class CostComponent:
    """One mandatory cost input to the profit test (§24, §28) — or the
    fact that it could not be computed.

    ``amount is None`` means UNAVAILABLE. It never means zero: a mandatory
    cost that cannot be computed makes the whole profit test unverifiable,
    so the component carries the reason it is missing instead of a number.
    """
    name: str
    amount: Optional[Decimal] = None
    #: Why the amount is unavailable. Empty when the amount is known.
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.amount is not None


@dataclass(frozen=True)
class CostLedger:
    """Typed result of costing an opportunity.

    The ledger is *complete* only when every mandatory component carries
    an amount. An incomplete ledger has **no total**: :attr:`total` is
    ``None``, and any net profit derived from it is ``None`` too. That is
    the whole point — an unavailable mandatory cost must never be replaced
    by zero, because zero is a claim that the cost is free.

    :attr:`known_total` sums the components that *are* known. It is a
    research figure and is deliberately **not** a cost total: the missing
    components are not zero, so it is a lower bound of unknown tightness.
    """
    components: tuple[CostComponent, ...] = ()

    @property
    def complete(self) -> bool:
        """True when at least one component exists and none is missing.

        An empty ledger is *not* complete: an opportunity that was never
        costed has not been shown to be free of mandatory costs.
        """
        return bool(self.components) and all(
            c.available for c in self.components)

    @property
    def total(self) -> Optional[Decimal]:
        """The full mandatory cost, or ``None`` if any part is unknown."""
        if not self.complete:
            return None
        return sum((c.amount for c in self.components), Decimal("0"))

    @property
    def known_total(self) -> Decimal:
        """Sum of the components that could be computed. Research only —
        this is a partial cost, never a total and never a profit."""
        return sum((c.amount for c in self.components if c.available),
                   Decimal("0"))

    @property
    def unavailable(self) -> tuple[CostComponent, ...]:
        return tuple(c for c in self.components if not c.available)

    def missing_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.unavailable)


@dataclass
class Leg:
    venue: str
    contract: NormalizedContract
    side: Side
    action: OrderAction
    quantity: Decimal
    limit_price: Decimal
    walk: Optional[WalkResult] = None
    fees: Decimal = Decimal("0")


@dataclass
class Opportunity:
    """One evaluated two-leg candidate.

    Money fields that depend on a mandatory cost are ``Optional`` and are
    ``None`` when that cost could not be computed (§24, §28):

    * ``total_fees`` / ``settlement_delay_cost`` — the cost itself,
      ``None`` when its schedule or its inputs are incomplete;
    * ``capital_required`` — ``gross_cost + fees``, so ``None`` whenever
      fees are unknown (an understated capital figure would hide how much
      cash the trade really needs);
    * ``net_profit`` / ``net_profit_per_contract`` / ``return_on_capital``
      — the profit test. ``None`` means UNVERIFIED, not zero and not
      "small".

    :attr:`gross_edge` is the only always-available money figure and it is
    explicitly **not** profit: it is the payout minus the walked purchase
    cost, before fees and before the cost of locked capital.
    """
    opportunity_id: str
    mode: Mode
    opportunity_type: OpportunityType
    timestamp: datetime
    normalized_event: str
    legs: list[Leg]
    payout_per_contract: Decimal
    quantity: Decimal
    gross_cost: Decimal
    total_fees: Optional[Decimal]
    slippage: Decimal
    settlement_delay_cost: Optional[Decimal]
    net_profit: Optional[Decimal]
    net_profit_per_contract: Optional[Decimal]
    capital_required: Optional[Decimal]
    return_on_capital: Optional[Decimal]
    independence: Independence
    match_id: Optional[str]
    match_confidence: Optional[float]
    status: OpportunityStatus
    block_reasons: list[str] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    #: Typed per-component cost result. ``costs.complete`` is the only
    #: warrant that ``net_profit`` is a verified number. Always populated
    #: by ``evaluate_two_leg``; hand-built opportunities default to an
    #: empty (therefore incomplete) ledger.
    costs: CostLedger = field(default_factory=CostLedger)

    # Replay evidence, including decision time and the exact input snapshots.
    # Empty for callers that did not perform a historical data assessment.
    evaluation_metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ metrics
    @property
    def gross_edge(self) -> Decimal:
        """Payout minus walked purchase cost — BEFORE fees and before the
        cost of capital. Always computable from the walks, and always a
        research figure: a positive gross edge says nothing about whether
        the trade is executable or profitable."""
        return self.payout_per_contract * self.quantity - self.gross_cost

    @property
    def known_costs(self) -> Decimal:
        """Sum of the mandatory costs that could be computed. Research
        only — it excludes the costs that are unavailable, so it is not a
        cost total and subtracting it from ``gross_edge`` is not a profit."""
        return self.costs.known_total

    @property
    def costs_complete(self) -> bool:
        """Every mandatory cost was computed, so ``net_profit`` is a
        verified number rather than an unavailable one."""
        return self.costs.complete and self.net_profit is not None

    @property
    def net_profit_is_verified(self) -> bool:
        """``net_profit`` is a real number, not UNAVAILABLE."""
        return self.net_profit is not None

    @property
    def profitable_after_all_costs(self) -> bool:
        """Verified positive net profit. False — never True — when net
        profit is unavailable, however large the gross edge is."""
        return self.net_profit is not None and self.net_profit > 0

    @property
    def is_executable(self) -> bool:
        """The only opportunities execution may act on: a TRADE verdict
        whose net profit is a verified positive number and whose capital
        requirement is known. A positive gross edge with an incomplete
        cost ledger does not qualify."""
        return (self.status == OpportunityStatus.TRADE
                and self.net_profit is not None
                and self.net_profit > 0
                and self.capital_required is not None
                and not self.block_reasons)


@dataclass
class OrderRequest:
    venue: str
    contract_id: str
    side: Side
    action: OrderAction
    quantity: Decimal
    order_type: OrderType
    limit_price: Optional[Decimal]
    client_order_id: str
    mode: Mode


@dataclass
class OrderResult:
    client_order_id: str
    venue_order_id: Optional[str]
    status: OrderStatus
    filled_quantity: Decimal
    average_fill_price: Optional[Decimal]
    fees: Decimal
    message: str = ""


class FeeDataError(Exception):
    """Raised when fee data is missing/expired. Never assume zero fees."""


class ModeIsolationError(Exception):
    """Raised when a mode-consistency invariant is violated at startup."""


class FirewallBlocked(Exception):
    """Raised when the live order firewall blocks an order."""
