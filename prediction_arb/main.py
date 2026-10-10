"""prediction_arb entry point.

Usage (after `pip install -e .` from the repository root):
  python -m prediction_arb.main --mode collect   # record real market data (default-safe)
  python -m prediction_arb.main --mode paper     # real data, simulated execution
  python -m prediction_arb.main --mode historical [--date YYYY-MM-DD | --start ... --end ...]
  python -m prediction_arb.main --mode live      # interactive confirmation required
  python -m prediction_arb.main --kill           # engage global kill switch
  python -m prediction_arb.main --unkill         # disengage kill switch
  python -m prediction_arb.main --status         # venue/API/provenance status
  python -m prediction_arb.main --readiness      # derived per-mode readiness (advisory)

Equivalent shortcuts: `python -m prediction_arb ...` and, once installed,
the console script `prediction-arb ...`.

Defaults to the mode in config/settings.yaml (paper). Never auto-enters
live mode (§78).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Optional

# Allow `python prediction_arb/main.py` (running the file directly, without
# installing the package): put the PROJECT ROOT on sys.path so that the
# absolute `prediction_arb.*` imports below resolve. Layout: <root>/prediction_arb/.
_PROJECT_ROOT = str(Path(__file__).resolve().parents[1])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from rich.console import Console

from prediction_arb.adapters.base import PredictionMarketAdapter
from prediction_arb.adapters.forecast_ex import ForecastExAdapter
from prediction_arb.adapters.kalshi import KalshiAdapter
from prediction_arb.adapters.moomoo import MoomooAdapter
from prediction_arb.adapters.polymarket_us import PolymarketUSAdapter
from prediction_arb.adapters.webull import WebullAdapter
from prediction_arb.core import config as core_config
from prediction_arb.core.config import AppConfig, load_config
from prediction_arb.core.logging import setup_logging
from prediction_arb.core.types import Mode, ModeIsolationError
from prediction_arb.database.repository import Repository
from prediction_arb.fees.calculator import FeeCalculator
from prediction_arb.fees.venue_fees import load_fee_calculator
from prediction_arb.matching.llm_client import (
    LLMConfigError,
    build_llm_matcher,
)
from prediction_arb.monitoring.dashboard import (
    print_mode_banner,
    print_startup_summary,
)
from prediction_arb.monitoring.readiness import (
    ReadinessInputs,
    evaluate_all,
    load_recorded_evidence,
    print_readiness,
    probe_live_configuration,
    probe_matcher,
)
from prediction_arb.provenance.exchange_registry import get_provenance
from prediction_arb.provenance.independence import is_independent
from prediction_arb.risk import kill_switch

console = Console()


def build_adapters(config: AppConfig) -> dict[str, PredictionMarketAdapter]:
    adapters: dict[str, PredictionMarketAdapter] = {}
    vcfg = config.venues.get("venues", {})

    if vcfg.get("kalshi", {}).get("enabled"):
        k = vcfg["kalshi"]
        adapters["kalshi"] = KalshiAdapter(
            rest_base=k.get("rest_base", ""),
            api_key_id=config.env.get("KALSHI_API_KEY_ID")
            or config.env.get("KALSHI_LIVE_API_KEY_ID"),
            private_key_pem=config.env.get("KALSHI_PRIVATE_KEY_PEM")
            or config.env.get("KALSHI_LIVE_PRIVATE_KEY_PEM"),
            reads_per_sec=float(k.get("rate_limit_reads_per_sec", 8)),
            writes_per_sec=float(k.get("rate_limit_writes_per_sec", 3)),
            ws_url=k.get("ws_url"),
            rest_base_fallbacks=list(k.get("rest_base_fallbacks") or []),
            ws_url_fallbacks=list(k.get("ws_url_fallbacks") or []),
            # The ONLY place order placement is enabled, and only in live:
            allow_order_placement=(config.mode == Mode.LIVE))
    if vcfg.get("polymarket_us", {}).get("enabled"):
        p = vcfg["polymarket_us"]
        adapters["polymarket_us"] = PolymarketUSAdapter(
            public_base=p.get("public_base",
                              "https://gateway.polymarket.us"),
            authed_base=p.get("authed_base", "https://api.polymarket.us"),
            # Market data needs NO credentials; a key only unlocks the
            # WebSocket and portfolio surfaces.
            key_id=config.env.get("POLYMARKET_KEY_ID")
            or config.env.get("POLYMARKET_LIVE_KEY_ID"),
            secret_key=config.env.get("POLYMARKET_SECRET_KEY")
            or config.env.get("POLYMARKET_LIVE_SECRET_KEY"),
            reads_per_sec=float(p.get("rate_limit_reads_per_sec", 8)),
            writes_per_sec=float(p.get("rate_limit_writes_per_sec", 3)),
            ws_url=p.get("ws_url"),
            rate_limit_headroom=float(p.get("rate_limit_headroom", 0.8)),
            # Order placement is not implemented for this venue at all;
            # the adapter refuses regardless of this flag.
            allow_order_placement=(config.mode == Mode.LIVE))
    if vcfg.get("webull", {}).get("enabled"):
        adapters["webull"] = WebullAdapter()
    if vcfg.get("moomoo", {}).get("enabled"):
        adapters["moomoo"] = MoomooAdapter()
    if vcfg.get("forecast_ex", {}).get("enabled"):
        adapters["forecast_ex"] = ForecastExAdapter(
            cp_gateway_base=vcfg["forecast_ex"].get("cp_gateway_base"))
    return adapters


async def connect_all(adapters: dict[str, PredictionMarketAdapter]) -> None:
    for venue, adapter in adapters.items():
        try:
            await adapter.connect()
        except Exception as exc:
            console.print(f"[red]{venue}: connect failed: {exc}[/red]")


async def disconnect_all(adapters: dict[str, PredictionMarketAdapter]) -> None:
    for adapter in adapters.values():
        try:
            await adapter.disconnect()
        except Exception:
            pass


async def maybe_calibrate_kalshi_rate_limits(
        adapters: dict[str, PredictionMarketAdapter],
        config: AppConfig) -> None:
    """Phase 2 (K5): with credentials, discover the account's real
    rate-limit tier and endpoint costs, and clamp the local read/write
    budgets below the published budget (headroom from config).

    Best-effort: a failure here NEVER blocks startup — the adapter keeps
    its configured, documented-conservative budgets. Without credentials
    (public market data only) there is nothing to calibrate."""
    kalshi = adapters.get("kalshi")
    if kalshi is None or not getattr(kalshi, "has_credentials", False):
        return
    # config.venues is the raw venues.yaml document; the per-venue entries
    # live under its `venues:` key (same unwrap build_adapters does). Reading
    # the top level silently yielded the hardcoded default and ignored the
    # operator's configured headroom (defect D14).
    kalshi_cfg = (config.venues.get("venues", {}) or {}).get("kalshi", {}) or {}
    headroom = float(kalshi_cfg.get("rate_limit_headroom", 0.8))
    try:
        limits = await kalshi.get_rate_limits()
    except Exception as exc:
        console.print(f"[yellow]kalshi: tier discovery unavailable "
                      f"({exc.__class__.__name__}: {exc}); keeping "
                      f"configured budgets[/yellow]")
        return
    try:
        costs = await kalshi.get_endpoint_costs()
    except Exception as exc:
        costs = {}
        console.print(f"[yellow]kalshi: endpoint-cost discovery unavailable "
                      f"({exc.__class__.__name__}: {exc})[/yellow]")
    kalshi.apply_rate_limits(limits, headroom=headroom)
    console.print(
        f"[green]kalshi rate limits:[/green] tier={limits.usage_tier} "
        f"read={limits.read_refill_rate:.0f} tok/s "
        f"(bucket {limits.read_bucket_capacity:.0f}), "
        f"write={limits.write_refill_rate:.0f} tok/s "
        f"(bucket {limits.write_bucket_capacity:.0f}); "
        f"{len(costs)} non-default endpoint costs; local budgets clamped "
        f"to read={kalshi.effective_reads_per_sec():.2f} req/s, "
        f"write={kalshi.effective_writes_per_sec():.2f} req/s "
        f"(headroom={headroom})")


def cmd_status(config: AppConfig) -> int:
    from rich.table import Table
    adapters = build_adapters(config)
    t = Table(title="VENUE STATUS")
    for col in ("Venue", "API status", "Underlying exchange", "Clearing",
                "Verified"):
        t.add_column(col)
    for venue, adapter in adapters.items():
        p = get_provenance(venue)
        t.add_row(venue, adapter.get_api_status().value,
                  p.underlying_exchange if p else "UNKNOWN",
                  p.clearing_entity if p else "UNKNOWN",
                  p.verified_at if p else "-")
    console.print(t)

    t2 = Table(title="INDEPENDENCE MATRIX")
    venues = list(adapters)
    t2.add_column("")
    for v in venues:
        t2.add_column(v)
    for va in venues:
        t2.add_row(va, *[is_independent(va, vb).value for vb in venues])
    console.print(t2)
    console.print(f"Kill switch: {kill_switch.status()}")
    return 0


def _fee_calculator(config: AppConfig) -> "FeeCalculator":
    """The production fee ledger plus the configured staleness limit."""
    return load_fee_calculator(config.fees, _max_schedule_age_days(config))


def _max_schedule_age_days(config: AppConfig) -> int:
    return int(config.settings.get("fees", {}).get("max_schedule_age_days", 30))


def cmd_readiness(config: AppConfig) -> int:
    """§58 readiness — advisory, and derived from verified state (§98, §99).

    Every criterion is computed from what the system can actually show:
    each adapter's own API/order-type verification, the provenance
    independence registry, the recorded store (opened read-only, never
    created by a status report), the fee ledger's completeness plus the
    mode's mandatory freshness policy, the settlement-timing evidence
    registry, the compliance eligibility ledger, and the risk/kill-switch
    configuration. D10: the two-venue gate used to be the literal ``False``;
    D19: fee freshness used to be documented as a live gate that no caller
    enforced. Both are derived now, and an unmet criterion names the
    observation that failed.

    This report enables nothing — live mode still requires config/live.yaml,
    the typed confirmation phrase and every LiveOrderFirewall check.
    """
    env_label = str(config.mode_config.get("env_file")
                    or f".env.{config.mode.value}")
    inputs = ReadinessInputs(
        adapters=build_adapters(config),
        fee_calc=_fee_calculator(config),
        settings=config.settings,
        risk_limits=config.risk_limits,
        recorded=load_recorded_evidence(config.recorded_store_path),
        matcher=probe_matcher(
            config.env, env_label=env_label,
            max_comparisons_per_cycle=int(config.settings.get(
                "llm", {}).get("max_comparisons_per_cycle", 200))),
        kill_switch_engaged=kill_switch.is_engaged(),
        jurisdiction=str(config.settings.get("compliance", {}).get(
            "jurisdiction", "US-UT")),
        live_configuration=probe_live_configuration(core_config.CONFIG_DIR,
                                                    core_config.ROOT),
    )
    print_readiness(evaluate_all(inputs), console, current_mode=config.mode)
    return 0


async def async_main(args: argparse.Namespace, config: AppConfig) -> int:
    setup_logging(config.mode)
    print_mode_banner(console, config.mode)
    print_startup_summary(console, config.mode,
                          config.execution_engine_name,
                          config.database_name)

    repo = Repository(config.database_path, config.mode)
    fee_calc = _fee_calculator(config)
    # D9: the LLM matcher is wired from the mode env file (§97). With no
    # LLM configured this is still the conservative default — every
    # ambiguous pair ends in REVIEW and nothing is auto-APPROVED (§19).
    # Partial configuration refuses startup: a half-configured matcher
    # must never be mistaken for a working one.
    try:
        llm, llm_model = build_llm_matcher(
            config.env,
            max_comparisons_per_cycle=int(config.settings.get(
                "llm", {}).get("max_comparisons_per_cycle", 200)))
    except LLMConfigError as exc:
        console.print(f"[red]STARTUP REFUSED: {exc}[/red]")
        return 2
    if llm.client is None:
        console.print(
            "[yellow]LLM matching: not configured — every ambiguous pair "
            "stays REVIEW and no cross-venue pair will be evaluated for "
            "arbitrage. See docs/API_SETUP.md to enable it.[/yellow]")
    else:
        console.print(
            f"[green]LLM matching enabled[/green]: model={llm_model} "
            f"endpoint={config.env.get('LLM_API_BASE', '').strip()} "
            f"(the key is never logged); per-cycle comparison budget="
            f"{llm.max_comparisons_per_cycle}")
    adapters = build_adapters(config)
    await connect_all(adapters)
    await maybe_calibrate_kalshi_rate_limits(adapters, config)

    try:
        if config.mode == Mode.COLLECT:
            from prediction_arb.modes.collect.engine import CollectEngine
            scan = config.settings.get("scanner", {})
            engine = CollectEngine(
                adapters, repo, console,
                poll_interval=float(scan.get("poll_interval_seconds", 10)),
                max_markets_per_venue=int(scan.get("max_markets_per_venue", 200)))
            await engine.run(cycles=args.cycles)
        elif config.mode == Mode.PAPER:
            from prediction_arb.modes.paper.engine import PaperEngine
            engine = PaperEngine(config, adapters, repo, fee_calc, llm, console)
            await engine.run(cycles=args.cycles)
        elif config.mode == Mode.HISTORICAL:
            from prediction_arb.modes.historical.engine import HistoricalEngine
            from prediction_arb.modes.historical.reports import (
                write_replay_report,
            )
            # D3: replay reads the store collect/paper recorded, read-only,
            # and writes its own database. A missing recorded store is the
            # honest empty state — the engine reports it, nothing is
            # created or fabricated here (§55, §92).
            recorded: Optional[Repository] = None
            recorded_path = config.recorded_store_path
            if recorded_path.exists():
                recorded = Repository(recorded_path, config.mode,
                                      read_only=True)
                console.print(f"Recorded store (read-only): {recorded_path}")
            else:
                console.print(
                    f"[yellow]No recorded store at {recorded_path} — nothing "
                    f"has been collected yet.[/yellow]")
            engine = HistoricalEngine(config, repo, fee_calc, llm, console,
                                      recorded=recorded)
            summary = await engine.run(
                start=date.fromisoformat(args.start) if args.start else None,
                end=date.fromisoformat(args.end) if args.end else None,
                single_date=date.fromisoformat(args.date) if args.date else None)
            path = write_replay_report(summary)
            console.print(f"Report: {path}")
        elif config.mode == Mode.LIVE:
            from prediction_arb.modes.live.engine import LiveEngine
            engine = LiveEngine(config, adapters, repo, fee_calc, llm, console)
            token = engine.confirm_interactively()  # raises if wrong phrase
            await engine.startup(token)
            await engine.run()
        return 0
    finally:
        await disconnect_all(adapters)


def main() -> int:
    parser = argparse.ArgumentParser(description="prediction_arb")
    parser.add_argument("--mode",
                        choices=["historical", "paper", "live", "collect"],
                        default=None,
                        help="operating mode (overrides settings.yaml)")
    parser.add_argument("--date", default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--cycles", type=int, default=None,
                        help="limit scan cycles (default: run forever)")
    parser.add_argument("--kill", action="store_true")
    parser.add_argument("--unkill", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--readiness", action="store_true")
    args = parser.parse_args()

    if args.kill:
        kill_switch.engage("manual --kill")
        console.print("[red]KILL SWITCH ENGAGED[/red]  (clear with --unkill)")
        return 0
    if args.unkill:
        kill_switch.disengage()
        console.print("[green]Kill switch disengaged.[/green]")
        return 0

    try:
        config = load_config(args.mode)
    except ModeIsolationError as exc:
        console.print(f"[red]STARTUP REFUSED: {exc}[/red]")
        return 2

    if args.status:
        return cmd_status(config)
    if args.readiness:
        return cmd_readiness(config)

    if kill_switch.is_engaged() and config.mode == Mode.LIVE:
        console.print("[red]Kill switch engaged — live mode refused. "
                      "Clear with --unkill first.[/red]")
        return 3

    return asyncio.run(async_main(args, config))


if __name__ == "__main__":
    sys.exit(main())
