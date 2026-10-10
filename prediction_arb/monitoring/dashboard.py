"""CLI dashboard (§36, §52). V1: rich terminal table. Web dashboard is a
future enhancement (interface note at bottom)."""
from __future__ import annotations

from typing import Sequence

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ..core.types import Mode, Opportunity
from .formatting import UNAVAILABLE_TEXT, format_amount

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
    """The opportunity table (§36, §52).

    ``Gross edge`` is payout minus walked purchase cost, before any cost —
    a research figure that is always computable. ``Fees``, ``Net`` and
    ``Capital`` render as ``n/a`` whenever a mandatory cost could not be
    computed, so a BLOCK row can never display a positive net profit next
    to a fabricated ``$0.00`` fee column.
    """
    unpriced = [op for op in opportunities if op.net_profit is None]
    table = Table(
        title=f"LIVE ARBITRAGE OPPORTUNITIES — {mode.value.upper()} MODE",
        caption=(
            f"[bold]{len(unpriced)}[/bold] opportunity(ies) have no verified "
            f"net profit: a mandatory cost is unavailable, so Net/Fees/"
            f"Capital read {UNAVAILABLE_TEXT}. Gross edge is pre-cost and is "
            f"NOT profit."
            if unpriced else None))
    for col in ("Event", "Legs", "YES exec", "NO exec", "Gross edge", "Fees",
                "Slippage", "Net", "Qty", "Capital", "Costs", "Indep",
                "Match conf", "Status"):
        table.add_column(col)
    for op in opportunities:
        yes_leg = next((l for l in op.legs if l.side.value == "yes"), None)
        no_leg = next((l for l in op.legs if l.side.value == "no"), None)
        missing = op.costs.missing_names()
        if op.costs.complete:
            costs = "complete"
        elif missing:
            costs = f"INCOMPLETE ({len(missing)})"
        else:
            costs = "UNCOSTED"   # hand-built row with no cost ledger
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
            format_amount(op.gross_edge),
            format_amount(op.total_fees),
            f"${op.slippage:.2f}",
            format_amount(op.net_profit),
            str(op.quantity),
            format_amount(op.capital_required),
            costs,
            op.independence.value,
            f"{op.match_confidence:.3f}" if op.match_confidence else "-",
            f"[{style}]{op.status.value}[/{style}]" if style
            else op.status.value,
        )
    return table


def print_block_reasons(console: Console,
                        opportunities: Sequence[Opportunity],
                        *, label: str = "", limit: int = 8) -> int:
    """Print the distinct BLOCK reasons behind a set of opportunities.

    The opportunity table shows a verdict, not a cause. A BLOCK whose reason
    is an unavailable mandatory cost — a stale or incomplete fee schedule, an
    unknown settlement lockup — has to be readable where the operator is
    looking, not only in the database (§100, D19). Reasons are deduplicated
    with a count, most common first, because one stale schedule blocks every
    opportunity it touches and repeating it per row buries the other causes.

    Returns the number of blocked opportunities (0 prints nothing).
    """
    blocked = [op for op in opportunities if op.block_reasons]
    if not blocked:
        return 0
    counts: dict[str, int] = {}
    for op in blocked:
        for reason in op.block_reasons:
            counts[reason] = counts.get(reason, 0) + 1
    # The label is operator text (for example "cycle 3:"), not markup.
    prefix = f"{escape(label)} " if label else ""
    console.print(
        f"[yellow]{prefix}{len(blocked)} opportunity(ies) BLOCKed — "
        f"distinct reason(s):[/yellow]", soft_wrap=True)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for reason, count in ordered[:limit]:
        console.print(f"[yellow]  ({count}x) {escape(reason)}[/yellow]",
                      soft_wrap=True)
    hidden = len(ordered) - limit
    if hidden > 0:
        console.print(f"[dim]  … and {hidden} further distinct reason(s)[/dim]",
                      soft_wrap=True)
    return len(blocked)


class WebDashboard:
    """V1 STUB (§36): web dashboard is a future enhancement. The CLI
    table above is the v1 deliverable."""

    def start(self) -> None:
        raise NotImplementedError(
            "Web dashboard is a v1 stub (spec §3, §36). Use the CLI table "
            "(opportunities_table) for now.")
