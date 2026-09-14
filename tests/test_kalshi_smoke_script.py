"""Offline tests for scripts/kalshi_smoke.py — the Phase 2 exit artifact.

The script's network path cannot run in this sandbox (Kalshi is
TLS-unreachable here — evidence row K9, UNAVAILABLE / LIVE), so these tests
exercise its decision functions and its safety posture instead:

* it detects the documented ``orderbook_fp`` book format and flags drift;
* it checks the invariants the arbitrage engine relies on;
* it constructs a read-only adapter that can never place an order;
* it refuses any host that is not on the official-host ledger (§13);
* the rate-limit report can only ever show a lowered budget (§13).
"""
from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from prediction_arb.adapters.kalshi import KalshiAdapter, KalshiRateLimits
from prediction_arb.core.types import MicrostructureType, OrderBook, PriceLevel

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "kalshi_smoke.py"
    spec = importlib.util.spec_from_file_location("kalshi_smoke_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_script()


def documented_book_payload() -> dict:
    """The fixed-point shape documented in K3 (both sides are bids)."""
    return {
        "orderbook_fp": {
            "yes_dollars": [["0.4500", "120"], ["0.4400", "80"]],
            "no_dollars": [["0.5200", "60"], ["0.5100", "40"]],
        },
        "ticker": "KXTEST-26SEP-T4",
    }


class TestBookFormatDetection:
    def test_documented_envelope_is_recognised(self):
        fmt = smoke.inspect_book_format(documented_book_payload())
        assert fmt["envelope"] == "orderbook_fp"
        assert fmt["levels_are_dollar_string_pairs"] is True
        assert fmt["yes_bid_levels"] == 2
        assert fmt["no_bid_levels"] == 2
        assert fmt["sample_yes_bid"] == ["0.4500", "120"]

    def test_legacy_envelope_is_reported_as_drift(self):
        payload = documented_book_payload()
        payload["orderbook"] = payload.pop("orderbook_fp")
        assert smoke.inspect_book_format(payload)["envelope"] == "orderbook"

    def test_non_dollar_string_levels_are_flagged(self):
        # Integer cents would be a silent format change; the parser drops
        # such levels, so the smoke test must say so rather than pass.
        payload = {"orderbook_fp": {"yes_dollars": [[45, 120]],
                                    "no_dollars": [[52, 60]]}}
        fmt = smoke.inspect_book_format(payload)
        assert fmt["levels_are_dollar_string_pairs"] is False

    def test_websocket_fp_key_variant_is_accepted(self):
        payload = {"orderbook_fp": {"yes_dollars_fp": [["0.45", "10"]],
                                    "no_dollars_fp": [["0.52", "5"]]}}
        fmt = smoke.inspect_book_format(payload)
        assert fmt["yes_bid_levels"] == 1
        assert fmt["no_bid_levels"] == 1


class TestBookInvariants:
    def _book(self, bids, asks):
        return OrderBook(
            venue="kalshi", contract_id="T",
            bids=[PriceLevel(Decimal(p), Decimal(q)) for p, q in bids],
            asks=[PriceLevel(Decimal(p), Decimal(q)) for p, q in asks],
            timestamp=None, microstructure_type=MicrostructureType.CLOB)

    def test_clean_book_passes(self):
        book = KalshiAdapter(rest_base="https://external-api.kalshi.com/"
                                       "trade-api/v2"
                             ).parse_order_book("T", documented_book_payload())
        # YES bids descending, synthesized YES asks ascending from NO bids.
        assert [str(l.price) for l in book.asks] == ["0.4800", "0.4900"]
        assert smoke.check_book_invariants(book) == []

    def test_crossed_book_is_reported(self):
        book = self._book([("0.55", "10")], [("0.50", "10")])
        problems = smoke.check_book_invariants(book)
        assert any("crossed" in p for p in problems)

    def test_missing_side_is_reported(self):
        problems = smoke.check_book_invariants(self._book([("0.40", "5")], []))
        assert any("no YES asks" in p for p in problems)

    def test_unsorted_and_out_of_range_levels_are_reported(self):
        book = self._book([("0.40", "5"), ("0.45", "5")], [("1.20", "5")])
        problems = smoke.check_book_invariants(book)
        assert any("not sorted" in p for p in problems)
        assert any("out of range" in p for p in problems)


class TestSafetyPosture:
    def test_adapter_is_built_read_only(self):
        adapter = smoke.build_adapter(
            {"kalshi": {
                "rest_base": "https://external-api.kalshi.com/trade-api/v2",
                "rest_base_fallbacks": [
                    "https://api.elections.kalshi.com/trade-api/v2"],
                "ws_url": "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
            }},
            {})
        assert adapter.allow_order_placement is False
        assert adapter.has_credentials is False
        assert adapter.rest_bases[0].startswith(
            "https://external-api.kalshi.com")
        assert len(adapter.rest_bases) == 2

    def test_credentials_are_picked_up_but_never_enable_orders(self):
        adapter = smoke.build_adapter(
            {"kalshi": {"rest_base":
                        "https://external-api.kalshi.com/trade-api/v2"}},
            {"KALSHI_API_KEY_ID": "key-id",
             "KALSHI_PRIVATE_KEY_PEM": "-----BEGIN RSA PRIVATE KEY-----"})
        assert adapter.has_credentials is True
        assert adapter.allow_order_placement is False

    def test_unofficial_host_is_refused(self):
        with pytest.raises(PermissionError, match="not a documented official"):
            smoke.build_adapter(
                {"kalshi": {"rest_base": "https://kalshi-mirror.example/v2"}},
                {})

    def test_rate_limit_report_never_raises_the_budget(self):
        adapter = smoke.build_adapter(
            {"kalshi": {"rest_base":
                        "https://external-api.kalshi.com/trade-api/v2",
                        "rate_limit_reads_per_sec": 8,
                        "rate_limit_writes_per_sec": 3}},
            {})
        # A generous tier must not raise the configured budget (§13).
        adapter.apply_rate_limits(KalshiRateLimits(
            usage_tier="prestige", read_refill_rate=10000.0,
            read_bucket_capacity=10000.0, write_refill_rate=8000.0,
            write_bucket_capacity=8000.0))
        report = smoke.rate_limit_report(adapter)
        assert report["usage_tier"] == "prestige"
        assert report["effective_reads_per_sec"] == 8
        assert report["effective_writes_per_sec"] == 3

        # The published Basic tier (200 read / 100 write tokens per second
        # at the default 10-token endpoint cost) allows 20/10 req/s, so at
        # 80% headroom the configured 8/3 is already under it and must stay.
        adapter.apply_rate_limits(KalshiRateLimits(
            usage_tier="basic", read_refill_rate=200.0,
            read_bucket_capacity=200.0, write_refill_rate=100.0,
            write_bucket_capacity=100.0))
        report = smoke.rate_limit_report(adapter)
        assert report["effective_reads_per_sec"] == 8
        assert report["effective_writes_per_sec"] == 3

        # A genuinely tight tier must clamp lower: 50 tokens/s at a
        # 10-token endpoint cost x 0.8 headroom = 4 req/s (and 25 -> 2).
        adapter.apply_rate_limits(KalshiRateLimits(
            usage_tier="restricted", read_refill_rate=50.0,
            read_bucket_capacity=50.0, write_refill_rate=25.0,
            write_bucket_capacity=25.0))
        report = smoke.rate_limit_report(adapter)
        assert report["effective_reads_per_sec"] == 4.0
        assert report["effective_writes_per_sec"] == 2.0
