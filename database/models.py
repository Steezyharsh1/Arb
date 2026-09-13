"""SQLAlchemy models (§40). Every row carries `mode`. Mode isolation is
also physical: each mode uses its own SQLite database file (§49)."""
from __future__ import annotations

from datetime import datetime

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

SCHEMA_VERSION = 1


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
    fees: Mapped[str] = mapped_column(String(32))
    slippage: Mapped[str] = mapped_column(String(32))
    net_profit: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[str] = mapped_column(String(32))
    capital_required: Mapped[str] = mapped_column(String(32))
    independence: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(16))
    block_reasons: Mapped[str] = mapped_column(Text, default="")
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


class MetaRow(Base):
    __tablename__ = "meta"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    value: Mapped[str] = mapped_column(Text)
