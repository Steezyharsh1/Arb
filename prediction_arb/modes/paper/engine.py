"""Paper trading engine (§44 MODE 2): real market data, simulated
execution via PaperExecutionEngine. No live-order code path exists in
this module or in anything it constructs."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Optional

from rich.console import Console

from ...adapters.base import PredictionMarketAdapter
from ...core.config import AppConfig
from ...core.logging import get_logger
from ...core.types import (
    APIStatus,
    Mode,
    NormalizedContract,
    Opportunity,
    OpportunityStatus,
    OrderBook,
)
from ...compliance.eligibility import check_contract_eligibility
from ...database.repository import Repository
from ...execution.coordinator import ExecutionCoordinator
from ...execution.partial_fills import PartialFillHandler
from ...execution.simulated import PaperExecutionEngine
from ...fees.calculator import FeeCalculator
from ...matching.llm_matcher import LLMMatcher
from ...monitoring.alerts import ConsoleAlertSink
from ...monitoring.dashboard import opportunities_table
from ...monitoring.health import HealthMonitor
from ...risk import kill_switch
from ...risk.exposure import ExposureTracker
from ...risk.limits import RiskLimits, RiskState, check_trade_against_limits
from ..shared import run_arbitrage_scan, run_matching

log = get_logger("modes.paper")


class PaperEngine:
    def __init__(self, config: AppConfig,
                 adapters: dict[str, PredictionMarketAdapter],
                 repo: Repository, fee_calc: FeeCalculator,
                 llm: LLMMatcher, console: Console):
        assert config.mode == Mode.PAPER
        self.config = config
        self.adapters = adapters
        self.repo = repo
        self.fee_calc = fee_calc
        self.llm = llm
        self.console = console
        self.health = HealthMonitor()
        self.alerts = ConsoleAlertSink()
        self.limits = RiskLimits.from_config(config.risk_limits)
        self.risk_state = RiskState()
        self.exposure = ExposureTracker()

        starting = {
            venue: Decimal(str(amount))
            for venue, amount in (config.mode_config.get(
                "paper_starting_cash") or {}).items()
        }
        self._latest_books: dict[tuple[str, str], OrderBook] = {}
        self.execution = PaperExecutionEngine(
            starting_cash=starting,
            fee_calc=fee_calc,
            book_lookup=lambda v, c: self._latest_books.get((v, c)),
            fill_probability_at_touch=float(config.mode_config.get(
                "simulated_fill_probability_at_touch", 0.9)),
            simulated_latency_ms=int(config.mode_config.get(
                "simulated_latency_ms", 250)))
        self.coordinator = ExecutionCoordinator(
            engine=self.execution, exposure=self.exposure,
            partial_fill_handler=PartialFillHandler(self.limits, self.alerts),
            mode=Mode.PAPER)

    async def run(self, cycles: Optional[int] = None) -> None:
        arb = self.config.settings.get("arbitrage", {})
        scan = self.config.settings.get("scanner", {})
        interval = float(scan.get("poll_interval_seconds", 10))
        n = 0
        while cycles is None or n < cycles:
            n += 1
            try:
                await self._cycle(n, arb, scan)
            except Exception as exc:
                log.error(f"cycle {n} failed: {exc}", exc_info=True)
            if cycles is None or n < cycles:
                await asyncio.sleep(interval)

    async def _cycle(self, cycle: int, arb: dict, scan: dict) -> None:
        contracts = await self._discover()
        if not contracts:
            self.console.print(f"[cycle {cycle}] no active-venue markets")
            return

        approved, match_result = await run_matching(contracts, self.llm)
        books = await self._fetch_books(contracts)
        self._latest_books = books

        ops = await run_arbitrage_scan(
            contracts, books, approved, self.fee_calc, Mode.PAPER,
            minimum_net_profit=Decimal(str(arb.get("minimum_net_profit", 2))),
            minimum_net_return=Decimal(str(arb.get("minimum_net_return", 0.005))),
            maximum_settlement_delay_days=Decimal(str(
                arb.get("maximum_settlement_delay_days", 5))),
            cost_of_capital_annual=Decimal(str(self.config.settings.get(
                "cost_of_capital", {}).get("annual_rate", 0.05))))

        for op in ops:
            self.repo.save_opportunity(op)
        for cert in match_result.certificates:
            self.repo.save_match_certificate(
                cert.match_id, cert.contract_a, cert.contract_b,
                cert.llm_confidence, cert.status, cert.independence,
                cert.to_json(), cert.llm_model_version,
                cert.llm_prompt_version, cert.matcher_version)

        self.console.print(
            f"[cycle {cycle}] markets={match_result.markets_scanned} "
            f"events={match_result.events_grouped} "
            f"pairs={match_result.pairs_considered} "
            f"certs={len(match_result.certificates)} "
            f"opportunities={len(ops)}")
        if ops:
            self.console.print(opportunities_table(ops, Mode.PAPER))

        for op in ops:
            if op.status == OpportunityStatus.TRADE:
                await self._maybe_execute(op)

    async def _discover(self) -> list[NormalizedContract]:
        out: list[NormalizedContract] = []
        limit = int(self.config.settings.get("scanner", {}).get(
            "max_markets_per_venue", 200))
        for venue, adapter in self.adapters.items():
            if adapter.get_api_status() != APIStatus.VERIFIED:
                continue
            try:
                ms = await adapter.get_markets(limit=limit)
                await self.health.record_success(venue)
            except Exception as exc:
                await self.health.record_error(venue, str(exc))
                continue
            for m in ms:
                decision = check_contract_eligibility(
                    m, self.config.settings.get("compliance", {}).get(
                        "jurisdiction", "US-UT"), Mode.PAPER)
                if decision.allowed:
                    out.append(m)
        return out

    async def _fetch_books(self, contracts: list[NormalizedContract]
                           ) -> dict[tuple[str, str], OrderBook]:
        books: dict[tuple[str, str], OrderBook] = {}
        for c in contracts:
            adapter = self.adapters.get(c.venue)
            if adapter is None or adapter.get_api_status() != APIStatus.VERIFIED:
                continue
            try:
                book = await adapter.get_order_book(c.market_id)
            except Exception as exc:
                await self.health.record_error(c.venue, str(exc))
                continue
            if book is not None:
                books[(c.venue, c.contract_id)] = book
        return books

    async def _maybe_execute(self, op: Opportunity) -> None:
        if kill_switch.is_engaged():
            self.console.print("[red]kill switch engaged — no new trades[/red]")
            return
        venues = [l.venue for l in op.legs]
        ok, why = await self.health.trading_allowed(venues)
        if not ok:
            log.warning(f"skipping {op.opportunity_id}: {why}")
            return
        violations = check_trade_against_limits(
            self.limits, self.risk_state, op.capital_required, venues,
            op.normalized_event)
        if violations:
            log.warning(f"risk blocked {op.opportunity_id}: {violations}")
            return
        report = await self.coordinator.execute(op)
        await self.alerts.alert(
            "PAPER EXECUTION" + (" OK" if report.success else " INCOMPLETE"),
            f"opportunity {op.opportunity_id}: success={report.success}; "
            f"notes={report.notes}")
        if report.success:
            self.risk_state.deployed_total += op.capital_required
            for leg in op.legs:
                self.risk_state.deployed_by_venue[leg.venue] = (
                    self.risk_state.deployed_by_venue.get(
                        leg.venue, Decimal("0"))
                    + op.capital_required / len(op.legs))
            self.risk_state.deployed_by_event[op.normalized_event] = (
                self.risk_state.deployed_by_event.get(
                    op.normalized_event, Decimal("0")) + op.capital_required)
            self.risk_state.open_positions += len(op.legs)
