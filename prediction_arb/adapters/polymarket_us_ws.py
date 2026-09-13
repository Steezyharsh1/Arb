"""Polymarket US markets WebSocket order-book feed (Phase 3).

Implements the documented authenticated market-data WebSocket
(docs.polymarket.us/api-reference/websocket/*, VERIFIED / DOC /
2026-09-13, evidence row P6):

* Connect to ``wss://api.polymarket.us/v1/ws/markets`` with
  ``X-PM-Access-Key`` / ``X-PM-Timestamp`` / ``X-PM-Signature`` handshake
  headers. The signature message is ``timestamp + "GET" + "/v1/ws/markets"``.
  Authentication is required even though the same data is available
  unauthenticated over REST.
* Subscribe with
  ``{"subscribe": {"request_id": ..., "subscription_type": 1,
  "market_slugs": [...]}}`` where type 1 = ``MARKET_DATA`` (full book).
  Unsubscribe echoes the original ``request_id``.
* Responses carry ``market_data`` containing the FULL book
  (``bids``/``offers``), not an incremental delta.
* The server sends ``{"heartbeat": {}}`` keepalives; a subscription
  failure arrives as ``{"request_id": ..., "error": "..."}``.
* Documented cap: 100 markets per subscription.

**Why there is no sequence-gap machinery here (contrast Kalshi).**
Kalshi streams `orderbook_delta` messages carrying a `seq`, so a dropped
message silently corrupts the local book and gap detection is mandatory.
Polymarket US pushes a complete replacement book on every update, so
there is no incremental state to corrupt: a dropped message costs one
stale interval, and the next message fully rebases the book. Inventing a
sequence counter the protocol does not have would be fabricating a
guarantee. What IS enforced instead:

* every pushed book is timestamped from the venue's ``transactTime`` so
  downstream staleness checks work on venue time, not arrival time;
* non-tradeable market states are filtered by the shared parser, so a
  halted book is dropped rather than pushed as executable;
* reconnects resubscribe every market and count toward ``reconnects``.

Safety notes:
* Market-data only. This module never places, amends, or cancels orders.
* An authentication rejection during the handshake is terminal — retrying
  cannot fix a bad key, so the feed stops rather than hot-looping.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

import websockets

from ..core.types import OrderBook

log = logging.getLogger(__name__)

OrderBookCallback = Callable[[OrderBook], Awaitable[None]]
BookParser = Callable[[str, dict], Optional[OrderBook]]

#: SUBSCRIPTION_TYPE_MARKET_DATA — full order book and stats (P6).
SUBSCRIPTION_TYPE_MARKET_DATA = 1

#: Documented maximum markets per subscription request (P6).
MAX_MARKETS_PER_SUBSCRIPTION = 100

_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
_OPEN_TIMEOUT_S = 15.0
_FIRST_BOOK_TIMEOUT_S = 30.0

#: Handshake path that is signed for the markets stream (P2/P6).
WS_MARKETS_PATH = "/v1/ws/markets"


class PolymarketWSAuthError(Exception):
    """WebSocket authentication rejected (terminal; do not retry)."""


class PolymarketWSUnavailable(Exception):
    """WebSocket feed could not be established or is closed."""


@dataclass
class FeedStats:
    """Operational counters, surfaced for run evidence."""
    books: int = 0
    reconnects: int = 0
    messages_dropped: int = 0
    heartbeats: int = 0
    subscription_errors: int = 0
    callback_errors: int = 0
    not_tradeable_dropped: int = 0
    last_book_at: Optional[datetime] = None


class PolymarketWebSocketFeed:
    """One authenticated Polymarket US markets-WebSocket connection
    serving any number of per-market full-book subscriptions.

    Usage::

        feed = PolymarketWebSocketFeed(
            venue="polymarket_us",
            ws_url="wss://api.polymarket.us/v1/ws/markets",
            auth_headers_factory=adapter.auth_headers,
            book_parser=adapter.parse_order_book)
        await feed.subscribe("btc-100k-2026", on_book)
        ...
        await feed.close()
    """

    def __init__(self, venue: str, ws_url: str,
                 auth_headers_factory: Callable[[str, str], dict[str, str]],
                 book_parser: BookParser):
        if not ws_url:
            raise ValueError("ws_url must not be empty")
        self.venue = venue
        self.ws_url = ws_url
        self._auth_headers_factory = auth_headers_factory
        self._parse_book = book_parser
        self._callbacks: dict[str, OrderBookCallback] = {}
        self._request_ids: dict[str, str] = {}   # slug → request_id
        self._ws: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self._auth_failed = False
        self._req_seq = 0
        self._first_books: dict[str, asyncio.Event] = {}
        # Slugs for which a real book has actually been delivered. The
        # wake-up Event alone is NOT proof of success: close() and
        # terminal auth failures also set it to release waiters, so
        # subscribe() must check delivery explicitly or it would report
        # success for a feed that never produced a book.
        self._delivered: set[str] = set()
        self.stats = FeedStats()

    # ------------------------------------------------------------ public
    @property
    def connected(self) -> bool:
        return self._ws is not None

    @property
    def auth_failed(self) -> bool:
        return self._auth_failed

    def markets(self) -> list[str]:
        return sorted(self._callbacks)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._closed = False
            self._task = asyncio.create_task(
                self._run(), name="polymarket-us-ws-feed")

    async def subscribe(self, slug: str,
                        callback: OrderBookCallback) -> None:
        """Register a market and wait for its first full book.

        Returns only once a book has actually been delivered, so callers
        can rely on the callback having fired at least once.
        """
        if self._closed or self._auth_failed:
            raise PolymarketWSUnavailable(
                "Polymarket US WebSocket feed is closed"
                + (" (authentication failed)" if self._auth_failed else ""))
        if len(self._callbacks) >= MAX_MARKETS_PER_SUBSCRIPTION \
                and slug not in self._callbacks:
            raise PolymarketWSUnavailable(
                f"Polymarket US allows at most "
                f"{MAX_MARKETS_PER_SUBSCRIPTION} markets per subscription "
                f"(documented limit); {len(self._callbacks)} are already "
                f"subscribed on this connection.")
        await self.start()
        self._callbacks[slug] = callback
        self._delivered.discard(slug)
        first_book = asyncio.Event()
        self._first_books[slug] = first_book
        if self.connected:
            await self._send_subscribe(slug)
        # If the connection is still establishing, _resubscribe_all()
        # picks the market up once it connects.
        try:
            await asyncio.wait_for(first_book.wait(),
                                   timeout=_FIRST_BOOK_TIMEOUT_S)
        except asyncio.TimeoutError:
            pass
        if slug in self._delivered and slug in self._callbacks:
            return
        # Failure path: clean up registration and surface the problem.
        self._callbacks.pop(slug, None)
        self._first_books.pop(slug, None)
        self._request_ids.pop(slug, None)
        self._delivered.discard(slug)
        reason = ("authentication failed" if self._auth_failed
                  else "no book received")
        raise PolymarketWSUnavailable(
            f"Polymarket US WebSocket did not deliver an order book for "
            f"{slug} (feed connected={self.connected}, {reason}). Check "
            f"API key validity and network connectivity.")

    async def unsubscribe(self, slug: str) -> None:
        """Remove a subscription; server-side unsubscribe echoes the
        original request_id."""
        self._callbacks.pop(slug, None)
        self._delivered.discard(slug)
        ev = self._first_books.pop(slug, None)
        if ev is not None and not ev.is_set():
            ev.set()   # unblock a still-waiting subscribe()
        request_id = self._request_ids.pop(slug, None)
        if self.connected and request_id is not None:
            try:
                assert self._ws is not None
                await self._ws.send(json.dumps(
                    {"unsubscribe": {"request_id": request_id}}))
            except Exception as exc:   # already dead → reconnect path handles it
                log.warning("polymarket-us-ws: unsubscribe send failed for "
                            "%s: %s", slug, exc)

    async def close(self) -> None:
        self._closed = True
        for ev in self._first_books.values():
            ev.set()
        task = self._task
        self._task = None
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                task.cancel()
        self._callbacks.clear()
        self._request_ids.clear()
        self._first_books.clear()
        self._delivered.clear()

    # -------------------------------------------------------------- loop
    async def _run(self) -> None:
        backoff = _BACKOFF_INITIAL_S
        first_attempt = True
        while not self._closed:
            try:
                headers = self._auth_headers_factory("GET", WS_MARKETS_PATH)
                self._ws = await websockets.connect(
                    self.ws_url, additional_headers=headers,
                    open_timeout=_OPEN_TIMEOUT_S)
                if first_attempt:
                    first_attempt = False
                else:
                    self.stats.reconnects += 1
                log.info("polymarket-us-ws: connected to %s", self.ws_url)
                backoff = _BACKOFF_INITIAL_S
                await self._resubscribe_all()
                async for raw in self._ws:
                    if self._closed:
                        break
                    await self._on_message(raw)
            except PolymarketWSAuthError as exc:
                self._auth_failed = True
                self._closed = True
                self._fail_waiters()
                log.error("polymarket-us-ws: terminal authentication "
                          "failure: %s", exc)
                return
            except Exception as exc:
                if self._closed:
                    break
                if _is_auth_rejection(exc):
                    self._auth_failed = True
                    self._closed = True
                    self._fail_waiters()
                    log.error("polymarket-us-ws: handshake rejected as "
                              "unauthorized (%s); not retrying", exc)
                    return
                log.warning("polymarket-us-ws: connection lost (%s: %s); "
                            "reconnecting in %.1fs",
                            type(exc).__name__, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
            else:
                # Clean server-side close: reconnect with backoff so a
                # flapping endpoint cannot hot-loop.
                if not self._closed:
                    log.info("polymarket-us-ws: server closed the "
                             "connection; reconnecting in %.1fs", backoff)
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, _BACKOFF_MAX_S)
            finally:
                ws = self._ws
                self._ws = None
                if ws is not None:
                    try:
                        await ws.close()
                    except Exception:
                        pass

    def _fail_waiters(self) -> None:
        for ev in self._first_books.values():
            ev.set()   # waiters re-check auth_failed/connected themselves

    async def _resubscribe_all(self) -> None:
        self._request_ids.clear()
        for slug in list(self._callbacks):
            await self._send_subscribe(slug)

    # ---------------------------------------------------------- commands
    def _next_request_id(self) -> str:
        self._req_seq += 1
        return f"md-{self._req_seq}"

    async def _send_subscribe(self, slug: str) -> None:
        assert self._ws is not None
        request_id = self._next_request_id()
        self._request_ids[slug] = request_id
        await self._ws.send(json.dumps({
            "subscribe": {
                "request_id": request_id,
                "subscription_type": SUBSCRIPTION_TYPE_MARKET_DATA,
                "market_slugs": [slug],
            }
        }))

    # ---------------------------------------------------------- messages
    async def _on_message(self, raw: Any) -> None:
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            self.stats.messages_dropped += 1
            log.warning("polymarket-us-ws: dropping non-JSON message")
            return
        if not isinstance(msg, dict):
            self.stats.messages_dropped += 1
            return

        if "heartbeat" in msg:
            self.stats.heartbeats += 1
            return

        error = msg.get("error")
        if error:
            self.stats.subscription_errors += 1
            request_id = msg.get("request_id") or msg.get("requestId")
            slug = self._slug_for_request(request_id)
            log.error("polymarket-us-ws: subscription error for %s: %s",
                      slug or request_id, error)
            if _is_auth_error_text(error):
                raise PolymarketWSAuthError(str(error))
            # A failed subscription must not leave subscribe() hanging for
            # the full timeout — wake it so it can report the failure.
            if slug is not None:
                ev = self._first_books.get(slug)
                if ev is not None and not ev.is_set():
                    self._callbacks.pop(slug, None)
                    ev.set()
            return

        market_data = msg.get("market_data") or msg.get("marketData")
        if market_data is None:
            # marketDataLite / trade / snapshot acks are not book updates.
            return
        await self._handle_book(market_data)

    def _slug_for_request(self, request_id: Any) -> Optional[str]:
        if not request_id:
            return None
        for slug, rid in self._request_ids.items():
            if rid == request_id:
                return slug
        return None

    async def _handle_book(self, market_data: dict[str, Any]) -> None:
        slug = (market_data.get("marketSlug")
                or market_data.get("market_slug"))
        if not slug:
            self.stats.messages_dropped += 1
            log.warning("polymarket-us-ws: book message without a market slug")
            return
        callback = self._callbacks.get(slug)
        if callback is None:
            return   # unsubscribed between send and receive

        # The shared adapter parser enforces the tradeable-state rule and
        # drops malformed levels, so streamed and polled books cannot
        # disagree about what counts as executable.
        book = self._parse_book(slug, market_data)
        if book is None:
            self.stats.not_tradeable_dropped += 1
            return

        self.stats.books += 1
        self.stats.last_book_at = book.timestamp
        self._delivered.add(slug)
        ev = self._first_books.get(slug)
        if ev is not None and not ev.is_set():
            ev.set()
        try:
            await callback(book)
        except Exception as exc:
            self.stats.callback_errors += 1
            log.warning("polymarket-us-ws: book callback failed for %s: %s",
                        slug, exc)


def _is_auth_error_text(error: Any) -> bool:
    text = str(error).lower()
    return any(token in text for token in
               ("unauthorized", "authentication", "invalid signature",
                "invalid key", "forbidden"))


def _is_auth_rejection(exc: BaseException) -> bool:
    """True when a handshake failure is an auth rejection (401/403).

    Retrying cannot fix a bad key, so these are terminal.
    """
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return True
    response = getattr(exc, "response", None)
    if response is not None and getattr(response, "status_code", None) in (
            401, 403):
        return True
    return False
