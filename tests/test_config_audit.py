"""Configuration audit regressions for settings removed in Arb.10.

These keys were present in the sample YAML but never fed into production
policy. The actual fixed safety behavior is pinned in the strategy, matching,
eligibility, and execution tests rather than represented as a dead toggle.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from prediction_arb.compliance.eligibility import check_contract_eligibility
from prediction_arb.core.types import Mode

from .conftest import make_contract

ROOT = Path(__file__).resolve().parents[1]


def test_unenforced_settings_are_not_advertised_as_runtime_controls():
    settings = yaml.safe_load((ROOT / "config/settings.yaml").read_text())

    assert not ({
        "guaranteed_only",
        "minimum_liquidity_ratio",
        "maximum_execution_latency_ms",
    } & set(settings["arbitrage"]))
    assert not ({
        "max_execution_latency_ms",
        "preferred_order_type",
        "allow_market_orders",
    } & set(settings["execution"]))
    assert not ({
        "block_unknown_status",
        "block_unknown_venue_relationship",
    } & set(settings["compliance"]))


def test_unknown_eligibility_is_mode_fixed_not_a_yaml_toggle():
    contract = make_contract(
        venue="kalshi", category="Economics",
        event="Economics replacement rate forecast")

    # Retain the helper's historical keyword for source compatibility. It does
    # not relax live eligibility or change non-live research treatment.
    for legacy_value in (False, True):
        paper = check_contract_eligibility(
            contract, "US-UT", Mode.PAPER,
            block_unknown_status=legacy_value)
        live = check_contract_eligibility(
            contract, "US-UT", Mode.LIVE,
            block_unknown_status=legacy_value)
        assert paper.allowed and paper.status.value == "UNKNOWN"
        assert not live.allowed and live.status.value == "UNKNOWN"
