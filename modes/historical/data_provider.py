"""Historical data provider: reads recorded snapshots from the
COLLECT/paper database lineage, groups them into replay frames."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from ...core.clock import ensure_utc
from ...core.types import (
    MarketMicrostructure,
    MicrostructureType,
    NormalizedContract,
    OrderBook,
    OrderTypeSupport,
    OutcomeType,
    PriceLevel,
)
from ...database.repository import Repository

Frame = tuple[datetime, list[NormalizedContract],
              dict[tuple[str, str], OrderBook]]


class HistoricalDataProvider:
    """Groups recorded snapshots into time-bucketed frames (default 60s)."""

    def __init__(self, repo: Repository, bucket_seconds: int = 60):
        self.repo = repo
        self.bucket_seconds = bucket_seconds

    def load_frames(self, start: Optional[datetime],
                    end: Optional[datetime]) -> list[Frame]:
        rows = self.repo.orderbook_snapshots(start, end)
        if not rows:
            return []
        frames: dict[datetime, dict[tuple[str, str], OrderBook]] = {}
        for r in rows:
            ts = ensure_utc(r.timestamp)
            bucket = ts - timedelta(
                seconds=ts.second % self.bucket_seconds,
                microseconds=ts.microsecond)
            book = OrderBook(
                venue=r.venue, contract_id=r.contract_id,
                bids=[PriceLevel(Decimal(p), Decimal(q))
                      for p, q in json.loads(r.bids_json)],
                asks=[PriceLevel(Decimal(p), Decimal(q))
                      for p, q in json.loads(r.asks_json)],
                timestamp=ts,
                microstructure_type=MicrostructureType.CLOB)
            frames.setdefault(bucket, {})[(r.venue, r.contract_id)] = book

        out: list[Frame] = []
        for bucket in sorted(frames):
            books = frames[bucket]
            contracts = [self._reconstruct_contract(v, c)
                         for (v, c) in books]
            out.append((bucket, contracts, books))
        return out

    def _reconstruct_contract(self, venue: str,
                              contract_id: str) -> NormalizedContract:
        """Minimal contract reconstruction from recorded metadata. Fields
        the recording lacks stay empty — the deterministic matcher will
        reject pairs missing settlement data, which is the correct
        conservative behavior for incomplete historical records."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session
        from ...database.models import MarketRow

        desc, category = "", ""
        with Session(self.repo.engine) as s:
            row = s.scalar(select(MarketRow).where(
                MarketRow.venue == venue,
                MarketRow.market_id == contract_id))
            if row is not None:
                desc, category = row.description, row.category

        return NormalizedContract(
            venue=venue, underlying_exchange="", clearing_entity="",
            market_id=contract_id, contract_id=contract_id,
            event_description=desc, normalized_event="",
            outcome_type=OutcomeType.BINARY, outcome="YES",
            normalized_outcome="", outcome_count=2,
            all_outcomes=("YES", "NO"), settlement_time=None,
            timezone="UTC", cutoff_time=None, resolution_source="",
            resolution_method="", threshold=None, threshold_operator=None,
            units=None, payout=Decimal("1.00"), currency="USD",
            early_resolution_rules="", cancellation_rules="",
            contract_version="historical", tick_size=Decimal("0.01"),
            min_order_size=Decimal("1"), max_order_size=None,
            jurisdiction_requirements="US", category=category,
            market_microstructure=MarketMicrostructure(),
            supported_order_types=OrderTypeSupport())
