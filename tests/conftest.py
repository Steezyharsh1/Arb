"""Shared test fixtures."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

# Make `prediction_arb` importable when the package is not installed
# (e.g. a bare `pytest` run in a fresh checkout). parents[1] is the project
# root, which holds the `prediction_arb/` package directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prediction_arb.core.types import (
    MarketMicrostructure,
    MicrostructureType,
    NormalizedContract,
    OrderBook,
    OrderTypeSupport,
    OutcomeType,
    PriceLevel,
)

# Settlement-dependent tests use this frozen test clock. The default
# contract settlement is three days later, so a default `make_contract()`
# always exercises a positive lockup without consulting the host wall clock.
#
# D16 was originally caused by an absolute 2026-09-15 settlement that had
# passed: `capital_lockup_days` correctly returned zero for a settled
# contract, which made `assert lockup > 0` fail and silently removed delay
# cost from other tests. A wall-clock-relative repair avoided expiry but still
# made the baseline time-dependent; pairing a fixed test clock with fixed,
# future-dated fixtures is repeatable indefinitely. Tests for other exact
# instants should pass `settlement=` and/or `now=` explicitly.
SETTLEMENT_TEST_NOW = datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc)
SETTLE = SETTLEMENT_TEST_NOW + timedelta(days=3)


def make_contract(venue: str = "kalshi", contract_id: str = "TEST-1",
                  event: str = "Fed raises rates in September 2026",
                  settlement: datetime = SETTLE,
                  payout: Decimal = Decimal("1.00"),
                  threshold=None, threshold_op=None, units=None,
                  resolution_source: str = "Federal Reserve statement",
                  early_resolution: str = "",
                  underlying: str = "", category: str = "KXFED",
                  ) -> NormalizedContract:
    if not underlying:
        underlying = {"kalshi": "kalshi_dcm", "webull": "kalshi_dcm",
                      "moomoo": "kalshi_dcm",
                      "forecast_ex": "forecastex_dcm"}.get(venue, "")
    return NormalizedContract(
        venue=venue, underlying_exchange=underlying,
        clearing_entity="", market_id=contract_id, contract_id=contract_id,
        event_description=event, normalized_event="",
        outcome_type=OutcomeType.BINARY, outcome="YES",
        normalized_outcome="", outcome_count=2, all_outcomes=("YES", "NO"),
        settlement_time=settlement, timezone="UTC",
        cutoff_time=settlement - timedelta(hours=1),
        resolution_source=resolution_source, resolution_method="",
        threshold=threshold, threshold_operator=threshold_op, units=units,
        payout=payout, currency="USD",
        early_resolution_rules=early_resolution, cancellation_rules="",
        contract_version="v1", tick_size=Decimal("0.01"),
        min_order_size=Decimal("1"), max_order_size=None,
        jurisdiction_requirements="US", category=category,
        market_microstructure=MarketMicrostructure(
            type=MicrostructureType.CLOB, has_order_book=True),
        supported_order_types=OrderTypeSupport(limit=True, gtc=True))


@pytest.fixture(autouse=True)
def freeze_settlement_clock(monkeypatch):
    """Keep settlement-lockup tests independent of the real wall clock.

    Only the settlement calculation's clock is frozen; other clock-dependent
    behaviour (for example order-book timestamps) remains untouched.
    """
    from prediction_arb.normalization import settlement
    monkeypatch.setattr(settlement, "utcnow", lambda: SETTLEMENT_TEST_NOW)


def make_book(venue: str, contract_id: str,
              asks: list[tuple[str, str]],
              bids: list[tuple[str, str]] | None = None,
              age_seconds: float = 0.0) -> OrderBook:
    now = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return OrderBook(
        venue=venue, contract_id=contract_id,
        bids=[PriceLevel(Decimal(p), Decimal(q)) for p, q in (bids or [])],
        asks=[PriceLevel(Decimal(p), Decimal(q)) for p, q in asks],
        timestamp=now, microstructure_type=MicrostructureType.CLOB)


@pytest.fixture
def fee_calc():
    """Production fee ledger (context-incomplete venues fail closed)."""
    from prediction_arb.fees.venue_fees import load_fee_calculator
    return load_fee_calculator()


@pytest.fixture
def complete_fee_calc():
    """Static synthetic schedules for deterministic engine math tests.

    Production Kalshi fees are intentionally context-incomplete. Tests whose
    purpose is sizing, coordination, or generic profitability use this isolated
    fixture instead of weakening the production fail-closed ledger.
    """
    from prediction_arb.fees.calculator import FeeCalculator
    return FeeCalculator.from_config({
        "schedules": [
            {
                "venue": "kalshi",
                "version": "test-static-v1",
                "effective_date": "2020-01-01",
                "verified_at": "2026-09-13",
                "verification_status": "VERIFIED",
                "documentation_tested": True,
                "source": "synthetic deterministic test fixture",
                "trade_calculation_status": "COMPLETE",
                "settlement_calculation_status": "COMPLETE",
                "fees": [
                    {"fee_type": "test_quadratic", "multiplier": "0.07",
                     "applies_to": "taker"},
                    {"fee_type": "test_quadratic", "multiplier": "0.0175",
                     "applies_to": "maker"},
                    {"fee_type": "settlement", "fixed_amount": "0",
                     "applies_to": "settlement"},
                ],
            },
            {
                "venue": "forecast_ex",
                "version": "test-static-v1",
                "effective_date": "2020-01-01",
                "verified_at": "2026-09-13",
                "verification_status": "VERIFIED",
                "documentation_tested": True,
                "source": "synthetic deterministic test fixture",
                "trade_calculation_status": "COMPLETE",
                "settlement_calculation_status": "COMPLETE",
                "fees": [
                    {"fee_type": "test_fixed", "fixed_amount": "0.01",
                     "applies_to": "taker"},
                    {"fee_type": "test_fixed", "fixed_amount": "0.01",
                     "applies_to": "maker"},
                    {"fee_type": "settlement", "fixed_amount": "0",
                     "applies_to": "settlement"},
                ],
            },
        ]
    })
