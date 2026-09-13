"""Live engine (§44 MODE 3). Requires interactive confirmation (§47),
runs crash recovery on startup (§61), and routes every order through the
LiveOrderFirewall via LiveExecutionEngine.

Current reality check: Phase 1 identified several independent DCMs, but Kalshi
is still the only implemented adapter with prior live verification. Every other
candidate lacks an owner-approved, live-tested adapter, and fee/Utah eligibility
gates remain closed. Live cross-venue arbitrage is therefore impossible and this
engine remains monitor-only by design.
"""
from __future__ import annotations

from rich.console import Console

from ...adapters.base import PredictionMarketAdapter
from ...core.config import AppConfig
from ...core.logging import get_logger
from ...core.types import APIStatus, Mode, ModeIsolationError
from ...database.repository import Repository
from ...execution.live import (
    LIVE_CONFIRMATION_PHRASE,
    LiveConfirmationToken,
    LiveExecutionEngine,
)
from ...fees.calculator import FeeCalculator
from ...matching.llm_matcher import LLMMatcher

log = get_logger("modes.live")

_WARNING = """
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

WARNING: LIVE TRADING MODE

REAL MONEY WILL BE USED.

Venues configured:
{venues}

Live orders may result in financial loss.

Type:

    {phrase}

to continue (anything else aborts):
"""


class LiveEngine:
    def __init__(self, config: AppConfig,
                 adapters: dict[str, PredictionMarketAdapter],
                 repo: Repository, fee_calc: FeeCalculator,
                 llm: LLMMatcher, console: Console):
        if config.mode != Mode.LIVE:
            raise ModeIsolationError("LiveEngine requires live mode config")
        self.config = config
        self.adapters = adapters
        self.repo = repo
        self.fee_calc = fee_calc
        self.llm = llm
        self.console = console
        self.execution: LiveExecutionEngine | None = None

    def confirm_interactively(self) -> LiveConfirmationToken:
        venues = "\n".join(f"  {v}" for v in self.adapters)
        self.console.print(_WARNING.format(
            venues=venues, phrase=LIVE_CONFIRMATION_PHRASE))
        typed = input("> ")
        return LiveConfirmationToken(typed)   # raises on mismatch

    async def startup(self, confirmation: LiveConfirmationToken) -> None:
        self.execution = LiveExecutionEngine(
            adapters={v: a for v, a in self.adapters.items()
                      if a.get_api_status() == APIStatus.VERIFIED},
            repository=self.repo,
            confirmation=confirmation,
            allow_live_orders=self.config.allow_live_orders)

        # §61: crash recovery before anything else
        notes = await self.execution.recover_on_startup()
        for n in notes:
            self.console.print(f"[recovery] {n}")

        # honest capability report
        verified = [v for v, a in self.adapters.items()
                    if a.get_api_status() == APIStatus.VERIFIED]
        self.console.print(
            f"[bold]Live-capable venues:[/bold] {verified or 'NONE'}")
        if len(verified) < 2:
            self.console.print(
                "[yellow]Fewer than two live-capable INDEPENDENT venues.\n"
                "Cross-venue guaranteed arbitrage is IMPOSSIBLE right now: "
                "Phase 1 candidates still need owner selection, an implemented "
                "and live-tested adapter, exact fees, and Utah eligibility "
                "evidence (see docs/VENUE_RESEARCH.md). Live mode will scan "
                "and report "
                "but cannot and will not execute two-leg cross-venue "
                "trades.[/yellow]")

    async def run(self) -> None:
        if self.execution is None:
            raise ModeIsolationError("startup() with confirmation required")
        self.console.print(
            "[red]Live scanning loop intentionally requires two verified "
            "independent venues before it can execute. Currently in "
            "monitor-only state. Use --mode paper for full pipeline "
            "operation.[/red]")
