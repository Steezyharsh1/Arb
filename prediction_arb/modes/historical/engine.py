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

from ...backtesting.metrics import decision_was_trade, summarize_frames
from ...core.config import AppConfig
from ...core.logging import get_logger
from ...core.types import Mode
from ...database.repository import Repository
from ...fees.calculator import FeeCalculator
from ...matching.llm_matcher import LLMMatcher
from ...monitoring.formatting import UNAVAILABLE_TEXT
from ..shared import run_arbitrage_scan, run_matching
from .data_provider import HistoricalDataProvider

log = get_logger("modes.historical")


def _fmt(value) -> str:
    """Render a metric for display: `n/a` when it was not computable.

    An unavailable metric must never print as a bare ``None`` (which reads
    like a bug) or as ``0`` (which reads like a real measurement of
    nothing).
    """
    return UNAVAILABLE_TEXT if value is None else str(value)


class HistoricalEngine:
    """Replays the RECORDED store; writes only to its own database.

    Two databases, on purpose (D3):
      * ``recorded`` — read-only handle on what collect/paper captured.
        Replay reads contracts and order books from here.
      * ``repo`` — this mode's own writable database, where replay output
        (opportunities) lands.

    Keeping them apart means replay can never contaminate the evidence it
    is replaying, and the recorded store can never be mutated by an
    analysis run (§49).
    """

    def __init__(self, config: AppConfig, repo: Repository,
                 fee_calc: FeeCalculator, llm: LLMMatcher,
                 console: Console,
                 recorded: Optional[Repository] = None):
        assert config.mode == Mode.HISTORICAL
        self.config = config
        self.repo = repo
        self.recorded = recorded
        self.fee_calc = fee_calc
        self.llm = llm
        self.console = console
        # No recorded store at all is not an error — it is the honest
        # empty state, and the run below reports it as such.
        self.provider = HistoricalDataProvider(
            recorded if recorded is not None else repo)

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
                "`python -m prediction_arb.main --mode collect` to start recording. "
                "This mode will NOT fabricate data or fall back to live "
                "data (§55, §92).[/yellow]")
            return {"frames": 0, "opportunities": 0, "reproducible": True,
                    "contracts_reconstructed": 0,
                    "contracts_unreconstructable": 0}

        # Contracts we could NOT faithfully rebuild are reported, not
        # silently dropped and not back-filled with guesses (D4).
        under = self.provider.under_recorded
        if under:
            reasons: dict[str, int] = {}
            for _v, _c, reason in under:
                reasons[reason] = reasons.get(reason, 0) + 1
            self.console.print(
                f"[yellow]{len(under)} snapshot(s) had no reconstructable "
                f"contract and were excluded from matching: {reasons}. "
                f"Nothing was invented in their place (§92).[/yellow]")

        arb = self.config.settings.get("arbitrage", {})
        total_ops = 0
        surviving_fees = 0
        executable = 0
        unpriced = 0
        per_frame: list[tuple[datetime, list]] = []
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
            # "Survived fees" means a VERIFIED positive net profit. An
            # opportunity whose mandatory costs could not be computed has no
            # net profit at all, so it is counted separately as unpriced
            # rather than being allowed to look profitable (D17).
            surviving_fees += sum(1 for o in ops
                                  if o.profitable_after_all_costs)
            unpriced += sum(1 for o in ops if o.net_profit is None)
            executable += sum(1 for o in ops if decision_was_trade(o))
            per_frame.append((ts, ops))
            for op in ops:
                self.repo.save_opportunity(op)

        # The metric battery runs over the opportunities the replay already
        # produced — it does not re-scan, so the numbers describe exactly
        # the run that was persisted above.
        risk = self.config.settings.get("risk", {})
        max_capital = risk.get("max_total_capital")
        metrics = summarize_frames(
            per_frame,
            max_total_capital=(Decimal(str(max_capital))
                               if max_capital is not None else None),
            contracts_reconstructed=sum(len(c) for _, c, _ in frames),
            contracts_unreconstructable=len(under))

        summary = {
            "frames": len(frames),
            "opportunities": total_ops,
            "surviving_fees": surviving_fees,
            #: Opportunities with no verified net profit because a mandatory
            #: cost was unavailable. They are reported, never counted as
            #: profitable and never given a net-profit figure.
            "net_profit_unavailable": unpriced,
            "executable": executable,
            "reproducible": True,
            "contracts_reconstructed": sum(len(c) for _, c, _ in frames),
            "contracts_unreconstructable": len(under),
            "recorded_store": (str(self.recorded.db_path)
                               if self.recorded is not None else None),
            "metrics": metrics.to_dict(),
        }
        self.console.print(
            f"Historical replay complete: {summary['frames']} frames, "
            f"{total_ops} opportunities ({executable} executable), "
            f"{summary['contracts_reconstructed']} contracts reconstructed"
            + (f", {len(under)} unreconstructable" if under else ""))
        if unpriced:
            self.console.print(
                f"[yellow]{unpriced} opportunity(ies) have no verified net "
                f"profit — a mandatory cost was unavailable, so their net "
                f"profit is {UNAVAILABLE_TEXT} and they are not counted as "
                f"profitable or executable.[/yellow]")
        # `n/a` rather than a bare `None`: an unavailable metric must read
        # as unavailable, never as zero or as a value.
        dur = metrics.durations.median_seconds
        self.console.print(
            f"Opportunity duration median: "
            f"{UNAVAILABLE_TEXT if dur is None else f'{dur}s'} | fill "
            f"probability: {_fmt(metrics.fills.fill_probability_at_touch)} | "
            f"fee drag: {_fmt(metrics.fee_drag.drag_ratio)}"
            + (f" ({metrics.fee_drag.uncosted_opportunities} uncosted, "
               f"ratio withheld)"
               if metrics.fee_drag.uncosted_opportunities else ""))
        self.console.print(
            f"[dim]Modelled equity (upper bound — assumes every executable "
            f"opportunity filled): final {metrics.equity.final_equity}, "
            f"max drawdown {metrics.equity.max_drawdown}[/dim]")
        return summary
