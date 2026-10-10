"""Backtesting engine (§33, §92).

Runs the same strategy pipeline that paper and live run — matching, then
the arbitrage scan — over RECORDED order books, and reports the metric
battery from ``backtesting/metrics.py``.

Two honesty rules shape this module:

  * No fabricated history. If nothing was recorded for the requested
    range the result is an empty report, never synthetic data (§92).
  * Measured vs modelled stays separated. Fill statistics and durations
    are read straight out of the recording. The equity curve assumes
    every executable opportunity filled, which is an upper bound, and the
    result carries that assumption in plain text.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..core.types import Mode, Opportunity
from ..database.repository import Repository
from ..fees.calculator import FeeCalculator
from ..matching.llm_matcher import LLMMatcher
from ..modes.historical.data_provider import HistoricalDataProvider
from ..modes.shared import run_arbitrage_scan, run_matching
from ..risk.limits import RiskLimits
from .metrics import BacktestResult, summarize_frames


@dataclass
class BacktestSummary:
    frames_available: int
    first_frame: Optional[datetime]
    last_frame: Optional[datetime]


class BacktestEngine:
    """Replays recorded frames through the real pipeline and measures them."""

    def __init__(self, repo: Repository,
                 fee_calc: Optional[FeeCalculator] = None,
                 llm: Optional[LLMMatcher] = None,
                 *,
                 settings: Optional[dict] = None,
                 risk_limits: Optional[dict] = None,
                 bucket_seconds: int = 60):
        self.repo = repo
        self.fee_calc = fee_calc
        self.llm = llm if llm is not None else LLMMatcher(client=None)
        self.settings = settings or {}
        self.risk_limits = risk_limits
        self.provider = HistoricalDataProvider(repo, bucket_seconds)

    def data_inventory(self, start: Optional[datetime] = None,
                       end: Optional[datetime] = None) -> BacktestSummary:
        frames = self.provider.load_frames(start, end)
        return BacktestSummary(
            frames_available=len(frames),
            first_frame=frames[0][0] if frames else None,
            last_frame=frames[-1][0] if frames else None)

    async def run_backtest(self, start: Optional[datetime] = None,
                           end: Optional[datetime] = None
                           ) -> BacktestResult:
        """Full backtest over recorded data.

        Requires a fee calculator: cost is what turns a price gap into a
        trade or a block, so a backtest without real fee schedules would
        report profit that the venues would never have paid (§92 — no
        fabricated results).
        """
        if self.fee_calc is None:
            raise ValueError(
                "run_backtest requires a FeeCalculator built from "
                "config/fees.yaml. Refusing to report profit computed "
                "without real fee schedules (§92).")

        frames = self.provider.load_frames(start, end)
        per_frame: list[tuple[datetime, list[Opportunity]]] = []
        reconstructed = 0
        for ts, contracts, books in frames:
            reconstructed += len(contracts)
            approved, _scan = await run_matching(contracts, self.llm)
            ops = await self._scan(contracts, books, approved, ts)
            per_frame.append((ts, ops))

        max_capital = (RiskLimits.from_config(
            self.risk_limits).max_total_capital
            if self.risk_limits is not None else None)
        return summarize_frames(
            per_frame,
            max_total_capital=max_capital,
            contracts_reconstructed=reconstructed,
            contracts_unreconstructable=len(self.provider.under_recorded))

    async def _scan(self, contracts, books, approved,
                    evaluation_time) -> list[Opportunity]:
        arb = self.settings.get("arbitrage", {})
        return await run_arbitrage_scan(
            contracts, books, approved, self.fee_calc, Mode.HISTORICAL,
            minimum_net_profit=Decimal(str(arb.get("minimum_net_profit", 2))),
            minimum_net_return=Decimal(str(arb.get("minimum_net_return",
                                                  0.005))),
            maximum_settlement_delay_days=Decimal(str(
                arb.get("maximum_settlement_delay_days", 5))),
            cost_of_capital_annual=Decimal(str(self.settings.get(
                "cost_of_capital", {}).get("annual_rate", 0.05))),
            # Lockup from the recorded frame time, not the wall clock (D18).
            evaluation_time=evaluation_time)
