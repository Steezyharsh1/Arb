"""Offline tests for the public-only Kalshi smoke and its evidence labels.

No venue call is made. Tests cover orderbook_fp parsing, invariants,
endpoint allow-listing, non-use of credentials, and the distinction between
UNAVAILABLE network evidence and adapter/data failures.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from prediction_arb.adapters.kalshi import KalshiAdapter
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
    def _config(self):
        return {"kalshi": {
            "rest_base": "https://external-api.kalshi.com/trade-api/v2",
            "rest_base_fallbacks": [
                "https://api.elections.kalshi.com/trade-api/v2"],
            "ws_url": "wss://external-api-ws.kalshi.com/trade-api/ws/v2",
            "rate_limit_reads_per_sec": 8,
            "rate_limit_writes_per_sec": 3,
        }}

    def test_adapter_is_public_only_and_cannot_use_credentials(self):
        adapter = smoke.build_adapter(self._config())
        assert adapter.allow_order_placement is False
        assert adapter.has_credentials is False
        assert adapter.api_key_id is None and adapter.private_key_pem is None
        assert adapter.rest_bases[0].startswith(
            "https://external-api.kalshi.com")
        assert len(adapter.rest_bases) == 2
        assert adapter.effective_reads_per_sec() <= 8
        assert adapter.effective_writes_per_sec() <= 3

    def test_unofficial_host_is_refused(self):
        with pytest.raises(PermissionError, match="not a documented official"):
            smoke.build_adapter({"kalshi": {
                "rest_base": "https://kalshi-mirror.example/v2"}})

    def test_transport_and_5xx_are_unavailable_not_adapter_failure(self):
        assert smoke.classify_exception(
            httpx.ConnectError("TLS/SSL EOF")) == "UNAVAILABLE"
        response = httpx.Response(
            503, request=httpx.Request("GET", "https://example.test"))
        with pytest.raises(httpx.HTTPStatusError) as exc:
            response.raise_for_status()
        assert smoke.classify_exception(exc.value) == "UNAVAILABLE"

    def test_schema_or_client_errors_are_not_mislabeled_network_failures(self):
        assert smoke.classify_exception(ValueError("bad book schema")) == "FAIL"
        response = httpx.Response(
            404, request=httpx.Request("GET", "https://example.test"))
        with pytest.raises(httpx.HTTPStatusError) as exc:
            response.raise_for_status()
        assert smoke.classify_exception(exc.value) == "FAIL"

    def test_evidence_layers_never_promote_full_adapter_automatically(self):
        evidence = {}
        smoke._set_evidence_levels(evidence, "OBSERVED", True)
        assert evidence["evidence_levels"]["documentation"]["state"] == "DOCUMENTED"
        assert evidence["evidence_levels"]["connectivity"]["state"] == "OBSERVED"
        assert evidence["evidence_levels"]["usable_market_data"]["state"] == "OBSERVED"
        assert evidence["evidence_levels"]["full_adapter_verification"]["state"] == "NOT_VERIFIED"
        assert evidence["promotion_candidate"] == "MARKET_DATA_OBSERVED"
        assert evidence["promotion_requires_manual_review"] is True

    def test_tls_failure_writes_unavailable_evidence_and_uses_no_order_path(
            self, tmp_path, monkeypatch):
        class OfflineAdapter:
            rest_bases = ["https://external-api.kalshi.com/trade-api/v2"]
            active_rest_base = rest_bases[0]

            async def connect(self):
                raise ConnectionError("TLS/SSL EOF before HTTP response")

            async def disconnect(self):
                return None

        monkeypatch.setattr(smoke, "_read_venues_config", lambda: {})
        monkeypatch.setattr(smoke, "build_adapter",
                            lambda _venues: OfflineAdapter())
        out = tmp_path / "evidence.json"
        code = asyncio.run(smoke.run(argparse.Namespace(
            limit=1, ticker=None, out=str(out), json=False)))
        payload = json.loads(out.read_text())
        assert code == 1
        assert payload["result"] == "UNAVAILABLE"
        assert payload["evidence_levels"]["connectivity"]["state"] == "UNAVAILABLE"
        assert payload["evidence_levels"]["usable_market_data"]["state"] == "NOT_OBSERVED"
        assert payload["evidence_levels"]["full_adapter_verification"]["state"] == "NOT_VERIFIED"
        assert payload["credentials_loaded"] is False
        assert payload["credentials_used"] is False
        assert payload["orders_placed"] == 0
