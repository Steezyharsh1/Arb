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
    VERIFIED = "VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    API_DOCUMENTED_UNTESTED = "API_DOCUMENTED_UNTESTED"
    SAME_VENUE_ROUTE = "SAME_VENUE_ROUTE"
    NO_API_AVAILABLE = "NO_API_AVAILABLE"


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
    PAIRED_BID = "PAIRED_BID"    # ForecastEx: YES bid + NO bid pairing
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

    tick_size: Decimal
    min_order_size: Decimal
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
    opportunity_id: str
    mode: Mode
    opportunity_type: OpportunityType
    timestamp: datetime
    normalized_event: str
    legs: list[Leg]
    payout_per_contract: Decimal
    quantity: Decimal
    gross_cost: Decimal
    total_fees: Decimal
    slippage: Decimal
    settlement_delay_cost: Decimal
    net_profit: Decimal
    net_profit_per_contract: Decimal
    capital_required: Decimal
    return_on_capital: Decimal
    independence: Independence
    match_id: Optional[str]
    match_confidence: Optional[float]
    status: OpportunityStatus
    block_reasons: list[str] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None


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
