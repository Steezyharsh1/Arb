"""Historical replay engine (§44 MODE 1).

Replays RECORDED order-book snapshots chronologically from the
historical database. Never connects to trading endpoints, never needs
credentials, fully reproducible (deterministic fills, no randomness).
If no recorded data exists for the requested range it says so — it
NEVER falls back to live data (§55) and NEVER fabricates history (§92).
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Optional

from rich.console import Console

from ...core.config import AppConfig
from ...core.logging import get_logger
from ...core.types import Mode, OrderBook, PriceLevel
from ...database.repository import Repository
from ...execution.simulated import HistoricalExecutionEngine
from ...fees.calculator import FeeCalculator
from ...matching.llm_matcher import LLMMatcher
from ..shared import run_arbitrage_scan, run_matching
from .data_provider import HistoricalDataProvider

log = get_logger("modes.historical")


class HistoricalEngine:
    def __init__(self, config: AppConfig, repo: Repository,
                 fee_calc: FeeCalculator, llm: LLMMatcher,
                 console: Console):
        assert config.mode == Mode.HISTORICAL
        self.config = config
        self.repo = repo
        self.fee_calc = fee_calc
        self.llm = llm
        self.console = console
        self.provider = HistoricalDataProvider(repo)

    async def run(self, start: Optional[date] = None,
                  end: Optional[date] = None,
                  single_date: Optional[date] = None) -> dict:
        if single_date is not None:
            start = end = single_date
        start_dt = (datetime.combine(start, time.min, tzinfo=timezone.utc)
                    if start else None)
        end_dt = (datetime.combine(end, time.max, tzinfo=timezone.utc)
                  if end else None)

        frames = self.provider.load_frames(start_dt, end_dt)
        if not frames:
            self.console.print(
                "[yellow]No recorded historical order-book data for the "
                "requested range. Historical order-book data for these "
                "venues is not retroactively available — run "
                "`python main.py --mode collect` to start recording. "
                "This mode will NOT fabricate data or fall back to live "
                "data (§55, §92).[/yellow]")
            return {"frames": 0, "opportunities": 0, "reproducible": True}

        arb = self.config.settings.get("arbitrage", {})
        total_ops = 0
        surviving_fees = 0
        executable = 0
        for ts, contracts, books in frames:
            approved, _mr = await run_matching(contracts, self.llm)
            ops = await run_arbitrage_scan(
                contracts, books, approved, self.fee_calc, Mode.HISTORICAL,
                minimum_net_profit=Decimal(str(arb.get("minimum_net_profit", 2))),
                minimum_net_return=Decimal(str(arb.get("minimum_net_return", 0.005))),
                maximum_settlement_delay_days=Decimal(str(
                    arb.get("maximum_settlement_delay_days", 5))),
                cost_of_capital_annual=Decimal(str(self.config.settings.get(
                    "cost_of_capital", {}).get("annual_rate", 0.05))))
            total_ops += len(ops)
            surviving_fees += sum(1 for o in ops if o.net_profit > 0)
            executable += sum(1 for o in ops if o.status.value == "TRADE")
            for op in ops:
                self.repo.save_opportunity(op)

        summary = {
            "frames": len(frames),
            "opportunities": total_ops,
            "surviving_fees": surviving_fees,
            "executable": executable,
            "reproducible": True,
        }
        self.console.print(f"Historical replay complete: {summary}")
        return summary
