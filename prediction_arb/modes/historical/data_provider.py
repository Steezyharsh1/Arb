"""Historical data provider: reads recorded snapshots from the recorded
store, groups them into replay frames, and rebuilds each contract exactly
as it was captured (D4)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from ...core.clock import ensure_utc
from ...core.types import (
    MicrostructureType,
    NormalizedContract,
    OrderBook,
    PriceLevel,
)
from ...database.contract_codec import decode_contract
from ...database.repository import Repository

Frame = tuple[datetime, list[NormalizedContract],
              dict[tuple[str, str], OrderBook]]


class HistoricalDataProvider:
    """Groups recorded snapshots into time-bucketed frames (default 60s).

    Also tracks what it could *not* faithfully rebuild, so a replay that
    produced nothing says why instead of looking like a quiet market.
    """

    def __init__(self, repo: Repository, bucket_seconds: int = 60):
        self.repo = repo
        self.bucket_seconds = bucket_seconds
        #: (venue, contract_id, reason) for every snapshot whose contract
        #: could not be reconstructed from the recorded store.
        self.under_recorded: list[tuple[str, str, str]] = []

    def load_frames(self, start: Optional[datetime],
                    end: Optional[datetime]) -> list[Frame]:
        self.under_recorded = []
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
            contracts = [c for c in (
                self._reconstruct_contract(v, cid)
                for (v, cid) in books) if c is not None]
            out.append((bucket, contracts, books))
        return out

    def _reconstruct_contract(self, venue: str, contract_id: str
                              ) -> Optional[NormalizedContract]:
        """Rebuild the contract exactly as it was recorded (D4).

        Returns ``None`` when the row was never recorded, or was recorded
        before migration 002 and so lacks the fields a contract needs.
        That is counted and reported, never papered over with a plausible
        default: an invented settlement time, payout, or tick size would
        let the matcher and the cost model reason about a contract that
        was never actually observed (§92 — never fabricate data).
        """
        row = self.repo.get_market_row(venue, contract_id)
        if row is None:
            self.under_recorded.append((venue, contract_id, "not recorded"))
            return None
        contract = decode_contract(row)
        if contract is None:
            self.under_recorded.append(
                (venue, contract_id,
                 "recorded before migration 002 (contract fields missing)"))
        return contract
