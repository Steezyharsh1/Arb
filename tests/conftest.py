"""Shared test fixtures."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from prediction_arb.core.types import (
    MarketMicrostructure,
    MicrostructureType,
    NormalizedContract,
    OrderBook,
    OrderTypeSupport,
    OutcomeType,
    PriceLevel,
)

SETTLE = datetime(2026, 9, 15, 20, 0, tzinfo=timezone.utc)


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
    from prediction_arb.fees.venue_fees import load_fee_calculator
    return load_fee_calculator()
