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
from ...fees.freshness import (
    policy_for_mode,
    policy_note,
    research_fee_label,
    summarize_checks,
)
from ...matching.llm_matcher import LLMMatcher
from ...monitoring.dashboard import print_block_reasons
from ...monitoring.formatting import UNAVAILABLE_TEXT
from ...risk.limits import RiskLimits
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

        # §98/D19: replay runs under the RESEARCH fee-freshness policy. A
        # schedule verified longer ago than the live limit is still costed —
        # replaying recorded history with the schedule that was current then
        # is more useful than refusing to measure it — but the run says so
        # out loud, and the report carries the same label.
        fee_policy = policy_for_mode(Mode.HISTORICAL)
        fee_venues = sorted({c.venue for _ts, cs, _bs in frames for c in cs})
        fee_checks = self.fee_calc.schedule_checks(fee_venues, fee_policy)
        fee_label = research_fee_label(fee_checks)
        self.console.print(
            f"[dim]Fee-freshness policy for replay: "
            f"{policy_note(fee_policy, self.fee_calc.max_schedule_age_days)}"
            f"[/dim]")
        if fee_label:
            self.console.print(f"[yellow]{fee_label}[/yellow]")
            log.warning("%s", fee_label)

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
                    "cost_of_capital", {}).get("annual_rate", 0.05))),
                # Lockup is measured from the recorded frame, not the wall
                # clock: a replayed contract was unsettled at `ts` (D18).
                evaluation_time=ts)
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
        # Reporting reads the same authoritative risk file as paper
        # execution; the removed settings.yaml duplicate could drift and used
        # a different per-trade key.
        max_capital = RiskLimits.from_config(
            self.config.risk_limits).max_total_capital
        metrics = summarize_frames(
            per_frame,
            max_total_capital=max_capital,
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
            #: The freshness policy this replay ran under, the RESEARCH USE
            #: ONLY label (null when every schedule was fresh), and the
            #: per-venue observations behind both (§98).
            "fee_freshness": {
                "policy": fee_policy.value,
                "max_schedule_age_days": self.fee_calc.max_schedule_age_days,
                "research_only_label": fee_label,
                "schedules": summarize_checks(fee_checks)["venues"],
            },
            "metrics": metrics.to_dict(),
        }
        self.console.print(
            f"Historical replay complete: {summary['frames']} frames, "
            f"{total_ops} opportunities ({executable} executable), "
            f"{summary['contracts_reconstructed']} contracts reconstructed"
            + (f", {len(under)} unreconstructable" if under else ""))
        # Why the blocked rows blocked, deduplicated: replay over many frames
        # would otherwise repeat one stale-schedule reason per opportunity.
        print_block_reasons(
            self.console, [op for _ts, frame in per_frame for op in frame],
            label="replay:")
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
