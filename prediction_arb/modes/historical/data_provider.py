"""As-of replay of recorded observations, NEVER a bucket of future prices.

Schema <=3 has a single ambiguous quote/observation timestamp and mutable
market metadata. Such rows can support labelled theoretical calculations,
not executable observations or fills. Schema 4 preserves local availability,
outcome identity and snapshot-bound terms. Even then, state between samples
is an explicit bounded carry-forward assumption, not observed continuity.
"""
from __future__ import annotations

import json
import warnings
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from itertools import groupby
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError

from ...backtesting.policy import MINIMUM_COLLECTION_REQUIREMENTS, ReplayPolicy
from ...core.clock import ensure_utc
from ...core.types import (
    MicrostructureType, NormalizedContract, Opportunity, OrderBook,
    OpportunityStatus, OutcomeType, PriceLevel, Side,
)
from ...database.contract_codec import decode_contract, decode_snapshot_contract
from ...database.repository import Repository
from ...normalization.contracts import with_normalized_keys
from ...orderbooks.validation import order_book_problems

Frame = tuple[datetime, list[NormalizedContract], dict[tuple[str, str], OrderBook]]


@dataclass
class BookObservation:
    available_at: datetime
    key: tuple[str, str]
    book: Optional[OrderBook]
    contract: Optional[NormalizedContract]
    limitations: list[str] = field(default_factory=list)


class HistoricalDataProvider:
    def __init__(self, repo: Repository, bucket_seconds: Optional[int] = None,
                 *, policy: Optional[ReplayPolicy] = None):
        if bucket_seconds is not None:
            warnings.warn("bucket_seconds no longer coalesces historical books; "
                          "replay uses exact observation times", DeprecationWarning,
                          stacklevel=2)
        self.repo = repo
        self.policy = policy or ReplayPolicy()
        self.under_recorded: list[tuple[str, str, str]] = []
        self.quality: Counter = Counter()
        self.limitation_counts: Counter = Counter()
        self._events: dict[tuple[str, str], list[BookObservation]] = {}
        self._times: dict[tuple[str, str], list[datetime]] = {}
        self.first_observation: Optional[datetime] = None
        self.last_observation: Optional[datetime] = None
        self.requested_end: Optional[datetime] = None

    def load_frames(self, start: Optional[datetime],
                    end: Optional[datetime]) -> list[Frame]:
        self.under_recorded = []
        self.quality = Counter()
        self.limitation_counts = Counter()
        self._events, self._times = {}, {}
        self.first_observation = self.last_observation = None
        start, end = ensure_utc(start), ensure_utc(end)
        self.requested_end = end
        # Look behind the range for initial state, never ahead of its end.
        rows = self.repo.orderbook_snapshots(None, end)
        self.quality["snapshots_read"] = len(rows)
        observations: list[BookObservation] = []
        previous_time = None
        for index, row in enumerate(rows):
            observation = self._decode_observation(row, index + 1)
            if observation is None:
                continue
            if previous_time is not None and observation.available_at < previous_time:
                self.quality["out_of_order_input_rows"] += 1
            previous_time = observation.available_at
            observations.append(observation)
        # Receive-time order is the only order in which the collector could
        # have known these prices. Never sort delayed venue quotes into the past.
        observations.sort(key=lambda o: (o.available_at, o.key,
                                         o.book.snapshot_id if o.book else 0))
        state: dict[tuple[str, str], BookObservation] = {}
        newest_source: dict[tuple[str, str], datetime] = {}
        out: list[Frame] = []
        for ts, group in groupby(observations, key=lambda o: o.available_at):
            if self.first_observation is None:
                self.first_observation = ts
            self.last_observation = ts
            by_key: dict[tuple[str, str], list[BookObservation]] = {}
            for obs in group:
                by_key.setdefault(obs.key, []).append(obs)
            for key, candidates in sorted(by_key.items()):
                obs = candidates[-1]
                # Same key/time but different prices cannot be sequenced from
                # the recording. Do not let insertion order choose the winner.
                signatures = {self._signature(c) for c in candidates}
                if len(signatures) > 1:
                    self.quality["ambiguous_same_time_books"] += len(candidates)
                    obs = BookObservation(ts, key, None, None,
                                          ["conflicting books at identical availability time"])
                if obs.book is not None:
                    source = obs.book.timestamp
                    if key in newest_source and source < newest_source[key]:
                        self.quality["source_timestamp_regressions_ignored"] += 1
                        continue
                    old = state.get(key)
                    if (old is not None and old.book is not None
                            and old.book.timestamp == source
                            and self._signature(old) != self._signature(obs)):
                        self.quality["ambiguous_same_source_time_books"] += 1
                        obs = BookObservation(ts, key, None, None,
                                              ["conflicting books at identical source time"])
                    else:
                        newest_source[key] = source
                # Invalid observations invalidate the cached state. Carrying an
                # older positive quote across a corrupt/empty update is unsafe.
                state[key] = obs
                self._events.setdefault(key, []).append(obs)
                self._times.setdefault(key, []).append(ts)
            if start is not None and ts < start:
                self.quality["seed_observation_times_before_range"] += 1
                continue
            books = {k: o.book for k, o in sorted(state.items()) if o.book is not None}
            contracts = [o.contract for k, o in sorted(state.items())
                         if k in books and o.contract is not None]
            self.quality["stale_book_decision_observations"] += sum(
                bool(self._age_reasons(book, ts)) for book in books.values())
            out.append((ts, contracts, books))
        self.quality["decision_frames"] = len(out)
        return out

    @staticmethod
    def _signature(obs: BookObservation) -> tuple:
        book, contract = obs.book, obs.contract
        if book is None:
            return (None, tuple(obs.limitations))
        return (tuple(book.bids), tuple(book.asks), book.outcome_side,
                contract.contract_version if contract else None,
                repr(contract))

    def _decode_observation(self, row, fallback_id: int) -> Optional[BookObservation]:
        ts = ensure_utc(getattr(row, "timestamp", None))
        received = ensure_utc(getattr(row, "received_at", None))
        available = received or ts
        key = (row.venue, row.contract_id)
        if available is None:
            self.quality["snapshots_without_any_time"] += 1
            return None
        reasons: list[str] = []
        if received is None:
            self.quality["snapshots_without_receipt_time"] += 1
            reasons.append("local receipt time unavailable; quote-time availability is THEORY ONLY")
        kind = getattr(row, "timestamp_source", None)
        if kind not in ("venue", "local_receipt"):
            self.quality["snapshots_without_timestamp_provenance"] += 1
            reasons.append("timestamp provenance unavailable")
        if ts is None or (received is not None and ts > received):
            self.quality["invalid_or_future_source_timestamps"] += 1
            return BookObservation(available, key, None, None,
                                   ["source timestamp missing or later than local receipt"])
        if kind == "local_receipt" and ts != received:
            reasons.append("local_receipt timestamp does not equal recorded receipt time")
        side_raw = getattr(row, "outcome_side", None)
        try:
            side = Side(side_raw) if side_raw else None
        except ValueError:
            side = None
        if side is None:
            self.quality["snapshots_without_outcome_identity"] += 1
            reasons.append("priced outcome of recorded ladders unavailable")
        micro_raw = getattr(row, "microstructure_type", None)
        try:
            micro = MicrostructureType(micro_raw) if micro_raw else MicrostructureType.UNKNOWN
        except ValueError:
            micro = MicrostructureType.UNKNOWN
        if micro not in (MicrostructureType.CLOB, MicrostructureType.PAIRED_BID):
            reasons.append("book microstructure unavailable or unsupported")
        try:
            book = OrderBook(
                venue=row.venue, contract_id=row.contract_id,
                bids=[PriceLevel(Decimal(p), Decimal(q)) for p, q in json.loads(row.bids_json)],
                asks=[PriceLevel(Decimal(p), Decimal(q)) for p, q in json.loads(row.asks_json)],
                timestamp=ts, received_at=received, timestamp_source=kind,
                outcome_side=side, sequence_number=getattr(row, "sequence_number", None),
                microstructure_type=micro,
                snapshot_id=getattr(row, "id", None) or fallback_id)
            problems = order_book_problems(book)
        except (TypeError, ValueError, InvalidOperation):
            book, problems = None, ["malformed recorded depth"]
        if problems:
            self.quality["invalid_depth_snapshots"] += 1
            return BookObservation(available, key, None, None, problems)

        context_time = ensure_utc(getattr(row, "contract_observed_at", None))
        payload = getattr(row, "contract_json", None)
        contract = decode_snapshot_contract(payload)
        if (contract is None or context_time is None or received is None
                or context_time > received):
            self.quality["snapshots_without_asof_contract_context"] += 1
            reasons.append("as-of contract terms unavailable; latest market metadata is THEORY ONLY")
            # With a future/invalid snapshot context, don't replace it with
            # today's metadata and pretend the contract was observed earlier.
            contract = self._reconstruct_contract(*key) if not payload else None
        if contract is None:
            if payload:
                self.under_recorded.append((*key, "invalid or future snapshot-bound contract context"))
        else:
            if (contract.venue, contract.contract_id) != key:
                reasons.append("snapshot key differs from recorded contract identity")
            if contract.outcome_type != OutcomeType.BINARY or contract.payout != Decimal(1):
                reasons.append("only identified $1 binary books support this fill model")
            if contract.tick_size is None or contract.min_order_size is None:
                reasons.append("tick size or minimum order quantity unavailable")
        for reason in reasons:
            self.limitation_counts[reason] += 1
        return BookObservation(available, key, book, contract, reasons)

    def _reconstruct_contract(self, venue: str, contract_id: str
                              ) -> Optional[NormalizedContract]:
        try:
            row = self.repo.get_market_row(venue, contract_id)
        except SQLAlchemyError:
            row = None
            reason = "recorded contract schema is incomplete"
        else:
            reason = "not recorded" if row is None else "recorded contract fields missing or invalid"
        contract = decode_contract(row) if row is not None else None
        if contract is None:
            self.under_recorded.append((venue, contract_id, reason))
        return contract

    def observation_at(self, key: tuple[str, str], ts: datetime) -> Optional[BookObservation]:
        index = bisect_right(self._times.get(key, []), ensure_utc(ts)) - 1
        return self._events[key][index] if index >= 0 else None

    def _age_reasons(self, book: OrderBook, ts: datetime) -> list[str]:
        reasons = []
        for name, observed in (("quote", book.timestamp), ("receipt", book.received_at)):
            if observed is None:
                continue  # reported as unavailable by _decode_observation
            age = (ts - observed).total_seconds() * 1000
            if age < 0:
                reasons.append(f"{name} time is after decision/arrival")
            elif age > self.policy.max_book_age_ms:
                reasons.append(f"stale {name}: age {age:g}ms exceeds {self.policy.max_book_age_ms}ms")
        return reasons

    def execution_book(self, key: tuple[str, str], ts: datetime,
                       *, require_horizon: bool = False
                       ) -> tuple[Optional[OrderBook], Optional[NormalizedContract], list[str]]:
        ts = ensure_utc(ts)
        if require_horizon and (self.last_observation is None or ts > self.last_observation
                                or self.requested_end is not None and ts > self.requested_end):
            return None, None, ["recording/range ended before simulated order arrival"]
        obs = self.observation_at(key, ts)
        if obs is None or obs.book is None:
            return None, None, (obs.limitations if obs else ["book state unavailable at this time"])
        reasons = obs.limitations + self._age_reasons(obs.book, ts)
        return obs.book, obs.contract, list(dict.fromkeys(reasons))

    @staticmethod
    def _book_identity(book: Optional[OrderBook]) -> Optional[tuple]:
        if book is None:
            return None
        return (book.venue, book.contract_id, book.snapshot_id, book.timestamp,
                book.received_at, book.timestamp_source,
                book.outcome_side, book.microstructure_type, book.sequence_number,
                tuple(book.bids), tuple(book.asks))

    def assess_opportunity(self, op: Opportunity, ts: datetime, *,
                           decision_books: Optional[dict[tuple[str, str], OrderBook]] = None) -> None:
        """Separate cost qualification from historical executability.

        A calculated TRADE on unsupported temporal data becomes BLOCK, while
        its theoretical net amount remains explicitly labelled and available.
        Valid provider state cannot certify arbitrary library-injected future
        calculation inputs: the supplied books and terms must match as-of state.
        """
        reasons: list[str] = []
        inputs = []
        receipts = []
        costed_trade = op.is_executable
        for leg in op.legs:
            key = (leg.venue, leg.contract.contract_id)
            book, contract, missing = self.execution_book(key, ts)
            reasons.extend(f"{key[0]}/{key[1]}: {m}" for m in missing)
            if contract is None:
                reasons.append(f"{key[0]}/{key[1]}: as-of contract unavailable")
            elif with_normalized_keys(contract) != leg.contract:
                reasons.append(f"{key[0]}/{key[1]}: decision contract differs from as-of recorded terms")
            if decision_books is not None:
                used = decision_books.get(key)
                if self._book_identity(used) != self._book_identity(book):
                    reasons.append(f"{key[0]}/{key[1]}: decision book differs from as-of recorded book")
            if book is not None:
                if book.received_at is not None:
                    receipts.append(book.received_at)
                inputs.append({
                    "venue": key[0], "contract_id": key[1], "side": leg.side.value,
                    "snapshot_id": book.snapshot_id,
                    "quote_time": book.timestamp.isoformat(),
                    "received_at": book.received_at.isoformat() if book.received_at else None,
                    "timestamp_source": book.timestamp_source,
                    "recorded_outcome_side": book.outcome_side.value if book.outcome_side else None,
                    "contract_version": leg.contract.contract_version,
                    "supplied_decision_snapshot_id": (decision_books[key].snapshot_id
                        if decision_books is not None and key in decision_books else None),
                })
        if len(receipts) == 2:
            skew = (max(receipts) - min(receipts)).total_seconds() * 1000
            if skew > self.policy.max_pair_skew_ms:
                reasons.append(f"independent book observation skew {skew:g}ms exceeds "
                               f"{self.policy.max_pair_skew_ms}ms")
        reasons = sorted(set(reasons))
        op.evaluation_metadata = {
            "kind": "CALCULATED_OPPORTUNITY_NOT_A_FILL",
            "decision_time": ts.isoformat(), "input_books": inputs,
            "costed_trade_before_data_gates": costed_trade,
            "execution_data_supported": not reasons,
            "data_limitations": reasons,
            "availability_basis": "local receipt time" if not reasons else "THEORY ONLY / unsupported",
        }
        if reasons:
            op.status = OpportunityStatus.BLOCK
            op.block_reasons.extend(f"historical execution unsupported: {r}" for r in reasons)

    def data_report(self) -> dict:
        counters = (
            "snapshots_read", "decision_frames", "snapshots_without_any_time",
            "snapshots_without_receipt_time", "snapshots_without_timestamp_provenance",
            "snapshots_without_outcome_identity", "snapshots_without_asof_contract_context",
            "invalid_or_future_source_timestamps", "invalid_depth_snapshots",
            "out_of_order_input_rows", "source_timestamp_regressions_ignored",
            "ambiguous_same_time_books", "ambiguous_same_source_time_books",
            "seed_observation_times_before_range", "stale_book_decision_observations",
        )
        return {
            "classification": "OBSERVED_DATA_INVENTORY",
            **{key: self.quality[key] for key in counters},
            "contracts_unreconstructable": len(self.under_recorded),
            "unreconstructable_reasons": dict(sorted(Counter(r for _, _, r in self.under_recorded).items())),
            "snapshot_limitations": dict(sorted(self.limitation_counts.items())),
            "temporal_reconstruction": "as-of local availability; legacy quote-time fallback is theory only",
            "tick_accurate_reconstruction_available": False,
            "minimum_additional_collection": MINIMUM_COLLECTION_REQUIREMENTS,
        }
