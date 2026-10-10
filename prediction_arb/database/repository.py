"""Repository over the mode-specific database (§40, §49, §61).

Includes the crash-recovery queries: PENDING/SUBMITTED trade intents are
discoverable at startup for reconciliation against venue state.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine, func, inspect, or_, select
from sqlalchemy.orm import Session

from ..core.clock import ensure_utc, utcnow
from ..core.types import (
    Mode,
    NormalizedContract,
    Opportunity,
    OrderBook,
    OrderStatus,
)
from .contract_codec import encode_contract, encode_snapshot_contract
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


#: Modes whose recorded market data historical replay is allowed to read
#: (read-only). Replay must be able to see what collect recorded — that is
#: the whole point of collecting — but it may never write into that store.
_REPLAY_READABLE_TAGS = frozenset({"collect", "paper"})


def _stored_amount(value: Optional[Decimal]) -> Optional[str]:
    """Stored form of a money amount.

    ``None`` stays ``None`` (SQL NULL): an unavailable mandatory cost is
    recorded as UNVERIFIED, never as ``"0"`` — a zero fee column next to a
    positive net profit is exactly the misleading row D17 removed.
    """
    return None if value is None else str(value)


class Repository:
    """Mode-tagged database handle.

    ``read_only=True`` opens an existing recorded store at the OS level in
    read-only mode: migrations are skipped, the mode tag is not written,
    and every write is refused by SQLite itself rather than by convention.
    It never creates the file. This is how historical mode reads the
    recorded store while keeping its own database for replay output (D3).
    """

    def __init__(self, db_path: Path, mode: Mode,
                 read_only: bool = False):
        self.mode = mode
        self.db_path = db_path
        self.read_only = read_only
        if read_only:
            if not Path(db_path).exists():
                raise FileNotFoundError(
                    f"Recorded store {db_path} does not exist. Historical "
                    f"replay never creates or fabricates data — run "
                    f"`--mode collect` to record real order books first "
                    f"(§55, §92).")
            uri = f"file:{Path(db_path).resolve()}?mode=ro"
            # `creator` keeps SQLAlchemy's SQLite dialect from rewriting the
            # `file:` URI into a relative path; mode=ro is enforced by
            # SQLite, not by us.
            self.engine = create_engine(
                "sqlite://", future=True,
                creator=lambda: sqlite3.connect(uri, uri=True))
            self._assert_mode_tag()
            return

        self.engine = create_engine(f"sqlite:///{db_path}", future=True)
        run_migrations(self.engine)
        self._assert_mode_tag()
        with Session(self.engine) as s:
            schema = s.scalar(select(MetaRow).where(MetaRow.key == "schema_version"))
            if schema is None:
                s.add(MetaRow(key="schema_version", value=str(SCHEMA_VERSION)))
            elif schema.value != str(SCHEMA_VERSION):
                schema.value = str(SCHEMA_VERSION)
            s.commit()

    def _assert_mode_tag(self) -> None:
        """The database file itself records which mode owns it. Opening a
        live DB from paper mode (or vice versa) fails hard (§49)."""
        with Session(self.engine) as s:
            row = s.scalar(select(MetaRow).where(MetaRow.key == "mode"))
            if row is None:
                if self.read_only:
                    raise RuntimeError(
                        f"Recorded store {self.db_path} has no mode tag; "
                        f"refusing to read an untagged database (§49).")
                s.add(MetaRow(key="mode", value=self.mode.value))
                s.add(MetaRow(key="schema_version", value=str(SCHEMA_VERSION)))
                s.commit()
                return
            if row.value == self.mode.value:
                return
            # collect <-> paper share one market-data lineage.
            if (row.value == "collect" and self.mode == Mode.PAPER
                    or row.value == "paper" and self.mode == Mode.COLLECT):
                return
            # Historical replay reads the recorded store read-only and
            # writes only to its own database, so reading a collect/paper
            # tag is the intended path, not a mode mix (D3).
            if (self.read_only and self.mode == Mode.HISTORICAL
                    and row.value in _REPLAY_READABLE_TAGS):
                self.recorded_store_tag = row.value
                return
            raise RuntimeError(
                f"Database {self.db_path} is tagged mode={row.value!r} "
                f"but application mode is {self.mode.value!r}. Refusing "
                f"to mix mode data (§49).")

    def _market_data_modes(self) -> set[str]:
        if (self.mode in (Mode.COLLECT, Mode.PAPER)
                or self.read_only and getattr(self, "recorded_store_tag", None) in _REPLAY_READABLE_TAGS):
            return set(_REPLAY_READABLE_TAGS)
        return {self.mode.value}

    # -------------------------------------------------------------- markets
    def upsert_market(self, venue: str, market_id: str, contract_id: str,
                      underlying_exchange: str, description: str,
                      outcome_type: str, category: str, status: str) -> None:
        """Legacy narrow upsert. Prefer :meth:`record_contract` (D4).

        Kept for callers that only know the discovery-time basics. It
        writes NULLs for the D4 columns rather than guessing at them, so a
        market recorded this way decodes as under-recorded at replay time
        instead of pretending to have settlement data it never had.
        """
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

    def record_contract(self, contract: NormalizedContract,
                        status: str = "open",
                        *, observed_at: Optional[datetime] = None) -> None:
        """Persist the FULL normalized contract + raw payload (D4).

        Replay can only reconstruct what was recorded, so collect mode
        records everything the matcher and the cost model need: settlement
        and cutoff times, resolution source/method, threshold + operator +
        units, early-resolution and cancellation rules, payout, tick size,
        order-size bounds, contract version, provenance, microstructure,
        supported order types, and the venue's own payload.
        """
        values = encode_contract(contract)
        # Only pass observed_at when importing an actual recorded receipt
        # time. Settlement/event times are NOT contract observation times.
        now = ensure_utc(observed_at) if observed_at is not None else utcnow()
        with Session(self.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.mode == self.mode.value,
                MarketRow.venue == contract.venue,
                MarketRow.market_id == contract.market_id))
            if row is None:
                s.add(MarketRow(
                    mode=self.mode.value, market_id=contract.market_id,
                    venue=contract.venue,
                    contract_id=contract.contract_id,
                    status=status, created_at=now, updated_at=now, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
                row.status = status
                row.updated_at = now
            s.commit()

    def get_market_row(self, venue: str, identifier: str
                       ) -> Optional[MarketRow]:
        """The recorded row for one contract, for faithful replay (D4).

        Snapshots are keyed by ``contract_id`` while contracts are keyed by
        ``market_id``. Every adapter currently sets those equal, but that
        is an implementation detail, not a guarantee — so replay accepts
        either rather than silently failing to find a contract it did
        record.
        """
        with Session(self.engine) as s:
            return s.scalar(select(MarketRow).where(
                MarketRow.mode.in_(self._market_data_modes()),
                MarketRow.venue == venue,
                or_(MarketRow.market_id == identifier,
                    MarketRow.contract_id == identifier)).order_by(
                        MarketRow.updated_at.desc(), MarketRow.id.desc()))

    # ------------------------------------------------------------ matches
    def save_match_certificate(self, match_id: str, contract_a: str,
                               contract_b: str, score: float, status: str,
                               independence: str, certificate_json: str,
                               llm_version: str, llm_prompt_version: str,
                               matcher_version: str) -> None:
        """Persist a match certificate with deduplication for unchanged pairs.

        Arb.9: the same unchanged pair must not create unbounded duplicate
        REVIEW records on every scan cycle. We deduplicate by checking whether
        all meaningful fields (status, score, independence, versions) are
        identical to the existing row — if so, we skip the write entirely.
        When something meaningful changes (verdict, model/prompt/matcher
        version, independence), we update the existing row, preserving the
        latest state while avoiding per-cycle bloat.

        Historical auditability: meaningful changes are recorded via the
        updated row (status, versions, certificate_json). Full history of
        unchanged repeats is intentionally NOT stored to prevent unbounded
        growth. If full versioned history is required, the smallest viable
        migration is documented in docs/MATCHING_DEDUP_MIGRATION.md — a
        separate audit log table rather than removing the unique constraint
        on match_id.
        """
        with Session(self.engine) as s:
            existing = s.scalar(select(ContractMatchRow).where(
                ContractMatchRow.match_id == match_id))
            if existing is not None:
                # Deduplicate unchanged: if all meaningful fields match,
                # skip the write to avoid per-cycle bloat.
                if (existing.validation_status == status
                        and existing.match_score == score
                        and existing.independence == independence
                        and existing.llm_version == llm_version
                        and existing.llm_prompt_version == llm_prompt_version
                        and existing.matcher_version == matcher_version
                        # certificate_json includes created_at which changes
                        # every call; compare without it by checking status
                        # and versions already, or parse and ignore created_at.
                        # For simplicity, we treat same status+versions+score
                        # as unchanged — the JSON's checks/reasons are covered
                        # by status and versions for deduplication purposes.
                        ):
                    return
                existing.validation_status = status
                existing.match_score = score
                existing.certificate_json = certificate_json
                existing.independence = independence
                existing.llm_version = llm_version
                existing.llm_prompt_version = llm_prompt_version
                existing.matcher_version = matcher_version
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
        """Persist one evaluated opportunity (§40).

        Money that depends on a mandatory cost is written as NULL when it
        could not be computed — never as ``"0"`` (D17). A row therefore
        cannot claim a verified positive net profit for an opportunity the
        evaluator blocked for incomplete costs, and `unavailable_costs`
        records exactly which components were missing.
        """
        legs = [{
            "venue": l.venue, "contract_id": l.contract.contract_id,
            "side": l.side.value, "quantity": str(l.quantity),
            "wap": str(l.walk.weighted_average_price) if l.walk else None,
        } for l in op.legs]
        missing = list(op.costs.missing_names())
        with Session(self.engine) as s:
            s.add(OpportunityRow(
                opportunity_id=op.opportunity_id, mode=op.mode.value,
                opportunity_type=op.opportunity_type.value,
                timestamp=op.timestamp,
                normalized_event=op.normalized_event,
                contracts=json.dumps(legs),
                venues=",".join(sorted({l.venue for l in op.legs})),
                gross_profit=str(op.gross_edge),
                fees=_stored_amount(op.total_fees), slippage=str(op.slippage),
                net_profit=_stored_amount(op.net_profit),
                quantity=str(op.quantity),
                capital_required=_stored_amount(op.capital_required),
                independence=op.independence.value, status=op.status.value,
                block_reasons=json.dumps(op.block_reasons),
                unavailable_costs=(json.dumps(missing) if missing else None),
                evaluation_json=(json.dumps(op.evaluation_metadata, sort_keys=True)
                                 if op.evaluation_metadata else None),
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
        """Record observed timing and immutable, already-observed terms.

        A persist-time clock is never substituted for an unknown receipt
        time. Contracts discovered AFTER the book was received cannot be
        attached retrospectively. Legacy rows remain theory-only.
        """
        market = self.get_market_row(book.venue, book.contract_id)
        contract_json = None
        contract_observed_at = None
        if (market is not None and book.received_at is not None
                and market.updated_at is not None
                and ensure_utc(market.updated_at) <= ensure_utc(book.received_at)):
            contract_json = encode_snapshot_contract(market)
            if contract_json:
                contract_observed_at = market.updated_at
        with Session(self.engine) as s:
            s.add(OrderBookSnapshotRow(
                mode=self.mode.value, venue=book.venue,
                contract_id=book.contract_id,
                timestamp=book.timestamp or utcnow(),
                received_at=book.received_at,
                # No timestamp provenance may be claimed for a missing time.
                timestamp_source=(book.timestamp_source if book.timestamp else None),
                outcome_side=(book.outcome_side.value if book.outcome_side else None),
                sequence_number=book.sequence_number,
                microstructure_type=book.microstructure_type.value,
                contract_json=contract_json,
                contract_observed_at=contract_observed_at,
                bids_json=json.dumps([[str(l.price), str(l.quantity)]
                                      for l in book.bids]),
                asks_json=json.dumps([[str(l.price), str(l.quantity)]
                                      for l in book.asks]),
                raw_json=json.dumps(book.raw, default=str) if book.raw else ""))
            s.commit()

    def orderbook_snapshots(self, start: Optional[datetime] = None,
                            end: Optional[datetime] = None
                            ) -> list[OrderBookSnapshotRow]:
        """Read old stores without migrating or selecting absent columns.

        Range/order use local receipt time when recorded. A legacy quote
        timestamp is a fallback for THEORY ONLY, assessed by the provider.
        """
        present = {c["name"] for c in inspect(self.engine).get_columns(
            "orderbook_snapshots")}
        columns = [c for c in OrderBookSnapshotRow.__table__.columns
                   if c.name in present]
        observed = (func.coalesce(OrderBookSnapshotRow.received_at,
                                  OrderBookSnapshotRow.timestamp)
                    if "received_at" in present else OrderBookSnapshotRow.timestamp)
        readable = self._market_data_modes()
        q = select(*columns).where(
            OrderBookSnapshotRow.mode.in_(readable)).order_by(
                observed, OrderBookSnapshotRow.id)
        if start is not None:
            q = q.where(observed >= ensure_utc(start))
        if end is not None:
            q = q.where(observed <= ensure_utc(end))
        with self.engine.connect() as conn:
            return [OrderBookSnapshotRow(**dict(row))
                    for row in conn.execute(q).mappings()]
