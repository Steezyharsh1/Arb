"""Backtesting engine — V1 STUB with working data-ingestion interface
(§33, §92). Data collection comes first: run `--mode collect` to record
real order books; full backtest metrics are implemented once sufficient
data exists. NO historical data is fabricated."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ..database.repository import Repository
from ..modes.historical.data_provider import HistoricalDataProvider


@dataclass
class BacktestSummary:
    frames_available: int
    first_frame: Optional[datetime]
    last_frame: Optional[datetime]


class BacktestEngine:
    """v1: reports what recorded data exists (ingestion pipeline works);
    the metric battery (drawdown, fill probability, partial-fill rate,
    capital utilization, opportunity duration) is a stub until data
    volume justifies implementation."""

    def __init__(self, repo: Repository):
        self.provider = HistoricalDataProvider(repo)

    def data_inventory(self, start: Optional[datetime] = None,
                       end: Optional[datetime] = None) -> BacktestSummary:
        frames = self.provider.load_frames(start, end)
        return BacktestSummary(
            frames_available=len(frames),
            first_frame=frames[0][0] if frames else None,
            last_frame=frames[-1][0] if frames else None)

    def run_backtest(self, start: Optional[datetime] = None,
                     end: Optional[datetime] = None) -> None:
        """Full backtest with P&L / drawdown / fill-probability metrics.

        NOT IMPLEMENTED IN V1 (spec §3, §33, §92): the historical replay
        engine (modes/historical) already provides opportunity counting
        over recorded data; the full metric battery lands after the data
        collection phase has produced a meaningful dataset."""
        raise NotImplementedError(
            "Full backtesting is a v1 stub (spec §33/§92). Use "
            "`python -m prediction_arb.main --mode historical` for replay over recorded "
            "data, and `--mode collect` to record more data first.")
