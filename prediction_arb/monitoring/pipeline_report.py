"""Operator view of the pipeline telemetry (§103, Arb.14).

Two read-only views over a recorded store, both bounded:

* :func:`build_pipeline_report` — one UTC day: where candidates are lost
  (deterministic checks, LLM/REVIEW, mandatory inputs), how gross edge
  compares with fully costed net edge, book age and inter-venue skew, the
  simulated execution funnel, and adapter disconnect/429 counts.
* :func:`build_lifecycle` — one opportunity id: its evaluation and its
  execution attempt, read from the attempt table rather than from logs.

Every value is either a count that was actually recorded, or the string
``UNAVAILABLE`` (with the reason). A missing source is never reported as
zero. The report reads only; it never creates a store or writes a counter.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any, Optional

from ..core.clock import utcnow
from . import pipeline as tel

UNAVAILABLE = "UNAVAILABLE"

LIMITATIONS = (
    "Counts are per UTC day. An opportunity is counted once per scan cycle in "
    "which it was evaluated, so opportunity observations repeat; they are not "
    "distinct opportunities.",
    "Paper opportunity ids are random per evaluation, so one economic contract "
    "pair can appear under many ids. 'distinct_contract_pairs' groups them.",
    "Execution is simulated against the books the engine held, with the "
    "configured touch fill probability and latency. These are not venue fills.",
    "Adapter counters belong to this process run. A restart resets feed "
    "counters; the engine counts deltas and treats a reset as a restart.",
)


def _grp(counts: dict[tuple[str, str], int], metric: str) -> dict[str, int]:
    out = {label: n for (m, label), n in counts.items() if m == metric}
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _total(counts: dict[tuple[str, str], int], metric: str) -> int:
    return sum(n for (m, _), n in counts.items() if m == metric)


def _money(value: Decimal) -> str:
    return f"{value:.4f}"


def _gross_vs_net(rows: list[tuple]) -> dict[str, Any]:
    """Gross and net edge over stored observations.

    Only observations with a verified net profit are summed for gross versus
    net, so the two columns are comparable. Unpriced observations are counted
    separately and never added as zero.
    """
    costed = [r for r in rows if r[4] is not None]
    gross_sum = sum((Decimal(r[2]) for r in costed), Decimal("0"))
    net_sum = sum((Decimal(r[4]) for r in costed), Decimal("0"))
    consumed = [r for r in costed
                if Decimal(r[2]) > 0 and Decimal(r[4]) <= 0]
    return {
        "observations": len(rows),
        "distinct_opportunity_ids": len({r[0] for r in rows}),
        "unpriced_observations": len(rows) - len(costed),
        "costed_observations": len(costed),
        "costed_gross_edge_sum": _money(gross_sum),
        "costed_net_edge_sum": _money(net_sum),
        "gross_positive_consumed_by_costs": len(consumed),
    }


def _adapters(counts: dict[tuple[str, str], int]) -> dict[str, dict[str, Any]]:
    telemetry = _grp(counts, tel.ADAPTER_TELEMETRY)
    venues: dict[str, dict[str, str]] = {}
    for label in telemetry:
        venue, _, state = label.partition(":")
        family, _, value = state.partition("=")
        venues.setdefault(venue, {})[family] = value
    rate = _grp(counts, tel.ADAPTER_RATE_LIMITED)
    reconnect = _grp(counts, tel.ADAPTER_FEED_RECONNECT)
    errors = _grp(counts, tel.ADAPTER_ERROR)
    fallback = _grp(counts, tel.ADAPTER_STREAM_FALLBACK)
    out: dict[str, dict[str, Any]] = {}
    for venue in sorted(venues):
        fam = venues[venue]
        feed = fam.get("feed", "unavailable")
        if fam.get("rate_limit") == "observed":
            rate_value: Any = rate.get(venue, 0)
        else:
            rate_value = f"{UNAVAILABLE}: adapter has no 429 counter"
        if feed == "streaming":
            reconnects: Any = reconnect.get(venue, 0)
        elif feed == "polled":
            reconnects = "N/A: polled, no WebSocket session this run"
        else:
            reconnects = f"{UNAVAILABLE}: adapter exposes no feed statistics"
        out[venue] = {
            "rate_limited_responses": rate_value,
            "feed": feed,
            "feed_reconnects": reconnects,
            "errors": errors.get(venue, 0),
            "stream_fallbacks": fallback.get(venue, 0),
        }
    return out


def build_pipeline_report(repo, day: Optional[str] = None) -> dict[str, Any]:
    """Assemble the daily report from recorded counters and attempt rows."""
    day = day or utcnow().date().isoformat()
    report: dict[str, Any] = {
        "mode": repo.mode.value,
        "day": day,
        "limitations": list(LIMITATIONS),
    }
    if not repo.pipeline_store_ready():
        report["status"] = (f"{UNAVAILABLE}: this store has no pipeline "
                            f"telemetry (schema below 5). Run paper mode once "
                            f"to migrate; nothing is inferred for older data.")
        return report
    counts = repo.pipeline_counts(day)
    cycles = counts.get((tel.SCAN_CYCLES, ""), 0)
    if cycles == 0:
        report["status"] = (f"{UNAVAILABLE}: no scan cycle was recorded for "
                            f"{day}, so zero counts would be meaningless.")
        return report

    report["status"] = "AVAILABLE"
    report["scan_cycles"] = cycles
    attempts = repo.execution_attempts_for_day(day)
    pairs = {
        tuple(sorted([(a.leg_a_venue, a.leg_a_contract_id),
                      (a.leg_b_venue, a.leg_b_contract_id)]))
        for a in attempts if a.leg_b_venue
    }
    completed = sum(n for label, n in _grp(counts, tel.EXEC_ATTEMPT).items()
                    if label in tel.COMPLETED_POSITION_OUTCOMES)
    naked = {k: v for k, v in _grp(counts, tel.EXEC_HEDGE).items()
             if k in (tel.HEDGE_PARTIAL, tel.HEDGE_FAILED)}

    blocked_by = _grp(counts, tel.OPP_BLOCKED_BY)
    report["candidate_pairs"] = {
        "generated": _total(counts, tel.PAIRS_CONSIDERED),
        "deterministic_passed": _total(counts, tel.PAIRS_DET_PASSED),
        "deterministic_rejected_by_check": _grp(counts, tel.PAIRS_DET_REJECTED),
    }
    report["match_decisions_by_status"] = _grp(counts, tel.MATCH_STATUS)
    report["opportunities"] = {
        "by_status": _grp(counts, tel.OPP_STATUS),
        "gross_vs_net": _grp(counts, tel.OPP_EDGE),
        "blocked_by_input": blocked_by,
        "dominant_blockers": [{"input": k, "observations": v}
                              for k, v in list(blocked_by.items())[:5]],
        "stored_observations": _gross_vs_net(
            repo.opportunity_summaries_for_day(day)),
    }
    report["books"] = {
        "age_at_scan": _grp(counts, tel.BOOK_AGE),
        "inter_venue_skew": _grp(counts, tel.BOOK_SKEW),
    }
    report["execution"] = {
        "attempts_by_outcome": _grp(counts, tel.EXEC_ATTEMPT),
        "attempt_rows": len(attempts),
        "completed_simulated_positions": completed,
        "naked_hedge_states": naked,
        "hedge_states": _grp(counts, tel.EXEC_HEDGE),
        "submissions": _grp(counts, tel.EXEC_SUBMISSION),
        "leg_gap": _grp(counts, tel.EXEC_LEG_GAP),
        "recovery_order_attempts": _total(counts, tel.EXEC_RECOVERY),
        "risk_breaches_by_limit": _grp(counts, tel.RISK_BREACH),
        "kill_switch_engagements": _total(counts, tel.RISK_KILL_ENGAGED),
        "distinct_contract_pairs": len(pairs),
    }
    report["adapters"] = _adapters(counts)
    return report


def build_lifecycle(repo, opportunity_id: str,
                    recent: int = 5) -> dict[str, Any]:
    """One opportunity's evaluation and execution, read from stored rows."""
    history = repo.opportunity_history(opportunity_id)
    attempts = repo.attempts_for_opportunity(opportunity_id)
    out: dict[str, Any] = {
        "opportunity_id": opportunity_id,
        "found": bool(history or attempts),
        "evaluations_recorded": len(history),
        "execution_attempts": len(attempts),
    }
    if history:
        last = history[-1]
        out["latest_evaluation"] = {
            "timestamp": str(last.timestamp),
            "status": last.status,
            "gross_edge": last.gross_profit,
            "fees": last.fees,                 # None = UNAVAILABLE
            "net_profit": last.net_profit,     # None = UNAVAILABLE
            "capital_required": last.capital_required,
            "quantity": last.quantity,
            "independence": last.independence,
            "block_reasons": json.loads(last.block_reasons or "[]"),
            "unavailable_costs": (json.loads(last.unavailable_costs)
                                  if last.unavailable_costs else []),
        }
    out["recent_attempts"] = [_attempt_view(a) for a in attempts[-recent:]]
    return out


def _attempt_view(a) -> dict[str, Any]:
    def leg(prefix: str) -> Optional[dict[str, Any]]:
        venue = getattr(a, f"{prefix}_venue")
        if not venue:
            return None
        return {"venue": venue,
                "contract_id": getattr(a, f"{prefix}_contract_id"),
                "status": getattr(a, f"{prefix}_status"),
                "filled": getattr(a, f"{prefix}_filled"),
                "average_price": getattr(a, f"{prefix}_avg_price")}
    return {
        "timestamp": str(a.timestamp),
        "outcome": a.outcome,
        "hedge_state": a.hedge_state,
        "leg_a": leg("leg_a"),
        "leg_b": leg("leg_b"),
        "time_between_legs_ms": a.time_between_legs_ms,
        "unhedged_remaining": a.unhedged_remaining,
        "recovery_attempts": a.recovery_attempts,
        "recovery_resolution": a.recovery_resolution,
        "risk_violations": json.loads(a.risk_violations or "[]"),
        "kill_switch_engaged": a.kill_switch_engaged,
        "notes": a.notes,
    }


# ------------------------------------------------------------ rendering


def print_pipeline_report(console, report: dict[str, Any]) -> None:
    from rich.markup import escape
    console.print(f"[bold]PIPELINE REPORT[/bold] — {escape(report['mode'])} "
                  f"— {report['day']} (UTC)")
    if report["status"] != "AVAILABLE":
        console.print(f"[yellow]{escape(report['status'])}[/yellow]")
        return
    console.print(f"scan cycles recorded: {report['scan_cycles']}")

    cp = report["candidate_pairs"]
    console.print(f"\n[bold]Candidate pairs[/bold]: generated {cp['generated']}, "
                  f"deterministic passed {cp['deterministic_passed']}")
    _kv(console, "  rejected by check", cp["deterministic_rejected_by_check"])
    _kv(console, "  match decisions", report["match_decisions_by_status"])

    opp = report["opportunities"]
    console.print("\n[bold]Opportunities[/bold] (observations per cycle, not distinct)")
    _kv(console, "  by status", opp["by_status"])
    _kv(console, "  gross vs net", opp["gross_vs_net"])
    _kv(console, "  blocked by mandatory input", opp["blocked_by_input"])
    stored = opp["stored_observations"]
    console.print(
        f"  stored: {stored['observations']} observation(s), "
        f"{stored['unpriced_observations']} unpriced (net UNAVAILABLE); "
        f"costed gross {stored['costed_gross_edge_sum']} vs net "
        f"{stored['costed_net_edge_sum']}; "
        f"{stored['gross_positive_consumed_by_costs']} gross-positive "
        f"consumed by costs")

    books = report["books"]
    console.print("\n[bold]Books[/bold]")
    _kv(console, "  age at scan", books["age_at_scan"])
    _kv(console, "  inter-venue skew", books["inter_venue_skew"])

    ex = report["execution"]
    console.print("\n[bold]Simulated execution[/bold]")
    _kv(console, "  attempts by outcome", ex["attempts_by_outcome"])
    console.print(f"  completed simulated positions: "
                  f"{ex['completed_simulated_positions']}; "
                  f"distinct contract pairs attempted: "
                  f"{ex['distinct_contract_pairs']} "
                  f"(attempt rows {ex['attempt_rows']})")
    _kv(console, "  hedge states", ex["hedge_states"])
    _kv(console, "  naked hedge states", ex["naked_hedge_states"])
    _kv(console, "  submissions", ex["submissions"])
    _kv(console, "  leg gap", ex["leg_gap"])
    console.print(f"  recovery order attempts: {ex['recovery_order_attempts']}; "
                  f"kill switch engagements: {ex['kill_switch_engagements']}")
    _kv(console, "  risk breaches by limit", ex["risk_breaches_by_limit"])

    console.print("\n[bold]Adapters[/bold]")
    if not report["adapters"]:
        console.print("  no adapter telemetry recorded")
    for venue, a in report["adapters"].items():
        console.print(f"  {escape(venue)}: feed={a['feed']}, "
                      f"429s={escape(str(a['rate_limited_responses']))}, "
                      f"reconnects={escape(str(a['feed_reconnects']))}, "
                      f"errors={a['errors']}, "
                      f"stream fallbacks={a['stream_fallbacks']}")
    console.print("\n[dim]Limitations:[/dim]")
    for line in report["limitations"]:
        console.print(f"[dim]  - {escape(line)}[/dim]", soft_wrap=True)


def _kv(console, label: str, value: Any) -> None:
    from rich.markup import escape
    if isinstance(value, dict):
        body = ", ".join(f"{k}={v}" for k, v in value.items()) or "none recorded"
    else:
        body = str(value)
    console.print(f"{escape(label)}: {escape(body)}", soft_wrap=True)


def print_lifecycle(console, lifecycle: dict[str, Any]) -> None:
    from rich.markup import escape
    oid = escape(lifecycle["opportunity_id"])
    if not lifecycle["found"]:
        console.print(f"[yellow]No evaluation or execution attempt recorded for "
                      f"opportunity {oid} in this store.[/yellow]")
        return
    console.print(f"[bold]OPPORTUNITY LIFECYCLE[/bold] {oid}")
    console.print(f"evaluations recorded: {lifecycle['evaluations_recorded']}; "
                  f"execution attempts: {lifecycle['execution_attempts']}")
    ev = lifecycle.get("latest_evaluation")
    if ev:
        console.print(f"latest evaluation {escape(ev['timestamp'])}: "
                      f"status={ev['status']}, gross={ev['gross_edge']}, "
                      f"fees={ev['fees'] or UNAVAILABLE}, "
                      f"net={ev['net_profit'] or UNAVAILABLE}, "
                      f"capital={ev['capital_required'] or UNAVAILABLE}")
        if ev["unavailable_costs"]:
            console.print(f"  unavailable mandatory inputs: "
                          f"{escape(', '.join(ev['unavailable_costs']))}")
        for reason in ev["block_reasons"]:
            console.print(f"  block reason: {escape(reason)}", soft_wrap=True)
    for att in lifecycle["recent_attempts"]:
        console.print(f"attempt {escape(att['timestamp'])}: "
                      f"outcome={att['outcome']}, hedge={att['hedge_state']}, "
                      f"gap={att['time_between_legs_ms']} ms, "
                      f"unhedged={att['unhedged_remaining'] or 'n/a'}, "
                      f"recovery attempts={att['recovery_attempts']}")
        for label in ("leg_a", "leg_b"):
            leg = att[label]
            if leg:
                console.print(f"  {label}: {escape(leg['venue'])} "
                              f"{escape(leg['contract_id'])} "
                              f"status={leg['status']} filled={leg['filled']} "
                              f"avg={leg['average_price']}", soft_wrap=True)
        if att["risk_violations"]:
            console.print(f"  risk: {escape(', '.join(att['risk_violations']))}")
        if att["notes"]:
            console.print(f"  notes: {escape(att['notes'][:400])}", soft_wrap=True)
