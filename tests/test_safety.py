"""Safety & mode-isolation tests (§43–§60): no live order from non-live
modes, firewall behavior, stub honesty, kill switch, credential
separation."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from prediction_arb.core.types import (
    FirewallBlocked,
    Mode,
    OrderAction,
    OrderRequest,
    OrderType,
    Side,
)
from .conftest import make_book


def _req(venue="kalshi", mode=Mode.PAPER):
    return OrderRequest(
        venue=venue, contract_id="K1", side=Side.YES,
        action=OrderAction.BUY, quantity=Decimal("10"),
        order_type=OrderType.LIMIT, limit_price=Decimal("0.40"),
        client_order_id=str(uuid.uuid4()), mode=mode)


class TestPaperCannotSendLiveOrders:
    def test_paper_engine_has_no_adapters(self, fee_calc):
        """Structural isolation: PaperExecutionEngine has no attribute
        that references a venue adapter."""
        from prediction_arb.execution.simulated import PaperExecutionEngine
        eng = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("100")}, fee_calc=fee_calc,
            book_lookup=lambda v, c: None)
        assert not hasattr(eng, "adapters")
        assert not hasattr(eng, "firewall")
        assert eng.simulated is True

    def test_kalshi_adapter_blocks_orders_outside_live(self):
        from prediction_arb.adapters.kalshi import KalshiAdapter
        a = KalshiAdapter(
            rest_base="https://api.elections.kalshi.com/trade-api/v2",
            allow_order_placement=False)
        with pytest.raises(PermissionError):
            asyncio.run(a.place_order(_req(mode=Mode.LIVE)))

    def test_mode_mismatch_rejected_by_sim_engine(self, fee_calc):
        from prediction_arb.execution.simulated import PaperExecutionEngine
        eng = PaperExecutionEngine(
            starting_cash={"kalshi": Decimal("100")}, fee_calc=fee_calc,
            book_lookup=lambda v, c: make_book("kalshi", "K1",
                                               asks=[("0.4", "100")]))
        res = asyncio.run(eng.submit_order(_req(mode=Mode.LIVE)))
        assert res.status.value == "REJECTED"
        assert "mode mismatch" in res.message


class TestLiveEngineGuards:
    def test_confirmation_phrase_required(self):
        from prediction_arb.execution.live import LiveConfirmationToken
        with pytest.raises(PermissionError):
            LiveConfirmationToken("yes")
        with pytest.raises(PermissionError):
            LiveConfirmationToken("i understand live trading")
        token = LiveConfirmationToken("I UNDERSTAND LIVE TRADING")
        assert token.confirmed

    def test_live_engine_refuses_without_allow_flag(self):
        from prediction_arb.execution.live import (
            LiveConfirmationToken, LiveExecutionEngine,
        )
        token = LiveConfirmationToken("I UNDERSTAND LIVE TRADING")
        with pytest.raises(PermissionError):
            LiveExecutionEngine(adapters={}, repository=None,  # type: ignore
                                confirmation=token, allow_live_orders=False)

    def test_unguarded_submit_blocked(self, tmp_path):
        from prediction_arb.database.repository import Repository
        from prediction_arb.execution.live import (
            LiveConfirmationToken, LiveExecutionEngine,
        )
        repo = Repository(tmp_path / "live.sqlite3", Mode.LIVE)
        eng = LiveExecutionEngine(
            adapters={}, repository=repo,
            confirmation=LiveConfirmationToken("I UNDERSTAND LIVE TRADING"),
            allow_live_orders=True)
        with pytest.raises(FirewallBlocked):
            asyncio.run(eng.submit_order(_req(mode=Mode.LIVE)))


class TestFirewall:
    def _ctx(self, **overrides):
        from prediction_arb.execution.firewall import FirewallContext
        base = dict(
            app_mode=Mode.LIVE, allow_live_orders=True,
            live_confirmation_given=True, credentials_loaded=True,
            account_verified=True, contract_eligible=True,
            eligibility_reason="ok", independence_verified=True,
            match_approved=True, fees_verified=True, risk_violations=[],
            book_timestamp=datetime.now(timezone.utc),
            max_book_age_ms=1000.0,
            opportunity_still_valid=lambda: True,
            expected_price=Decimal("0.40"),
            max_price_deviation=Decimal("0.02"),
            quantity_limit=Decimal("100"))
        base.update(overrides)
        return FirewallContext(**base)

    def test_all_pass(self):
        from prediction_arb.execution.firewall import LiveOrderFirewall
        LiveOrderFirewall().verify(_req(mode=Mode.LIVE), self._ctx())

    @pytest.mark.parametrize("override,fragment", [
        (dict(app_mode=Mode.PAPER), "LIVE mode"),
        (dict(allow_live_orders=False), "allow_live_orders"),
        (dict(live_confirmation_given=False), "confirmed"),
        (dict(credentials_loaded=False), "credentials"),
        (dict(contract_eligible=False), "eligibility"),
        (dict(independence_verified=False), "INDEPENDENT"),
        (dict(match_approved=False), "certificate"),
        (dict(fees_verified=False), "fee"),
        (dict(risk_violations=["x"]), "risk"),
        (dict(book_timestamp=None), "fresh"),
        (dict(expected_price=Decimal("0.90")), "price"),
        (dict(quantity_limit=Decimal("1")), "quantity"),
    ])
    def test_each_check_blocks(self, override, fragment):
        from prediction_arb.execution.firewall import LiveOrderFirewall
        with pytest.raises(FirewallBlocked) as e:
            LiveOrderFirewall().verify(_req(mode=Mode.LIVE),
                                       self._ctx(**override))
        assert fragment.lower() in str(e.value).lower()

    def test_kill_switch_blocks(self, tmp_path, monkeypatch):
        from prediction_arb.execution.firewall import LiveOrderFirewall
        from prediction_arb.risk import kill_switch
        monkeypatch.setattr(kill_switch, "KILL_FILE",
                            tmp_path / "KILL_SWITCH")
        kill_switch.engage("test")
        try:
            with pytest.raises(FirewallBlocked) as e:
                LiveOrderFirewall().verify(_req(mode=Mode.LIVE), self._ctx())
            assert "kill switch" in str(e.value).lower()
        finally:
            kill_switch.disengage()


class TestStubHonesty:
    def test_stub_adapters_raise_not_fabricate(self):
        from prediction_arb.adapters.moomoo import MoomooAdapter
        from prediction_arb.adapters.webull import WebullAdapter
        for adapter in (MoomooAdapter(), WebullAdapter()):
            with pytest.raises(NotImplementedError):
                asyncio.run(adapter.get_markets())
            with pytest.raises(NotImplementedError):
                asyncio.run(adapter.place_order(_req()))

    def test_forecastex_stub_status(self):
        from prediction_arb.adapters.forecast_ex import ForecastExAdapter
        a = ForecastExAdapter()
        assert a.get_api_status().value == "API_DOCUMENTED_UNTESTED"
        with pytest.raises(NotImplementedError):
            asyncio.run(a.get_order_book("X"))

    def test_v1_stubs_raise(self):
        from prediction_arb.arbitrage.graph import ArbGraph
        from prediction_arb.arbitrage.logical import (
            find_logical_opportunities,
        )
        from prediction_arb.arbitrage.multi_leg import (
            find_multi_leg_opportunities,
        )
        # NOTE: BacktestEngine used to belong in this list. Its metrics are
        # implemented (Phase 4), so it no longer raises NotImplementedError;
        # see tests/test_phase4_recording.py.
        from prediction_arb.monitoring.dashboard import WebDashboard
        with pytest.raises(NotImplementedError):
            ArbGraph().find_cycles()
        with pytest.raises(NotImplementedError):
            find_logical_opportunities([], {}, {}, Mode.PAPER)
        with pytest.raises(NotImplementedError):
            find_multi_leg_opportunities([], {}, None, Mode.PAPER)  # type: ignore
        with pytest.raises(NotImplementedError):
            WebDashboard().start()


class TestCredentialSeparation:
    def test_live_keys_in_paper_env_refused(self, tmp_path, monkeypatch):
        from prediction_arb.core import config as cfg
        from prediction_arb.core.types import ModeIsolationError
        env = tmp_path / ".env.paper"
        env.write_text("KALSHI_LIVE_API_KEY_ID=abc\n")
        monkeypatch.setattr(cfg, "ROOT", tmp_path)
        with pytest.raises(ModeIsolationError):
            cfg.load_mode_env(Mode.PAPER, {"env_file": ".env.paper"})

    def test_db_mode_tag_enforced(self, tmp_path):
        from prediction_arb.database.repository import Repository
        p = tmp_path / "x.sqlite3"
        Repository(p, Mode.PAPER)
        with pytest.raises(RuntimeError):
            Repository(p, Mode.LIVE)


class TestKillSwitch:
    def test_engage_disengage(self, tmp_path, monkeypatch):
        from prediction_arb.risk import kill_switch
        monkeypatch.setattr(kill_switch, "KILL_FILE", tmp_path / "KS")
        assert not kill_switch.is_engaged()
        kill_switch.engage("test")
        assert kill_switch.is_engaged()
        assert kill_switch.status()["reason"] == "test"
        kill_switch.disengage()
        assert not kill_switch.is_engaged()


class TestOrderPostIsNotRetried:
    """Arb.16 / D30: an order POST is sent exactly once per intent.

    Before the fix a ReadTimeout, ConnectError, or 5xx on POST /portfolio/orders
    was retried by RetryPolicy and rotated across fallback hosts, sending the
    same client_order_id up to eight times. Reads keep their failover; see
    tests/test_kalshi_phase2.py. Offline only: httpx.MockTransport, no network.
    """

    PRIMARY = "https://external-api.kalshi.com/trade-api/v2"
    FALLBACK = "https://api.elections.kalshi.com/trade-api/v2"

    @staticmethod
    def _adapter(handler):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        from prediction_arb.adapters.kalshi import KalshiAdapter
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()).decode()
        adapter = KalshiAdapter(
            rest_base=TestOrderPostIsNotRetried.PRIMARY,
            rest_base_fallbacks=[TestOrderPostIsNotRetried.FALLBACK],
            api_key_id="test-key", private_key_pem=pem,
            allow_order_placement=True)
        import httpx
        adapter._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler))
        return adapter

    @staticmethod
    def _live_order():
        return OrderRequest(
            venue="kalshi", contract_id="K1", side=Side.YES,
            action=OrderAction.BUY, quantity=Decimal("10"),
            order_type=OrderType.LIMIT, limit_price=Decimal("0.40"),
            client_order_id=str(uuid.uuid4()), mode=Mode.LIVE)

    @pytest.mark.parametrize("failure", ["read_timeout", "connect_error",
                                         "http_500", "http_429"])
    def test_failed_order_post_is_sent_once_and_not_failed_over(self,
                                                                failure):
        import httpx
        posts: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method != "POST":
                return httpx.Response(200, json={})
            import json as _json
            posts.append((request.url.host,
                          _json.loads(request.content)["client_order_id"]))
            if failure == "read_timeout":
                raise httpx.ReadTimeout("read timed out", request=request)
            if failure == "connect_error":
                raise httpx.ConnectError("TLS/SSL EOF", request=request)
            if failure == "http_500":
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(429, json={"error": "slow down"})

        adapter = self._adapter(handler)
        order = self._live_order()
        with pytest.raises(Exception):
            asyncio.run(adapter.place_order(order))
        asyncio.run(adapter._client.aclose())

        assert len(posts) == 1, posts
        assert posts[0] == ("external-api.kalshi.com", order.client_order_id)

    def test_successful_order_post_is_one_request_and_returns_result(self):
        import httpx
        posts = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                posts.append(request)
                return httpx.Response(201, json={"order": {
                    "order_id": "venue-1", "status": "resting",
                    "taker_fill_count": 0}})
            return httpx.Response(200, json={})

        adapter = self._adapter(handler)
        order = self._live_order()
        result = asyncio.run(adapter.place_order(order))
        asyncio.run(adapter._client.aclose())

        assert len(posts) == 1
        assert result.venue_order_id == "venue-1"
        assert result.client_order_id == order.client_order_id

    def test_reads_still_fail_over_to_the_fallback_host(self):
        import httpx
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.host)
            if request.url.host == "external-api.kalshi.com":
                raise httpx.ConnectError("down", request=request)
            return httpx.Response(200, json={"orders": []})

        adapter = self._adapter(handler)
        orders = asyncio.run(adapter.get_open_orders())
        asyncio.run(adapter._client.aclose())

        assert orders == []
        assert seen == ["external-api.kalshi.com", "api.elections.kalshi.com"]
