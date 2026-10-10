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
from ...orderbooks.aggregator import OrderBookAggregator
from ...risk import kill_switch
from ...risk.exposure import ExposureTracker
from ...risk.limits import RiskLimits, RiskState, check_trade_against_limits
from ..shared import run_arbitrage_scan, run_matching

log = get_logger("modes.paper")


def _book_age_ms(settings: dict) -> float:
    """The configured order-book staleness limit, in milliseconds.

    `execution.max_book_age_ms` is the execution-time bound (what the live
    firewall enforces); `arbitrage.maximum_book_age_ms` is the scan-time
    bound. Paper applies one gate for both, taking the stricter of the two
    — a book stale enough to fail either check must not be scanned *or*
    filled against.
    """
    execution = settings.get("execution", {})
    arbitrage = settings.get("arbitrage", {})
    candidates = [
        float(v) for v in (execution.get("max_book_age_ms"),
                           arbitrage.get("maximum_book_age_ms"))
        if v is not None
    ]
    return min(candidates) if candidates else 1000.0


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
        # D11: the scan and the simulator both read through this aggregator,
        # which enforces `maximum_book_age_ms`. Before this the paper engine
        # kept its own dict and applied NO freshness gate, so a book could
        # age indefinitely and still be traded against — while `settings.yaml`
        # declared a 1000ms limit that only the live firewall honoured.
        self.aggregator = OrderBookAggregator(max_staleness_ms=_book_age_ms(
            config.settings))
        # (venue, contract_id) pairs actively streamed via the venue's
        # WebSocket feed (Phase 2). Streaming books arrive as events and
        # update the aggregator in real time (sub-second staleness) instead
        # of waiting for the next poll. Without credentials this set stays
        # empty and everything is polled, exactly as before.
        self._streaming: set[tuple[str, str]] = set()
        # Venues whose streaming failed at least once this run — we stop
        # retrying them (one bounded failure, then polling for the rest).
        self._streaming_failed_venues: set[str] = set()
        self.execution = PaperExecutionEngine(
            starting_cash=starting,
            fee_calc=fee_calc,
            # Freshness-gated: a simulated fill must not be priced against a
            # book the scan would already have rejected for staleness (D11).
            book_lookup=lambda v, c: self.aggregator.peek(v, c),
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
        await self._reap_vanished(contracts)
        if not contracts:
            self.console.print(f"[cycle {cycle}] no active-venue markets")
            return

        approved, match_result = await run_matching(contracts, self.llm)
        await self._ensure_streaming(contracts)
        books = await self._fetch_books(contracts)
        # Merge: streamed books (updated in real time by WS callbacks) stay
        # authoritative; polling only fills in what is not streamed.
        for key, book in books.items():
            if key not in self._streaming:
                await self.aggregator.update(book)

        # D11: scan over the FRESH subset only. Books that aged past
        # `maximum_book_age_ms` are counted and reported, never silently
        # traded on — and never silently dropped either, because a scan that
        # quietly sees nothing looks identical to a quiet market.
        fresh = await self.aggregator.fresh_snapshot()
        stale = await self.aggregator.stale_keys()
        if stale:
            self.console.print(
                f"[yellow][cycle {cycle}] {len(stale)} order book(s) older "
                f"than {self.aggregator.max_staleness_ms:.0f}ms were excluded "
                f"from the scan and from execution: {sorted(stale)}[/yellow]")
            log.warning("stale books excluded from cycle %s: %s", cycle, stale)

        ops = await run_arbitrage_scan(
            contracts, fresh, approved, self.fee_calc, Mode.PAPER,
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
            # Only a TRADE with a VERIFIED positive net profit may execute.
            # `is_executable` re-checks net profit, capital and block
            # reasons, so an opportunity blocked for an unavailable
            # mandatory cost can never reach execution on the strength of a
            # positive gross edge (D17).
            if op.is_executable:
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

    async def _ensure_streaming(self, contracts: list[NormalizedContract]) -> None:
        """Open (once per market) WebSocket book subscriptions on adapters
        that support streaming AND have credentials. Failures fall back to
        REST polling for that market — streaming is an optimization, never
        a requirement (UNKNOWN never means yes)."""
        for c in contracts:
            key = (c.venue, c.contract_id)
            if key in self._streaming:
                continue
            if c.venue in self._streaming_failed_venues:
                continue  # already fell back to polling this run
            adapter = self.adapters.get(c.venue)
            if adapter is None:
                continue
            if not getattr(adapter, "supports_streaming", False):
                continue
            if adapter.get_api_status() != APIStatus.VERIFIED:
                continue
            if not getattr(adapter, "has_credentials", False):
                continue  # no key → polling only
            try:
                await adapter.subscribe_order_book(
                    c.market_id,
                    lambda book, k=key: self._on_streamed_book(k, book))
                self._streaming.add(key)
                self.console.print(
                    f"[cyan]{c.venue}[/cyan]: streaming orderbook for "
                    f"{c.market_id} via WebSocket (sub-second updates)")
            except Exception as exc:
                self._streaming_failed_venues.add(c.venue)
                log.warning("streaming unavailable for %s/%s (%s: %s); "
                            "falling back to REST polling for this venue",
                            c.venue, c.market_id,
                            type(exc).__name__, exc)

    async def _on_streamed_book(self, key: tuple[str, str],
                                book: OrderBook) -> None:
        """WebSocket callback: a fresh book for a streamed market.

        Routed through the aggregator so streamed books get the same
        sequence-number guard and staleness accounting as polled ones
        (D11).
        """
        await self.aggregator.update(book)
        await self.health.record_success(key[0])

    async def _reap_vanished(self, contracts: list[NormalizedContract]) -> None:
        """Drop books and release WS subscriptions for markets that fell
        out of discovery (including when discovery is empty this cycle),
        so the scan never sees a vanished market's stale book."""
        live = {(c.venue, c.contract_id) for c in contracts}
        for key in [k for k in await self.aggregator.keys() if k not in live]:
            await self.aggregator.drop(key)
        for key in [k for k in self._streaming if k not in live]:
            adapter = self.adapters.get(key[0])
            if adapter is not None:
                try:
                    await adapter.unsubscribe_order_book(key[1])
                except Exception as exc:
                    log.warning("unsubscribe %s/%s failed: %s",
                                key[0], key[1], exc)
            self._streaming.discard(key)

    async def _fetch_books(self, contracts: list[NormalizedContract]
                           ) -> dict[tuple[str, str], OrderBook]:
        """Poll books for everything NOT actively streamed."""
        books: dict[tuple[str, str], OrderBook] = {}
        for c in contracts:
            key = (c.venue, c.contract_id)
            if key in self._streaming:
                continue  # WS feed updates the aggregator directly
            adapter = self.adapters.get(c.venue)
            if adapter is None or adapter.get_api_status() != APIStatus.VERIFIED:
                continue
            try:
                book = await adapter.get_order_book(c.market_id)
            except Exception as exc:
                await self.health.record_error(c.venue, str(exc))
                continue
            if book is not None:
                books[key] = book
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
