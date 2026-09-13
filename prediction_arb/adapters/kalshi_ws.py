"""Kalshi Trade API WebSocket order-book feed (Phase 2).

Implements the documented authenticated WebSocket protocol:

* Connect with ``KALSHI-ACCESS-KEY`` / ``KALSHI-ACCESS-SIGNATURE`` /
  ``KALSHI-ACCESS-TIMESTAMP`` handshake headers; the signature message is
  ``timestamp + "GET" + "/trade-api/ws/v2"`` (host-independent path from the
  API root, no query string).
* Subscribe to the ``orderbook_delta`` channel for specific market tickers.
  The server sends an ``orderbook_snapshot`` (full state) first, then
  incremental ``orderbook_delta`` messages.
* Every snapshot/delta carries a top-level ``seq``. Sequence gaps and
  out-of-order updates trigger **recovery** (request a fresh snapshot for the
  subscription, or resubscribe) and are never applied silently — this is the
  K4 invariant from docs/VENUE_RESEARCH.md.
* Connection drops trigger reconnection with exponential backoff; all active
  markets are resubscribed on reconnect so every book is rebased from a new
  snapshot.

Wire format verified against docs.kalshi.com (API docs + AsyncAPI 3.0 spec)
on 2026-09-13. Evidence ledger: docs/VENUE_RESEARCH.md rows K2 (hosts) and
K4 (sequencing/recovery), both VERIFIED / DOC / 2026-09-13. A live smoke test
was UNAVAILABLE from this sandbox (TLS EOF), so this module is documented
as DOC-verified, not live-tested.

Safety notes:
* This module is market-data only. It never places, amends, or cancels
  orders.
* Errors with code 9 (authentication required) or 25 (subscription buffer
  overflow) / 10 (channel error) are terminal for the affected subscription
  or the whole connection; the feed reports them and stops retrying rather
  than masking a broken session.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable, Optional

import websockets

from ..core.clock import utcnow
from ..core.types import (
    MicrostructureType,
    OrderBook,
    PriceLevel,
)

log = logging.getLogger(__name__)

OrderBookCallback = Callable[[OrderBook], Awaitable[None]]

WS_ORDERBOOK_CHANNEL = "orderbook_delta"

# Error codes that are terminal for a subscription (the user must
# resubscribe): 10 channel error, 25 subscription buffer overflow.
_TERMINAL_SUB_ERROR_CODES = frozenset({10, 25})
# Error code 9: authentication required — fatal for the whole connection;
# retrying cannot fix a bad/missing key.
_AUTH_ERROR_CODE = 9

_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 30.0
_OPEN_TIMEOUT_S = 15.0


class KalshiWSAuthError(Exception):
    """WebSocket authentication rejected (terminal; do not retry)."""


class KalshiWSUnavailable(Exception):
    """WebSocket feed could not be established or is closed."""


def _parse_dollars(value: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return d if d.is_finite() else None


def _parse_levels(pairs: Any) -> dict[Decimal, Decimal]:
    """Parse ``[[price, quantity], ...]`` fixed-point dollar strings into a
    price→quantity map, dropping malformed or non-tradeable levels (price
    must lie in (0,1); quantity > 0)."""
    out: dict[Decimal, Decimal] = {}
    if not isinstance(pairs, list):
        return out
    for pair in pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            log.warning("kalshi-ws: skipping malformed level %r", pair)
            continue
        price = _parse_dollars(pair[0])
        qty = _parse_dollars(pair[1])
        if price is None or qty is None:
            log.warning("kalshi-ws: skipping malformed level %r", pair)
            continue
        if not (Decimal(0) < price < Decimal(1)) or qty <= 0:
            log.warning("kalshi-ws: skipping non-tradeable level %r", pair)
            continue
        out[price] = out.get(price, Decimal(0)) + qty
    return out


class _BookState:
    """Local order-book state for one market, built from one WS
    subscription.  ``yes``/``no`` hold resting BIDs (Kalshi books are
    bid-only; YES asks are synthesized as ``1 - no_bid``)."""

    __slots__ = ("venue", "ticker", "yes", "no", "last_seq", "sid", "ready")

    def __init__(self, venue: str, ticker: str):
        self.venue = venue
        self.ticker = ticker
        self.yes: dict[Decimal, Decimal] = {}
        self.no: dict[Decimal, Decimal] = {}
        self.last_seq: Optional[int] = None
        self.sid: Optional[int] = None
        self.ready = False  # False until a snapshot has been applied

    def reset_sequencing(self) -> None:
        """Invalidate sequence tracking (e.g. after a reconnect). The book
        itself is kept — consumers treat it as stale via its timestamp."""
        self.last_seq = None
        self.ready = False

    def apply_snapshot(self, msg: dict[str, Any], seq: Optional[int]) -> None:
        self.yes = _parse_levels(
            msg.get("yes_dollars_fp") or msg.get("yes_dollars"))
        self.no = _parse_levels(
            msg.get("no_dollars_fp") or msg.get("no_dollars"))
        self.last_seq = seq
        self.ready = True

    def apply_delta(self, msg: dict[str, Any], seq: Optional[int]) -> bool:
        """Apply one delta. Returns False when the delta is not the next
        expected sequence (gap or out-of-order) — the caller must recover,
        not apply."""
        if (not self.ready or seq is None
                or self.last_seq is None or seq != self.last_seq + 1):
            return False
        self.last_seq = seq
        side = str(msg.get("side", "")).lower()
        price = _parse_dollars(msg.get("price_dollars"))
        delta = _parse_dollars(msg.get("delta_fp"))
        if side not in ("yes", "no") or price is None or delta is None:
            return False
        book = self.yes if side == "yes" else self.no
        new_qty = book.get(price, Decimal(0)) + delta
        if new_qty <= 0:
            book.pop(price, None)
        else:
            book[price] = new_qty
        return True

    def to_order_book(self) -> OrderBook:
        """Express the book from the YES perspective: resting YES bids, and
        synthesized YES asks (a NO bid at p is a sell-YES at 1 - p)."""
        yes_bids = [PriceLevel(p, q)
                    for p, q in sorted(self.yes.items(), key=lambda kv: -kv[0])]
        # NO bids sorted descending → asks (1-p) sorted ascending.
        yes_asks = [PriceLevel(Decimal(1) - p, q)
                    for p, q in sorted(self.no.items(), key=lambda kv: -kv[0])]
        return OrderBook(
            venue=self.venue,
            contract_id=self.ticker,
            bids=yes_bids,
            asks=yes_asks,
            timestamp=utcnow(),
            sequence_number=self.last_seq,
            microstructure_type=MicrostructureType.CLOB,
            raw=None,
        )


@dataclass
class FeedStats:
    """Operational counters for the feed (surfaced for the 24h-run exit
    criterion: cycle success, gaps, reconnects)."""
    snapshots: int = 0
    deltas_applied: int = 0
    sequence_gaps: int = 0
    reconnects: int = 0
    messages_dropped: int = 0
    terminal_errors: int = 0
    callback_errors: int = 0
    last_book_at: Optional[datetime] = None


class KalshiWebSocketFeed:
    """One authenticated Kalshi WebSocket connection serving any number of
    per-market ``orderbook_delta`` subscriptions.

    Usage::

        feed = KalshiWebSocketFeed(
            venue="kalshi",
            ws_urls=[primary, fallback],
            # sign the host-independent WS path: GET /trade-api/ws/v2
            auth_headers=lambda: adapter._auth_headers(
                "GET", "", api_prefix="/trade-api/ws/v2"),
        )
        await feed.start()
        await feed.subscribe("KXBTCD-25AUG0517-T114999.99", on_book)
        ...
        await feed.close()
    """

    def __init__(self, venue: str, ws_urls: list[str],
                 auth_headers: Callable[[], dict[str, str]]):
        if not ws_urls:
            raise ValueError("ws_urls must not be empty")
        self.venue = venue
        self.ws_urls = list(ws_urls)
        self._auth_headers = auth_headers
        self._callbacks: dict[str, OrderBookCallback] = {}
        self._states: dict[str, _BookState] = {}
        self._pending_sid: dict[int, str] = {}  # command id → ticker
        self._ws: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None
        self._url_index = 0
        self._closed = False
        self._auth_failed = False
        self._cmd_seq = 0
        self._first_books: dict[str, asyncio.Event] = {}
        self.stats = FeedStats()

    # ------------------------------------------------------------- public
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
                self._run(), name="kalshi-ws-feed")

    async def subscribe(self, ticker: str,
                        callback: OrderBookCallback) -> None:
        """Register a market. Waits until the feed has applied an initial
        snapshot book for the ticker (or fails), so callers can rely on the
        callback having fired at least once before returning."""
        if self._closed or self._auth_failed:
            raise KalshiWSUnavailable(
                "Kalshi WebSocket feed is closed "
                + ("(authentication failed)" if self._auth_failed
                   else ""))
        await self.start()
        self._callbacks[ticker] = callback
        state = self._states.setdefault(
            ticker, _BookState(self.venue, ticker))
        state.reset_sequencing()
        first_book = asyncio.Event()
        self._first_books[ticker] = first_book
        if self.connected:
            await self._send_subscribe(ticker)
        # If the connection is still establishing, _resubscribe_all() will
        # pick the ticker up once it connects.
        try:
            await asyncio.wait_for(first_book.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            pass
        if state.ready:
            return  # initial snapshot applied; keep the callback live
        # Failure path: clean up registration and surface the problem.
        self._callbacks.pop(ticker, None)
        self._first_books.pop(ticker, None)
        self._states.pop(ticker, None)
        reason = ("authentication failed" if self._auth_failed
                  else "no initial snapshot received")
        raise KalshiWSUnavailable(
            f"Kalshi WebSocket did not deliver an initial orderbook "
            f"snapshot for {ticker} (feed connected={self.connected}, "
            f"{reason}). Check API key validity and network "
            f"connectivity.")

    async def unsubscribe(self, ticker: str) -> None:
        """Remove a market subscription. Server-side unsubscribe is by
        subscription id; if the sid was never confirmed we only drop local
        state (the server will eventually prune the idle subscription)."""
        state = self._states.get(ticker)
        self._callbacks.pop(ticker, None)
        ev = self._first_books.pop(ticker, None)
        if ev is not None and not ev.is_set():
            ev.set()  # unblock a still-waiting subscribe()
        if state is None:
            return
        if self.connected and state.sid is not None:
            try:
                await self._send_command("unsubscribe", {"sids": [state.sid]})
            except Exception as exc:  # connection already dead → reconnect path
                log.warning("kalshi-ws: unsubscribe send failed for %s: %s",
                            ticker, exc)
        self._states.pop(ticker, None)

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
            except (asyncio.TimeoutError, asyncio.CancelledError,
                    Exception):
                task.cancel()
        self._callbacks.clear()
        self._states.clear()
        self._first_books.clear()

    # ------------------------------------------------------------- loop
    async def _run(self) -> None:
        backoff = _BACKOFF_INITIAL_S
        first_attempt = True
        while not self._closed:
            url = self.ws_urls[self._url_index % len(self.ws_urls)]
            try:
                headers = self._auth_headers()
                self._ws = await websockets.connect(
                    url, additional_headers=headers,
                    open_timeout=_OPEN_TIMEOUT_S)
                if first_attempt:
                    first_attempt = False
                else:
                    self.stats.reconnects += 1
                log.info("kalshi-ws: connected to %s", url)
                backoff = _BACKOFF_INITIAL_S
                await self._resubscribe_all()
                async for raw in self._ws:
                    if self._closed:
                        break
                    await self._on_message(raw)
            except KalshiWSAuthError as exc:
                self._auth_failed = True
                self._closed = True
                self._fail_waiters()
                log.error("kalshi-ws: terminal authentication failure: %s", exc)
                return
            except Exception as exc:
                if self._closed:
                    break
                # Rotate host only when we have a fallback and the failure
                # looks host-related; otherwise retry the same URL.
                if len(self.ws_urls) > 1:
                    self._url_index = (self._url_index + 1) % len(self.ws_urls)
                log.warning("kalshi-ws: connection lost (%s: %s); "
                            "reconnecting in %.1fs",
                            type(exc).__name__, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX_S)
            else:
                # Clean close (server ended the connection): reconnect with
                # backoff so a flapping endpoint cannot hot-loop.
                if not self._closed:
                    if len(self.ws_urls) > 1:
                        self._url_index = (self._url_index + 1) % len(
                            self.ws_urls)
                    log.info("kalshi-ws: server closed the connection; "
                             "reconnecting in %.1fs", backoff)
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
                for state in self._states.values():
                    state.reset_sequencing()

    def _fail_waiters(self) -> None:
        for ev in self._first_books.values():
            ev.set()  # waiters then check auth_failed/connected themselves

    async def _resubscribe_all(self) -> None:
        # Called right after (re)connecting: stale ack-correlation entries
        # from a dead connection cannot be matched anymore.
        self._pending_sid.clear()
        for ticker in list(self._callbacks):
            await self._send_subscribe(ticker)

    # ------------------------------------------------------------- commands
    def _next_cmd_id(self) -> int:
        self._cmd_seq += 1
        return self._cmd_seq

    async def _send_command(self, cmd: str, params: dict[str, Any]) -> int:
        assert self._ws is not None
        cmd_id = self._next_cmd_id()
        await self._ws.send(json.dumps({"id": cmd_id, "cmd": cmd,
                                        "params": params}))
        return cmd_id

    async def _send_subscribe(self, ticker: str) -> None:
        cmd_id = await self._send_command("subscribe", {
            "channels": [WS_ORDERBOOK_CHANNEL],
            "market_tickers": [ticker],
        })
        self._pending_sid[cmd_id] = ticker

    # ------------------------------------------------------------- messages
    async def _on_message(self, raw: Any) -> None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            self.stats.messages_dropped += 1
            return
        mtype = data.get("type")
        msg = data.get("msg") or {}

        if mtype == "subscribed":
            cmd_id = data.get("id")
            ticker = self._pending_sid.pop(cmd_id, None)
            sid = msg.get("sid")
            state = self._states.get(ticker) if ticker is not None else None
            if state is not None and isinstance(sid, int):
                state.sid = sid
            return

        if mtype == "orderbook_snapshot":
            ticker = msg.get("market_ticker")
            state = self._states.get(ticker)
            if state is None:
                self.stats.messages_dropped += 1
                return
            state.apply_snapshot(msg, data.get("seq"))
            self.stats.snapshots += 1
            self.stats.last_book_at = utcnow()
            ev = self._first_books.pop(ticker, None)
            if ev is not None:
                ev.set()
            await self._emit(ticker, state.to_order_book())
            return

        if mtype == "orderbook_delta":
            ticker = msg.get("market_ticker")
            state = self._states.get(ticker)
            if state is None:
                self.stats.messages_dropped += 1
                return
            seq = data.get("seq")
            if state.apply_delta(msg, seq):
                self.stats.deltas_applied += 1
                self.stats.last_book_at = utcnow()
                await self._emit(ticker, state.to_order_book())
            else:
                # K4: gap/out-of-order ⇒ recover, never apply silently.
                self.stats.sequence_gaps += 1
                log.warning("kalshi-ws: sequence gap for %s (got seq=%s, "
                            "expected %s); recovering",
                            ticker, seq,
                            None if state.last_seq is None
                            else state.last_seq + 1)
                await self._recover(ticker)
            return

        if mtype == "error":
            code = msg.get("code")
            text = msg.get("msg")
            log.error("kalshi-ws: error code=%s: %s", code, text)
            if code == _AUTH_ERROR_CODE:
                raise KalshiWSAuthError(
                    f"Kalshi WebSocket error 9 (authentication required): {text}")
            if code in _TERMINAL_SUB_ERROR_CODES:
                sid = data.get("sid")
                ticker = self._sid_to_ticker(sid)
                if ticker is not None:
                    self.stats.terminal_errors += 1
                    # Terminal subscription error → resubscribe (the
                    # documented remedy), with a fresh snapshot.
                    state = self._states.get(ticker)
                    if state is not None:
                        state.reset_sequencing()
                    await self._recover(ticker)
            return

        # ok / unsubscribed / other channel traffic: nothing to do.

    def _sid_to_ticker(self, sid: Any) -> Optional[str]:
        if not isinstance(sid, int):
            return None
        for ticker, state in self._states.items():
            if state.sid == sid:
                return ticker
        return None

    async def _recover(self, ticker: str) -> None:
        """Request a fresh snapshot for the subscription (the documented
        remedy for sequence gaps). Falls back to unsubscribe+resubscribe
        when the sid is unknown, and lets the reconnect loop handle a dead
        socket."""
        state = self._states.get(ticker)
        if self._ws is None or state is None:
            return
        try:
            if state.sid is not None:
                await self._send_command("update_subscription", {
                    "sid": state.sid,
                    "action": "get_snapshot",
                    "market_tickers": [ticker],
                })
            else:
                await self._send_subscribe(ticker)
        except Exception as exc:
            log.warning("kalshi-ws: recovery send failed for %s (%s); "
                        "reconnect loop will rebase", ticker, exc)

    async def _emit(self, ticker: str, book: OrderBook) -> None:
        callback = self._callbacks.get(ticker)
        if callback is None:
            return
        try:
            await callback(book)
        except Exception as exc:
            self.stats.callback_errors += 1
            log.error("kalshi-ws: callback for %s raised %s: %s",
                      ticker, type(exc).__name__, exc)
