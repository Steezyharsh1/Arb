"""Backtest metrics over recorded order books (§33).

Everything here is computed from recorded frames. Nothing is simulated,
extrapolated, or filled in. The split below is the honest one and the
code keeps it visible:

  * ``measured`` — directly observable in the recorded data: how long an
    opportunity persisted across frames, how much of each order the book
    could actually have filled, fee drag, capital required.
  * ``modelled`` — an assumption, stated in ``assumptions``: the equity
    curve and drawdown assume every executable opportunity filled at the
    recorded prices. Real fills are strictly worse than this (queue
    position, latency, adverse selection), so these numbers are an upper
    bound, never a forecast.

If a metric cannot be computed from the data present, it is ``None`` —
not zero, not an estimate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Optional

from ..core.types import Opportunity

# An opportunity is "the same opportunity" across frames when it is the
# same cross-venue leg pair. This is the identity `run_matching` already
# uses for approved pairs, so replay tracking and matching agree.
# (The per-call `opportunity_id` is a fresh uuid4 every frame and is
# therefore useless as a cross-frame identity — defect noted in ROADMAP.)


def decision_was_trade(op: Opportunity) -> bool:
    """The strategy's own verdict was TRADE *and* its net profit is a
    verified number rather than an unavailable one.

    Deliberately not :attr:`Opportunity.is_executable`, which also requires
    a positive net profit: the equity curve and capital statistics must
    include losing trades. Dropping an opportunity because its verified net
    profit turned out negative would flatter the curve and understate the
    capital the strategy would have locked up — the exact optimism this
    module exists to avoid. An unavailable net profit is excluded because it
    is not a number, never because it is small.
    """
    return op.status.value == "TRADE" and op.net_profit is not None


def opportunity_key(op: Opportunity) -> tuple[str, ...]:
    """Stable cross-frame identity: the sorted leg pair."""
    return tuple(sorted(
        f"{leg.venue}:{leg.contract.contract_id}" for leg in op.legs))


@dataclass
class FillStats:
    """Measured from recorded books: could the order actually have filled?"""
    leg_observations: int = 0
    fully_filled: int = 0
    partially_filled: int = 0
    unfilled: int = 0
    #: Share of leg observations the recorded book could fill completely.
    fill_probability_at_touch: Optional[float] = None
    #: Share that would have filled only partly (0 < filled < requested).
    partial_fill_rate: Optional[float] = None
    #: Mean filled/requested across observations.
    mean_fill_ratio: Optional[float] = None


@dataclass
class DurationStats:
    """How long each distinct opportunity persisted across frames."""
    distinct_opportunities: int = 0
    #: Seen in exactly one frame — too brief to act on.
    single_frame_only: int = 0
    min_seconds: Optional[float] = None
    median_seconds: Optional[float] = None
    max_seconds: Optional[float] = None


@dataclass
class CapitalStats:
    peak_capital_required: Optional[Decimal] = None
    max_total_capital: Optional[Decimal] = None
    peak_utilisation: Optional[float] = None


@dataclass
class FeeDrag:
    gross_cost: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    #: Opportunities whose fees could not be computed. Their fees are
    #: excluded from ``total_fees``, and they make ``drag_ratio``
    #: unreportable: a ratio over a partial set would understate real drag.
    uncosted_opportunities: int = 0
    #: total_fees / gross_cost. None when nothing was costed, or when any
    #: opportunity's fees were unavailable.
    drag_ratio: Optional[float] = None


@dataclass
class EquityCurve:
    """MODELLED — assumes every executable opportunity filled."""
    points: list[tuple[datetime, Decimal]] = field(default_factory=list)
    final_equity: Decimal = Decimal("0")
    peak_equity: Decimal = Decimal("0")
    max_drawdown: Decimal = Decimal("0")
    max_drawdown_pct: Optional[float] = None


@dataclass
class BacktestResult:
    frames_available: int = 0
    first_frame: Optional[datetime] = None
    last_frame: Optional[datetime] = None
    opportunities_observed: int = 0
    executable_observed: int = 0
    fills: FillStats = field(default_factory=FillStats)
    durations: DurationStats = field(default_factory=DurationStats)
    capital: CapitalStats = field(default_factory=CapitalStats)
    fee_drag: FeeDrag = field(default_factory=FeeDrag)
    #: MODELLED, not measured.
    equity: EquityCurve = field(default_factory=EquityCurve)
    contracts_reconstructed: int = 0
    contracts_unreconstructable: int = 0
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """JSON-safe view for reports. Decimals become strings so no
        precision is lost and nothing is silently float-rounded."""
        def d(x):
            return None if x is None else str(x)
        return {
            "frames_available": self.frames_available,
            "first_frame": self.first_frame.isoformat()
            if self.first_frame else None,
            "last_frame": self.last_frame.isoformat()
            if self.last_frame else None,
            "opportunities_observed": self.opportunities_observed,
            "executable_observed": self.executable_observed,
            "contracts_reconstructed": self.contracts_reconstructed,
            "contracts_unreconstructable": self.contracts_unreconstructable,
            "fills": {
                "leg_observations": self.fills.leg_observations,
                "fully_filled": self.fills.fully_filled,
                "partially_filled": self.fills.partially_filled,
                "unfilled": self.fills.unfilled,
                "fill_probability_at_touch":
                    self.fills.fill_probability_at_touch,
                "partial_fill_rate": self.fills.partial_fill_rate,
                "mean_fill_ratio": self.fills.mean_fill_ratio,
            },
            "durations": {
                "distinct_opportunities":
                    self.durations.distinct_opportunities,
                "single_frame_only": self.durations.single_frame_only,
                "min_seconds": self.durations.min_seconds,
                "median_seconds": self.durations.median_seconds,
                "max_seconds": self.durations.max_seconds,
            },
            "capital": {
                "peak_capital_required": d(self.capital.peak_capital_required),
                "max_total_capital": d(self.capital.max_total_capital),
                "peak_utilisation": self.capital.peak_utilisation,
            },
            "fee_drag": {
                "gross_cost": d(self.fee_drag.gross_cost),
                "total_fees": d(self.fee_drag.total_fees),
                "uncosted_opportunities":
                    self.fee_drag.uncosted_opportunities,
                "drag_ratio": self.fee_drag.drag_ratio,
            },
            "modelled_equity": {
                "final_equity": d(self.equity.final_equity),
                "peak_equity": d(self.equity.peak_equity),
                "max_drawdown": d(self.equity.max_drawdown),
                "max_drawdown_pct": self.equity.max_drawdown_pct,
                "points": [
                    [ts.isoformat(), d(eq)] for ts, eq in self.equity.points],
            },
            "assumptions": self.assumptions,
        }


def _ratio(numerator: float, denominator: int) -> Optional[float]:
    return None if denominator <= 0 else numerator / denominator


def compute_fill_stats(opportunities: list[Opportunity]) -> FillStats:
    """Measured: how much of each leg the recorded book could have filled."""
    stats = FillStats()
    ratios: list[float] = []
    for op in opportunities:
        for leg in op.legs:
            walk = leg.walk
            if walk is None:
                continue
            stats.leg_observations += 1
            filled, requested = walk.filled_quantity, walk.requested_quantity
            if filled <= 0:
                stats.unfilled += 1
                ratios.append(0.0)
            elif filled >= requested:
                stats.fully_filled += 1
                ratios.append(1.0)
            else:
                stats.partially_filled += 1
                ratios.append(float(filled / requested)
                              if requested > 0 else 0.0)
    total = stats.leg_observations
    stats.fill_probability_at_touch = _ratio(stats.fully_filled, total)
    stats.partial_fill_rate = _ratio(stats.partially_filled, total)
    if ratios:
        stats.mean_fill_ratio = sum(ratios) / len(ratios)
    return stats


def compute_duration_stats(
        frames: list[tuple[datetime, list[Opportunity]]]) -> DurationStats:
    """Measured: persistence of each distinct opportunity across frames."""
    seen: dict[tuple[str, ...], list[datetime]] = {}
    for ts, ops in frames:
        for op in ops:
            seen.setdefault(opportunity_key(op), []).append(ts)
    stats = DurationStats(distinct_opportunities=len(seen))
    durations: list[float] = []
    for timestamps in seen.values():
        if len(timestamps) < 2:
            stats.single_frame_only += 1
            durations.append(0.0)
            continue
        durations.append(
            (max(timestamps) - min(timestamps)).total_seconds())
    if durations:
        stats.min_seconds = min(durations)
        stats.max_seconds = max(durations)
        stats.median_seconds = median(durations)
    return stats


def compute_fee_drag(opportunities: list[Opportunity]) -> FeeDrag:
    """Measured fee drag over opportunities whose fees are known.

    An opportunity whose fees could not be computed contributes no fees and
    is counted in ``uncosted_opportunities``. Its fees are not treated as
    zero — the ratio is withheld instead, because averaging over a subset
    would report a smaller drag than the strategy would really pay.
    """
    drag = FeeDrag()
    for op in opportunities:
        drag.gross_cost += op.gross_cost
        if op.total_fees is None:
            drag.uncosted_opportunities += 1
            continue
        drag.total_fees += op.total_fees
    if drag.gross_cost > 0 and drag.uncosted_opportunities == 0:
        drag.drag_ratio = float(drag.total_fees / drag.gross_cost)
    return drag


def compute_capital_stats(
        frames: list[tuple[datetime, list[Opportunity]]],
        max_total_capital: Optional[Decimal]) -> CapitalStats:
    """Measured: peak capital the executable set would have needed."""
    stats = CapitalStats(max_total_capital=max_total_capital)
    for _ts, ops in frames:
        # Only opportunities the strategy decided to trade lock capital.
        # `capital_required` is None when fees are unknown, which a TRADE
        # cannot be; the guard keeps a defect from reading as $0 of locked
        # capital instead of raising.
        needed = sum((op.capital_required for op in ops
                      if decision_was_trade(op)
                      and op.capital_required is not None),
                     Decimal("0"))
        if stats.peak_capital_required is None or needed > stats.peak_capital_required:
            stats.peak_capital_required = needed
    if (stats.peak_capital_required is not None and max_total_capital
            and max_total_capital > 0):
        stats.peak_utilisation = float(
            stats.peak_capital_required / max_total_capital)
    return stats


def compute_equity_curve(
        frames: list[tuple[datetime, list[Opportunity]]]) -> EquityCurve:
    """MODELLED equity curve: every executable opportunity fills at the
    recorded prices, in full, with no latency and no queue. Real fills are
    strictly worse, so this is an upper bound on performance."""
    curve = EquityCurve()
    equity = Decimal("0")
    peak = Decimal("0")
    for ts, ops in frames:
        for op in ops:
            # Only opportunities the strategy decided to trade enter the
            # curve, and only with a VERIFIED net profit — losses included,
            # because a curve of winners only is not a curve. An unavailable
            # net profit is skipped, never counted as zero and never
            # replaced by the gross edge.
            if not decision_was_trade(op) or op.net_profit is None:
                continue
            equity += op.net_profit
            curve.points.append((ts, equity))
            if equity > peak:
                peak = equity
            drawdown = peak - equity
            if drawdown > curve.max_drawdown:
                curve.max_drawdown = drawdown
                curve.max_drawdown_pct = (
                    float(drawdown / peak) if peak > 0 else None)
    curve.final_equity = equity
    curve.peak_equity = peak
    return curve


def summarize_frames(
    frames: list[tuple[datetime, list[Opportunity]]],
    *,
    max_total_capital: Optional[Decimal] = None,
    contracts_reconstructed: int = 0,
    contracts_unreconstructable: int = 0,
) -> BacktestResult:
    """Full metric battery over per-frame opportunity lists."""
    all_ops = [op for _ts, ops in frames for op in ops]
    result = BacktestResult(
        frames_available=len(frames),
        first_frame=frames[0][0] if frames else None,
        last_frame=frames[-1][0] if frames else None,
        opportunities_observed=len(all_ops),
        executable_observed=sum(1 for o in all_ops
                                if decision_was_trade(o)),
        fills=compute_fill_stats(all_ops),
        durations=compute_duration_stats(frames),
        fee_drag=compute_fee_drag(all_ops),
        capital=compute_capital_stats(frames, max_total_capital),
        equity=compute_equity_curve(frames),
        contracts_reconstructed=contracts_reconstructed,
        contracts_unreconstructable=contracts_unreconstructable,
        assumptions=[
            "MODELLED, not measured: the equity curve and drawdown assume "
            "every executable opportunity filled in full at the recorded "
            "prices. Treat them as an upper bound, not a forecast.",
            "No latency, queue position, or adverse selection is modelled; "
            "real fills are strictly worse than these figures.",
            "Fill statistics come from recorded book depth only. They say "
            "what the book could have filled, not what it did fill.",
            "Contracts that could not be reconstructed from the recorded "
            "store are counted and excluded, never substituted (§92).",
        ])
    if result.fills.leg_observations == 0:
        result.assumptions.append(
            "No leg walk data was recorded, so fill statistics are null "
            "rather than assumed perfect.")
    return result


__all__ = [
    "BacktestResult", "CapitalStats", "DurationStats", "EquityCurve",
    "FeeDrag", "FillStats", "compute_capital_stats",
    "compute_duration_stats", "compute_equity_curve", "compute_fee_drag",
    "compute_fill_stats", "opportunity_key", "summarize_frames",
]
