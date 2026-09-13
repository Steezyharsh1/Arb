"""Repository over the mode-specific database (§40, §49, §61).

Includes the crash-recovery queries: PENDING/SUBMITTED trade intents are
discoverable at startup for reconciliation against venue state.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from ..core.clock import utcnow
from ..core.types import Mode, Opportunity, OrderBook, OrderStatus
from .migrations.runner import run_migrations
from .models import (
    Base,
    ContractMatchRow,
    MarketRow,
    MetaRow,
    OpportunityRow,
    OrderBookSnapshotRow,
    PositionRow,
    TradeRow,
    VenueProvenanceRow,
    SCHEMA_VERSION,
)


class Repository:
    def __init__(self, db_path: Path, mode: Mode):
        self.mode = mode
        self.db_path = db_path
        self.engine = create_engine(f"sqlite:///{db_path}", future=True)
        run_migrations(self.engine)
        self._assert_mode_tag()

    def _assert_mode_tag(self) -> None:
        """The database file itself records which mode owns it. Opening a
        live DB from paper mode (or vice versa) fails hard (§49)."""
        with Session(self.engine) as s:
            row = s.scalar(select(MetaRow).where(MetaRow.key == "mode"))
            if row is None:
                s.add(MetaRow(key="mode", value=self.mode.value))
                s.add(MetaRow(key="schema_version", value=str(SCHEMA_VERSION)))
                s.commit()
            elif row.value != self.mode.value and not (
                    row.value == "collect" and self.mode == Mode.PAPER
                    or row.value == "paper" and self.mode == Mode.COLLECT):
                raise RuntimeError(
                    f"Database {self.db_path} is tagged mode={row.value!r} "
                    f"but application mode is {self.mode.value!r}. Refusing "
                    f"to mix mode data (§49).")

    # -------------------------------------------------------------- markets
    def upsert_market(self, venue: str, market_id: str, contract_id: str,
                      underlying_exchange: str, description: str,
                      outcome_type: str, category: str, status: str) -> None:
        now = utcnow()
        with Session(self.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.mode == self.mode.value,
                MarketRow.venue == venue,
                MarketRow.market_id == market_id))
            if row is None:
                s.add(MarketRow(
                    mode=self.mode.value, market_id=market_id, venue=venue,
                    underlying_exchange=underlying_exchange,
                    contract_id=contract_id, description=description,
                    outcome_type=outcome_type, category=category,
                    status=status, created_at=now, updated_at=now))
            else:
                row.description = description
                row.status = status
                row.updated_at = now
            s.commit()

    # ------------------------------------------------------------ matches
    def save_match_certificate(self, match_id: str, contract_a: str,
                               contract_b: str, score: float, status: str,
                               independence: str, certificate_json: str,
                               llm_version: str, llm_prompt_version: str,
                               matcher_version: str) -> None:
        with Session(self.engine) as s:
            existing = s.scalar(select(ContractMatchRow).where(
                ContractMatchRow.match_id == match_id))
            if existing is not None:
                existing.validation_status = status
                existing.match_score = score
                existing.certificate_json = certificate_json
            else:
                s.add(ContractMatchRow(
                    mode=self.mode.value, match_id=match_id,
                    contract_a=contract_a, contract_b=contract_b,
                    match_score=score, validation_status=status,
                    independence=independence,
                    certificate_json=certificate_json,
                    llm_version=llm_version,
                    llm_prompt_version=llm_prompt_version,
                    matcher_version=matcher_version, created_at=utcnow()))
            s.commit()

    def approved_matches(self) -> list[ContractMatchRow]:
        with Session(self.engine) as s:
            return list(s.scalars(select(ContractMatchRow).where(
                ContractMatchRow.validation_status == "APPROVED")))

    # -------------------------------------------------------- provenance
    def record_provenance(self, venue: str, underlying_exchange: str,
                          clearing_entity: str, issuer: str,
                          liquidity_source: str, source_url: str,
                          verified_at: str, confidence: str,
                          version: int) -> None:
        with Session(self.engine) as s:
            s.add(VenueProvenanceRow(
                venue=venue, underlying_exchange=underlying_exchange,
                clearing_entity=clearing_entity, issuer=issuer,
                liquidity_source=liquidity_source, source_url=source_url,
                verified_at=verified_at, confidence=confidence,
                version=version))
            s.commit()

    # ------------------------------------------------------ opportunities
    def save_opportunity(self, op: Opportunity) -> None:
        legs = [{
            "venue": l.venue, "contract_id": l.contract.contract_id,
            "side": l.side.value, "quantity": str(l.quantity),
            "wap": str(l.walk.weighted_average_price) if l.walk else None,
        } for l in op.legs]
        with Session(self.engine) as s:
            s.add(OpportunityRow(
                opportunity_id=op.opportunity_id, mode=op.mode.value,
                opportunity_type=op.opportunity_type.value,
                timestamp=op.timestamp,
                normalized_event=op.normalized_event,
                contracts=json.dumps(legs),
                venues=",".join(sorted({l.venue for l in op.legs})),
                gross_profit=str(op.payout_per_contract * op.quantity
                                 - op.gross_cost),
                fees=str(op.total_fees), slippage=str(op.slippage),
                net_profit=str(op.net_profit), quantity=str(op.quantity),
                capital_required=str(op.capital_required),
                independence=op.independence.value, status=op.status.value,
                block_reasons=json.dumps(op.block_reasons),
                first_seen=op.first_seen, last_seen=op.last_seen))
            s.commit()

    # -------------------------------------------------------------- trades
    def write_trade_intent(self, trade_id: str, opportunity_id: str,
                           venue: str, client_order_id: str,
                           contract_id: str, side: str, action: str,
                           quantity: str, requested_price: str) -> None:
        """Crash recovery step 1 (§61): intent BEFORE transmission."""
        with Session(self.engine) as s:
            s.add(TradeRow(
                trade_id=trade_id, opportunity_id=opportunity_id,
                mode=self.mode.value, venue=venue,
                client_order_id=client_order_id, contract_id=contract_id,
                side=side, action=action, quantity=quantity,
                requested_price=requested_price,
                status=OrderStatus.PENDING.value, timestamp=utcnow()))
            s.commit()

    def update_trade_status(self, client_order_id: str, status: OrderStatus,
                            order_id: str = "", fill_price: str = "",
                            filled_quantity: str = "",
                            fees: str = "") -> None:
        with Session(self.engine) as s:
            row = s.scalar(select(TradeRow).where(
                TradeRow.client_order_id == client_order_id,
                TradeRow.mode == self.mode.value))
            if row is None:
                return
            row.status = status.value
            if order_id:
                row.order_id = order_id
            if fill_price:
                row.fill_price = fill_price
            if filled_quantity:
                row.filled_quantity = filled_quantity
            if fees:
                row.fees = fees
            s.commit()

    def unreconciled_trades(self) -> list[TradeRow]:
        """PENDING/SUBMITTED intents — crash recovery input (§61)."""
        with Session(self.engine) as s:
            return list(s.scalars(select(TradeRow).where(
                TradeRow.mode == self.mode.value,
                TradeRow.status.in_([OrderStatus.PENDING.value,
                                     OrderStatus.SUBMITTED.value]))))

    # ------------------------------------------------------------ positions
    def open_position(self, position_id: str, venue: str, contract_id: str,
                      side: str, quantity: str, entry_price: str,
                      expected_resolution: Optional[datetime],
                      expected_settlement: Optional[datetime]) -> None:
        with Session(self.engine) as s:
            s.add(PositionRow(
                position_id=position_id, mode=self.mode.value, venue=venue,
                contract_id=contract_id, side=side, quantity=quantity,
                entry_price=entry_price, entry_time=utcnow(),
                expected_resolution_date=expected_resolution,
                expected_settlement_date=expected_settlement))
            s.commit()

    def open_positions(self) -> list[PositionRow]:
        with Session(self.engine) as s:
            return list(s.scalars(select(PositionRow).where(
                PositionRow.mode == self.mode.value,
                PositionRow.status == "open")))

    # ----------------------------------------------------------- orderbooks
    def save_orderbook_snapshot(self, book: OrderBook) -> None:
        with Session(self.engine) as s:
            s.add(OrderBookSnapshotRow(
                mode=self.mode.value, venue=book.venue,
                contract_id=book.contract_id,
                timestamp=book.timestamp or utcnow(),
                bids_json=json.dumps([[str(l.price), str(l.quantity)]
                                      for l in book.bids]),
                asks_json=json.dumps([[str(l.price), str(l.quantity)]
                                      for l in book.asks]),
                raw_json=json.dumps(book.raw, default=str) if book.raw else ""))
            s.commit()

    def orderbook_snapshots(self, start: Optional[datetime] = None,
                            end: Optional[datetime] = None
                            ) -> list[OrderBookSnapshotRow]:
        with Session(self.engine) as s:
            q = select(OrderBookSnapshotRow).order_by(
                OrderBookSnapshotRow.timestamp)
            if start is not None:
                q = q.where(OrderBookSnapshotRow.timestamp >= start)
            if end is not None:
                q = q.where(OrderBookSnapshotRow.timestamp <= end)
            return list(s.scalars(q))
