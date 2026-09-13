"""CLI dashboard (§36, §52). V1: rich terminal table. Web dashboard is a
future enhancement (interface note at bottom)."""
from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ..core.types import Mode, Opportunity

_MODE_BANNERS = {
    Mode.HISTORICAL: ("HISTORICAL MODE", "REPLAY — NO REAL ORDERS ARE SENT", "cyan"),
    Mode.PAPER: ("PAPER MODE", "NO REAL ORDERS ARE SENT", "green"),
    Mode.LIVE: ("LIVE MODE", "REAL MONEY IS AT RISK", "red"),
    Mode.COLLECT: ("COLLECT MODE", "DATA COLLECTION ONLY — NO ORDERS", "blue"),
}


def print_mode_banner(console: Console, mode: Mode) -> None:
    title, subtitle, color = _MODE_BANNERS[mode]
    console.print(Panel(
        f"[bold]{title}[/bold]\n{subtitle}",
        title="PREDICTION ARBITRAGE BOT",
        border_style=color, expand=False))


def print_startup_summary(console: Console, mode: Mode,
                          engine_name: str | None, database: str) -> None:
    real_orders = "ENABLED" if mode == Mode.LIVE else "DISABLED"
    color = "red" if mode == Mode.LIVE else "green"
    console.print(
        f"ACTIVE MODE: [bold]{mode.value.upper()}[/bold]\n"
        f"EXECUTION ENGINE: {engine_name or 'None (collect)'}\n"
        f"DATABASE: {database}\n"
        f"REAL ORDERS: [bold {color}]{real_orders}[/bold {color}]")


def opportunities_table(opportunities: list[Opportunity],
                        mode: Mode) -> Table:
    table = Table(title=f"LIVE ARBITRAGE OPPORTUNITIES — "
                        f"{mode.value.upper()} MODE")
    for col in ("Event", "Legs", "YES exec", "NO exec", "Gross", "Fees",
                "Slippage", "Net", "Qty", "Capital", "Indep",
                "Match conf", "Status"):
        table.add_column(col)
    for op in opportunities:
        yes_leg = next((l for l in op.legs if l.side.value == "yes"), None)
        no_leg = next((l for l in op.legs if l.side.value == "no"), None)
        gross = op.payout_per_contract * op.quantity - op.gross_cost
        style = {"TRADE": "green", "WATCH": "yellow",
                 "BLOCK": "red"}.get(op.status.value, "")
        table.add_row(
            op.normalized_event[:48],
            " + ".join(f"{l.venue}:{l.contract.contract_id[:20]}"
                       for l in op.legs),
            f"{yes_leg.walk.weighted_average_price:.4f}"
            if yes_leg and yes_leg.walk else "-",
            f"{no_leg.walk.weighted_average_price:.4f}"
            if no_leg and no_leg.walk else "-",
            f"${gross:.2f}", f"${op.total_fees:.2f}",
            f"${op.slippage:.2f}", f"${op.net_profit:.2f}",
            str(op.quantity), f"${op.capital_required:.2f}",
            op.independence.value,
            f"{op.match_confidence:.3f}" if op.match_confidence else "-",
            f"[{style}]{op.status.value}[/{style}]" if style
            else op.status.value,
        )
    return table


class WebDashboard:
    """V1 STUB (§36): web dashboard is a future enhancement. The CLI
    table above is the v1 deliverable."""

    def start(self) -> None:
        raise NotImplementedError(
            "Web dashboard is a v1 stub (spec §3, §36). Use the CLI table "
            "(opportunities_table) for now.")
