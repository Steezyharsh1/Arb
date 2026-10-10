"""Read-only historical evaluation (§44, §55, §92, §102).

Calculated opportunities are not executions. Only data-qualified entries
reach the shared dated simulator, and all its assumptions/results are labelled.
Legacy/insufficient data remains theoretical, never repaired from live data.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional

from rich.console import Console
from rich.markup import escape

from ...backtesting.engine import BacktestEngine
from ...core.config import AppConfig
from ...core.logging import get_logger
from ...core.types import Mode
from ...database.repository import Repository
from ...fees.calculator import FeeCalculator
from ...fees.freshness import policy_for_mode, policy_note, research_fee_label, summarize_checks
from ...matching.llm_matcher import LLMMatcher
from ...monitoring.dashboard import print_block_reasons
from ...monitoring.formatting import UNAVAILABLE_TEXT
from ..shared import run_matching

log = get_logger("modes.historical")


def _fmt(value) -> str:
    return UNAVAILABLE_TEXT if value is None else str(value)


class HistoricalEngine:
    def __init__(self, config: AppConfig, repo: Repository,
                 fee_calc: FeeCalculator, llm: LLMMatcher,
                 console: Console, recorded: Optional[Repository] = None):
        assert config.mode == Mode.HISTORICAL
        self.config, self.repo, self.recorded = config, repo, recorded
        self.fee_calc, self.llm, self.console = fee_calc, llm, console
        self.backtest = BacktestEngine(
            recorded if recorded is not None else repo,
            fee_calc=fee_calc, llm=llm, settings=config.settings,
            risk_limits=config.risk_limits,
            simulation_config=config.mode_config.get("simulation"),
            matching=run_matching)
        self.provider = self.backtest.provider

    async def run(self, start: Optional[date] = None,
                  end: Optional[date] = None, single_date: Optional[date] = None) -> dict:
        if single_date is not None:
            start = end = single_date
        start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc) if start else None
        end_dt = datetime.combine(end, time.max, tzinfo=timezone.utc) if end else None
        frames = self.provider.load_frames(start_dt, end_dt)
        if not frames:
            self.console.print(
                "[yellow]No recorded historical order-book data for the requested range. "
                "Historical order-book data for these venues is not retroactively available — "
                "run `python -m prediction_arb.main --mode collect` to start recording. "
                "This mode will NOT fabricate data or fall back to live data (§55, §92).[/yellow]")
        if self.provider.under_recorded:
            self.console.print(
                f"[yellow]{len(self.provider.under_recorded)} snapshot(s) had no reconstructable "
                "as-of contract and were excluded from matching. Nothing was invented (§92).[/yellow]")
        fee_policy = policy_for_mode(Mode.HISTORICAL)
        venues = sorted({c.venue for _, cs, _ in frames for c in cs})
        checks = self.fee_calc.schedule_checks(venues, fee_policy)
        fee_label = research_fee_label(checks)
        self.console.print(f"[dim]Fee-freshness policy for replay: "
                           f"{policy_note(fee_policy, self.fee_calc.max_schedule_age_days)}[/dim]")
        if fee_label:
            self.console.print(f"[yellow]{escape(fee_label)}[/yellow]")
            log.warning("%s", fee_label)

        metrics = await self.backtest.run_backtest(
            start_dt, end_dt, frames=frames, opportunity_sink=self.repo.save_opportunity)
        unpriced = metrics.opportunities_observed - metrics.costed_opportunities
        simulation = metrics.simulation
        summary = {
            "evaluation_kind": "HISTORICAL_RESEARCH_NOT_REAL_TRADING",
            "frames": metrics.frames_available,
            "opportunities": metrics.opportunities_observed,
            "surviving_fees": metrics.costed_positive_opportunities,
            "net_profit_unavailable": unpriced,
            "executable": metrics.executable_observed,
            "counter_definitions": {
                "opportunities": "calculated gross-edge observations (may repeat across frames)",
                "surviving_fees": "calculated positive net edges with complete costs, NOT fills",
                "executable": "cost/decision/data-qualified under replay assumptions, NOT guaranteed fills",
            },
            "deterministic_given_identical_inputs_and_match_verdicts": True,
            "contracts_reconstructed": metrics.contracts_reconstructed,
            "contracts_unreconstructable": metrics.contracts_unreconstructable,
            "recorded_store": str(self.recorded.db_path) if self.recorded is not None else None,
            "fee_freshness": {
                "policy": fee_policy.value,
                "max_schedule_age_days": self.fee_calc.max_schedule_age_days,
                "research_only_label": fee_label,
                "schedules": summarize_checks(checks)["venues"],
            },
            "metrics": metrics.to_dict(),
            "warning": "Historical/simulated results do not guarantee real execution or profitability.",
        }
        self.console.print(
            f"Historical replay complete: {metrics.frames_available} as-of decision frames, "
            f"{metrics.opportunities_observed} calculated opportunity observations "
            f"({metrics.costed_opportunities} fully costed, "
            f"{metrics.executable_observed} executable under replay assumptions).")
        print_block_reasons(self.console, [op for _, ops in self.backtest.opportunity_frames for op in ops],
                            label="replay:")
        if unpriced:
            self.console.print(
                f"[yellow]{unpriced} opportunity(ies) have no verified net profit — a mandatory "
                f"cost was unavailable; net profit is {UNAVAILABLE_TEXT}, never zero.[/yellow]")
        quality = metrics.data_quality
        self.console.print(
            f"Observed data limitations: {quality['snapshots_without_receipt_time']} snapshots without "
            f"receipt time, {quality['snapshots_without_asof_contract_context']} without as-of terms, "
            f"{quality['invalid_depth_snapshots']} invalid books, "
            f"{quality['stale_book_decision_observations']} stale book/decision observations.")
        if metrics.data_unsupported_opportunities or not frames:
            self.console.print(
                "[yellow]Insufficient execution evidence: unsupported observations remain THEORY ONLY. "
                "Minimum additional collection requirements are in the JSON report and "
                "docs/HISTORICAL_EVALUATION.md.[/yellow]")
        self.console.print(
            f"Theoretical costed edge sum (repeated observations, NOT equity/P&L): "
            f"{_fmt(metrics.theoretical_net.net_edge_sum_known_subset)} | "
            f"calculated fee drag: {_fmt(metrics.fee_drag.drag_ratio)}")
        self.console.print(
            f"Simulated: {simulation['attempted_entries']} attempted entries, "
            f"{simulation['filled_orders']} fully filled orders, {simulation['partial_orders']} partial orders, "
            f"{simulation['completed_two_leg_positions']} completed two-leg positions, "
            f"{len(simulation['unresolved_exposure'])} unresolved exposure(s), "
            f"{simulation['orders_with_unavailable_data']} orders with unavailable data.")
        self.console.print(
            f"[dim]Net simulated P&L (conditional terminal payoff, NOT realized cash): "
            f"{_fmt(simulation['net_simulated_pnl'])}; realized P&L: "
            f"{UNAVAILABLE_TEXT}. No result guarantees real execution.[/dim]")
        return summary
