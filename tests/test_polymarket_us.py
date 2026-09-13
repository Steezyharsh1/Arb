"""Phase 3 tests — Polymarket US adapter (second independent venue).

Covers:
* the two-base split: public market data with NO credentials vs the
  authenticated host (P1),
* Ed25519 request signing (P2),
* per-market trading rules carried, never inferred (P3),
* market-state gating — only MARKET_STATE_OPEN is executable (P4),
* rate-limit clamping below the published 20 req/s ceiling (P5, §13),
* two-sided book parsing with malformed/non-tradeable levels dropped,
* the markets WebSocket against a scripted LOCAL server (P6),
* independence from Kalshi and the order-placement refusal.

Nothing here claims a live Polymarket US test: the sandbox cannot reach
the venue (UNAVAILABLE, see docs/VENUE_RESEARCH.md P7).
"""
from __future__ import annotations

import asyncio
import base64
import json
from decimal import Decimal

import httpx
import pytest
import websockets

from prediction_arb.adapters.polymarket_us import (
    PolymarketAuthError,
    PolymarketOrderPlacementDisabled,
    PolymarketUSAdapter,
)
from prediction_arb.adapters.polymarket_us_ws import (
    PolymarketWebSocketFeed,
    PolymarketWSUnavailable,
)
from prediction_arb.core.types import APIStatus, Independence, Mode, OrderRequest, OrderType, Side, OrderAction
from prediction_arb.provenance.independence import is_independent

PUBLIC = "https://gateway.polymarket.us"
AUTHED = "https://api.polymarket.us"


def _keypair() -> tuple[str, object]:
    """Generate an Ed25519 key and return (base64 secret, public key)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    key = ed25519.Ed25519PrivateKey.generate()
    raw = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption())
    return base64.b64encode(raw).decode(), key.public_key()


def _adapter(**kw) -> PolymarketUSAdapter:
    kw.setdefault("public_base", PUBLIC)
    kw.setdefault("authed_base", AUTHED)
    return PolymarketUSAdapter(**kw)


def _inject(adapter: PolymarketUSAdapter, handler) -> None:
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _amount(v: str) -> dict:
    return {"value": v, "currency": "USD"}


def _book_payload(state: str = "MARKET_STATE_OPEN",
                  slug: str = "fed-cut-2026") -> dict:
    return {"marketData": {
        "marketSlug": slug,
        "bids": [{"px": _amount("0.55"), "qty": "120"},
                 {"px": _amount("0.54"), "qty": "300"}],
        "offers": [{"px": _amount("0.57"), "qty": "80"},
                   {"px": _amount("0.58"), "qty": "150"}],
        "state": state,
        "transactTime": "2026-09-13T10:30:00Z",
    }}


def _market_payload(**over) -> dict:
    m = {
        "id": "1",
        "slug": "fed-cut-2026",
        "question": "Will the Fed cut rates in 2026?",
        "description": "Resolves per the FOMC statement.",
        "category": "economics",
        "endDate": "2026-12-31T23:59:00Z",
        "active": True,
        "closed": False,
        "orderPriceMinTickSize": 0.005,
        "minimumTradeQty": 0.01,
        "updatedAt": "2026-09-13T00:00:00Z",
    }
    m.update(over)
    return m


# ------------------------------------------------------- public/no-key

class TestPublicMarketData:
    def test_discovery_and_books_need_no_credentials(self):
        """P1: the whole market-data path works with no API key."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            if request.url.path == "/v1/markets":
                return httpx.Response(200, json={"markets": [_market_payload()]})
            if request.url.path.endswith("/book"):
                return httpx.Response(200, json=_book_payload())
            return httpx.Response(404, json={})

        a = _adapter()          # no key_id / secret_key at all
        assert a.has_credentials is False
        _inject(a, handler)

        markets = asyncio.run(a.get_markets(limit=1))
        book = asyncio.run(a.get_order_book("fed-cut-2026"))

        assert len(markets) == 1
        assert book is not None and book.bids and book.asks
        # Every request went to the PUBLIC host and carried no auth header.
        assert all(r.url.host == "gateway.polymarket.us" for r in seen)
        assert not any("X-PM-Access-Key" in r.headers for r in seen)

    def test_books_are_two_sided_not_synthesized(self):
        """Real offers are used; nothing is derived as 1 - bid."""
        a = _adapter()
        book = a.parse_order_book("fed-cut-2026", _book_payload())
        assert book is not None
        assert [str(l.price) for l in book.bids] == ["0.55", "0.54"]
        assert [str(l.price) for l in book.asks] == ["0.57", "0.58"]
        # A synthesized Kalshi-style ask would have been 1-0.55 = 0.45.
        assert all(l.price != Decimal("0.45") for l in book.asks)

    def test_levels_sorted_best_first(self):
        payload = _book_payload()
        payload["marketData"]["bids"] = [
            {"px": _amount("0.40"), "qty": "10"},
            {"px": _amount("0.52"), "qty": "10"}]
        payload["marketData"]["offers"] = [
            {"px": _amount("0.70"), "qty": "10"},
            {"px": _amount("0.60"), "qty": "10"}]
        book = _adapter().parse_order_book("fed-cut-2026", payload)
        assert str(book.bids[0].price) == "0.52"   # highest bid first
        assert str(book.asks[0].price) == "0.60"   # lowest ask first

    def test_malformed_and_non_tradeable_levels_dropped(self):
        payload = _book_payload()
        payload["marketData"]["bids"] = [
            {"px": _amount("0.55"), "qty": "120"},
            {"px": _amount("0.00"), "qty": "5"},     # price out of (0,1)
            {"px": _amount("1.00"), "qty": "5"},     # price out of (0,1)
            {"px": _amount("0.30"), "qty": "0"},     # non-positive qty
            {"px": _amount("abc"), "qty": "5"},      # unparseable
            "not-a-level",                            # malformed
        ]
        book = _adapter().parse_order_book("fed-cut-2026", payload)
        assert len(book.bids) == 1
        assert str(book.bids[0].price) == "0.55"

    def test_404_market_returns_none(self):
        def handler(request):
            return httpx.Response(404, json={})
        a = _adapter()
        _inject(a, handler)
        assert asyncio.run(a.get_order_book("nope")) is None


# ------------------------------------------------------ tradeable state

class TestMarketState:
    @pytest.mark.parametrize("state", [
        "MARKET_STATE_PREOPEN", "MARKET_STATE_SUSPENDED",
        "MARKET_STATE_HALTED", "MARKET_STATE_EXPIRED",
        "MARKET_STATE_TERMINATED", "MARKET_STATE_MATCH_AND_CLOSE_AUCTION",
    ])
    def test_non_tradeable_states_never_yield_a_book(self, state):
        """P4: a halted/pre-open book is real data but NOT executable.

        Returning it would let the engine size a trade it could not fill.
        """
        a = _adapter()
        assert a.parse_order_book("fed-cut-2026", _book_payload(state)) is None

    def test_open_state_yields_a_book(self):
        a = _adapter()
        book = a.parse_order_book("fed-cut-2026",
                                  _book_payload("MARKET_STATE_OPEN"))
        assert book is not None and len(book.bids) == 2

    def test_get_order_book_filters_non_tradeable(self):
        def handler(request):
            return httpx.Response(200,
                                  json=_book_payload("MARKET_STATE_HALTED"))
        a = _adapter()
        _inject(a, handler)
        assert asyncio.run(a.get_order_book("fed-cut-2026")) is None


# -------------------------------------------------------- market rules

class TestMarketRules:
    def test_per_market_tick_and_min_qty_are_carried(self):
        """P3: docs say never infer these from product type or slug."""
        a = _adapter()
        nc = a._normalize_market(_market_payload(
            orderPriceMinTickSize=0.005, minimumTradeQty=0.01))
        assert nc.tick_size == Decimal("0.005")
        assert nc.min_order_size == Decimal("0.01")

    def test_missing_rules_fall_back_to_coarsest_safe_values(self):
        a = _adapter()
        m = _market_payload()
        del m["orderPriceMinTickSize"]
        del m["minimumTradeQty"]
        nc = a._normalize_market(m)
        # Coarser than any real market → a price on this grid is always
        # placeable; a finer invented grid could be rejected.
        assert nc.tick_size == Decimal("0.01")
        assert nc.min_order_size == Decimal("1")

    def test_closed_and_archived_markets_are_skipped(self):
        a = _adapter()
        assert a._normalize_market(_market_payload(closed=True)) is None
        assert a._normalize_market(_market_payload(archived=True)) is None
        assert a._normalize_market(_market_payload(slug=None)) is None

    def test_sports_markets_keep_a_sports_category(self):
        """§35: Utah restricts sports; the signal must survive."""
        from prediction_arb.compliance.jurisdiction import category_for
        a = _adapter()
        nc = a._normalize_market(_market_payload(
            category="sports-nfl",
            sportsMarketType="football_team_full_game_spread",
            gameId="g-1"))
        assert nc.category == "sports"
        assert category_for("polymarket_us", nc.category) == "sports"

    def test_provenance_fields_are_contract_qualified(self):
        a = _adapter()
        nc = a._normalize_market(_market_payload())
        assert nc.underlying_exchange == "qcx_dcm"
        assert nc.clearing_entity == "qc_clearing"
        assert nc.payout == Decimal("1.00")


# ------------------------------------------------------------- signing

class TestAuthentication:
    def test_signature_verifies_over_timestamp_method_path(self):
        """P2: message = timestamp + METHOD + path."""
        secret, public = _keypair()
        a = _adapter(key_id="key-1", secret_key=secret)
        headers = a.auth_headers("GET", "/v1/portfolio/positions")
        assert headers["X-PM-Access-Key"] == "key-1"
        message = (headers["X-PM-Timestamp"] + "GET"
                   + "/v1/portfolio/positions").encode()
        public.verify(base64.b64decode(headers["X-PM-Signature"]), message)

    def test_authenticated_calls_hit_the_authed_host_with_headers(self):
        secret, _ = _keypair()
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"balances": [
                {"currentBalance": 1000.00, "currency": "USD"}]})

        a = _adapter(key_id="key-1", secret_key=secret)
        _inject(a, handler)
        balances = asyncio.run(a.get_balances())
        assert balances["USD"] == Decimal("1000.00")
        assert seen[0].url.host == "api.polymarket.us"
        assert "X-PM-Signature" in seen[0].headers

    def test_missing_credentials_raise_a_descriptive_error(self):
        a = _adapter()
        with pytest.raises(PolymarketAuthError, match="POLYMARKET_KEY_ID"):
            a.auth_headers("GET", "/v1/portfolio/positions")

    def test_malformed_secret_key_is_rejected_clearly(self):
        a = _adapter(key_id="key-1", secret_key="not-base64!!!")
        with pytest.raises(PolymarketAuthError):
            a.auth_headers("GET", "/v1/orders/open")

    def test_short_secret_key_is_rejected(self):
        a = _adapter(key_id="key-1",
                     secret_key=base64.b64encode(b"tooshort").decode())
        with pytest.raises(PolymarketAuthError, match="32 bytes"):
            a.auth_headers("GET", "/v1/orders/open")


# --------------------------------------------------------- rate limits

class TestRateLimits:
    def test_budgets_clamped_below_published_ceiling(self):
        """P5/§13: never exceed headroom of the published 20 req/s."""
        a = _adapter(reads_per_sec=100.0, writes_per_sec=100.0,
                     rate_limit_headroom=0.8)
        assert a.effective_reads_per_sec() == pytest.approx(16.0)
        assert a.effective_writes_per_sec() == pytest.approx(16.0)

    def test_conservative_configured_budget_is_kept(self):
        a = _adapter(reads_per_sec=8.0, writes_per_sec=3.0)
        assert a.effective_reads_per_sec() == pytest.approx(8.0)
        assert a.effective_writes_per_sec() == pytest.approx(3.0)

    def test_invalid_headroom_rejected(self):
        with pytest.raises(ValueError):
            _adapter(rate_limit_headroom=1.5)

    def test_429_is_raised_for_backoff_not_swallowed(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429, json={"status": 429,
                                             "message": "Too Many Requests"})

        a = _adapter()
        a._retry.max_attempts = 2
        a._retry.base_delay = 0.001
        _inject(a, handler)
        with pytest.raises(httpx.TransportError, match="rate limited"):
            asyncio.run(a.get_order_book("fed-cut-2026"))
        assert calls["n"] == 2      # retried, then surfaced


# ------------------------------------------------------ safety posture

class TestSafetyPosture:
    def test_order_placement_refused_even_in_live_mode(self):
        a = _adapter(allow_order_placement=True)
        req = OrderRequest(
            venue="polymarket_us", contract_id="fed-cut-2026",
            side=Side.YES, action=OrderAction.BUY, quantity=Decimal("1"),
            order_type=OrderType.LIMIT, limit_price=Decimal("0.55"),
            client_order_id="c-1", mode=Mode.LIVE)
        with pytest.raises(PolymarketOrderPlacementDisabled):
            asyncio.run(a.place_order(req))

    def test_api_status_is_not_upgraded_from_documentation(self):
        assert _adapter().get_api_status() == APIStatus.API_DOCUMENTED_UNTESTED

    def test_independent_of_kalshi(self):
        assert is_independent("polymarket_us", "kalshi") is Independence.INDEPENDENT

    def test_official_hosts_allowed_unknown_host_refused(self):
        from prediction_arb.compliance.restrictions import (
            assert_endpoint_is_official)
        assert_endpoint_is_official(PUBLIC + "/v1/markets")
        assert_endpoint_is_official(AUTHED + "/v1/orders/open")
        with pytest.raises(PermissionError):
            assert_endpoint_is_official("https://polymarket.com/api/markets")

    def test_streaming_requires_credentials(self):
        async def cb(book):
            return None
        a = _adapter()
        with pytest.raises(PolymarketAuthError, match="WebSocket requires"):
            asyncio.run(a.subscribe_order_book("fed-cut-2026", cb))


# --------------------------------------------------------- ws feed

async def _run_ws_scenario(script, *, slug="fed-cut-2026",
                           expect_error=False):
    """Run the feed against a scripted LOCAL websockets server."""
    secret, public = _keypair()
    received: list = []
    handshakes: list = []

    async def server_handler(ws):
        handshakes.append(dict(ws.request.headers))
        await script(ws)

    server = await websockets.serve(server_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    adapter = _adapter(key_id="key-1", secret_key=secret,
                       ws_url=f"ws://127.0.0.1:{port}/v1/ws/markets")

    async def on_book(book):
        received.append(book)

    feed_state: dict = {}
    try:
        if expect_error:
            with pytest.raises(PolymarketWSUnavailable):
                await adapter.subscribe_order_book(slug, on_book)
        else:
            await adapter.subscribe_order_book(slug, on_book)
        await asyncio.sleep(0.25)
        # Snapshot stats BEFORE disconnect(), which tears the feed down.
        feed_state["stats"] = adapter.streaming_stats()
        feed_state["auth_failed"] = (
            adapter._feed is not None and adapter._feed.auth_failed)
    finally:
        await adapter.disconnect()
        server.close()
        await server.wait_closed()
    return feed_state, received, handshakes, public


class TestWebSocketFeed:
    def test_handshake_is_signed_over_the_ws_path(self):
        """P2/P6: signature covers timestamp + GET + /v1/ws/markets."""
        async def script(ws):
            await ws.recv()
            await ws.send(json.dumps({"request_id": "md-1",
                                      "subscription_type": 1,
                                      "market_data":
                                      _book_payload()["marketData"]}))
            await asyncio.sleep(0.3)

        _, received, handshakes, public = asyncio.run(_run_ws_scenario(script))
        assert len(received) == 1
        h = {k.lower(): v for k, v in handshakes[0].items()}
        message = (h["x-pm-timestamp"] + "GET" + "/v1/ws/markets").encode()
        public.verify(base64.b64decode(h["x-pm-signature"]), message)

    def test_subscribe_sends_documented_frame_and_pushes_books(self):
        sent: list = []

        async def script(ws):
            sent.append(json.loads(await ws.recv()))
            await ws.send(json.dumps({"request_id": "md-1",
                                      "market_data":
                                      _book_payload()["marketData"]}))
            await asyncio.sleep(0.3)

        _, received, _, _ = asyncio.run(_run_ws_scenario(script))
        assert sent[0]["subscribe"]["subscription_type"] == 1
        assert sent[0]["subscribe"]["market_slugs"] == ["fed-cut-2026"]
        assert "request_id" in sent[0]["subscribe"]
        book = received[0]
        assert str(book.bids[0].price) == "0.55"
        assert str(book.asks[0].price) == "0.57"

    def test_full_snapshot_replaces_book_no_stale_levels(self):
        """Each message is a complete book, so old levels must vanish."""
        async def script(ws):
            await ws.recv()
            await ws.send(json.dumps({"market_data":
                                      _book_payload()["marketData"]}))
            await asyncio.sleep(0.05)
            second = _book_payload()["marketData"]
            second["bids"] = [{"px": _amount("0.60"), "qty": "10"}]
            second["offers"] = [{"px": _amount("0.62"), "qty": "10"}]
            await ws.send(json.dumps({"market_data": second}))
            await asyncio.sleep(0.3)

        _, received, _, _ = asyncio.run(_run_ws_scenario(script))
        assert len(received) == 2
        assert len(received[1].bids) == 1
        assert str(received[1].bids[0].price) == "0.60"

    def test_non_tradeable_streamed_book_is_dropped(self):
        async def script(ws):
            await ws.recv()
            await ws.send(json.dumps({"market_data":
                                      _book_payload()["marketData"]}))
            await asyncio.sleep(0.05)
            halted = _book_payload("MARKET_STATE_HALTED")["marketData"]
            await ws.send(json.dumps({"market_data": halted}))
            await asyncio.sleep(0.3)

        feed_state, received, _, _ = asyncio.run(_run_ws_scenario(script))
        assert len(received) == 1        # halted book never reached the engine
        assert feed_state["stats"]["stats"]["not_tradeable_dropped"] == 1

    def test_heartbeats_are_counted_not_treated_as_books(self):
        async def script(ws):
            await ws.recv()
            await ws.send(json.dumps({"heartbeat": {}}))
            await ws.send(json.dumps({"market_data":
                                      _book_payload()["marketData"]}))
            await asyncio.sleep(0.3)

        feed_state, received, _, _ = asyncio.run(_run_ws_scenario(script))
        assert len(received) == 1
        assert feed_state["stats"]["stats"]["heartbeats"] == 1

    def test_subscription_error_fails_fast(self):
        async def script(ws):
            req = json.loads(await ws.recv())
            rid = req["subscribe"]["request_id"]
            await ws.send(json.dumps({"request_id": rid,
                                      "error": "unknown market"}))
            await asyncio.sleep(0.3)

        _, received, _, _ = asyncio.run(
            _run_ws_scenario(script, expect_error=True))
        assert received == []

    def test_auth_error_is_terminal(self):
        async def script(ws):
            req = json.loads(await ws.recv())
            rid = req["subscribe"]["request_id"]
            await ws.send(json.dumps({"request_id": rid,
                                      "error": "unauthorized: invalid signature"}))
            await asyncio.sleep(0.3)

        feed_state, received, _, _ = asyncio.run(
            _run_ws_scenario(script, expect_error=True))
        assert received == []
        assert feed_state["auth_failed"] is True

    def test_reconnect_resubscribes_after_clean_close(self):
        state = {"conns": 0}

        async def script(ws):
            state["conns"] += 1
            await ws.recv()
            await ws.send(json.dumps({"market_data":
                                      _book_payload()["marketData"]}))
            if state["conns"] == 1:
                await asyncio.sleep(0.05)
                await ws.close()      # clean server-side close
            else:
                await asyncio.sleep(0.5)

        async def scenario():
            secret, _ = _keypair()
            received = []

            async def handler(ws):
                await script(ws)

            server = await websockets.serve(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            a = _adapter(key_id="k", secret_key=secret,
                         ws_url=f"ws://127.0.0.1:{port}/v1/ws/markets")

            async def on_book(book):
                received.append(book)

            try:
                await a.subscribe_order_book("fed-cut-2026", on_book)
                await asyncio.sleep(1.6)   # past the 1s initial backoff
                return a.streaming_stats(), state["conns"], received
            finally:
                await a.disconnect()
                server.close()
                await server.wait_closed()

        stats, conns, received = asyncio.run(scenario())
        assert conns >= 2                       # reconnected
        assert stats["stats"]["reconnects"] >= 1
        assert len(received) >= 2               # rebased after resubscribe

    def test_unsubscribe_stops_delivery(self):
        async def scenario():
            secret, _ = _keypair()
            received = []

            async def handler(ws):
                await ws.recv()
                await ws.send(json.dumps({"market_data":
                                          _book_payload()["marketData"]}))
                await asyncio.sleep(0.1)
                # Pushed after the client unsubscribes.
                await ws.send(json.dumps({"market_data":
                                          _book_payload()["marketData"]}))
                await asyncio.sleep(0.3)

            server = await websockets.serve(handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            a = _adapter(key_id="k", secret_key=secret,
                         ws_url=f"ws://127.0.0.1:{port}/v1/ws/markets")

            async def on_book(book):
                received.append(book)

            try:
                await a.subscribe_order_book("fed-cut-2026", on_book)
                await a.unsubscribe_order_book("fed-cut-2026")
                await asyncio.sleep(0.35)
                return received
            finally:
                await a.disconnect()
                server.close()
                await server.wait_closed()

        received = asyncio.run(scenario())
        assert len(received) == 1     # only the pre-unsubscribe book
