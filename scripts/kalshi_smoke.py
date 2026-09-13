#!/usr/bin/env python3
"""Kalshi read-only smoke test + sanitized evidence capture (Phase 2 exit).

WHAT THIS IS
    Phase 2's exit criterion is: "a collect run with no unhandled 5xx; a
    verification script the owner can run to confirm both hosts and the
    book format."

    This script is that verification script. It performs READ-ONLY calls
    against Kalshi's documented public REST endpoints, confirms the
    recommended `external-api` host and the legacy `api.elections`
    fallback, re-checks the `orderbook_fp` book format the adapter parses,
    and writes sanitized evidence you can paste into a review.

    It places no orders and reads no account data unless you have a key
    configured. With a key it additionally verifies the authenticated
    surfaces Phase 2 depends on: the account rate-limit tier, endpoint
    costs, bulk order books, and (with --with-stream) the WebSocket.

WHY YOU NEED IT
    The sandbox that wrote the Phase 2 code could not reach Kalshi (TLS
    EOF — evidence row K9, UNAVAILABLE / LIVE). Every Phase 2 claim is
    therefore documentation-verified, not live-tested. Running this on a
    machine that can reach the venue is what turns K9 from UNAVAILABLE
    into observed evidence.

HOW TO USE IT
    From the repository root:

        python scripts/kalshi_smoke.py

    Options:
        --limit N            how many markets to list (default 5)
        --ticker TICKER      fetch this specific market's book
        --with-stream SECS   with a key, subscribe one book for SECS
                             seconds and report sequencing counters
        --out PATH           where to write evidence
                             (default reports/evidence/kalshi_<UTC>.json)
        --json               print the evidence to stdout as well

    Exit code 0 means every hard check passed. Soft checks (the legacy
    fallback host, optional authenticated surfaces) are recorded but do
    not fail the run — a decommissioned legacy host or a missing API key
    is not a defect.

WHAT "SANITIZED" MEANS
    The evidence file records SHAPE, not content you might not want to
    share: field names actually observed, types, counts, and a few
    numeric samples (prices/quantities are public market data). It records
    whether credentials were *present*, never their values — no key ID, no
    private key, no header. This script only ever issues GETs and never
    constructs an adapter with order placement enabled.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prediction_arb.adapters.kalshi import KalshiAdapter  # noqa: E402
from prediction_arb.compliance.restrictions import (  # noqa: E402
    assert_endpoint_is_official,
)
from prediction_arb.core.config import load_config  # noqa: E402

from decimal import Decimal  # noqa: E402


def _shape(value, depth: int = 0):
    """Describe a payload's structure without dumping everything."""
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return []
        return [_shape(value[0], depth + 1), f"...({len(value)} items)"]
    return type(value).__name__


def build_adapter(venues: dict[str, Any], env: dict[str, str]) -> KalshiAdapter:
    """Construct the adapter exactly as collect/paper mode would.

    Order placement is deliberately never enabled here: this is a
    read-only verification script, and the only place that flag may be set
    is live mode in ``main.py``.
    """
    k = (venues or {}).get("kalshi", {}) or {}
    if not k.get("rest_base"):
        raise ValueError(
            "config/venues.yaml has no venues.kalshi.rest_base — nothing to "
            "verify. Expected e.g. "
            "https://external-api.kalshi.com/trade-api/v2")
    for base in [k.get("rest_base"), *(k.get("rest_base_fallbacks") or [])]:
        if base:
            assert_endpoint_is_official(base + "/exchange/status")
    return KalshiAdapter(
        rest_base=k.get("rest_base", ""),
        api_key_id=env.get("KALSHI_API_KEY_ID"),
        private_key_pem=env.get("KALSHI_PRIVATE_KEY_PEM"),
        reads_per_sec=float(k.get("rate_limit_reads_per_sec", 8)),
        writes_per_sec=float(k.get("rate_limit_writes_per_sec", 3)),
        ws_url=k.get("ws_url"),
        rest_base_fallbacks=list(k.get("rest_base_fallbacks") or []),
        ws_url_fallbacks=list(k.get("ws_url_fallbacks") or []))


def inspect_book_format(raw: dict[str, Any]) -> dict[str, Any]:
    """What the observed order-book payload actually looks like (K3).

    The adapter expects fixed-point dollar strings under ``orderbook_fp``
    with ``yes_dollars`` / ``no_dollars`` lists of ``[price, qty]`` pairs,
    both sides being BIDS. Anything else is documentation drift and is
    reported rather than silently accepted.
    """
    fp = raw.get("orderbook_fp")
    legacy = raw.get("orderbook")
    body = fp if isinstance(fp, dict) else (legacy if isinstance(legacy, dict)
                                            else {})
    yes = body.get("yes_dollars") or body.get("yes_dollars_fp") or []
    no = body.get("no_dollars") or body.get("no_dollars_fp") or []

    def pair_ok(pair: Any) -> bool:
        return (isinstance(pair, (list, tuple)) and len(pair) >= 2
                and isinstance(pair[0], str) and isinstance(pair[1], str))

    return {
        "envelope": "orderbook_fp" if isinstance(fp, dict) else (
            "orderbook" if isinstance(legacy, dict) else "unknown"),
        "keys_observed": sorted(body.keys()),
        "yes_bid_levels": len(yes) if isinstance(yes, list) else 0,
        "no_bid_levels": len(no) if isinstance(no, list) else 0,
        "levels_are_dollar_string_pairs": bool(
            (yes or no)
            and all(pair_ok(p) for p in list(yes) + list(no))),
        "sample_yes_bid": yes[0] if yes else None,
        "sample_no_bid": no[0] if no else None,
    }


def check_book_invariants(book) -> list[str]:
    """Return a list of problems with a parsed book (empty = all good).

    These are the properties the arbitrage engine relies on, so a smoke
    test that cannot confirm them has not verified the book format.
    """
    problems: list[str] = []
    if not book.bids:
        problems.append("no YES bids parsed")
    if not book.asks:
        problems.append("no YES asks synthesized from NO bids")
    for name, levels, descending in (
            ("bids", book.bids, True), ("asks", book.asks, False)):
        prices = [l.price for l in levels]
        if prices != sorted(prices, reverse=descending):
            problems.append(f"{name} are not sorted best-first: {prices[:5]}")
        for level in levels:
            if not (Decimal("0") < level.price < Decimal("1")):
                problems.append(f"{name} price out of range: {level.price}")
            if level.quantity <= 0:
                problems.append(f"{name} non-positive size: {level.quantity}")
    bb, ba = book.best_bid(), book.best_ask()
    if bb and ba and ba.price <= bb.price:
        problems.append(f"book is crossed: bid {bb.price} >= ask {ba.price}")
    return problems


def rate_limit_report(adapter: KalshiAdapter) -> dict[str, Any]:
    """The discovered tier next to the budgets the adapter now uses (K5).

    §13: budgets may only ever be lowered, never raised above the
    discovered tier.
    """
    limits = adapter.rate_limits
    return {
        "usage_tier": limits.usage_tier if limits else None,
        "read_refill_rate": limits.read_refill_rate if limits else None,
        "write_refill_rate": limits.write_refill_rate if limits else None,
        "effective_reads_per_sec": adapter.effective_reads_per_sec(),
        "effective_writes_per_sec": adapter.effective_writes_per_sec(),
        "configured_reads_per_sec": adapter._configured_reads_per_sec,
        "default_endpoint_cost": adapter.default_endpoint_cost,
        "non_default_endpoint_costs": len(adapter.endpoint_costs),
    }


async def run(args: argparse.Namespace) -> int:
    config = load_config("paper")   # never live: this script cannot trade
    # config.venues is the raw venues.yaml document; per-venue entries live
    # under its `venues:` key (the same unwrap main.py:build_adapters does).
    venues_cfg = (config.venues.get("venues", {}) or {})
    adapter = build_adapter(venues_cfg, config.env)
    for base in adapter.rest_bases:
        assert_endpoint_is_official(base + "/exchange/status")

    evidence: dict[str, Any] = {
        "venue": "kalshi",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "rest_bases_configured": adapter.rest_bases,
        "ws_urls_configured": adapter.ws_urls,
        "credentials_present": adapter.has_credentials,
        "credentials_used": False,
        "orders_placed": 0,
        "checks": [],
    }
    failures: list[str] = []
    soft_failures: list[str] = []

    def record(name: str, ok: bool, detail: Any, hard: bool = True) -> None:
        evidence["checks"].append({
            "check": name,
            "result": "PASS" if ok else ("FAIL" if hard else "SOFT-FAIL"),
            "detail": detail,
        })
        tag = "PASS" if ok else ("FAIL" if hard else "SOFT")
        shown = detail if isinstance(detail, str) else json.dumps(
            detail, default=str)
        print(f"[{tag}] {name}: {shown}")
        if not ok:
            (failures if hard else soft_failures).append(name)

    try:
        # ---- 1. recommended host answers and the exchange is active -----
        try:
            await adapter.connect()
            record("connect: recommended host reachable, exchange active",
                   True, adapter.active_rest_base)
            evidence["active_rest_base"] = adapter.active_rest_base
        except Exception as exc:
            record("connect: recommended host reachable, exchange active",
                   False, f"{type(exc).__name__}: {exc}")
            _write(evidence, args, failures, soft_failures)
            return 1

        # ---- 2. every configured base, probed individually --------------
        for base in adapter.rest_bases:
            try:
                status = await adapter._probe_status(base)
                active = bool(status.get("exchange_active"))
                record(f"GET /exchange/status on {base}", active,
                       f"exchange_active={active}",
                       # The recommended host is a hard requirement; the
                       # legacy compatibility host is not.
                       hard=(base == adapter.rest_bases[0]))
            except Exception as exc:
                record(f"GET /exchange/status on {base}", False,
                       f"{type(exc).__name__}: {exc}",
                       hard=(base == adapter.rest_bases[0]))

        # ---- 3. public market discovery + normalization -----------------
        tickers: list[str] = []
        try:
            contracts = await adapter.get_markets(limit=args.limit)
            tickers = [c.contract_id for c in contracts]
            record(f"GET /markets normalized {len(contracts)} contract(s)",
                   bool(contracts), ", ".join(tickers[:3]))
            if contracts:
                c = contracts[0]
                evidence["normalized_sample"] = {
                    "market_id": c.market_id,
                    "event_description": c.event_description[:120],
                    "underlying_exchange": c.underlying_exchange,
                    "clearing_entity": c.clearing_entity,
                    "category": c.category,
                    "settlement_time": (c.settlement_time.isoformat()
                                        if c.settlement_time else None),
                    "tick_size": str(c.tick_size),
                    "min_order_size": str(c.min_order_size),
                    "payout": str(c.payout),
                }
                record("provenance is contract-qualified "
                       "(kalshi_dcm/kalshi_klearo)",
                       c.underlying_exchange == "kalshi_dcm",
                       f"{c.underlying_exchange}/{c.clearing_entity}")
        except Exception as exc:
            record("GET /markets", False, f"{type(exc).__name__}: {exc}")

        # ---- 4. book format (the heart of K3) ---------------------------
        ticker = args.ticker or (tickers[0] if tickers else None)
        if ticker:
            try:
                raw = await adapter._get(f"/markets/{ticker}/orderbook",
                                         params={"depth": 32})
                fmt = inspect_book_format(raw)
                evidence["observed_book_format"] = fmt
                evidence["observed_book_envelope"] = _shape(raw)
                record("book envelope is fixed-point orderbook_fp",
                       fmt["envelope"] == "orderbook_fp",
                       f"envelope={fmt['envelope']} keys={fmt['keys_observed']}")
                record("levels are [dollar-string, quantity-string] pairs",
                       fmt["levels_are_dollar_string_pairs"],
                       f"yes={fmt['sample_yes_bid']} no={fmt['sample_no_bid']}")

                book = adapter.parse_order_book(ticker, raw)
                problems = check_book_invariants(book)
                record("parsed book satisfies engine invariants",
                       not problems, problems or (
                           f"best_bid={book.best_bid().price} "
                           f"best_ask={book.best_ask().price} "
                           f"bids={len(book.bids)} asks={len(book.asks)}"))
                evidence["parsed_book_sample"] = {
                    "bid_levels": len(book.bids),
                    "ask_levels": len(book.asks),
                    "best_bid": str(book.best_bid().price)
                    if book.best_bid() else None,
                    "best_ask": str(book.best_ask().price)
                    if book.best_ask() else None,
                    # YES asks are synthesized as 1 - NO bid; both sides
                    # present means the payload carried both bid ladders.
                    "both_bid_ladders_present": bool(book.bids and book.asks),
                }
            except Exception as exc:
                record(f"GET /markets/{ticker}/orderbook", False,
                       f"{type(exc).__name__}: {exc}")
        else:
            record("book format check (no ticker available)", False,
                   "no market to sample")

        # ---- 5. authenticated surfaces (only with a key) ----------------
        if adapter.has_credentials:
            evidence["credentials_used"] = True
            try:
                limits = await adapter.get_rate_limits()
                adapter.apply_rate_limits(limits, headroom=0.8)
                report = rate_limit_report(adapter)
                evidence["rate_limits"] = report
                clamped = (report["effective_reads_per_sec"]
                           <= report["configured_reads_per_sec"])
                record("GET /account/limits: tier discovered and local "
                       "budget never raised above it", clamped, report)
            except Exception as exc:
                record("GET /account/limits", False,
                       f"{type(exc).__name__}: {exc}", hard=False)

            try:
                costs = await adapter.get_endpoint_costs()
                record("GET /account/endpoint_costs",
                       True,
                       f"default_cost={adapter.default_endpoint_cost} "
                       f"non_default={len(costs)}")
            except Exception as exc:
                record("GET /account/endpoint_costs", False,
                       f"{type(exc).__name__}: {exc}", hard=False)

            if tickers:
                try:
                    bulk = await adapter.get_order_books(tickers)
                    record(f"GET /markets/orderbooks returned "
                           f"{len(bulk)} book(s) for {len(tickers)} ticker(s)",
                           bool(bulk), ", ".join(sorted(bulk)[:3]))
                    overlap = [t for t in tickers if t in bulk]
                    if overlap and ticker in bulk:
                        single = await adapter.get_order_book(ticker)
                        b_bulk, b_single = bulk[ticker], single
                        same = (
                            b_single is not None
                            and str(b_bulk.best_bid().price
                                    if b_bulk.best_bid() else None)
                            == str(b_single.best_bid().price
                                   if b_single.best_bid() else None))
                        record("bulk book agrees with the per-ticker book",
                               same,
                               f"bulk={b_bulk.best_bid().price if b_bulk.best_bid() else None} "
                               f"single={b_single.best_bid().price if b_single.best_bid() else None}")
                except Exception as exc:
                    record("GET /markets/orderbooks", False,
                           f"{type(exc).__name__}: {exc}", hard=False)

            if args.with_stream and ticker:
                seen: list = []

                async def on_book(book) -> None:
                    seen.append(book)

                try:
                    await adapter.subscribe_order_book(ticker, on_book)
                    await asyncio.sleep(float(args.with_stream))
                    stats = adapter.streaming_stats()
                    evidence["streaming"] = {
                        "seconds_observed": args.with_stream,
                        "books_received": len(seen),
                        "stats": stats.get("stats"),
                    }
                    counters = stats.get("stats") or {}
                    record("WebSocket snapshot received and sequencing "
                           "counters exposed",
                           len(seen) > 0,
                           f"books={len(seen)} "
                           f"snapshots={counters.get('snapshots')} "
                           f"deltas={counters.get('deltas_applied')} "
                           f"gaps={counters.get('sequence_gaps')} "
                           f"reconnects={counters.get('reconnects')}")
                except Exception as exc:
                    record("WebSocket order-book stream", False,
                           f"{type(exc).__name__}: {exc}", hard=False)
        else:
            record("authenticated surfaces (no API key configured)", True,
                   "skipped — public checks only; add a key to .env.paper "
                   "to verify tier discovery, bulk books and the WebSocket",
                   hard=False)
    finally:
        await adapter.disconnect()

    path = _write(evidence, args, failures, soft_failures)

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)} hard check(s) failed)")
    elif soft_failures:
        print("RESULT: PASS with soft failures: "
              + ", ".join(soft_failures))
    else:
        print("RESULT: PASS — all hard read-only checks succeeded.")
    print(f"Evidence written to: {path}")
    print()
    print("Next step: paste the evidence file into the Phase 2 section of "
          "docs/ROADMAP.md so evidence row K9 can move from UNAVAILABLE to "
          "observed. Then run a 24-hour `--mode collect` to close the "
          "remaining Phase 2 exit criterion.")
    return 1 if failures else 0


def _write(evidence: dict, args: argparse.Namespace,
           failures: list[str], soft_failures: list[str]) -> Path:
    evidence["result"] = "FAIL" if failures else (
        "PASS_WITH_SOFT_FAILURES" if soft_failures else "PASS")
    evidence["failed_checks"] = failures
    evidence["soft_failed_checks"] = soft_failures
    if args.out:
        path = Path(args.out)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = ROOT / "reports" / "evidence" / f"kalshi_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True,
                               default=str))
    if args.json:
        print(json.dumps(evidence, indent=2, sort_keys=True, default=str))
    return path


def main() -> int:
    p = argparse.ArgumentParser(
        description="Read-only Kalshi smoke test: hosts, book format, and "
                    "(with a key) tier/bulk/WebSocket verification. "
                    "Never places an order.")
    p.add_argument("--limit", type=int, default=5,
                   help="how many markets to list (default 5)")
    p.add_argument("--ticker", help="fetch this specific market's book")
    p.add_argument("--with-stream", type=int, default=0, metavar="SECONDS",
                   help="subscribe one order book for N seconds (needs a key)")
    p.add_argument("--out", help="evidence output path")
    p.add_argument("--json", action="store_true",
                   help="also print the evidence to stdout")
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except (ValueError, PermissionError) as exc:
        # Configuration problems are the operator's to fix; say so plainly
        # instead of dumping a traceback.
        print(f"CONFIGURATION PROBLEM: {exc}")
        return 2
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
