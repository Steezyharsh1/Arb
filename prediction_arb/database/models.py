"""SQLAlchemy models (§40). Every row carries `mode`. Mode isolation is
also physical: each mode uses its own SQLite database file (§49)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_VERSION = 4


class Base(DeclarativeBase):
    pass


class MarketRow(Base):
    __tablename__ = "markets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    market_id: Mapped[str] = mapped_column(String(128), index=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    underlying_exchange: Mapped[str] = mapped_column(String(64))
    contract_id: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    outcome_type: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # --- Phase 4 / D4 (migration 002): the rest of the NormalizedContract.
    # Everything here is nullable on purpose. Rows recorded before
    # migration 002 simply do not have the data, and `_reconstruct_contract`
    # must return "unknown" for them rather than an invented value. Adding
    # a NOT NULL column with a fabricated default would be exactly the
    # silent fabrication §92 forbids.
    clearing_entity: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    normalized_event: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    normalized_outcome: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    outcome_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    all_outcomes_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    settlement_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True)
    timezone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    cutoff_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    resolution_source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolution_method: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    threshold: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    threshold_operator: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    units: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    payout: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    currency: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    early_resolution_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cancellation_rules: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    contract_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tick_size: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    min_order_size: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    max_order_size: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    jurisdiction_requirements: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True)
    microstructure_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_types_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The venue's own payload, verbatim. Replay must be able to prove what
    # was actually observed rather than trusting the normalized copy.
    raw_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class ContractMatchRow(Base):
    __tablename__ = "contract_matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    match_id: Mapped[str] = mapped_column(String(64), index=True, unique=True)
    contract_a: Mapped[str] = mapped_column(String(160))
    contract_b: Mapped[str] = mapped_column(String(160))
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    validation_status: Mapped[str] = mapped_column(String(16))
    independence: Mapped[str] = mapped_column(String(24))
    certificate_json: Mapped[str] = mapped_column(Text, default="")
    llm_version: Mapped[str] = mapped_column(String(64), default="")
    llm_prompt_version: Mapped[str] = mapped_column(String(64), default="")
    matcher_version: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class VenueProvenanceRow(Base):
    __tablename__ = "venue_provenance"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    underlying_exchange: Mapped[str] = mapped_column(String(64))
    clearing_entity: Mapped[str] = mapped_column(String(64))
    issuer: Mapped[str] = mapped_column(String(64))
    liquidity_source: Mapped[str] = mapped_column(String(64))
    source_url: Mapped[str] = mapped_column(Text, default="")
    verified_at: Mapped[str] = mapped_column(String(32), default="")
    confidence: Mapped[str] = mapped_column(String(16), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)


class FeeRow(Base):
    __tablename__ = "fees"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    fee_type: Mapped[str] = mapped_column(String(48))
    rate: Mapped[str] = mapped_column(String(32), default="")
    fixed_amount: Mapped[str] = mapped_column(String(32), default="")
    effective_date: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(32))


class OpportunityRow(Base):
    __tablename__ = "opportunities"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    opportunity_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    opportunity_type: Mapped[str] = mapped_column(String(32))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    normalized_event: Mapped[str] = mapped_column(Text)
    contracts: Mapped[str] = mapped_column(Text)     # JSON legs
    venues: Mapped[str] = mapped_column(String(128))
    gross_profit: Mapped[str] = mapped_column(String(32))
    # NULL, not "0": these three depend on mandatory costs (fees, and the
    # fees-inclusive capital base for net profit), so a NULL means the cost
    # could not be computed and the figure is UNVERIFIED. Writing "0" here
    # would record an unknown fee as a free one, and a positive net profit
    # next to a BLOCK status (defect D17).
    fees: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    slippage: Mapped[str] = mapped_column(String(32))
    net_profit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    quantity: Mapped[str] = mapped_column(String(32))
    capital_required: Mapped[Optional[str]] = mapped_column(
        String(32), nullable=True)
    independence: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16))
    block_reasons: Mapped[str] = mapped_column(Text, default="")
    #: JSON list of the mandatory cost components that were unavailable
    #: (e.g. ["kalshi:trade_fee", "settlement_delay"]). NULL when every
    #: component could be computed.
    unavailable_costs: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True)
    # Calculated opportunities are not fill evidence. Replay records the
    # decision time, input snapshot IDs and temporal limitations here.
    evaluation_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TradeRow(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), index=True)
    opportunity_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(32))
    order_id: Mapped[str] = mapped_column(String(64), default="")
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    contract_id: Mapped[str] = mapped_column(String(128))
    side: Mapped[str] = mapped_column(String(8))
    action: Mapped[str] = mapped_column(String(8), default="buy")
    quantity: Mapped[str] = mapped_column(String(32))
    requested_price: Mapped[str] = mapped_column(String(32))
    fill_price: Mapped[str] = mapped_column(String(32), default="")
    filled_quantity: Mapped[str] = mapped_column(String(32), default="0")
    fees: Mapped[str] = mapped_column(String(32), default="0")
    status: Mapped[str] = mapped_column(String(24), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PositionRow(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(32))
    contract_id: Mapped[str] = mapped_column(String(128))
    side: Mapped[str] = mapped_column(String(8), default="yes")
    quantity: Mapped[str] = mapped_column(String(32))
    entry_price: Mapped[str] = mapped_column(String(32))
    entry_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_resolution_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    expected_settlement_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)
    payout_received: Mapped[bool] = mapped_column(Boolean, default=False)
    final_pnl: Mapped[str] = mapped_column(String(32), default="")


class OrderBookSnapshotRow(Base):
    __tablename__ = "orderbook_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    venue: Mapped[str] = mapped_column(String(32), index=True)
    contract_id: Mapped[str] = mapped_column(String(128), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bids_json: Mapped[str] = mapped_column(Text)
    asks_json: Mapped[str] = mapped_column(Text)
    raw_json: Mapped[str] = mapped_column(Text, default="")

    # Migration 004: unknown on legacy rows, never backfilled from quote time.
    received_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True)
    timestamp_source: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    outcome_side: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    sequence_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    microstructure_type: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    contract_observed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    contract_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class MetaRow(Base):
    __tablename__ = "meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[str] = mapped_column(Text)
