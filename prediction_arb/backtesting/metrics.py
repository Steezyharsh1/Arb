"""Observed inputs, calculated edges and simulated trades are distinct.

An opportunity walk is sizing arithmetic, NOT a fill and NOT a probability.
Repeated edge observations are not portfolio returns. There is deliberately
no modelled equity/drawdown curve that credits every TRADE as a full fill.
Unavailable data/costs/P&L stay None, never zero or an optimistic substitute.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Optional

from ..core.types import Opportunity

ZERO = Decimal(0)


def decision_was_trade(op: Opportunity) -> bool:
    """Costed and data-qualified under the replay policy, not fill evidence."""
    return (op.is_executable and op.costs_complete
            and op.evaluation_metadata.get("execution_data_supported") is True)


def costed_trade_candidate(op: Opportunity) -> bool:
    """Strategy thresholds before the separate historical data gates."""
    return op.costs_complete and bool(op.evaluation_metadata.get(
        "costed_trade_before_data_gates", op.is_executable))


def opportunity_key(op: Opportunity) -> tuple[str, ...]:
    """Stable directional identity, including outcome and contract version."""
    return tuple(sorted(f"{l.venue}:{l.contract.contract_id}:"
                        f"{l.contract.contract_version}:{l.side.value}" for l in op.legs))


@dataclass
class DepthStats:
    """CALCULATED sizing-walk coverage, never observed or simulated fills."""
    leg_observations: int = 0
    fully_covered: int = 0
    partially_covered: int = 0
    no_coverage: int = 0
    full_coverage_rate: Optional[float] = None
    partial_coverage_rate: Optional[float] = None
    mean_coverage_ratio: Optional[float] = None


@dataclass
class DurationStats:
    """Sampled first/last spans; no claim of continuous persistence."""
    distinct_opportunities: int = 0
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
    gross_cost: Decimal = ZERO
    total_fees: Decimal = ZERO
    uncosted_opportunities: int = 0
    drag_ratio: Optional[float] = None


@dataclass
class TheoreticalNetSeries:
    """A sum of costed edge observations, NOT equity, returns or realized P&L."""
    points: list[tuple[datetime, Decimal]] = field(default_factory=list)
    net_edge_sum_known_subset: Optional[Decimal] = None
    costed_observations: int = 0
    uncosted_observations: int = 0


@dataclass
class BacktestResult:
    frames_available: int = 0
    first_frame: Optional[datetime] = None
    last_frame: Optional[datetime] = None
    # Retained Python attributes for the library's observation counters.
    # Their report definitions explicitly say CALCULATED, not exchange fills.
    opportunities_observed: int = 0
    executable_observed: int = 0
    gross_theoretical_edge_sum: Decimal = ZERO
    costed_opportunities: int = 0
    costed_positive_opportunities: int = 0
    costed_trade_candidates: int = 0
    data_unsupported_opportunities: int = 0
    depth: DepthStats = field(default_factory=DepthStats)
    durations: DurationStats = field(default_factory=DurationStats)
    capital: CapitalStats = field(default_factory=CapitalStats)
    fee_drag: FeeDrag = field(default_factory=FeeDrag)
    theoretical_net: TheoreticalNetSeries = field(default_factory=TheoreticalNetSeries)
    contracts_reconstructed: int = 0
    contracts_unreconstructable: int = 0
    data_quality: dict = field(default_factory=dict)
    calculation_inputs: dict = field(default_factory=dict)
    blocked_reason_counts: dict = field(default_factory=dict)
    unavailable_cost_counts: dict = field(default_factory=dict)
    simulation: dict = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        def d(value):
            return str(value) if value is not None else None
        return {
            "report_schema_version": 2,
            "frames_available": self.frames_available,
            "first_frame": self.first_frame.isoformat() if self.first_frame else None,
            "last_frame": self.last_frame.isoformat() if self.last_frame else None,
            "contracts_reconstructed": self.contracts_reconstructed,
            "contracts_unreconstructable": self.contracts_unreconstructable,
            "contract_counter_definitions": {
                "contracts_reconstructed": "as-of contract references across decision frames, NOT unique contracts",
                "contracts_unreconstructable": "snapshot references lacking reconstructable contract context",
            },
            "observed_data": self.data_quality,
            "calculation_inputs": self.calculation_inputs,
            "calculated_opportunities": {
                "classification": "CALCULATED_FROM_RECORDED_QUOTES_NOT_FILLS",
                "opportunity_observations": self.opportunities_observed,
                "gross_theoretical_edge_sum": d(self.gross_theoretical_edge_sum),
                "costed_opportunities": self.costed_opportunities,
                "opportunities_with_unavailable_costs": self.opportunities_observed - self.costed_opportunities,
                "costed_positive_opportunities": self.costed_positive_opportunities,
                "costed_trade_candidates_before_data_gates": self.costed_trade_candidates,
                "opportunities_without_supported_execution_data": self.data_unsupported_opportunities,
                "executable_under_replay_policy": self.executable_observed,
                "block_reason_counts": self.blocked_reason_counts,
                "unavailable_cost_components": self.unavailable_cost_counts,
                "meaning": "Executable means cost/decision/temporal gates pass under the stated "
                           "research assumptions, NOT that either leg filled or could be guaranteed live.",
            },
            "theoretical_depth_coverage": {
                "classification": "CALCULATED_SIZING_WALKS_NOT_FILL_PROBABILITY",
                "leg_observations": self.depth.leg_observations,
                "fully_covered": self.depth.fully_covered,
                "partially_covered": self.depth.partially_covered,
                "no_coverage": self.depth.no_coverage,
                "full_coverage_rate": self.depth.full_coverage_rate,
                "partial_coverage_rate": self.depth.partial_coverage_rate,
                "mean_coverage_ratio": self.depth.mean_coverage_ratio,
            },
            "sampled_observation_spans": {
                "classification": "CALCULATED_SAMPLE_SPANS_NOT_CONTINUOUS_LIFETIMES",
                "distinct_opportunities": self.durations.distinct_opportunities,
                "single_frame_only": self.durations.single_frame_only,
                "min_seconds": self.durations.min_seconds,
                "median_seconds": self.durations.median_seconds,
                "max_seconds": self.durations.max_seconds,
            },
            "capital": {
                "classification": "CALCULATED_PER_FRAME_DEMAND_NOT_CAPITAL_ACTUALLY_DEPLOYED",
                "peak_capital_required": d(self.capital.peak_capital_required),
                "max_total_capital": d(self.capital.max_total_capital),
                "peak_utilisation": self.capital.peak_utilisation,
            },
            "fee_drag": {
                "classification": "CALCULATED_OPPORTUNITY_FEES_NOT_EXECUTION_FEES",
                "gross_cost": d(self.fee_drag.gross_cost),
                "total_fees_known_subset": d(self.fee_drag.total_fees),
                "uncosted_opportunities": self.fee_drag.uncosted_opportunities,
                "drag_ratio": self.fee_drag.drag_ratio,
            },
            "theoretical_net_edge_observations": {
                "classification": "THEORETICAL_COSTED_EDGE_SUM_NOT_EQUITY_OR_TRADING_PNL",
                "net_edge_sum_known_subset": d(self.theoretical_net.net_edge_sum_known_subset),
                "costed_observations": self.theoretical_net.costed_observations,
                "uncosted_observations": self.theoretical_net.uncosted_observations,
                "points": [[ts.isoformat(), d(value)] for ts, value in self.theoretical_net.points],
                "meaning": "Repeated/overlapping observations may count the same quotes many times. "
                           "No fill, simultaneous execution, deployed capital or payout is implied; "
                           "this is not a portfolio return or a performance bound.",
            },
            "simulation": self.simulation,
            "assumptions": self.assumptions,
        }


def compute_depth_stats(opportunities: list[Opportunity]) -> DepthStats:
    stats = DepthStats()
    ratios: list[float] = []
    for op in opportunities:
        for leg in op.legs:
            walk = leg.walk
            if walk is None:
                continue
            stats.leg_observations += 1
            filled, requested = walk.filled_quantity, walk.requested_quantity
            if filled <= 0:
                stats.no_coverage += 1
                ratios.append(0.0)
            elif filled >= requested:
                stats.fully_covered += 1
                ratios.append(1.0)
            else:
                stats.partially_covered += 1
                ratios.append(float(filled / requested) if requested > 0 else 0.0)
    total = stats.leg_observations
    if total:
        stats.full_coverage_rate = stats.fully_covered / total
        stats.partial_coverage_rate = stats.partially_covered / total
        stats.mean_coverage_ratio = sum(ratios) / total
    return stats


def compute_duration_stats(frames: list[tuple[datetime, list[Opportunity]]]) -> DurationStats:
    seen: dict[tuple[str, ...], set[datetime]] = {}
    for ts, ops in frames:
        for op in ops:
            seen.setdefault(opportunity_key(op), set()).add(ts)
    stats = DurationStats(distinct_opportunities=len(seen))
    spans = []
    for timestamps in seen.values():
        if len(timestamps) == 1:
            stats.single_frame_only += 1
        spans.append((max(timestamps) - min(timestamps)).total_seconds())
    if spans:
        stats.min_seconds, stats.median_seconds, stats.max_seconds = min(spans), median(spans), max(spans)
    return stats


def compute_fee_drag(opportunities: list[Opportunity]) -> FeeDrag:
    drag = FeeDrag()
    for op in opportunities:
        drag.gross_cost += op.gross_cost
        if op.total_fees is None:
            drag.uncosted_opportunities += 1
        else:
            drag.total_fees += op.total_fees
    if drag.gross_cost > 0 and drag.uncosted_opportunities == 0:
        drag.drag_ratio = float(drag.total_fees / drag.gross_cost)
    return drag


def compute_capital_stats(frames: list[tuple[datetime, list[Opportunity]]],
                          max_total_capital: Optional[Decimal]) -> CapitalStats:
    stats = CapitalStats(max_total_capital=max_total_capital)
    for _, ops in frames:
        needed = sum((o.capital_required for o in ops if costed_trade_candidate(o)
                      and o.capital_required is not None), ZERO)
        if stats.peak_capital_required is None or needed > stats.peak_capital_required:
            stats.peak_capital_required = needed
    if stats.peak_capital_required is not None and max_total_capital and max_total_capital > 0:
        stats.peak_utilisation = float(stats.peak_capital_required / max_total_capital)
    return stats


def compute_theoretical_net_series(frames: list[tuple[datetime, list[Opportunity]]]
                                   ) -> TheoreticalNetSeries:
    series = TheoreticalNetSeries()
    total = ZERO
    for ts, ops in sorted(frames, key=lambda f: f[0]):
        for op in ops:
            if not op.costs_complete:
                series.uncosted_observations += 1
                continue
            # Include calculated negative edges, too. This is an edge
            # observation series, not a winner-only equity curve.
            total += op.net_profit
            series.costed_observations += 1
            series.points.append((ts, total))
    if series.costed_observations:
        series.net_edge_sum_known_subset = total
    return series


def summarize_frames(frames: list[tuple[datetime, list[Opportunity]]], *,
                     max_total_capital: Optional[Decimal] = None,
                     contracts_reconstructed: int = 0,
                     contracts_unreconstructable: int = 0,
                     data_quality: Optional[dict] = None,
                     calculation_inputs: Optional[dict] = None,
                     simulation: Optional[dict] = None) -> BacktestResult:
    frames = sorted(frames, key=lambda f: f[0])
    ops = [o for _, observations in frames for o in observations]
    return BacktestResult(
        frames_available=len(frames), first_frame=frames[0][0] if frames else None,
        last_frame=frames[-1][0] if frames else None,
        opportunities_observed=len(ops), executable_observed=sum(decision_was_trade(o) for o in ops),
        gross_theoretical_edge_sum=sum((o.gross_edge for o in ops), ZERO),
        costed_opportunities=sum(o.costs_complete for o in ops),
        costed_positive_opportunities=sum(o.costs_complete and o.profitable_after_all_costs for o in ops),
        costed_trade_candidates=sum(costed_trade_candidate(o) for o in ops),
        data_unsupported_opportunities=sum(o.evaluation_metadata.get("execution_data_supported") is not True
                                           for o in ops),
        depth=compute_depth_stats(ops), durations=compute_duration_stats(frames),
        fee_drag=compute_fee_drag(ops), capital=compute_capital_stats(frames, max_total_capital),
        theoretical_net=compute_theoretical_net_series(frames),
        contracts_reconstructed=contracts_reconstructed,
        contracts_unreconstructable=contracts_unreconstructable,
        data_quality=data_quality or {},
        calculation_inputs=calculation_inputs or {},
        blocked_reason_counts=dict(sorted(Counter(r for o in ops for r in o.block_reasons).items())),
        unavailable_cost_counts=dict(sorted(Counter(n for o in ops for n in o.costs.missing_names()).items())),
        simulation=simulation or {"classification": "NOT_SIMULATED", "net_simulated_pnl": None,
                                  "reason": "No dated execution simulation was supplied"},
        assumptions=[
            "OBSERVED: recorded depth and timestamps only. CALCULATED: gross edges, costs, sizing "
            "coverage and sampled spans. ASSUMED/SIMULATED: only the separately labelled simulation.",
            "The theoretical edge sum is not MODELLED equity: repeated quotes are not trades, and "
            "unsupported observations do not contribute any simulated profitability.",
            "Legacy quote-time availability, unidentified ladder outcomes and latest-only contract "
            "metadata cannot establish common-time executable books. Their calculations are THEORY ONLY.",
            "Fee calculations use the supplied schedule only where its effective dates cover the "
            "decision/arrival time. This is not reconstruction of an entire historical fee/account ledger.",
            "Matching is a retrospective re-evaluation of recorded terms. Determinism requires identical "
            "inputs, policy, fee/timing versions and match verdicts; an external LLM is not deterministic evidence.",
            "No historical or simulated result guarantees real fills, settlement or profit.",
        ])


__all__ = ["BacktestResult", "CapitalStats", "DepthStats", "DurationStats", "FeeDrag",
           "TheoreticalNetSeries", "compute_capital_stats", "compute_depth_stats",
           "compute_duration_stats", "compute_fee_drag", "compute_theoretical_net_series",
           "costed_trade_candidate", "decision_was_trade", "opportunity_key", "summarize_frames"]
