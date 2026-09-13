#!/usr/bin/env python3
"""Polymarket US read-only smoke test + sanitized evidence capture.

WHAT THIS IS
    Phase 3's second exit gate says: "Exercise official public/sandbox
    reads and save sanitized evidence of the observed payload/schema; do
    not upgrade API status from documentation alone."

    This script is that gate. It performs READ-ONLY calls against the
    PUBLIC Polymarket US gateway — which needs no API key at all — and
    writes a sanitized evidence file you can paste into a review.

    It places no orders, needs no credentials, and touches no account
    data. It is safe to run from any machine that can reach the venue.

WHY YOU NEED IT
    The sandbox that wrote the adapter could not reach the venue (TLS
    EOF), so `polymarket_us` is recorded as API_DOCUMENTED_UNTESTED and
    collect mode SKIPS it. That status is deliberate: the repository
    never upgrades an API from documentation alone. Running this on your
    machine produces the observed-payload evidence needed to promote it.

HOW TO USE IT
    From the repository root:

        python scripts/polymarket_us_smoke.py

    Options:
        --limit N        how many markets to list (default 5)
        --slug SLUG      also fetch this specific market's order book
        --out PATH       where to write evidence
                         (default reports/evidence/polymarket_us_<UTC>.json)
        --json           print the evidence to stdout as well

    Exit code 0 means every check passed.

WHAT "SANITIZED" MEANS
    The evidence file records SHAPE, not content you might not want to
    share: field names actually observed, types, counts, and a few
    numeric samples (prices/quantities are market data, not secrets).
    No credentials are read, sent, or written — this script never even
    looks at your env files.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prediction_arb.adapters.polymarket_us import (  # noqa: E402
    TRADEABLE_STATE,
    PolymarketUSAdapter,
)
from prediction_arb.compliance.restrictions import (  # noqa: E402
    assert_endpoint_is_official,
)

PUBLIC_BASE = "https://gateway.polymarket.us"


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


async def run(args: argparse.Namespace) -> int:
    # §13: refuse to touch anything that is not a documented official host.
    assert_endpoint_is_official(PUBLIC_BASE + "/v1/markets")

    evidence: dict = {
        "venue": "polymarket_us",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "public_base": PUBLIC_BASE,
        "credentials_used": False,
        "checks": [],
    }
    failures: list[str] = []

    def record(name: str, ok: bool, detail) -> None:
        evidence["checks"].append(
            {"check": name, "result": "PASS" if ok else "FAIL",
             "detail": detail})
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}: {detail if isinstance(detail, str) else ''}")
        if not ok:
            failures.append(name)

    adapter = PolymarketUSAdapter(public_base=PUBLIC_BASE)
    try:
        await adapter.connect()
        record("connect (public gateway reachable, no API key)", True,
               PUBLIC_BASE)
    except Exception as exc:
        record("connect (public gateway reachable, no API key)", False,
               f"{type(exc).__name__}: {exc}")
        await adapter.disconnect()
        _write(evidence, args)
        return 1

    try:
        # ---- 1. raw markets payload shape ------------------------------
        try:
            raw = await adapter._get_public(
                "/v1/markets", params={"limit": args.limit, "active": True,
                                       "closed": False})
            markets = raw.get("markets") or []
            evidence["observed_markets_envelope"] = _shape(
                {"markets": markets[:1]})
            record(f"GET /v1/markets returned {len(markets)} market(s)",
                   bool(markets), f"{len(markets)} markets")
            if markets:
                evidence["observed_market_fields"] = sorted(markets[0].keys())
        except Exception as exc:
            record("GET /v1/markets", False, f"{type(exc).__name__}: {exc}")
            markets = []

        # ---- 2. normalization ------------------------------------------
        contracts = []
        try:
            contracts = await adapter.get_markets(limit=args.limit)
            record(f"normalized {len(contracts)} contract(s)",
                   bool(contracts),
                   ", ".join(c.market_id for c in contracts[:3]))
        except Exception as exc:
            record("normalize markets", False, f"{type(exc).__name__}: {exc}")

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
            # Provenance must be contract-qualified (Phase 3 gate 3).
            ok = (c.underlying_exchange == "qcx_dcm"
                  and c.clearing_entity == "qc_clearing")
            record("provenance is contract-qualified (qcx_dcm/qc_clearing)",
                   ok, f"{c.underlying_exchange}/{c.clearing_entity}")
            # Per-market rules must be present, never inferred (P3).
            record("per-market tick/min-qty carried from the payload",
                   c.tick_size > 0 and c.min_order_size > 0,
                   f"tick={c.tick_size} min_qty={c.min_order_size}")

        # ---- 3. order book ---------------------------------------------
        slug = args.slug or (contracts[0].market_id if contracts else None)
        if slug:
            try:
                rawbook = await adapter._get_public(f"/v1/markets/{slug}/book")
                evidence["observed_book_envelope"] = _shape(rawbook)
                md = rawbook.get("marketData") or {}
                evidence["observed_book_state"] = md.get("state")
                bids = md.get("bids") or []
                offers = md.get("offers") or []
                record(f"GET /v1/markets/{slug}/book",
                       True,
                       f"state={md.get('state')} "
                       f"bids={len(bids)} offers={len(offers)}")
                if bids:
                    evidence["observed_bid_level_sample"] = bids[0]

                book = adapter.parse_order_book(slug, rawbook)
                if md.get("state") == TRADEABLE_STATE:
                    ok = book is not None
                    detail = ("parsed" if ok else
                              "tradeable market produced no book")
                    if book is not None:
                        bb = book.best_bid()
                        ba = book.best_ask()
                        detail = (f"best_bid={bb.price if bb else None} "
                                  f"best_ask={ba.price if ba else None}")
                        evidence["parsed_book_sample"] = {
                            "bid_levels": len(book.bids),
                            "ask_levels": len(book.asks),
                            "best_bid": str(bb.price) if bb else None,
                            "best_ask": str(ba.price) if ba else None,
                            "two_sided": bool(book.bids and book.asks),
                        }
                        # A genuine CLOB: both sides are real resting orders.
                        if bb and ba and ba.price <= bb.price:
                            record("book is not crossed", False,
                                   f"ask {ba.price} <= bid {bb.price}")
                    record("parse tradeable order book", ok, detail)
                else:
                    record("non-tradeable market correctly yields no book",
                           book is None, f"state={md.get('state')}")
            except Exception as exc:
                record(f"GET /v1/markets/{slug}/book", False,
                       f"{type(exc).__name__}: {exc}")
    finally:
        await adapter.disconnect()

    evidence["result"] = "PASS" if not failures else "FAIL"
    evidence["failed_checks"] = failures
    path = _write(evidence, args)

    print()
    if failures:
        print(f"RESULT: FAIL ({len(failures)} check(s) failed)")
    else:
        print("RESULT: PASS — all read-only checks succeeded.")
    print(f"Evidence written to: {path}")
    print()
    print("Next step: paste this file into the Phase 3 record in "
          "docs/ROADMAP.md so `polymarket_us` can be promoted from "
          "API_DOCUMENTED_UNTESTED to VERIFIED with observed-payload "
          "evidence. Until then collect mode intentionally skips it.")
    return 1 if failures else 0


def _write(evidence: dict, args: argparse.Namespace) -> Path:
    if args.out:
        path = Path(args.out)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = ROOT / "reports" / "evidence" / f"polymarket_us_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
    if args.json:
        print(json.dumps(evidence, indent=2, sort_keys=True))
    return path


def main() -> int:
    p = argparse.ArgumentParser(
        description="Read-only Polymarket US smoke test (no credentials, "
                    "no orders).")
    p.add_argument("--limit", type=int, default=5,
                   help="how many markets to list (default 5)")
    p.add_argument("--slug", help="also fetch this market's order book")
    p.add_argument("--out", help="evidence output path")
    p.add_argument("--json", action="store_true",
                   help="also print the evidence to stdout")
    args = p.parse_args()
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
