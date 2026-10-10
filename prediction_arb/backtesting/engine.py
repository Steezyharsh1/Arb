"""Shared causal historical evaluation: observations -> costs -> simulation (§102).

Missing/insufficient history never becomes executable fill evidence. The CLI
and library use this same evaluator, data assessment and logical-clock model.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from ..core.types import Mode, Opportunity
from ..database.repository import Repository
from ..fees.calculator import FeeCalculator
from ..matching.llm_matcher import LLMMatcher
from ..modes.historical.data_provider import Frame, HistoricalDataProvider
from ..modes.shared import run_arbitrage_scan, run_matching
from ..normalization.settlement import settlement_timing_source
from ..risk.limits import RiskLimits
from .metrics import BacktestResult, summarize_frames
from .policy import ReplayPolicy
from .simulator import HistoricalFillSimulator


@dataclass
class BacktestSummary:
    frames_available: int
    first_frame: Optional[datetime]
    last_frame: Optional[datetime]
    data_quality: dict


class BacktestEngine:
    def __init__(self, repo: Repository, fee_calc: Optional[FeeCalculator] = None,
                 llm: Optional[LLMMatcher] = None, *, settings: Optional[dict] = None,
                 risk_limits: Optional[dict] = None,
                 simulation_config: Optional[dict] = None,
                 bucket_seconds: Optional[int] = None,
                 matching: Optional[Callable] = None):
        self.repo, self.fee_calc = repo, fee_calc
        self.llm = llm if llm is not None else LLMMatcher(client=None)
        self.settings = settings or {}
        self.annual_rate = Decimal(str(self.settings.get("cost_of_capital", {}).get("annual_rate", 0.05)))
        self.risk_limits = risk_limits
        self.policy = ReplayPolicy.from_config(self.settings, simulation_config)
        self.provider = HistoricalDataProvider(repo, bucket_seconds, policy=self.policy)
        self.matching = matching
        self.opportunity_frames: list[tuple[datetime, list[Opportunity]]] = []

    def data_inventory(self, start: Optional[datetime] = None,
                       end: Optional[datetime] = None) -> BacktestSummary:
        frames = self.provider.load_frames(start, end)
        return BacktestSummary(len(frames), frames[0][0] if frames else None,
                               frames[-1][0] if frames else None, self.provider.data_report())

    async def run_backtest(self, start: Optional[datetime] = None,
                           end: Optional[datetime] = None, *,
                           frames: Optional[list[Frame]] = None,
                           opportunity_sink: Optional[Callable[[Opportunity], None]] = None
                           ) -> BacktestResult:
        if self.fee_calc is None:
            raise ValueError("run_backtest requires a FeeCalculator; unknown fees are not zero (§92).")
        if frames is None:
            frames = self.provider.load_frames(start, end)
        limits = RiskLimits.from_config(self.risk_limits) if self.risk_limits is not None else None
        simulator = HistoricalFillSimulator(
            self.provider, self.fee_calc, self.policy,
            annual_rate=self.annual_rate,
            max_total_capital=limits.max_total_capital if limits else None,
            max_capital_per_trade=limits.max_capital_per_trade if limits else None)
        self.opportunity_frames = []
        reconstructed = 0
        for ts, contracts, books in sorted(frames, key=lambda f: f[0]):
            reconstructed += len(contracts)
            approved, _scan = await (self.matching or run_matching)(contracts, self.llm)
            ops = await self._scan(contracts, books, approved, ts)
            for op in ops:
                self.provider.assess_opportunity(op, ts, decision_books=books)
                op.evaluation_metadata["fee_schedule_versions"] = {
                    leg.venue: (self.fee_calc.schedules[leg.venue].version
                                if leg.venue in self.fee_calc.schedules else None)
                    for leg in op.legs}
                if opportunity_sink is not None:
                    opportunity_sink(op)
                simulator.execute(op, ts)
            self.opportunity_frames.append((ts, ops))
        simulation = simulator.report()
        all_ops = [o for _, ops in self.opportunity_frames for o in ops]
        simulation["opportunity_observations_without_execution_data"] = sum(
            o.evaluation_metadata.get("execution_data_supported") is not True for o in all_ops)
        simulation["opportunity_observations_without_complete_costs"] = sum(
            not o.costs_complete for o in all_ops)
        simulation["coverage_note"] = (
            "Simulation covers only model-selected entries. Unsupported, uncosted, capital-blocked "
            "or repeated opportunity observations are not assumed filled and earn no simulated P&L.")
        return summarize_frames(
            self.opportunity_frames,
            max_total_capital=limits.max_total_capital if limits else None,
            contracts_reconstructed=reconstructed,
            contracts_unreconstructable=len(self.provider.under_recorded),
            data_quality=self.provider.data_report(), simulation=simulation,
            calculation_inputs=self._calculation_inputs(frames))

    def _calculation_inputs(self, frames: list[Frame]) -> dict:
        venues = sorted({c.venue for _, cs, _ in frames for c in cs})
        fees, timing = {}, {}
        for venue in venues:
            schedule = self.fee_calc.schedules.get(venue)
            fees[venue] = ({
                "version": schedule.version, "source": schedule.source,
                "effective_date": str(schedule.effective_date) if schedule.effective_date else None,
                "expiration_date": str(schedule.expiration_date) if schedule.expiration_date else None,
                "verified_at": str(schedule.verified_at) if schedule.verified_at else None,
                "trade_calculation_status": schedule.trade_calculation_status,
                "settlement_calculation_status": schedule.settlement_calculation_status,
            } if schedule else None)
            source = settlement_timing_source(venue)
            timing[venue] = ({
                "status": source.status.value, "reviewed_on": source.reviewed_on,
                "sources": list(source.sources), "release_meaning": source.release_meaning,
                "resolution_basis": source.resolution_basis,
                "post_resolution_delay": source.post_resolution_delay,
                "evidence": source.evidence, "gaps": source.gaps,
            } if source else None)
        return {"classification": "SUPPLIED_RESEARCH_COST_INPUTS_NOT_ACTUAL_ACCOUNT_CHARGES",
                "fee_schedules": fees, "settlement_timing": timing,
                "cost_of_capital_annual": str(self.annual_rate)}

    async def _scan(self, contracts, books, approved, evaluation_time) -> list[Opportunity]:
        arb = self.settings.get("arbitrage", {})
        return await run_arbitrage_scan(
            contracts, books, approved, self.fee_calc, Mode.HISTORICAL,
            minimum_net_profit=Decimal(str(arb.get("minimum_net_profit", 2))),
            minimum_net_return=Decimal(str(arb.get("minimum_net_return", 0.005))),
            maximum_settlement_delay_days=Decimal(str(arb.get("maximum_settlement_delay_days", 5))),
            cost_of_capital_annual=Decimal(str(self.settings.get("cost_of_capital", {}).get("annual_rate", 0.05))),
            evaluation_time=evaluation_time, resolve_recorded_sides=True)
