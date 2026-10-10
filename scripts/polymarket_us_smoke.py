#!/usr/bin/env python3
"""Low-volume, public, read-only Polymarket US smoke test.

Only documented public GET endpoints are used. No credentials are loaded
or sent, no account surfaces or authenticated WebSockets are called, and
no orders are placed. The script samples at most 10 markets and one book.
Network/TLS failures are recorded as UNAVAILABLE, not as adapter defects.

Documentation, connectivity, usable market data, and full adapter
verification remain separate evidence layers. A successful public smoke
is only a manual promotion candidate; it never changes APIStatus.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

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
MAX_MARKET_SAMPLE = 10
DOC_REVIEWED_AT = "2026-09-13"


def _shape(value, depth: int = 0):
    """Describe payload structure without dumping the entire response."""
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return []
        return [_shape(value[0], depth + 1), f"...({len(value)} items)"]
    return type(value).__name__


def check_book_invariants(book) -> list[str]:
    """Return failures for a usable, two-sided, uncrossed CLOB snapshot."""
    problems: list[str] = []
    if not book.bids:
        problems.append("no bid levels parsed")
    if not book.asks:
        problems.append("no offer levels parsed")
    for name, levels, descending in (
            ("bids", book.bids, True), ("offers", book.asks, False)):
        prices: list[Decimal] = []
        for index, level in enumerate(levels):
            if not level.price.is_finite():
                problems.append(f"{name}[{index}] price is non-finite")
                continue
            if not level.quantity.is_finite():
                problems.append(f"{name}[{index}] quantity is non-finite")
                continue
            if not Decimal(0) < level.price < Decimal(1):
                problems.append(f"{name}[{index}] price out of range: "
                                f"{level.price}")
            if level.quantity <= 0:
                problems.append(f"{name}[{index}] quantity is non-positive: "
                                f"{level.quantity}")
            prices.append(level.price)
        if prices != sorted(prices, reverse=descending):
            problems.append(f"{name} are not sorted best-first")
    bb, ba = book.best_bid(), book.best_ask()
    if (bb and ba and bb.price.is_finite() and ba.price.is_finite()
            and ba.price <= bb.price):
        problems.append(f"book is crossed: ask {ba.price} <= bid {bb.price}")
    return problems


def classify_exception(exc: BaseException) -> str:
    """Classify transport/service unavailability separately from bad data."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, httpx.HTTPStatusError):
            status = current.response.status_code
            return ("UNAVAILABLE" if status == 429 or status >= 500
                    else "FAIL")
        if isinstance(current, (httpx.TransportError, TimeoutError,
                                ConnectionError)):
            return "UNAVAILABLE"
        current = current.__cause__

    message = str(exc).lower()
    if any(marker in message for marker in (
            "tls", "ssl", "eof", "dns", "timed out", "timeout",
            "connection refused", "connection reset", "network is unreachable",
            "unreachable", "temporarily unavailable")):
        return "UNAVAILABLE"
    return "FAIL"


def _set_evidence_levels(evidence: dict, connectivity: str,
                         usable_market_data: bool) -> None:
    evidence["evidence_levels"] = {
        "documentation": {
            "state": "DOCUMENTED",
            "as_of": DOC_REVIEWED_AT,
            "source": "docs/VENUE_RESEARCH.md P1-P6",
        },
        "connectivity": {"state": connectivity},
        "usable_market_data": {
            "state": "OBSERVED" if usable_market_data else "NOT_OBSERVED",
        },
        "full_adapter_verification": {"state": "NOT_VERIFIED"},
    }
    evidence["promotion_candidate"] = (
        "MARKET_DATA_OBSERVED" if usable_market_data else "NONE")
    evidence["promotion_requires_manual_review"] = True


def _rules_ok(contract) -> bool:
    tick, minimum = contract.tick_size, contract.min_order_size
    return bool(
        tick is not None and tick.is_finite() and Decimal(0) < tick < Decimal(1)
        and minimum is not None and minimum.is_finite() and minimum > 0)


async def run(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= MAX_MARKET_SAMPLE:
        raise ValueError(f"--limit must be between 1 and {MAX_MARKET_SAMPLE}")
    assert_endpoint_is_official(PUBLIC_BASE + "/v1/markets")

    evidence: dict = {
        "venue": "polymarket_us",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "public_base": PUBLIC_BASE,
        "scope": "public read-only GETs only",
        "credentials_loaded": False,
        "credentials_used": False,
        "orders_placed": 0,
        "market_limit": args.limit,
        "checks": [],
    }
    failures: list[str] = []
    unavailable: list[str] = []
    connectivity = "NOT_OBSERVED"
    usable_market_data = False

    def record(name: str, ok: bool, detail, *,
               failure_state: str = "FAIL") -> None:
        result = "PASS" if ok else failure_state
        evidence["checks"].append({
            "check": name, "result": result, "detail": detail,
        })
        shown = detail if isinstance(detail, str) else json.dumps(
            detail, default=str)
        print(f"[{result}] {name}: {shown}")
        if not ok:
            (unavailable if result == "UNAVAILABLE" else failures).append(name)

    adapter = PolymarketUSAdapter(public_base=PUBLIC_BASE,
                                  reads_per_sec=8.0,
                                  allow_order_placement=False)
    try:
        try:
            await adapter.connect()
            connectivity = "OBSERVED"
            record("connect: public gateway reachability", True,
                   PUBLIC_BASE)
        except Exception as exc:
            state = classify_exception(exc)
            connectivity = ("UNAVAILABLE" if state == "UNAVAILABLE"
                           else "NOT_OBSERVED")
            record("connect: public gateway reachability", False,
                   f"{type(exc).__name__}: {exc}", failure_state=state)
            _set_evidence_levels(evidence, connectivity, usable_market_data)
            _write(evidence, args, failures, unavailable)
            return 1

        markets_raw: list[dict] = []
        try:
            raw = await adapter._get_public(
                "/v1/markets", params={"limit": args.limit, "active": True,
                                       "closed": False})
            raw_value = raw.get("markets") or []
            markets_raw = (raw_value if isinstance(raw_value, list) else [])
            evidence["observed_markets_envelope"] = _shape(
                {"markets": markets_raw[:1]})
            if markets_raw:
                evidence["observed_market_fields"] = sorted(
                    markets_raw[0].keys())
            record(f"GET /v1/markets returned {len(markets_raw)} market(s)",
                   bool(markets_raw), f"{len(markets_raw)} markets",
                   failure_state="UNAVAILABLE")
        except Exception as exc:
            record("GET /v1/markets raw response", False,
                   f"{type(exc).__name__}: {exc}",
                   failure_state=classify_exception(exc))

        contracts = []
        try:
            # Normalize the already-observed public payload instead of
            # issuing a duplicate GET /v1/markets just for the smoke test.
            contracts = [c for c in
                         (adapter._normalize_market(m) for m in markets_raw)
                         if c is not None]
            contracts = contracts[:args.limit]
            record(f"normalized {len(contracts)} observed contract(s)",
                   bool(contracts),
                   ", ".join(c.market_id for c in contracts[:3]),
                   failure_state=("UNAVAILABLE" if not markets_raw else "FAIL"))
        except Exception as exc:
            record("normalize observed markets", False,
                   f"{type(exc).__name__}: {exc}",
                   failure_state=classify_exception(exc))

        slug = args.slug or (contracts[0].market_id if contracts else None)
        contract = next((c for c in contracts if c.market_id == slug), None)
        if args.slug and contract is None:
            try:
                contract = await adapter.get_market(args.slug)
            except Exception as exc:
                record("GET /v1/market/slug/<slug>", False,
                       f"{type(exc).__name__}: {exc}",
                       failure_state=classify_exception(exc))

        provenance_ok = False
        rules_ok = False
        if contract is not None:
            evidence["normalized_sample"] = {
                "market_id": contract.market_id,
                "event_description": contract.event_description[:120],
                "underlying_exchange": contract.underlying_exchange,
                "clearing_entity": contract.clearing_entity,
                "category": contract.category,
                "settlement_time": (contract.settlement_time.isoformat()
                                    if contract.settlement_time else None),
                "tick_size": (str(contract.tick_size)
                              if contract.tick_size is not None else None),
                "min_order_size": (str(contract.min_order_size)
                                   if contract.min_order_size is not None
                                   else None),
                "payout": str(contract.payout),
            }
            provenance_ok = (
                contract.underlying_exchange == "qcx_dcm"
                and contract.clearing_entity == "qc_clearing")
            record("provenance is contract-qualified (qcx_dcm/qc_clearing)",
                   provenance_ok,
                   f"{contract.underlying_exchange}/"
                   f"{contract.clearing_entity}")
            rules_ok = _rules_ok(contract)
            record("positive finite per-market tick/minimum quantity are "
                   "carried from the payload", rules_ok,
                   f"tick={contract.tick_size} "
                   f"min_qty={contract.min_order_size}")
        elif args.slug:
            record("selected market is present and normalizable", False,
                   f"no market contract observed for {args.slug}",
                   failure_state="UNAVAILABLE")

        if not slug:
            record("public book check", False,
                   "no market slug was available to sample",
                   failure_state="UNAVAILABLE")
        else:
            try:
                rawbook = await adapter._get_public(
                    f"/v1/markets/{slug}/book")
                evidence["observed_book_envelope"] = _shape(rawbook)
                md = rawbook.get("marketData") or rawbook.get("market_data") or {}
                state = md.get("state") if isinstance(md, dict) else None
                if state is None and isinstance(md, dict):
                    state = md.get("marketState")
                evidence["observed_book_state"] = state
                bids = (md.get("bids") or []) if isinstance(md, dict) else []
                offers = ((md.get("offers") or md.get("asks") or [])
                          if isinstance(md, dict) else [])
                record("book response contains an explicit market state",
                       isinstance(state, str) and bool(state),
                       f"state={state!r}")

                book = adapter.parse_order_book(slug, rawbook)
                if state != TRADEABLE_STATE:
                    rejected = book is None
                    record("non-tradeable or unknown state is fail-closed",
                           rejected,
                           f"state={state!r}; book_returned={book is not None}")
                    if rejected:
                        unavailable.append(
                            "no open tradeable two-sided book observed")
                elif book is None:
                    record("open-state book parses", False,
                           "open market state produced no parsed book")
                else:
                    problems = check_book_invariants(book)
                    record("open-state book is two-sided, ordered, finite, "
                           "in-range, positive-size, and uncrossed",
                           not problems,
                           problems or {
                               "best_bid": str(book.best_bid().price),
                               "best_offer": str(book.best_ask().price),
                               "bid_levels": len(book.bids),
                               "offer_levels": len(book.asks),
                           },
                           failure_state=("UNAVAILABLE" if
                                          not book.bids or not book.asks
                                          else "FAIL"))
                    evidence["parsed_book_sample"] = {
                        "bid_levels": len(book.bids),
                        "offer_levels": len(book.asks),
                        "best_bid": (str(book.best_bid().price)
                                     if book.best_bid() else None),
                        "best_offer": (str(book.best_ask().price)
                                       if book.best_ask() else None),
                        "two_sided": bool(book.bids and book.asks),
                    }
                    usable_market_data = bool(
                        contract is not None and provenance_ok and rules_ok
                        and not problems)
            except Exception as exc:
                record(f"GET /v1/markets/{slug}/book", False,
                       f"{type(exc).__name__}: {exc}",
                       failure_state=classify_exception(exc))
    finally:
        await adapter.disconnect()

    _set_evidence_levels(evidence, connectivity, usable_market_data)
    path = _write(evidence, args, failures, unavailable)
    print()
    print(f"RESULT: {evidence['result']}")
    if usable_market_data:
        print("Public market/book/rule evidence is a manual "
              "MARKET_DATA_OBSERVED candidate only; full adapter "
              "verification is NOT_VERIFIED.")
    print(f"Evidence written to: {path}")
    return 0 if evidence["result"] == "PASS" else 1


def _write(evidence: dict, args: argparse.Namespace,
           failures: list[str], unavailable: list[str]) -> Path:
    usable = (evidence.get("evidence_levels", {})
              .get("usable_market_data", {}).get("state") == "OBSERVED")
    evidence["result"] = ("FAIL" if failures else
                          "UNAVAILABLE" if unavailable or not usable else
                          "PASS")
    evidence["failed_checks"] = failures
    evidence["unavailable_checks"] = unavailable
    evidence["unavailable_is_not_adapter_failure"] = True
    if args.out:
        path = Path(args.out)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = ROOT / "reports" / "evidence" / f"polymarket_us_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True,
                               default=str))
    if args.json:
        print(json.dumps(evidence, indent=2, sort_keys=True, default=str))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Low-volume public Polymarket US read-only smoke test; "
                    "no credentials, account calls, authenticated WS, or orders.")
    parser.add_argument("--limit", type=int, default=1,
                        help=f"markets to sample, 1-{MAX_MARKET_SAMPLE} "
                             "(default 1)")
    parser.add_argument("--slug", help="also fetch this public market's book")
    parser.add_argument("--out", help="evidence output path")
    parser.add_argument("--json", action="store_true",
                        help="also print evidence JSON")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args))
    except (ValueError, PermissionError) as exc:
        print(f"CONFIGURATION PROBLEM: {exc}")
        return 2
    except KeyboardInterrupt:
        print("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
