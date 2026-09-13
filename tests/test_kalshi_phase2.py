"""Phase 2 tests — Kalshi adapter refresh + streaming.

Covers:
* host configuration and 5xx/transport failover (D5),
* the authenticated bulk orderbook endpoint (GET /markets/orderbooks),
* rate-limit tier + endpoint-cost discovery and budget clamping (K5),
* orderbook_fp parser hardening (K3),
* the authenticated WebSocket feed: sequenced snapshot/delta, gap
  recovery, reconnect, auth failure, and the WS handshake signature
  (K2/K4) — against a scripted LOCAL websockets server (no network).

Nothing here claims a live Kalshi test: the sandbox cannot reach
Kalshi (UNAVAILABLE, see docs/VENUE_RESEARCH.md K9).
"""
from __future__ import annotations

import asyncio
import json
import time
from decimal import Decimal

import httpx
import pytest
from cryptography.hazmat.primitives import serialization

from prediction_arb.adapters.kalshi import (
    KalshiAdapter,
    KalshiAuthError,
)

PRIMARY = "https://external-api.kalshi.com/trade-api/v2"
FALLBACK = "https://api.elections.kalshi.com/trade-api/v2"
WS_PRIMARY = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_FALLBACK = "wss://api.elections.kalshi.com/trade-api/ws/v2"


def _adapter(**kw) -> KalshiAdapter:
    kw.setdefault("rest_base", PRIMARY)
    kw.setdefault("rest_base_fallbacks", [FALLBACK])
    kw.setdefault("ws_url", WS_PRIMARY)
    kw.setdefault("ws_url_fallbacks", [WS_FALLBACK])
    return KalshiAdapter(**kw)


def _inject_client(adapter: KalshiAdapter, handler) -> None:
    adapter._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler))


def _keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    return pem, key.public_key()


# --------------------------------------------------------------- failover

class TestHostFailover:
    def test_5xx_rotates_to_fallback_and_succeeds(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "external-api.kalshi.com":
                return httpx.Response(500, json={"error": "boom"})
            return httpx.Response(200, json={"markets": []})

        a = _adapter()
        _inject_client(a, handler)

        async def go():
            return await a._get("/markets")

        data = asyncio.run(go())
        assert data == {"markets": []}
        assert a.active_rest_base == FALLBACK

    def test_404_does_not_rotate(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "external-api.kalshi.com"
            return httpx.Response(404, json={"error": "not found"})

        a = _adapter()
        _inject_client(a, handler)

        async def go():
            return await a._get("/markets/NOPE")

        with pytest.raises(httpx.HTTPStatusError) as e:
            asyncio.run(go())
        assert e.value.response.status_code == 404
        assert a.active_rest_base == PRIMARY

    def test_dns_failure_rotates_to_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "external-api.kalshi.com":
                raise httpx.ConnectError("DNS failure")
            return httpx.Response(200, json={"markets": []})

        a = _adapter()
        _inject_client(a, handler)

        async def go():
            return await a._get("/markets")

        # RetryPolicy wraps the pass; the first pass already rotated to the
        # fallback, so it succeeds without a second attempt.
        assert asyncio.run(go()) == {"markets": []}
        assert a.active_rest_base == FALLBACK

    def test_all_bases_5xx_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "down"})

        a = _adapter()
        _inject_client(a, handler)

        async def go():
            return await a._request("GET", "/markets")

        with pytest.raises(httpx.HTTPStatusError) as e:
            asyncio.run(go())
        assert e.value.response.status_code == 500

    def test_sticky_host_kept_on_success(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.host)
            return httpx.Response(200, json={"markets": []})

        a = _adapter()
        _inject_client(a, handler)
        a._base_index = 1  # start on the fallback (e.g. after a prior 5xx)

        async def go():
            return await a._get("/markets")

        asyncio.run(go())
        assert calls == ["api.elections.kalshi.com"]
        assert a.active_rest_base == FALLBACK

    def test_429_backs_off_and_never_rotates(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "too many requests"})

        a = _adapter()
        _inject_client(a, handler)

        async def go():
            # single pass (no retry wrapper): 429 → transport error,
            # host unchanged
            return await a._request("GET", "/markets")

        with pytest.raises(httpx.TransportError, match="rate limited"):
            asyncio.run(go())
        assert a.active_rest_base == PRIMARY

    def test_connect_probes_bases_until_healthy(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "external-api.kalshi.com":
                raise httpx.ConnectError("TLS EOF")
            return httpx.Response(200, json={"exchange_active": True})

        a = _adapter()
        _inject_client(a, handler)
        asyncio.run(a.connect())
        assert a.active_rest_base == FALLBACK

    def test_connect_refused_when_every_base_dead(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("dead")

        a = _adapter()
        _inject_client(a, handler)
        with pytest.raises(ConnectionError, match="all configured bases"):
            asyncio.run(a.connect())


# ------------------------------------------------------------------- bulk

class TestBulkOrderbooks:
    def test_requires_credentials(self):
        a = _adapter()
        _inject_client(a, lambda r: httpx.Response(200, json={}))

        async def go():
            return await a.get_order_books(["T1"])

        with pytest.raises(KalshiAuthError, match="API key"):
            asyncio.run(go())

    def test_batches_of_100_and_parsing(self):
        pem, _ = _keypair()
        a = _adapter(api_key_id="key-1", private_key_pem=pem)
        seen: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/markets/orderbooks")
            assert "KALSHI-ACCESS-KEY" in request.headers
            assert "KALSHI-ACCESS-SIGNATURE" in request.headers
            assert "KALSHI-ACCESS-TIMESTAMP" in request.headers
            tickers = [v for k, v in request.url.params.multi_items()
                       if k == "tickers"]
            seen.append(tickers)
            books = [{
                "ticker": t,
                "orderbook_fp": {
                    "yes_dollars": [["0.4000", "10.00"]],
                    "no_dollars": [["0.5500", "5.00"]],
                },
            } for t in tickers[:2]]
            return httpx.Response(200, json={"orderbooks": books})

        _inject_client(a, handler)

        async def go():
            return await a.get_order_books([f"T{i:03d}" for i in range(250)])

        books = asyncio.run(go())
        assert [len(s) for s in seen] == [100, 100, 50]
        assert set(books) == {f"T{i:03d}" for i in (0, 1, 100, 101, 200, 201)}
        b = books["T100"]
        assert b.bids[0].price == Decimal("0.40")
        assert b.asks[0].price == Decimal("0.45")  # 1 - 0.55
        assert b.asks[0].quantity == Decimal("5.00")


# ------------------------------------------------------------ rate limits

class TestRateLimitDiscovery:
    def test_tier_parsing(self):
        pem, _ = _keypair()
        a = _adapter(api_key_id="k", private_key_pem=pem)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/account/limits")
            return httpx.Response(200, json={
                "usage_tier": "expert",
                "read": {"refill_rate": 600, "bucket_capacity": 600},
                "write": {"refill_rate": 600, "bucket_capacity": 1200},
                "grants": [],
            })

        _inject_client(a, handler)

        async def go():
            return await a.get_rate_limits()

        limits = asyncio.run(go())
        assert limits.usage_tier == "expert"
        assert limits.read_refill_rate == 600.0
        assert limits.write_refill_rate == 600.0
        assert a.rate_limits is limits

    def test_endpoint_cost_parsing(self):
        pem, _ = _keypair()
        a = _adapter(api_key_id="k", private_key_pem=pem)

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/account/endpoint_costs")
            return httpx.Response(200, json={
                "default_cost": 10,
                "endpoint_costs": [
                    {"method": "post", "path": "/portfolio/orders",
                     "cost": 2},
                    {"method": "delete", "path": "/portfolio/orders/{id}",
                     "cost": 1},
                ],
            })

        _inject_client(a, handler)

        async def go():
            return await a.get_endpoint_costs()

        costs = asyncio.run(go())
        assert costs == {"POST /portfolio/orders": 2,
                         "DELETE /portfolio/orders/{id}": 1}
        assert a.endpoint_cost("get", "/markets") == 10
        assert a.endpoint_cost("post", "/portfolio/orders") == 2

    def test_apply_clamps_only_never_raises_budget(self):
        from prediction_arb.adapters.kalshi import KalshiRateLimits
        a = _adapter(reads_per_sec=8.0, writes_per_sec=3.0)
        limits = KalshiRateLimits(
            usage_tier="basic", read_refill_rate=50.0,
            read_bucket_capacity=50.0, write_refill_rate=100.0,
            write_bucket_capacity=200.0)
        # 50 tok/s / 10 tok * 0.8 headroom = 4 read req/s  → clamps 8→4
        # 100 tok/s / 10 tok * 0.8 = 8 write req/s         → 3 stays 3
        a.apply_rate_limits(limits, headroom=0.8)
        assert a.effective_reads_per_sec() == pytest.approx(4.0)
        assert a.effective_writes_per_sec() == pytest.approx(3.0)

    def test_apply_with_zero_budget_keeps_configured(self):
        from prediction_arb.adapters.kalshi import KalshiRateLimits
        a = _adapter(reads_per_sec=8.0, writes_per_sec=3.0)
        limits = KalshiRateLimits(
            usage_tier="unknown", read_refill_rate=0.0,
            read_bucket_capacity=0.0, write_refill_rate=0.0,
            write_bucket_capacity=0.0)
        a.apply_rate_limits(limits)
        assert a.effective_reads_per_sec() == pytest.approx(8.0)
        assert a.effective_writes_per_sec() == pytest.approx(3.0)


# ---------------------------------------------------------- book parsing

class TestBookParsing:
    def test_ws_snapshot_key_names(self):
        a = _adapter()
        data = {"orderbook_fp": {
            "yes_dollars_fp": [["0.0800", "300.00"], ["0.2200", "333.00"]],
            "no_dollars_fp": [["0.5400", "20.00"], ["0.5600", "146.00"]],
        }}
        book = a.parse_order_book("T", data, sequence_number=7)
        assert book.bids[0].price == Decimal("0.2200")
        assert book.asks[0].price == Decimal("0.4400")
        assert book.sequence_number == 7

    def test_malformed_levels_dropped_not_guessed(self):
        a = _adapter()
        data = {"orderbook_fp": {
            "yes_dollars": [
                ["0.40", "10.00"],     # valid
                ["1.00", "10.00"],     # non-tradeable price
                ["0.00", "10.00"],     # non-tradeable price
                ["0.50", "-5.00"],     # negative quantity
                ["0.60", "0.00"],      # zero quantity
                ["abc", "5.00"],       # not a number
                ["0.70"],              # short pair
            ],
            "no_dollars": [["0.55", "7.5"]],
        }}
        book = a.parse_order_book("T", data)
        assert [l.price for l in book.bids] == [Decimal("0.40")]
        assert [l.price for l in book.asks] == [Decimal("0.45")]
        assert book.asks[0].quantity == Decimal("7.5")

    def test_empty_payload_is_empty_book(self):
        a = _adapter()
        book = a.parse_order_book("T", {"orderbook_fp": {}})
        assert book.bids == [] and book.asks == []


# ------------------------------------------------------- websocket feed

class TestWebSocketFeed:
    """Scripted local WS server; verifies the full documented protocol."""

    @staticmethod
    def _mk_adapter(pem, public_key, port):
        url = f"ws://127.0.0.1:{port}/trade-api/ws/v2"
        return _adapter(rest_base=PRIMARY, rest_base_fallbacks=[FALLBACK],
                        ws_url=url, ws_url_fallbacks=[],
                        api_key_id="test-key", private_key_pem=pem), \
            public_key

    def _run_feed_scenario(self, server_script, expect=None):
        """server_script(ws_client_conn, commands) → coroutine run per
        connection. Returns (feed, collected_books, commands, sig_ok)."""
        pem, public_key = _keypair()
        adapter, pub = self._mk_adapter(pem, public_key, port=0)

        commands: list[dict] = []
        books: list = []
        sig_checks: list[bool] = []

        async def server_handler(ws):
            # Verify the handshake signature: sign
            # ts + "GET" + "/trade-api/ws/v2" (host-independent path).
            headers = dict(ws.request.headers)
            ts = headers.get("kalshi-access-timestamp", "")
            sig_b64 = headers.get("kalshi-access-signature", "")
            key_id = headers.get("kalshi-access-key", "")
            ok = False
            try:
                import base64
                from cryptography.hazmat.primitives import hashes
                from cryptography.hazmat.primitives.asymmetric import padding
                msg = f"{ts}GET/trade-api/ws/v2".encode()
                pub.verify(base64.b64decode(sig_b64), msg,
                           padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                       salt_length=padding.PSS.DIGEST_LENGTH),
                           hashes.SHA256())
                ok = (key_id == "test-key")
            except Exception:
                ok = False
            sig_checks.append(ok)
            await server_script(ws, commands)

        async def main2():
            import websockets
            server = await websockets.serve(server_handler, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            adapter.ws_url = f"ws://127.0.0.1:{port}/trade-api/ws/v2"
            adapter.ws_urls = [adapter.ws_url]
            adapter._client = httpx.AsyncClient()
            results = await expect(adapter, books)
            await adapter.disconnect()
            server.close()
            await server.wait_closed()
            return results, books

        results, books = asyncio.run(main2())
        return results, commands, sig_checks, books

    def test_snapshot_delta_and_unsubscribe(self):
        async def script(ws, commands):
            async for raw in ws:
                cmd = json.loads(raw)
                commands.append(cmd)
                if cmd["cmd"] == "subscribe":
                    t = cmd["params"]["market_tickers"][0]
                    await ws.send(json.dumps({
                        "id": cmd["id"], "type": "subscribed",
                        "msg": {"channel": "orderbook_delta", "sid": 1}}))
                    await ws.send(json.dumps({
                        "type": "orderbook_snapshot", "sid": 1, "seq": 2,
                        "msg": {
                            "market_ticker": t,
                            "market_id": "uuid-1",
                            "yes_dollars_fp": [["0.2200", "333.00"],
                                               ["0.0800", "300.00"]],
                            "no_dollars_fp": [["0.5600", "146.00"],
                                              ["0.5400", "20.00"]],
                        }}))
                    await asyncio.sleep(0.05)
                    await ws.send(json.dumps({
                        "type": "orderbook_delta", "sid": 1, "seq": 3,
                        "msg": {"market_ticker": t, "market_id": "uuid-1",
                                "price_dollars": "0.2200",
                                "delta_fp": "-100.00", "side": "yes",
                                "ts_ms": 1}}))
                    await asyncio.sleep(0.2)

        books: list = []

        async def expect(adapter, out):
            cb = []

            async def on_book(book):
                cb.append(book)
                out.append(book)
            await adapter.subscribe_order_book("KXTEST-1", on_book)
            await asyncio.sleep(0.3)
            assert len(cb) >= 2
            await adapter.unsubscribe_order_book("KXTEST-1")
            await asyncio.sleep(0.1)
            return cb

        cb, commands, sig_checks, _books = self._run_feed_scenario(
            script, expect)

        # handshake signed the host-independent WS path
        assert all(sig_checks)
        # snapshot: YES bids desc, asks synthesized asc
        snap = cb[0]
        assert snap.bids[0].price == Decimal("0.2200")
        assert snap.bids[0].quantity == Decimal("333.00")
        assert snap.asks[0].price == Decimal("0.4400")
        assert snap.asks[0].quantity == Decimal("146.00")
        assert snap.sequence_number == 2
        # delta: 333.00 - 100.00 = 233.00 at 0.22
        delta_book = cb[1]
        lvl = [l for l in delta_book.bids
               if l.price == Decimal("0.2200")][0]
        assert lvl.quantity == Decimal("233.00")
        assert delta_book.sequence_number == 3
        # unsubscribe carried the server-assigned sid
        unsubs = [c for c in commands if c["cmd"] == "unsubscribe"]
        assert unsubs and unsubs[0]["params"]["sids"] == [1]

    def test_sequence_gap_triggers_recovery_not_silent_apply(self):
        async def script(ws, commands):
            async for raw in ws:
                cmd = json.loads(raw)
                commands.append(cmd)
                if cmd["cmd"] == "subscribe":
                    t = cmd["params"]["market_tickers"][0]
                    await ws.send(json.dumps({
                        "id": cmd["id"], "type": "subscribed",
                        "msg": {"channel": "orderbook_delta", "sid": 4}}))
                    await ws.send(json.dumps({
                        "type": "orderbook_snapshot", "sid": 4, "seq": 2,
                        "msg": {"market_ticker": t,
                                "yes_dollars_fp": [["0.5000", "10.00"]],
                                "no_dollars_fp": []}}))
                    await asyncio.sleep(0.05)
                    # GAP: seq jumps 2 → 5, skipping 3 and 4
                    await ws.send(json.dumps({
                        "type": "orderbook_delta", "sid": 4, "seq": 5,
                        "msg": {"market_ticker": t,
                                "price_dollars": "0.5000",
                                "delta_fp": "-10.00", "side": "yes",
                                "ts_ms": 1}}))
                    await asyncio.sleep(0.05)
                    # feed must now ask for a fresh snapshot (K4 recovery)
                elif cmd["cmd"] == "update_subscription":
                    assert cmd["params"]["action"] == "get_snapshot"
                    assert cmd["params"]["sid"] == 4
                    await ws.send(json.dumps({
                        "type": "orderbook_snapshot", "sid": 4, "seq": 10,
                        "msg": {"market_ticker":
                                cmd["params"]["market_tickers"][0],
                                "yes_dollars_fp": [["0.3000", "99.00"]],
                                "no_dollars_fp": []}}))
                    await asyncio.sleep(0.2)

        async def expect(adapter, out):
            async def on_book(book):
                out.append(book)
            await adapter.subscribe_order_book("KXGAP-1", on_book)
            await asyncio.sleep(0.5)
            return adapter._feed.stats

        stats, commands, sig_checks, books = self._run_feed_scenario(
            script, expect)

        assert stats.sequence_gaps == 1
        recovers = [c for c in commands
                    if c["cmd"] == "update_subscription"]
        assert recovers and recovers[0]["params"]["action"] == "get_snapshot"
        # The gap delta (which would have removed the 0.50 level) must NOT
        # have been applied: final book is the recovery snapshot.
        final = books[-1]
        assert [l.price for l in final.bids] == [Decimal("0.3000")]
        assert final.bids[0].quantity == Decimal("99.00")
        assert final.sequence_number == 10

    def test_reconnect_resubscribes_and_rebases(self):
        connections = {"n": 0}

        async def script(ws, commands):
            connections["n"] += 1
            first = connections["n"] == 1
            async for raw in ws:
                cmd = json.loads(raw)
                commands.append(cmd)
                if cmd["cmd"] == "subscribe":
                    t = cmd["params"]["market_tickers"][0]
                    await ws.send(json.dumps({
                        "id": cmd["id"], "type": "subscribed",
                        "msg": {"channel": "orderbook_delta",
                                "sid": 1 if first else 2}}))
                    await ws.send(json.dumps({
                        "type": "orderbook_snapshot",
                        "sid": 1 if first else 2,
                        "seq": 2,
                        "msg": {"market_ticker": t,
                                "yes_dollars_fp": [["0.5000", "10.00"]],
                                "no_dollars_fp": []}}))
                    if first:
                        await asyncio.sleep(0.1)
                        await ws.close()
                    else:
                        await asyncio.sleep(0.3)

        async def expect(adapter, out):
            async def on_book(book):
                out.append(book)
            await adapter.subscribe_order_book("KXREC-1", on_book)
            # first snapshot applied, then connection drops → feed must
            # reconnect (1s backoff) and resubscribe → second snapshot
            await asyncio.sleep(4.0)
            return adapter._feed.stats

        stats, commands, sig_checks, books = self._run_feed_scenario(
            script, expect)

        assert connections["n"] >= 2
        assert stats.reconnects >= 1
        assert stats.snapshots >= 2
        # two independent snapshots for the same ticker
        assert len([b for b in books]) >= 2
        subs = [c for c in commands if c["cmd"] == "subscribe"]
        assert len(subs) >= 2

    def test_auth_error_is_terminal(self):
        async def script(ws, commands):
            async for raw in ws:
                cmd = json.loads(raw)
                commands.append(cmd)
                if cmd["cmd"] == "subscribe":
                    await ws.send(json.dumps({
                        "id": cmd["id"], "type": "error",
                        "msg": {"code": 9,
                                "msg": "Authentication required"}}))
                    await asyncio.sleep(0.3)

        async def expect(adapter, out):
            async def on_book(book):
                out.append(book)
            from prediction_arb.adapters.kalshi_ws import (
                KalshiWSUnavailable,
            )
            with pytest.raises(KalshiWSUnavailable):
                await adapter.subscribe_order_book("KXAUTH-1", on_book)
            # terminal: no further retries — feed is closed
            assert adapter._feed.auth_failed
            await asyncio.sleep(0.5)
            return adapter._feed.stats

        stats, commands, sig_checks, _books = self._run_feed_scenario(
            script, expect)
        assert stats.reconnects == 0

    def test_subscribe_without_credentials_raises(self):
        a = _adapter(ws_url="ws://127.0.0.1:1/trade-api/ws/v2",
                     ws_url_fallbacks=[])

        async def go():
            await a.subscribe_order_book(
                "T", lambda b: asyncio.sleep(0))

        with pytest.raises(KalshiAuthError, match="API key"):
            asyncio.run(go())
