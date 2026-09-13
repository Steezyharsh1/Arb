"""prediction_arb entry point.

Usage:
  python main.py --mode collect              # record real market data (default-safe)
  python main.py --mode paper                # real data, simulated execution
  python main.py --mode historical [--date YYYY-MM-DD | --start ... --end ...]
  python main.py --mode live                 # interactive confirmation required
  python main.py --kill                      # engage global kill switch
  python main.py --unkill                    # disengage kill switch
  python main.py --status                    # venue/API/provenance status
  python main.py --readiness                 # historical→paper→live readiness

Defaults to the mode in config/settings.yaml (paper). Never auto-enters
live mode (§78).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

# Allow `python main.py` from inside the package directory: import the
# package by name so relative imports inside modules resolve.
_PKG_PARENT = str(Path(__file__).resolve().parent.parent)
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from rich.console import Console

from prediction_arb.adapters.base import PredictionMarketAdapter
from prediction_arb.adapters.forecast_ex import ForecastExAdapter
from prediction_arb.adapters.kalshi import KalshiAdapter
from prediction_arb.adapters.moomoo import MoomooAdapter
from prediction_arb.adapters.webull import WebullAdapter
from prediction_arb.core.config import AppConfig, load_config
from prediction_arb.core.logging import setup_logging
from prediction_arb.core.types import Mode, ModeIsolationError
from prediction_arb.database.repository import Repository
from prediction_arb.fees.venue_fees import load_fee_calculator
from prediction_arb.matching.llm_matcher import LLMMatcher
from prediction_arb.monitoring.dashboard import (
    print_mode_banner,
    print_startup_summary,
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
            # The ONLY place order placement is enabled, and only in live:
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


def cmd_readiness(config: AppConfig) -> int:
    """§58 readiness checks — advisory; the final decision is the user's."""
    repo = Repository(config.database_path, config.mode)
    from sqlalchemy import func, select
    from sqlalchemy.orm import Session
    from prediction_arb.database.models import (
        OpportunityRow,
        OrderBookSnapshotRow,
    )
    with Session(repo.engine) as s:
        snaps = s.scalar(select(func.count(OrderBookSnapshotRow.id))) or 0
        ops = s.scalar(select(func.count(OpportunityRow.id))) or 0
    checks = [
        ("Recorded order-book snapshots", snaps, snaps > 1000),
        ("Opportunities analyzed", ops, ops > 50),
        ("Two live-capable independent venues", 1,
         False),  # kalshi verified; forecast_ex untested
    ]
    ready = all(ok for _, _, ok in checks)
    for name, value, ok in checks:
        console.print(f"{'PASS' if ok else 'NOT YET'}: {name} = {value}")
    console.print(f"\nLIVE MODE: {'READY (final decision is yours)' if ready else 'NOT RECOMMENDED YET'}")
    return 0


async def async_main(args: argparse.Namespace, config: AppConfig) -> int:
    setup_logging(config.mode)
    print_mode_banner(console, config.mode)
    print_startup_summary(console, config.mode,
                          config.execution_engine_name,
                          config.database_name)

    repo = Repository(config.database_path, config.mode)
    fee_calc = load_fee_calculator(
        config.fees,
        int(config.settings.get("fees", {}).get("max_schedule_age_days", 30)))
    llm = LLMMatcher(client=None)   # no LLM configured in v1 → conservative
    adapters = build_adapters(config)
    await connect_all(adapters)

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
            engine = HistoricalEngine(config, repo, fee_calc, llm, console)
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
