"""Offline tests for Polymarket US public smoke evidence handling."""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "polymarket_us_smoke.py"
    spec = importlib.util.spec_from_file_location("polymarket_us_smoke_script", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_script()


def test_network_failure_is_unavailable_not_adapter_failure():
    assert smoke.classify_exception(httpx.ConnectError("TLS/SSL EOF")) == "UNAVAILABLE"
    response = httpx.Response(
        503, request=httpx.Request("GET", "https://example.test"))
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        assert smoke.classify_exception(exc) == "UNAVAILABLE"
    else:  # pragma: no cover - the response must be an error
        raise AssertionError("expected HTTPStatusError")


def test_bad_payload_and_unexpected_client_response_are_not_network_errors():
    assert smoke.classify_exception(ValueError("bad state field")) == "FAIL"
    response = httpx.Response(
        404, request=httpx.Request("GET", "https://example.test"))
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        assert smoke.classify_exception(exc) == "FAIL"
    else:  # pragma: no cover
        raise AssertionError("expected HTTPStatusError")


def test_rule_evidence_requires_positive_finite_market_specific_values():
    assert smoke._rules_ok(SimpleNamespace(
        tick_size=Decimal("0.005"), min_order_size=Decimal("0.01")))
    assert not smoke._rules_ok(SimpleNamespace(
        tick_size=None, min_order_size=Decimal("1")))
    assert not smoke._rules_ok(SimpleNamespace(
        tick_size=Decimal("NaN"), min_order_size=Decimal("1")))


def test_evidence_does_not_collapse_connectivity_into_data_or_full_verification():
    evidence = {}
    smoke._set_evidence_levels(evidence, "OBSERVED", False)
    assert evidence["evidence_levels"]["documentation"]["state"] == "DOCUMENTED"
    assert evidence["evidence_levels"]["connectivity"]["state"] == "OBSERVED"
    assert evidence["evidence_levels"]["usable_market_data"]["state"] == "NOT_OBSERVED"
    assert evidence["evidence_levels"]["full_adapter_verification"]["state"] == "NOT_VERIFIED"
    assert evidence["promotion_candidate"] == "NONE"


def test_tls_failure_writes_exact_unavailable_layer_and_never_loads_credentials(
        tmp_path, monkeypatch):
    constructed = []

    class OfflineAdapter:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

        async def connect(self):
            raise ConnectionError(
                "Polymarket US public API unreachable: TLS/SSL EOF before HTTP")

        async def disconnect(self):
            return None

    monkeypatch.setattr(smoke, "PolymarketUSAdapter", OfflineAdapter)
    out = tmp_path / "polymarket-evidence.json"
    code = asyncio.run(smoke.run(argparse.Namespace(
        limit=1, slug=None, out=str(out), json=False)))
    payload = json.loads(out.read_text())

    assert code == 1
    assert payload["result"] == "UNAVAILABLE"
    # A failed connect must not be recorded under a name that asserts an HTTP
    # response was received (Arb.15: the old label contradicted its own result).
    connect = payload["checks"][0]
    assert connect["check"] == "connect: public gateway reachability"
    assert connect["result"] == "UNAVAILABLE"
    assert payload["unavailable_is_not_adapter_failure"] is True
    assert payload["evidence_levels"]["connectivity"]["state"] == "UNAVAILABLE"
    assert payload["evidence_levels"]["usable_market_data"]["state"] == "NOT_OBSERVED"
    assert payload["evidence_levels"]["full_adapter_verification"]["state"] == "NOT_VERIFIED"
    assert payload["credentials_loaded"] is False
    assert payload["credentials_used"] is False
    assert payload["orders_placed"] == 0
    assert "key_id" not in constructed[0]
    assert "secret_key" not in constructed[0]
    assert constructed[0]["allow_order_placement"] is False
