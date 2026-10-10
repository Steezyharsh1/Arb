"""Paper-mode streaming integration (Phase 2).

Verifies the engine wiring, not the wire protocol (that lives in
test_kalshi_phase2.py): with a streaming-capable, credentialed adapter the
engine subscribes each eligible market once, stops polling it, merges
pushed books into the live book cache, and keeps polling the rest. When
streaming fails it falls back to polling without retrying the venue.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from rich.console import Console

from prediction_arb.core.config import AppConfig
from prediction_arb.core.types import (
    APIStatus,
    MarketMicrostructure,
    MicrostructureType,
    Mode,
    OrderTypeSupport,
)
from prediction_arb.database.repository import Repository
from prediction_arb.matching.llm_matcher import LLMMatcher
from .conftest import make_book, make_contract


class FakeAdapter:
    """Minimal adapter double: streaming-capable iff flag set."""

    def __init__(self, venue: str, contracts: list, *,
                 streaming: bool = False, credentials: bool = True,
                 fail_subscribe: bool = False,
                 api_status: APIStatus = APIStatus.VERIFIED):
        self.venue = venue
        self.supports_streaming = streaming
        self._credentials = credentials
        self.fail_subscribe = fail_subscribe
        self.api_status = api_status
        self._contracts = {c.market_id: c for c in contracts}
        self.subscribed: dict[str, object] = {}
        self.polled: list[str] = []
        self.unsubscribed: list[str] = []

    @property
    def has_credentials(self) -> bool:
        return self._credentials

    # -- lifecycle / metadata
    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    def get_api_status(self) -> APIStatus:
        return self.api_status

    def get_supported_order_types(self) -> OrderTypeSupport:
        return OrderTypeSupport(limit=True)

    def get_market_microstructure(self) -> MarketMicrostructure:
        return MarketMicrostructure(type=MicrostructureType.CLOB,
                                    has_order_book=True)

    # -- market data
    async def get_markets(self, limit: int = 200, **_kw) -> list:
        return list(self._contracts.values())[:limit]

    async def get_market(self, market_id: str):
        return self._contracts.get(market_id)

    async def get_order_book(self, market_id: str):
        self.polled.append(market_id)
        return make_book(self.venue, market_id,
                         asks=[("0.50", "10")], bids=[("0.48", "10")])

    # -- streaming
    async def subscribe_order_book(self, market_id: str, callback) -> None:
        if self.fail_subscribe:
            raise RuntimeError("ws handshake failed")
        self.subscribed[market_id] = callback

    async def unsubscribe_order_book(self, market_id: str) -> None:
        self.unsubscribed.append(market_id)

    # -- account / orders (unused in this test)
    async def get_account(self) -> dict:
        return {}

    async def get_balances(self) -> dict:
        return {}

    async def get_positions(self) -> list:
        return []

    async def get_open_orders(self) -> list:
        return []

    async def place_order(self, request):
        raise NotImplementedError

    async def cancel_order(self, order_id: str) -> bool:
        return False

    async def cancel_all_orders(self) -> int:
        return 0

    async def get_order(self, order_id: str):
        return None

    async def get_fills(self) -> list:
        return []


def _engine(tmp_path, adapters, complete_fee_calc) -> tuple:
    from prediction_arb.modes.paper.engine import PaperEngine
    config = AppConfig(
        mode=Mode.PAPER,
        settings={
            "arbitrage": {"minimum_net_profit": 2, "minimum_net_return": 0.005,
                          "maximum_settlement_delay_days": 5},
            "scanner": {"poll_interval_seconds": 10,
                        "max_markets_per_venue": 50},
            "compliance": {"jurisdiction": "US-UT"},
            "cost_of_capital": {"annual_rate": 0.05},
        },
        venues={}, fees={}, risk_limits={},
        mode_config={"mode": "paper",
                     "execution_engine": "PaperExecutionEngine",
                     "database": "prediction_arb_paper",
                     "allow_live_orders": False},
    )
    repo = Repository(tmp_path / "paper.sqlite3", Mode.PAPER)
    engine = PaperEngine(config, adapters, repo, complete_fee_calc,
                         LLMMatcher(client=None), Console())
    return engine, config


class TestPaperStreaming:
    def test_streamed_contract_is_not_polled_and_push_updates_cache(
            self, tmp_path, complete_fee_calc):
        k = make_contract(venue="kalshi", contract_id="KXTEST-1",
                          event="Fed raises rates in September 2026")
        f = make_contract(venue="forecast_ex", contract_id="FXTEST-1",
                          event="CPI prints above 3% in September 2026")
        kalshi = FakeAdapter("kalshi", [k], streaming=True)
        fx = FakeAdapter("forecast_ex", [f], streaming=False,
                         credentials=False)
        engine, _ = _engine(tmp_path, {"kalshi": kalshi,
                                       "forecast_ex": fx}, complete_fee_calc)

        asyncio.run(engine.run(cycles=1))

        # streaming opened for kalshi market, not for forecast_ex
        assert list(kalshi.subscribed) == ["KXTEST-1"]
        assert fx.subscribed == {}
        # polling happened ONLY for the non-streamed contract
        assert kalshi.polled == []
        assert fx.polled == ["FXTEST-1"]
        key = ("kalshi", "KXTEST-1")
        assert key in engine._streaming

        # simulate the WS feed pushing a fresher book
        pushed = make_book("kalshi", "KXTEST-1",
                           asks=[("0.42", "7")], bids=[("0.40", "9")])
        asyncio.run(kalshi.subscribed["KXTEST-1"](pushed))
        store = asyncio.run(engine.aggregator.snapshot())
        assert store[key] is pushed
        # the polled venue's book is in the merged store too
        assert store[("forecast_ex", "FXTEST-1")] is not None
        # and a fresh book is visible to the execution engine's lookup
        assert engine.aggregator.peek(*key) is pushed

    def test_streaming_failure_falls_back_to_polling_once(self, tmp_path,
                                                          complete_fee_calc):
        k = make_contract(venue="kalshi", contract_id="KXTEST-1",
                          event="Fed raises rates in September 2026")
        kalshi = FakeAdapter("kalshi", [k], streaming=True,
                             fail_subscribe=True)
        engine, _ = _engine(tmp_path, {"kalshi": kalshi}, complete_fee_calc)

        asyncio.run(engine.run(cycles=2))

        # one bounded failure: no subscription, venue marked failed,
        # market polled (every cycle), and no further subscribe attempts
        assert kalshi.subscribed == {}
        assert "kalshi" in engine._streaming_failed_venues
        assert ("kalshi", "KXTEST-1") not in engine._streaming
        assert kalshi.polled.count("KXTEST-1") == 2
        store = asyncio.run(engine.aggregator.snapshot())
        assert store[("kalshi", "KXTEST-1")] is not None

    def test_no_credentials_means_polling_only(self, tmp_path,
                                               complete_fee_calc):
        k = make_contract(venue="kalshi", contract_id="KXTEST-1",
                          event="Fed raises rates in September 2026")
        kalshi = FakeAdapter("kalshi", [k], streaming=True,
                             credentials=False)
        engine, _ = _engine(tmp_path, {"kalshi": kalshi}, complete_fee_calc)

        asyncio.run(engine.run(cycles=1))

        assert kalshi.subscribed == {}
        assert kalshi.polled == ["KXTEST-1"]
        assert engine._streaming == set()

    def test_market_data_observed_admits_rest_but_not_ws_streaming(
            self, tmp_path, complete_fee_calc):
        """REST market data may be observed while WS/full verification is not."""
        k = make_contract(venue="kalshi", contract_id="KXTEST-1",
                          event="Fed raises rates in September 2026")
        kalshi = FakeAdapter(
            "kalshi", [k], streaming=True, credentials=True,
            api_status=APIStatus.MARKET_DATA_OBSERVED)
        engine, _ = _engine(tmp_path, {"kalshi": kalshi}, complete_fee_calc)

        asyncio.run(engine.run(cycles=1))

        assert kalshi.subscribed == {}
        assert kalshi.polled == ["KXTEST-1"]
        assert engine._streaming == set()
        assert engine._streaming_failed_venues == set()
        assert asyncio.run(engine.aggregator.keys()) == [
            ("kalshi", "KXTEST-1")]

    def test_streaming_market_disappearing_releases_subscription(
            self, tmp_path, complete_fee_calc):
        k = make_contract(venue="kalshi", contract_id="KXTEST-1",
                          event="Fed raises rates in September 2026")
        kalshi = FakeAdapter("kalshi", [k], streaming=True)
        engine, _ = _engine(tmp_path, {"kalshi": kalshi}, complete_fee_calc)

        asyncio.run(engine.run(cycles=1))
        assert ("kalshi", "KXTEST-1") in engine._streaming

        # the market vanishes from discovery
        kalshi._contracts.clear()
        asyncio.run(engine.run(cycles=1))

        assert engine._streaming == set()
        assert kalshi.unsubscribed == ["KXTEST-1"]
        assert asyncio.run(engine.aggregator.keys()) == []
