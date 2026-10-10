#!/usr/bin/env python3
"""Low-volume, public, read-only Kalshi smoke test + evidence capture.

Only documented public GET endpoints are called. The script does not load
mode env files, use credentials, inspect accounts, call authenticated bulk
book/WS surfaces, or place orders. It probes at most 10 markets and at most
one order book. A network/TLS failure is recorded as UNAVAILABLE and never
as evidence that Kalshi or the adapter is broken/verified.

The output keeps documentation, connectivity, usable market data, and full
adapter verification separate. A successful public smoke is only a manual
promotion candidate; it never edits APIStatus or claims that authenticated
bulk/WS paths are live-verified.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prediction_arb.adapters.kalshi import KalshiAdapter  # noqa: E402
from prediction_arb.compliance.restrictions import (  # noqa: E402
    assert_endpoint_is_official,
)

MAX_MARKET_SAMPLE = 10
DOC_REVIEWED_AT = "2026-09-13"


def _shape(value: Any, depth: int = 0):
    """Describe payload structure without dumping the whole response."""
    if depth > 3:
        return "..."
    if isinstance(value, dict):
        return {k: _shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        if not value:
            return []
        return [_shape(value[0], depth + 1), f"...({len(value)} items)"]
    return type(value).__name__


def build_adapter(venues: dict[str, Any]) -> KalshiAdapter:
    """Build an adapter that is structurally incapable of using credentials."""
    k = (venues or {}).get("kalshi", {}) or {}
    if not k.get("rest_base"):
        raise ValueError(
            "config/venues.yaml has no venues.kalshi.rest_base — nothing to "
            "verify. Expected e.g. "
            "https://external-api.kalshi.com/trade-api/v2")
    bases = [k.get("rest_base"), *(k.get("rest_base_fallbacks") or [])]
    for base in bases:
        if base:
            assert_endpoint_is_official(base + "/exchange/status")
    return KalshiAdapter(
        rest_base=k.get("rest_base", ""),
        api_key_id=None,
        private_key_pem=None,
        reads_per_sec=min(float(k.get("rate_limit_reads_per_sec", 8)), 8.0),
        writes_per_sec=min(float(k.get("rate_limit_writes_per_sec", 3)), 3.0),
        allow_order_placement=False,
        ws_url=k.get("ws_url"),
        rest_base_fallbacks=list(k.get("rest_base_fallbacks") or []),
        ws_url_fallbacks=list(k.get("ws_url_fallbacks") or []))


def inspect_book_format(raw: dict[str, Any]) -> dict[str, Any]:
    """Summarize the public fixed-point order-book shape (K3)."""
    fp = raw.get("orderbook_fp")
    legacy = raw.get("orderbook")
    body = fp if isinstance(fp, dict) else (
        legacy if isinstance(legacy, dict) else {})
    yes_value = body.get("yes_dollars") or body.get("yes_dollars_fp") or []
    no_value = body.get("no_dollars") or body.get("no_dollars_fp") or []
    yes = yes_value if isinstance(yes_value, list) else []
    no = no_value if isinstance(no_value, list) else []

    def pair_ok(pair: Any) -> bool:
        return (isinstance(pair, (list, tuple)) and len(pair) >= 2
                and isinstance(pair[0], str) and isinstance(pair[1], str))

    levels = yes + no
    return {
        "envelope": "orderbook_fp" if isinstance(fp, dict) else (
            "orderbook" if isinstance(legacy, dict) else "unknown"),
        "keys_observed": sorted(body.keys()),
        "yes_bid_levels": len(yes),
        "no_bid_levels": len(no),
        "levels_are_dollar_string_pairs": bool(levels)
        and all(pair_ok(pair) for pair in levels),
        "sample_yes_bid": yes[0] if yes else None,
        "sample_no_bid": no[0] if no else None,
    }


def check_book_invariants(book) -> list[str]:
    """Return invariant failures (empty means a usable two-sided book)."""
    problems: list[str] = []
    if not book.bids:
        problems.append("no YES bids parsed")
    if not book.asks:
        problems.append("no YES asks synthesized from NO bids")
    for name, levels, descending in (
            ("bids", book.bids, True), ("asks", book.asks, False)):
        prices = [level.price for level in levels if level.price.is_finite()]
        if len(prices) != len(levels):
            problems.append(f"{name} contain a non-finite price")
        quantities = [level.quantity for level in levels]
        if any(not quantity.is_finite() for quantity in quantities):
            problems.append(f"{name} contain a non-finite quantity")
        if prices != sorted(prices, reverse=descending):
            problems.append(f"{name} are not sorted best-first: {prices[:5]}")
        for level in levels:
            if level.price.is_finite() and not (
                    Decimal("0") < level.price < Decimal("1")):
                problems.append(f"{name} price out of range: {level.price}")
            if level.quantity.is_finite() and level.quantity <= 0:
                problems.append(f"{name} non-positive size: {level.quantity}")
    bb, ba = book.best_bid(), book.best_ask()
    if (bb and ba and bb.price.is_finite() and ba.price.is_finite()
            and ba.price <= bb.price):
        problems.append(f"book is crossed: bid {bb.price} >= ask {ba.price}")
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
            "exchange was not confirmed active", "unreachable")):
        return "UNAVAILABLE"
    return "FAIL"


def _set_evidence_levels(evidence: dict[str, Any], connectivity: str,
                         usable_market_data: bool) -> None:
    evidence["evidence_levels"] = {
        "documentation": {
            "state": "DOCUMENTED",
            "as_of": DOC_REVIEWED_AT,
            "source": "docs/VENUE_RESEARCH.md K1-K5",
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


def _read_venues_config() -> dict[str, Any]:
    """Read only non-secret venue URLs; do not load .env or mode settings."""
    path = ROOT / "config" / "venues.yaml"
    with path.open("r", encoding="utf-8") as stream:
        document = yaml.safe_load(stream) or {}
    return document.get("venues", {}) or {}


async def run(args: argparse.Namespace) -> int:
    if not 1 <= args.limit <= MAX_MARKET_SAMPLE:
        raise ValueError(f"--limit must be between 1 and {MAX_MARKET_SAMPLE}")
    venues_cfg = _read_venues_config()
    adapter = build_adapter(venues_cfg)

    evidence: dict[str, Any] = {
        "venue": "kalshi",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "rest_bases_configured": adapter.rest_bases,
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

    def record(name: str, ok: bool, detail: Any, *,
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

    try:
        # Connect probes the configured public /exchange/status endpoints.
        try:
            await adapter.connect()
            connectivity = "OBSERVED"
            record("connect: a configured public status endpoint responded",
                   True, adapter.active_rest_base)
            evidence["active_rest_base"] = adapter.active_rest_base
        except Exception as exc:
            state = classify_exception(exc)
            connectivity = ("UNAVAILABLE" if state == "UNAVAILABLE"
                           else "NOT_OBSERVED")
            record("connect: a configured public status endpoint responded",
                   False, f"{type(exc).__name__}: {exc}",
                   failure_state=state)
            _set_evidence_levels(evidence, connectivity, usable_market_data)
            _write(evidence, args, failures, unavailable)
            return 1

        # Report each configured public status endpoint individually.
        for base in adapter.rest_bases:
            try:
                status = await adapter._probe_status(base)
                connectivity = "OBSERVED"
                active = bool(status.get("exchange_active"))
                record(f"GET /exchange/status on {base}", active,
                       f"exchange_active={active}",
                       failure_state="UNAVAILABLE")
            except Exception as exc:
                state = classify_exception(exc)
                record(f"GET /exchange/status on {base}", False,
                       f"{type(exc).__name__}: {exc}", failure_state=state)

        # Public market discovery: at most the validated small sample size.
        contracts = []
        try:
            contracts = await adapter.get_markets(limit=args.limit)
            record(f"GET /markets normalized {len(contracts)} contract(s)",
                   bool(contracts), ", ".join(c.contract_id
                                               for c in contracts[:3]),
                   failure_state="UNAVAILABLE")
        except Exception as exc:
            record("GET /markets", False,
                   f"{type(exc).__name__}: {exc}",
                   failure_state=classify_exception(exc))

        if contracts:
            contract = contracts[0]
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
                contract.underlying_exchange == "kalshi_dcm"
                and contract.clearing_entity == "kalshi_klear_dco")
            record("provenance is contract-qualified "
                   "(kalshi_dcm/kalshi_klear_dco)", provenance_ok,
                   f"{contract.underlying_exchange}/"
                   f"{contract.clearing_entity}")
            evidence["market_rules"] = {
                "state": "NOT_VERIFIED",
                "tick_size": evidence["normalized_sample"]["tick_size"],
                "min_order_size": evidence["normalized_sample"][
                    "min_order_size"],
                "detail": "Current adapter does not parse/source these "
                          "per-market rules; no default is treated as evidence.",
            }

        # One public book only. No credentials, account endpoints, or WS.
        ticker = args.ticker or (contracts[0].contract_id if contracts else None)
        if not ticker:
            record("public order-book check", False,
                   "no market was returned to sample",
                   failure_state="UNAVAILABLE")
        else:
            try:
                raw = await adapter._get(f"/markets/{ticker}/orderbook",
                                         params={"depth": 32})
                fmt = inspect_book_format(raw)
                evidence["observed_book_format"] = fmt
                evidence["observed_book_envelope"] = _shape(raw)
                record("book envelope is fixed-point orderbook_fp",
                       fmt["envelope"] == "orderbook_fp",
                       f"envelope={fmt['envelope']} "
                       f"keys={fmt['keys_observed']}")
                record("levels are [dollar-string, quantity-string] pairs",
                       fmt["levels_are_dollar_string_pairs"],
                       f"yes={fmt['sample_yes_bid']} "
                       f"no={fmt['sample_no_bid']}")

                book = adapter.parse_order_book(ticker, raw)
                problems = check_book_invariants(book)
                record("parsed book is two-sided, ordered, finite, "
                       "in-range, positive-size, and uncrossed",
                       not problems, problems or {
                           "best_bid": str(book.best_bid().price),
                           "best_ask": str(book.best_ask().price),
                           "bid_levels": len(book.bids),
                           "ask_levels": len(book.asks),
                       },
                       failure_state="UNAVAILABLE" if not book.bids
                       or not book.asks else "FAIL")
                evidence["parsed_book_sample"] = {
                    "bid_levels": len(book.bids),
                    "ask_levels": len(book.asks),
                    "best_bid": (str(book.best_bid().price)
                                 if book.best_bid() else None),
                    "best_ask": (str(book.best_ask().price)
                                 if book.best_ask() else None),
                    "two_sided": bool(book.bids and book.asks),
                }
                usable_market_data = (
                    fmt["envelope"] == "orderbook_fp"
                    and fmt["levels_are_dollar_string_pairs"]
                    and provenance_ok if contracts else False)
                usable_market_data = usable_market_data and not problems
            except Exception as exc:
                record(f"GET /markets/{ticker}/orderbook", False,
                       f"{type(exc).__name__}: {exc}",
                       failure_state=classify_exception(exc))
    finally:
        await adapter.disconnect()

    _set_evidence_levels(evidence, connectivity, usable_market_data)
    path = _write(evidence, args, failures, unavailable)
    result = evidence["result"]
    print()
    print(f"RESULT: {result}")
    if usable_market_data:
        print("Public market/book evidence is a manual MARKET_DATA_OBSERVED "
              "candidate only; full adapter verification is NOT_VERIFIED.")
    print(f"Evidence written to: {path}")
    return 0 if result == "PASS" else 1


def _write(evidence: dict[str, Any], args: argparse.Namespace,
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
        path = ROOT / "reports" / "evidence" / f"kalshi_{stamp}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, indent=2, sort_keys=True,
                               default=str))
    if args.json:
        print(json.dumps(evidence, indent=2, sort_keys=True, default=str))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Low-volume public Kalshi read-only smoke test; no keys, "
                    "account calls, WebSocket, or orders.")
    parser.add_argument("--limit", type=int, default=1,
                        help=f"markets to sample, 1-{MAX_MARKET_SAMPLE} "
                             "(default 1)")
    parser.add_argument("--ticker", help="fetch this public market's book")
    parser.add_argument("--out", help="evidence output path")
    parser.add_argument("--json", action="store_true",
                        help="also print the evidence JSON")
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
