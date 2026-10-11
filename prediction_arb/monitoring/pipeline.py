"""Pipeline telemetry: a closed, low-cardinality vocabulary (§103, Arb.14).

The operator needs to answer two questions without reading logs: *why are
opportunities not progressing?* and *does simulated execution look like a
real fill process?* This module supplies the pure, unit-tested half of that:

* a **closed metric vocabulary** (``METRICS``) and label syntax, so a counter
  can never acquire a free-text or per-opportunity label. Free text such as
  "settlement delay spread 6.0 days exceeds limit 5" is classified into a
  fixed category here and the raw text stays in the opportunity row;
* **bucketing** for continuous values (book age, inter-venue skew, leg gap),
  with an explicit ``unknown`` bucket for values that were not observed;
* a :class:`PipelineTally` that accumulates counts in memory. The engine
  flushes one tally per cycle, and the repository upserts it into one row per
  (mode, UTC day, metric, label), so storage grows with days, not cycles;
* :func:`classify_execution`, which turns a coordinator ``ExecutionReport``
  into a single explicit outcome and hedge state, plus the attempt record that
  lets an operator inspect one simulated opportunity's lifecycle.

Nothing here places orders or changes a decision. It only observes.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Mapping, Optional

from ..core.types import Opportunity, OrderBook, OrderStatus

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from ..execution.coordinator import ExecutionReport

# ---------------------------------------------------------------- vocabulary

SCAN_CYCLES = "scan.cycles"
PAIRS_CONSIDERED = "pairs.considered"                    # candidate pairs generated
PAIRS_DET_PASSED = "pairs.deterministic_passed"
PAIRS_DET_REJECTED = "pairs.deterministic_rejected"      # label: failing check
PAIRS_DET_UNVERIFIED = "pairs.deterministic_unverified"  # label: unverifiable field (D25)
MATCH_STATUS = "match.status"                            # label: APPROVED/REVIEW/REJECTED
OPP_STATUS = "opportunity.status"                        # label: TRADE/WATCH/BLOCK
OPP_BLOCKED_BY = "opportunity.blocked_by"                # label: mandatory input category
OPP_EDGE = "opportunity.edge"                            # label: gross-vs-net class
BOOK_AGE = "book.age"                                    # label: age bucket
BOOK_SKEW = "book.inter_venue_skew"                      # label: skew bucket (per opportunity)
EXEC_ATTEMPT = "exec.attempt"                            # label: attempt outcome
EXEC_SUBMISSION = "exec.submission"                      # label: "A:FILLED", "B:REJECTED", ...
EXEC_HEDGE = "exec.hedge"                                # label: hedge state
EXEC_LEG_GAP = "exec.leg_gap"                            # label: time-between-legs bucket
EXEC_RECOVERY = "exec.recovery_attempt"                  # recovery order attempts (no label)
RISK_BREACH = "risk.breach"                              # label: limit name or "other"
RISK_KILL_ENGAGED = "risk.kill_engaged"                  # kill switch engaged by an attempt
ADAPTER_TELEMETRY = "adapter.telemetry"                  # label: "<venue>:<family>=<state>"
ADAPTER_RATE_LIMITED = "adapter.rate_limited"            # label: venue (429 responses)
ADAPTER_ERROR = "adapter.error"                          # label: venue (failed poll/parse/stream)
ADAPTER_FEED_RECONNECT = "adapter.feed_reconnect"        # label: venue (WebSocket recovery)
ADAPTER_STREAM_FALLBACK = "adapter.stream_fallback"      # label: venue (gave up streaming)

METRICS: frozenset[str] = frozenset({
    SCAN_CYCLES, PAIRS_CONSIDERED, PAIRS_DET_PASSED, PAIRS_DET_REJECTED,
    PAIRS_DET_UNVERIFIED,
    MATCH_STATUS, OPP_STATUS, OPP_BLOCKED_BY, OPP_EDGE, BOOK_AGE, BOOK_SKEW,
    EXEC_ATTEMPT, EXEC_SUBMISSION, EXEC_HEDGE, EXEC_LEG_GAP, EXEC_RECOVERY,
    RISK_BREACH, RISK_KILL_ENGAGED, ADAPTER_TELEMETRY, ADAPTER_RATE_LIMITED,
    ADAPTER_ERROR, ADAPTER_FEED_RECONNECT, ADAPTER_STREAM_FALLBACK,
})

#: Label syntax. Labels are enumerations or venue names; anything with
#: spaces, quotes or free text is refused at the tally so it cannot reach the
#: database.
_LABEL_RE = re.compile(r"[A-Za-z0-9_.:<>=+\-]{0,48}")

UNKNOWN_BUCKET = "unknown"

# Execution outcomes (one per attempt). Exactly one is recorded per attempt.
OUTCOME_HEALTH_BLOCKED = "HEALTH_BLOCKED"
OUTCOME_KILL_SWITCH_BLOCKED = "KILL_SWITCH_BLOCKED"
OUTCOME_PRECHECK_REJECTED = "PRECHECK_REJECTED"
OUTCOME_RISK_BLOCKED = "RISK_BLOCKED"
OUTCOME_LEG_A_ERROR = "LEG_A_ERROR"
OUTCOME_LEG_A_NOT_FILLED = "LEG_A_NOT_FILLED"
OUTCOME_COMPLETED = "COMPLETED"        # both legs filled: a simulated position is open
OUTCOME_RECOVERED = "RECOVERED"        # hedge completed by recovery: position open
OUTCOME_FLATTENED = "FLATTENED"        # recovery unwound leg A: no position kept
OUTCOME_UNHEDGED = "UNHEDGED"          # naked exposure retained (incident)

#: Outcomes that count as a completed simulated (paired) position.
COMPLETED_POSITION_OUTCOMES = frozenset({OUTCOME_COMPLETED, OUTCOME_RECOVERED})

HEDGE_NOT_SUBMITTED = "NOT_SUBMITTED"
HEDGE_NO_FILL = "NO_FILL"
HEDGE_HEDGED = "HEDGED"
HEDGE_FLATTENED = "FLATTENED"
HEDGE_PARTIAL = "PARTIAL_HEDGE"        # naked: leg B partly filled, exposure remains
HEDGE_FAILED = "FAILED_HEDGE"          # naked: leg B never filled, exposure remains

#: Risk-limit names as produced by risk/limits.py and risk/exposure.py.
RISK_KINDS: frozenset[str] = frozenset({
    "max_capital_per_trade", "max_total_capital", "max_capital_per_venue",
    "max_capital_per_event", "max_daily_loss", "max_open_positions",
    "max_concurrent_orders", "max_unhedged_exposure", "max_unhedged_seconds",
    "max_unhedged_contracts", "max_unhedged_dollars",
})

#: Mandatory-input categories. Cost components come from the structured cost
#: ledger; the rest are classified from the block text.
COST_KINDS = ("trade_fee", "settlement_fee", "settlement_delay")
OTHER_BLOCK_KINDS = ("independence", "settlement_spread", "settlement_lockup",
                     "net_not_positive", "net_unpriced", "other")

EDGE_NET_POSITIVE = "net_positive"
EDGE_CONSUMED_BY_COSTS = "gross_consumed_by_costs"
EDGE_UNPRICED = "gross_unpriced"
EDGE_NO_GROSS = "no_gross_edge"


# ------------------------------------------------------------------ tally


class PipelineTally:
    """In-memory counts for one flush. Refuses unknown metrics and labels."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, str]] = Counter()

    def add(self, metric: str, label: str = "", n: int = 1) -> None:
        if metric not in METRICS:
            raise ValueError(f"unknown pipeline metric {metric!r}")
        if not _LABEL_RE.fullmatch(label):
            raise ValueError(f"pipeline label {label!r} is not a bounded token")
        if n:
            self._counts[(metric, label)] += int(n)

    def items(self) -> dict[tuple[str, str], int]:
        return dict(self._counts)

    def __len__(self) -> int:
        return len(self._counts)

    def __bool__(self) -> bool:
        return bool(self._counts)


# ------------------------------------------------------------ bucketing


def _ms_bucket(value_ms: Optional[float], edges: tuple[float, ...]) -> str:
    if value_ms is None:
        return UNKNOWN_BUCKET
    for edge in edges:
        if value_ms <= edge:
            return f"<={edge:g}ms"
    return f">{edges[-1]:g}ms"


def book_age_bucket(age_ms: Optional[float]) -> str:
    """Age of a book at scan time. ``None`` is UNKNOWN, never zero."""
    return _ms_bucket(age_ms, (250.0, 1000.0))


def skew_bucket(skew_ms: Optional[float]) -> str:
    """Absolute timestamp difference between the two legs' books."""
    return _ms_bucket(skew_ms, (250.0, 1000.0))


def leg_gap_bucket(gap_ms: Optional[float]) -> str:
    """Time between submitting leg A and leg B."""
    return _ms_bucket(gap_ms, (250.0, 1000.0, 5000.0))


def book_skew_ms(a: OrderBook, b: OrderBook) -> Optional[float]:
    if a.timestamp is None or b.timestamp is None:
        return None
    return abs((a.timestamp - b.timestamp).total_seconds()) * 1000.0


def risk_kind(violation: str) -> str:
    head = violation.split(":", 1)[0].strip()
    return head if head in RISK_KINDS else "other"


# ---------------------------------------------------- opportunity evidence


def _text_block_kind(reason: str) -> str:
    text = reason.lower()
    if text.startswith("venue relationship"):
        return "independence"
    if "settlement delay spread" in text:
        return "settlement_spread"
    if "settlement delay cost unavailable" in text:
        return "settlement_delay"
    if "settlement lockup" in text:
        return "settlement_lockup"
    if "net profit not positive" in text:
        return "net_not_positive"
    if "net profit unavailable" in text:
        return "net_unpriced"
    return "other"


def blocked_by_kinds(op: Opportunity) -> set[str]:
    """Distinct mandatory inputs that blocked an opportunity.

    Cost components are read from the structured ledger (``venue:trade_fee``
    → ``trade_fee``), so the category never depends on free text. Block
    reasons that merely repeat a ledger failure are skipped to avoid double
    counting; anything else is classified from its text.
    """
    kinds: set[str] = set()
    for name in op.costs.missing_names():
        kinds.add(name.rsplit(":", 1)[-1])
    ledger_reasons = {c.reason for c in op.costs.unavailable if c.reason}
    for reason in op.block_reasons:
        if reason in ledger_reasons:
            continue
        kinds.add(_text_block_kind(reason))
    return kinds


def edge_class(op: Opportunity) -> str:
    """Gross edge versus fully costed net edge, as one label."""
    if op.gross_edge <= 0:
        return EDGE_NO_GROSS
    if op.net_profit is None:
        return EDGE_UNPRICED
    if op.net_profit <= 0:
        return EDGE_CONSUMED_BY_COSTS
    return EDGE_NET_POSITIVE


def opportunity_observations(
        op: Opportunity,
        books: Mapping[tuple[str, str], OrderBook]) -> list[tuple[str, str]]:
    """Every (metric, label) one evaluated opportunity contributes."""
    out: list[tuple[str, str]] = [
        (OPP_STATUS, op.status.value),
        (OPP_EDGE, edge_class(op)),
    ]
    for kind in sorted(blocked_by_kinds(op)):
        out.append((OPP_BLOCKED_BY, kind))
    if len(op.legs) == 2:
        a = books.get((op.legs[0].venue, op.legs[0].contract.contract_id))
        b = books.get((op.legs[1].venue, op.legs[1].contract.contract_id))
        skew = book_skew_ms(a, b) if a is not None and b is not None else None
        out.append((BOOK_SKEW, skew_bucket(skew)))
    return out


# ---------------------------------------------------------- execution


@dataclass(frozen=True)
class LegSummary:
    venue: str
    contract_id: str
    status: str                  # OrderStatus value, or ERROR / NOT_SENT
    filled: Decimal
    average_price: Optional[Decimal]


@dataclass(frozen=True)
class ExecutionSummary:
    outcome: str
    hedge_state: str
    leg_a: Optional[LegSummary] = None
    leg_b: Optional[LegSummary] = None
    time_between_legs_ms: Optional[float] = None
    unhedged_remaining: Optional[Decimal] = None
    recovery_attempts: int = 0
    recovery_resolution: Optional[str] = None
    risk_categories: tuple[str, ...] = ()
    kill_switch_engaged: bool = False
    notes: tuple[str, ...] = field(default=())

    @property
    def completed_position(self) -> bool:
        return self.outcome in COMPLETED_POSITION_OUTCOMES


def blocked_summary(outcome: str) -> ExecutionSummary:
    """An attempt that never reached the coordinator (no legs submitted)."""
    return ExecutionSummary(outcome=outcome, hedge_state=HEDGE_NOT_SUBMITTED)


_FILLED = (OrderStatus.FILLED.value, OrderStatus.PARTIALLY_FILLED.value)


def _leg_summary(leg) -> LegSummary:
    request = leg.request
    if leg.result is not None:
        status = leg.result.status.value
        filled = leg.result.filled_quantity
        avg = leg.result.average_fill_price
    else:
        status = "ERROR" if leg.error else "NOT_SENT"
        filled, avg = Decimal("0"), None
    return LegSummary(venue=request.venue, contract_id=request.contract_id,
                      status=status, filled=filled, average_price=avg)


def classify_execution(report: "ExecutionReport") -> ExecutionSummary:
    """Reduce a coordinator report to one explicit outcome per attempt.

    The coordinator already decides what happened; this only names it, so
    the counters and the attempt table agree with each other and with the
    simulated fills. Unknown is kept explicit: a missing leg is NOT_SENT,
    never a zero fill.
    """
    risk = tuple(sorted({risk_kind(v) for v in report.risk_violations}))
    kill_now = any(n.startswith("kill switch engaged:") for n in report.notes)
    notes = tuple(n[:300] for n in report.notes)
    if not report.legs:
        outcome = (OUTCOME_RISK_BLOCKED if report.risk_violations
                   else OUTCOME_PRECHECK_REJECTED)
        return ExecutionSummary(outcome=outcome, hedge_state=HEDGE_NOT_SUBMITTED,
                                risk_categories=risk, kill_switch_engaged=kill_now,
                                notes=notes)

    leg_a = _leg_summary(report.legs[0])
    leg_b = _leg_summary(report.legs[1]) if len(report.legs) > 1 else None
    common = dict(leg_a=leg_a, leg_b=leg_b,
                  time_between_legs_ms=report.time_between_legs_ms,
                  risk_categories=risk, kill_switch_engaged=kill_now,
                  notes=notes)
    recovery = report.recovery
    if recovery is not None:
        common["recovery_attempts"] = int(recovery.attempts)
        common["recovery_resolution"] = recovery.resolution

    if report.legs[0].result is None:
        return ExecutionSummary(outcome=OUTCOME_LEG_A_ERROR,
                                hedge_state=HEDGE_NO_FILL, **common)
    if leg_a.status not in _FILLED:
        return ExecutionSummary(outcome=OUTCOME_LEG_A_NOT_FILLED,
                                hedge_state=HEDGE_NO_FILL, **common)

    if report.success:
        if recovery is None:
            return ExecutionSummary(outcome=OUTCOME_COMPLETED,
                                    hedge_state=HEDGE_HEDGED,
                                    unhedged_remaining=Decimal("0"), **common)
        if recovery.open_hedged_quantity > 0:
            return ExecutionSummary(outcome=OUTCOME_RECOVERED,
                                    hedge_state=HEDGE_HEDGED,
                                    unhedged_remaining=Decimal("0"), **common)
        return ExecutionSummary(outcome=OUTCOME_FLATTENED,
                                hedge_state=HEDGE_FLATTENED,
                                unhedged_remaining=Decimal("0"), **common)

    # Not hedged: exposure remains. Distinguish a hedge that never filled from
    # one that partly filled; both are naked until recovery says otherwise.
    filled_b = leg_b.filled if leg_b is not None else Decimal("0")
    if recovery is not None:
        remaining = recovery.remaining_unhedged
    else:
        remaining = max(Decimal("0"), leg_a.filled - filled_b)
    hedge = HEDGE_PARTIAL if filled_b > 0 else HEDGE_FAILED
    return ExecutionSummary(outcome=OUTCOME_UNHEDGED, hedge_state=hedge,
                            unhedged_remaining=remaining, **common)


def attempt_record(opportunity_id: str, when: datetime,
                   summary: ExecutionSummary) -> dict:
    """Column values for one ``execution_attempts`` row. ``mode`` is supplied
    by the repository that owns the row, never by the caller."""
    import json

    def leg_cols(prefix: str, leg: Optional[LegSummary]) -> dict:
        if leg is None:
            return {f"{prefix}_venue": "", f"{prefix}_contract_id": "",
                    f"{prefix}_status": "", f"{prefix}_filled": None,
                    f"{prefix}_avg_price": None}
        return {f"{prefix}_venue": leg.venue,
                f"{prefix}_contract_id": leg.contract_id[:128],
                f"{prefix}_status": leg.status,
                f"{prefix}_filled": str(leg.filled),
                f"{prefix}_avg_price": (str(leg.average_price)
                                        if leg.average_price is not None else None)}

    return {
        "opportunity_id": opportunity_id,
        "timestamp": when,
        "outcome": summary.outcome,
        "hedge_state": summary.hedge_state,
        **leg_cols("leg_a", summary.leg_a),
        **leg_cols("leg_b", summary.leg_b),
        "time_between_legs_ms": summary.time_between_legs_ms,
        "unhedged_remaining": (str(summary.unhedged_remaining)
                               if summary.unhedged_remaining is not None else None),
        "recovery_attempts": summary.recovery_attempts,
        "recovery_resolution": summary.recovery_resolution,
        "risk_violations": json.dumps(list(summary.risk_categories)),
        "kill_switch_engaged": summary.kill_switch_engaged,
        "notes": " | ".join(summary.notes)[:1000],
    }


def tally_execution(tally: PipelineTally, summary: ExecutionSummary) -> None:
    """Counter observations for one attempt, mirroring the attempt record."""
    tally.add(EXEC_ATTEMPT, summary.outcome)
    if summary.leg_a is not None:
        tally.add(EXEC_HEDGE, summary.hedge_state)
        tally.add(EXEC_SUBMISSION, f"A:{summary.leg_a.status}")
    if summary.leg_b is not None:
        tally.add(EXEC_SUBMISSION, f"B:{summary.leg_b.status}")
    if summary.time_between_legs_ms is not None:
        tally.add(EXEC_LEG_GAP, leg_gap_bucket(summary.time_between_legs_ms))
    if summary.recovery_attempts:
        tally.add(EXEC_RECOVERY, n=summary.recovery_attempts)
    for kind in summary.risk_categories:
        tally.add(RISK_BREACH, kind)
    if summary.kill_switch_engaged:
        tally.add(RISK_KILL_ENGAGED)
