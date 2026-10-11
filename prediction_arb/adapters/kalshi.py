"""Kalshi adapter — the one fully implemented adapter in v1.

Phase 2 (2026-09-13) refresh, per docs/VENUE_RESEARCH.md rows K1-K5
(VERIFIED / DOC / 2026-09-13, re-checked against docs.kalshi.com):

* **Hosts.** The recommended production REST base is
  ``https://external-api.kalshi.com/trade-api/v2`` and the recommended
  WebSocket is ``wss://external-api-ws.kalshi.com/trade-api/ws/v2``.
  The legacy shared hosts (``api.elections.kalshi.com``,
  ``demo-api.kalshi.co``) remain supported compatibility hosts and are
  configured as automatic fallbacks. Requests fail over to the next
  configured base on HTTP 5xx or transport failures (DNS/TLS/timeout);
  4xx responses never rotate hosts (the request reached a healthy host).
* **Order books.** The public single-book endpoint stays
  ``GET /markets/{ticker}/orderbook`` (fixed-point dollar strings;
  fractional quantities; bid-only — YES asks are synthesized as
  ``1 - no_bid``). The authenticated bulk endpoint
  ``GET /markets/orderbooks`` (up to 100 tickers per request) is used
  by collect mode when credentials are available.
* **Streaming.** ``subscribe_order_book`` implements the authenticated
  WebSocket protocol (see ``kalshi_ws.py``): sequenced snapshot/delta
  updates, gap-triggered recovery, reconnect with backoff.
* **Rate limits.** Authenticated limits are separate read/write token
  buckets (K5). When credentials are configured the adapter discovers
  the account's tier (``GET /account/limits``) and endpoint costs
  (``GET /account/endpoint_costs``) and clamps its local request budgets
  BELOW the published budget (with configurable headroom) — it never
  circumvents limits (§13).

Last observed live 2026-08-30 (then on the legacy compatibility host);
official documentation re-verified 2026-09-13. A fresh sandbox smoke
test was UNAVAILABLE (TLS EOF), so nothing here claims a new live test.

Authenticated endpoints (portfolio, orders, WS, bulk books, limits)
implement Kalshi's documented RSA-PSS signing scheme. They are exercised
only in live mode via the firewall; in paper/collect modes credentials
may be absent and every account method raises a descriptive error rather
than fabricating data.
"""
from __future__ import annotations

import asyncio
import base64
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from urllib.parse import urlparse

import httpx

from ..core.clock import parse_iso_utc, utcnow
from ..core.logging import get_logger
from ..core.types import (
    APIStatus,
    MarketMicrostructure,
    MicrostructureType,
    NormalizedContract,
    OrderBook,
    OrderRequest,
    OrderResult,
    OrderStatus,
    OrderTypeSupport,
    OutcomeType,
    PriceLevel,
    Side,
)
from .base import OrderBookCallback, PredictionMarketAdapter
from .evidence import AdapterEvidence, EvidenceClaim, EvidenceState
from .kalshi_ws import (
    KalshiWebSocketFeed,
    KalshiWSAuthError,
    KalshiWSUnavailable,
)
from .ratelimit import RetryPolicy, TokenBucket

log = get_logger("adapters.kalshi")

_RETRYABLE = (httpx.TransportError, httpx.ReadTimeout)

DEFAULT_ENDPOINT_COST_TOKENS = 10  # K5: default cost for listed endpoints
BULK_ORDERBOOK_MAX_TICKERS = 100   # documented max per request


class KalshiAuthError(Exception):
    """Credentials missing or rejected for an authenticated surface."""


@dataclass(frozen=True)
class KalshiRateLimits:
    """Account rate-limit tier as reported by GET /account/limits (K5)."""
    usage_tier: str
    read_refill_rate: float      # tokens/second
    read_bucket_capacity: float
    write_refill_rate: float     # tokens/second
    write_bucket_capacity: float
    raw: dict[str, Any] = field(default_factory=dict, compare=False)


class KalshiAdapter(PredictionMarketAdapter):
    venue = "kalshi"
    supports_streaming = True

    def __init__(self, rest_base: str,
                 api_key_id: str | None = None,
                 private_key_pem: str | None = None,
                 reads_per_sec: float = 8.0,
                 writes_per_sec: float = 3.0,
                 allow_order_placement: bool = False,
                 ws_url: str | None = None,
                 rest_base_fallbacks: list[str] | None = None,
                 ws_url_fallbacks: list[str] | None = None):
        self.rest_base = rest_base.rstrip("/")
        self.rest_bases = _dedup_hosts([self.rest_base]
                                       + [b.rstrip("/")
                                          for b in (rest_base_fallbacks or [])
                                          if b])
        self.ws_url = ws_url
        ws_candidates = ([ws_url.rstrip("/")] if ws_url else []) + \
            [u.rstrip("/") for u in (ws_url_fallbacks or []) if u]
        self.ws_urls = _dedup_hosts(ws_candidates)
        self.api_key_id = api_key_id
        self.private_key_pem = private_key_pem
        self.allow_order_placement = allow_order_placement
        self._client: Optional[httpx.AsyncClient] = None
        self._read_bucket = TokenBucket(reads_per_sec)
        self._write_bucket = TokenBucket(writes_per_sec)
        self._configured_reads_per_sec = float(reads_per_sec)
        self._configured_writes_per_sec = float(writes_per_sec)
        self._retry = RetryPolicy()
        self._private_key = None  # lazily loaded cryptography key object
        self._base_index = 0
        self._feed: Optional[KalshiWebSocketFeed] = None
        self._streamed_markets: set[str] = set()
        self.rate_limits: Optional[KalshiRateLimits] = None
        self.endpoint_costs: dict[str, int] = {}
        self.default_endpoint_cost = DEFAULT_ENDPOINT_COST_TOKENS
        # §103: every 429 seen, including ones the retry policy absorbs.
        self.rate_limit_events = 0

    # ------------------------------------------------------------- props
    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key_id and self.private_key_pem)

    @property
    def active_rest_base(self) -> str:
        return self.rest_bases[self._base_index % len(self.rest_bases)]

    def effective_reads_per_sec(self) -> float:
        return self._read_bucket.rate

    def effective_writes_per_sec(self) -> float:
        return self._write_bucket.rate

    # ------------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        # Idempotent: tests (and re-connects after an explicit disconnect)
        # may supply their own client; disconnect() nulls it.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        # Probe /exchange/status on each configured base and stick to the
        # first healthy one (failover is live from the very first request).
        # Preserve per-host failures so an offline smoke report can distinguish
        # TLS/DNS failures from an HTTP response or an inactive exchange.
        probe_errors: list[str] = []
        inactive_bases: list[str] = []
        for step in range(len(self.rest_bases)):
            i = (self._base_index + step) % len(self.rest_bases)
            base = self.rest_bases[i]
            try:
                r = await self._probe_status(base)
            except Exception as exc:
                probe_errors.append(
                    f"{base}: {type(exc).__name__}: {exc}")
                log.warning("kalshi: base %s unreachable: %s", base, exc)
                continue
            if r.get("exchange_active", False):
                self._base_index = i
                return
            inactive_bases.append(base)
            log.warning("kalshi: base %s reports exchange inactive", base)
        details = []
        if probe_errors:
            details.append("probe failures: " + " | ".join(probe_errors))
        if inactive_bases:
            details.append("exchange inactive: " + ", ".join(inactive_bases))
        raise ConnectionError(
            "Kalshi exchange was not confirmed active on any configured "
            f"base ({'; '.join(details) or 'no usable response'})")

    async def disconnect(self) -> None:
        if self._feed is not None:
            await self._feed.close()
            self._feed = None
            self._streamed_markets.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _probe_status(self, base: str) -> dict[str, Any]:
        assert self._client is not None
        r = await self._client.get(base + "/exchange/status")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------- signing
    def _load_private_key(self):
        if self._private_key is None:
            if not self.private_key_pem:
                raise KalshiAuthError(
                    "Kalshi private key not configured. Authenticated "
                    "endpoints require KALSHI_API_KEY_ID and "
                    "KALSHI_PRIVATE_KEY_PEM in the mode env file.")
            from cryptography.hazmat.primitives import serialization
            self._private_key = serialization.load_pem_private_key(
                self.private_key_pem.encode(), password=None)
        return self._private_key

    def _auth_headers(self, method: str, path: str,
                      api_prefix: str = "/trade-api/v2") -> dict[str, str]:
        if not self.api_key_id:
            raise KalshiAuthError(
                "Kalshi API key id not configured; cannot call "
                "authenticated endpoint.")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        key = self._load_private_key()
        ts = str(int(time.time() * 1000))
        # Sign the full request path from the API root, without query
        # parameters (docs.kalshi.com "Request Signing"). For the
        # WebSocket handshake the path is /trade-api/ws/v2 (K2).
        msg = f"{ts}{method.upper()}{api_prefix}{path}".encode()
        sig = key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
        }

    # ------------------------------------------------------------- http
    async def _do(self, method: str, base: str, path: str, *,
                  params: dict[str, Any] | None,
                  body: dict[str, Any] | None,
                  headers: dict[str, str]) -> httpx.Response:
        assert self._client is not None
        url = base + path
        if method == "GET":
            return await self._client.get(url, params=params, headers=headers)
        if method == "POST":
            return await self._client.post(url, json=body, headers=headers)
        if method == "DELETE":
            return await self._client.delete(url, headers=headers)
        raise ValueError(f"unsupported method {method}")

    async def _request(self, method: str, path: str, *,
                       params: dict[str, Any] | None = None,
                       body: dict[str, Any] | None = None,
                       auth: bool = False) -> dict[str, Any]:
        """One pass across the configured bases with 5xx/transport
        failover. 429 is account-wide (K5), so it never rotates hosts and
        is surfaced as a transport error for the retry policy's backoff."""
        assert self._client is not None, "connect() first"
        bucket = self._read_bucket if method == "GET" else self._write_bucket
        await bucket.acquire()
        headers = self._auth_headers(method, path) if auth else {}
        idx = self._base_index
        n = len(self.rest_bases)
        last_exc: Optional[BaseException] = None
        last_resp: Optional[httpx.Response] = None
        for step in range(n):
            i = (idx + step) % n
            base = self.rest_bases[i]
            try:
                resp = await self._do(method, base, path,
                                      params=params, body=body,
                                      headers=headers)
            except _RETRYABLE as exc:
                last_exc = exc
                if step == n - 1:
                    break
                self._base_index = (i + 1) % n
                log.warning("kalshi: transport failure on %s (%s); "
                            "rotating to %s", urlparse(base).netloc,
                            type(exc).__name__,
                            self.rest_bases[self._base_index])
                continue
            if resp.status_code == 429:
                # Rate-limited: back off (retry policy), stay on this host.
                self.rate_limit_events += 1   # §103: counted even if retried
                raise httpx.TransportError(
                    f"rate limited (429) from {urlparse(base).netloc}")
            if 500 <= resp.status_code < 600:
                last_resp = resp
                if step == n - 1:
                    break
                self._base_index = (i + 1) % n
                log.warning("kalshi: HTTP %s from %s; rotating to %s",
                            resp.status_code, urlparse(base).netloc,
                            self.rest_bases[self._base_index])
                continue
            # 2xx or 4xx: the host answered — keep it sticky.
            self._base_index = i
            resp.raise_for_status()
            return resp.json()
        if last_resp is not None:
            last_resp.raise_for_status()
        assert last_exc is not None
        raise last_exc

    async def _get(self, path: str, params: dict[str, Any] | None = None,
                   auth: bool = False) -> dict[str, Any]:
        return await self._retry.run(
            lambda: self._request("GET", path, params=params, auth=auth),
            _RETRYABLE)

    async def _post_order_once(self, path: str,
                               body: dict[str, Any]) -> dict[str, Any]:
        """Exactly ONE order POST to the sticky base (Arb.16 / D30).

        Order writes are not idempotent at the transport layer. A ReadTimeout,
        connection reset, or 5xx can follow a request the venue already
        accepted, so retrying it, or failing over to another host, can
        transmit the same intent again (the pre-Arb.16 path sent up to eight
        POSTs for one intent). This method therefore never retries and never
        rotates hosts. The error propagates unchanged so the caller treats the
        outcome as UNKNOWN and reconciles by client_order_id.
        """
        assert self._client is not None, "connect() first"
        await self._write_bucket.acquire()
        headers = self._auth_headers("POST", path)
        base = self.rest_bases[self._base_index]
        resp = await self._client.post(base + path, json=body,
                                       headers=headers)
        if resp.status_code == 429:
            self.rate_limit_events += 1   # §103: counted; never retried here
        resp.raise_for_status()
        return resp.json()

    async def _delete(self, path: str) -> int:
        return await self._retry.run(lambda: self._raw_delete(path),
                                     _RETRYABLE)

    async def _raw_delete(self, path: str) -> int:
        """DELETE returns a status code, not a JSON body (cancel order)."""
        assert self._client is not None, "connect() first"
        await self._write_bucket.acquire()
        headers = self._auth_headers("DELETE", path)
        idx = self._base_index
        n = len(self.rest_bases)
        for step in range(n):
            i = (idx + step) % n
            base = self.rest_bases[i]
            try:
                resp = await self._client.delete(base + path, headers=headers)
            except _RETRYABLE:
                if step == n - 1:
                    raise
                self._base_index = (i + 1) % n
                continue
            if 500 <= resp.status_code < 600 and step < n - 1:
                self._base_index = (i + 1) % n
                continue
            self._base_index = i
            return resp.status_code
        raise httpx.TransportError("kalshi: DELETE failed on all bases")

    # ------------------------------------------------------------- account
    async def get_account(self) -> dict[str, Any]:
        return await self._get("/portfolio/balance", auth=True)

    async def get_balances(self) -> dict[str, Decimal]:
        data = await self.get_account()
        # documented: balance in cents
        bal = data.get("balance")
        if bal is None:
            return {}
        return {"USD": Decimal(str(bal)) / Decimal("100")}

    async def get_positions(self) -> list[dict[str, Any]]:
        data = await self._get("/portfolio/positions", auth=True)
        return data.get("market_positions", [])

    # --------------------------------------------------------- rate limits
    async def get_rate_limits(self) -> KalshiRateLimits:
        """GET /account/limits (authenticated) — the account's effective
        tier and token budgets (K5)."""
        data = await self._get("/account/limits", auth=True)
        read = data.get("read") or {}
        write = data.get("write") or {}
        limits = KalshiRateLimits(
            usage_tier=str(data.get("usage_tier", "unknown")).lower(),
            read_refill_rate=float(read.get("refill_rate", 0) or 0),
            read_bucket_capacity=float(read.get("bucket_capacity", 0) or 0),
            write_refill_rate=float(write.get("refill_rate", 0) or 0),
            write_bucket_capacity=float(write.get("bucket_capacity", 0) or 0),
            raw=data)
        self.rate_limits = limits
        return limits

    async def get_endpoint_costs(self) -> dict[str, int]:
        """GET /account/endpoint_costs — authoritative non-default
        endpoint costs (K5). Returns {"METHOD /path": cost}; endpoints not
        listed use ``default_endpoint_cost``."""
        data = await self._get("/account/endpoint_costs", auth=False)
        self.default_endpoint_cost = int(data.get("default_cost",
                                                  DEFAULT_ENDPOINT_COST_TOKENS)
                                         or DEFAULT_ENDPOINT_COST_TOKENS)
        costs: dict[str, int] = {}
        for row in data.get("endpoint_costs") or []:
            method = str(row.get("method", "")).upper()
            path = str(row.get("path", ""))
            cost = row.get("cost")
            if method and path and cost is not None:
                costs[f"{method} {path}"] = int(cost)
        self.endpoint_costs = costs
        return costs

    def endpoint_cost(self, method: str, path: str) -> int:
        return self.endpoint_costs.get(f"{method.upper()} {path}",
                                       self.default_endpoint_cost)

    def apply_rate_limits(self, limits: KalshiRateLimits,
                          headroom: float = 0.8) -> None:
        """Clamp local request budgets so sustained traffic stays under
        the discovered tier (K5 + §13: never circumvent limits).

        Per-second request budget for an endpoint = refill_rate /
        endpoint_cost. We budget ``headroom`` (default 80%) of that and
        only ever LOWER a bucket — configured values remain the ceiling
        and the published tier remains the hard bound."""
        self.rate_limits = limits
        if not 0.0 < headroom <= 1.0:
            raise ValueError("headroom must be in (0, 1]")
        read_rps = _budget_rps(limits.read_refill_rate,
                               self.default_endpoint_cost, headroom)
        write_rps = _budget_rps(limits.write_refill_rate,
                                self.default_endpoint_cost, headroom)
        if read_rps is not None:
            self._read_bucket.rate = min(self._read_bucket.rate, read_rps)
            self._read_bucket.capacity = max(1, int(read_rps * 2))
            self._read_bucket.tokens = float(self._read_bucket.capacity)
        if write_rps is not None:
            self._write_bucket.rate = min(self._write_bucket.rate, write_rps)
            self._write_bucket.capacity = max(1, int(write_rps * 2))
            self._write_bucket.tokens = float(self._write_bucket.capacity)

    # --------------------------------------------------------- market data
    async def get_markets(self, limit: int = 200,
                          series_ticker: str | None = None) -> list[NormalizedContract]:
        params: dict[str, Any] = {"limit": min(limit, 200), "status": "open"}
        if series_ticker:
            params["series_ticker"] = series_ticker
        out: list[NormalizedContract] = []
        cursor: str | None = None
        while len(out) < limit:
            if cursor:
                params["cursor"] = cursor
            data = await self._get("/markets", params=params)
            for m in data.get("markets", []):
                nc = self._normalize_market(m)
                if nc is not None:
                    out.append(nc)
                if len(out) >= limit:
                    break
            cursor = data.get("cursor")
            if not cursor or not data.get("markets"):
                break
        return out

    async def get_market(self, market_id: str) -> Optional[NormalizedContract]:
        try:
            data = await self._get(f"/markets/{market_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        m = data.get("market")
        return self._normalize_market(m) if m else None

    def _normalize_market(self, m: dict[str, Any]) -> Optional[NormalizedContract]:
        ticker = m.get("ticker", "")
        if not ticker:
            return None
        # Skip provisional multivariate combo shards — they are parlays,
        # not simple binary event contracts, and have empty books.
        if m.get("mve_collection_ticker") or m.get("is_provisional"):
            return None
        if m.get("market_type") != "binary":
            return None

        strike_type = m.get("strike_type") or ""
        floor_strike = m.get("floor_strike")
        try:
            threshold = (Decimal(str(floor_strike))
                         if floor_strike is not None else None)
            if threshold is not None and not threshold.is_finite():
                threshold = None
        except (InvalidOperation, ValueError, TypeError):
            threshold = None

        settlement_time = parse_iso_utc(m.get("expected_expiration_time")
                                        or m.get("expiration_time"))
        close_time = parse_iso_utc(m.get("close_time"))

        series = ticker.split("-")[0]
        return NormalizedContract(
            venue=self.venue,
            underlying_exchange="kalshi_dcm",
            clearing_entity="kalshi_klear_dco",
            market_id=ticker,
            contract_id=ticker,
            event_description=m.get("title", ""),
            normalized_event="",           # filled by normalization layer
            outcome_type=OutcomeType.BINARY,
            outcome=m.get("yes_sub_title") or m.get("subtitle") or "YES",
            normalized_outcome="",
            outcome_count=2,
            all_outcomes=("YES", "NO"),
            settlement_time=settlement_time,
            timezone="UTC",
            cutoff_time=close_time,
            resolution_source=m.get("rules_primary", "")[:500],
            resolution_method=strike_type,
            threshold=threshold,
            threshold_operator=strike_type or None,
            units=None,
            payout=Decimal("1.00"),
            currency="USD",
            early_resolution_rules="can_close_early" if m.get("can_close_early") else "",
            cancellation_rules=m.get("rules_secondary", "")[:500],
            contract_version=str(m.get("updated_time", "")),
            # These are not populated by this public payload adapter. Keep
            # absent rules unknown rather than storing invented defaults.
            tick_size=None,
            min_order_size=None,
            max_order_size=None,
            jurisdiction_requirements="US",
            category=series,
            market_microstructure=self.get_market_microstructure(),
            supported_order_types=self.get_supported_order_types(),
            raw=m,
        )

    async def get_order_book(self, market_id: str) -> Optional[OrderBook]:
        """Public single-market book (no auth required)."""
        try:
            data = await self._get(f"/markets/{market_id}/orderbook",
                                   params={"depth": 32})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return self.parse_order_book(market_id, data)

    async def get_order_books(self,
                              market_ids: list[str]) -> dict[str, OrderBook]:
        """Authenticated bulk books: GET /markets/orderbooks, up to 100
        tickers per request (K3/doc 2026-09-13). Returns
        ``{ticker: OrderBook}``; tickers absent from the response (closed
        or unknown) are simply not in the result.

        Requires credentials — the endpoint is authenticated. Without a
        key, raise KalshiAuthError; callers fall back to the public
        per-ticker endpoint (collect mode does this automatically)."""
        if not self.has_credentials:
            raise KalshiAuthError(
                "GET /markets/orderbooks requires an API key. Configure "
                "KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PEM, or use the "
                "public get_order_book() per ticker.")
        out: dict[str, OrderBook] = {}
        for chunk in _chunks(market_ids, BULK_ORDERBOOK_MAX_TICKERS):
            data = await self._get("/markets/orderbooks",
                                   params={"tickers": chunk}, auth=True)
            for ob in data.get("orderbooks") or []:
                ticker = ob.get("ticker")
                if ticker:
                    out[ticker] = self.parse_order_book(ticker, ob)
        return out

    def parse_order_book(self, market_id: str,
                         data: dict[str, Any],
                         sequence_number: int | None = None) -> OrderBook:
        """Parse Kalshi's fixed-point order-book payload (K3).

        ``orderbook_fp.yes_dollars`` = resting YES bids;
        ``orderbook_fp.no_dollars`` = resting NO bids (both sides are
        bids; prices are dollar strings, quantities fractional). The
        WebSocket snapshot uses the ``*_fp`` key names — both are
        accepted. The executable YES ask at a NO bid p is synthesized as
        ``1 - p``. Malformed or non-tradeable levels (price outside
        (0,1), non-positive quantity) are dropped and logged, never
        guessed at.
        """
        ob = data.get("orderbook_fp") or data.get("orderbook") or {}
        yes_raw = ob.get("yes_dollars") or ob.get("yes_dollars_fp") or []
        no_raw = ob.get("no_dollars") or ob.get("no_dollars_fp") or []

        def levels(pairs: Any, what: str) -> list[PriceLevel]:
            out: list[PriceLevel] = []
            for pair in pairs if isinstance(pairs, list) else []:
                if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                    log.warning("kalshi: skipping malformed %s level %r",
                                what, pair)
                    continue
                try:
                    price = Decimal(str(pair[0]))
                    qty = Decimal(str(pair[1]))
                except (InvalidOperation, ValueError, TypeError):
                    log.warning("kalshi: skipping malformed %s level %r",
                                what, pair)
                    continue
                if (not price.is_finite() or not qty.is_finite()
                        or not (Decimal(0) < price < Decimal(1)) or qty <= 0):
                    log.warning("kalshi: skipping non-tradeable %s level %r",
                                what, pair)
                    continue
                out.append(PriceLevel(price=price, quantity=qty))
            return out

        yes_bids = sorted(levels(yes_raw, "yes"), key=lambda l: -l.price)
        no_bids = sorted(levels(no_raw, "no"), key=lambda l: -l.price)
        # NO bids sorted descending → synthesized YES asks ascending.
        yes_asks = [PriceLevel(price=Decimal("1") - l.price, quantity=l.quantity)
                    for l in no_bids]

        received_at = utcnow()
        return OrderBook(
            venue=self.venue,
            contract_id=market_id,
            bids=yes_bids,
            asks=yes_asks,
            timestamp=received_at,
            received_at=received_at,
            timestamp_source="local_receipt",
            outcome_side=Side.YES,
            sequence_number=sequence_number,
            microstructure_type=MicrostructureType.CLOB,
            raw=data,
        )

    # ------------------------------------------------------------- streaming
    async def subscribe_order_book(self, market_id: str,
                                   callback: OrderBookCallback) -> None:
        """Stream the market's order book via the authenticated WebSocket
        (K2/K4). The callback is awaited with a fresh OrderBook on every
        snapshot/delta; it is guaranteed to have fired at least once
        (initial snapshot) when this returns.

        Requires KALSHI_API_KEY_ID + KALSHI_PRIVATE_KEY_PEM — Kalshi WS
        needs authentication even for market data (re-verified in docs
        2026-09-13). Without a key, use REST polling (get_order_book)."""
        if not self.has_credentials:
            raise KalshiAuthError(
                "Kalshi WebSocket requires an API key even for market data "
                "(re-verified in docs 2026-09-13). Configure KALSHI_API_KEY_ID "
                "and KALSHI_PRIVATE_KEY_PEM, or use REST polling "
                "(get_order_book) which needs no credentials.")
        if not self.ws_urls:
            raise KalshiWSUnavailable(
                "No ws_url configured for kalshi in config/venues.yaml; "
                "cannot stream order books.")
        if self._feed is None:
            self._feed = KalshiWebSocketFeed(
                venue=self.venue,
                ws_urls=self.ws_urls,
                auth_headers=lambda: self._auth_headers(
                    "GET", "", api_prefix="/trade-api/ws/v2"))
        await self._feed.subscribe(market_id, callback)
        self._streamed_markets.add(market_id)

    async def unsubscribe_order_book(self, market_id: str) -> None:
        if self._feed is not None and market_id in self._streamed_markets:
            await self._feed.unsubscribe(market_id)
        self._streamed_markets.discard(market_id)

    def streaming_stats(self) -> dict[str, Any]:
        if self._feed is None:
            return {"connected": False, "markets": [], "stats": None}
        return {"connected": self._feed.connected,
                "markets": self._feed.markets(),
                "stats": self._feed.stats.__dict__}

    # ------------------------------------------------------------- orders
    async def get_open_orders(self) -> list[dict[str, Any]]:
        data = await self._get("/portfolio/orders",
                               params={"status": "resting"}, auth=True)
        return data.get("orders", [])

    async def place_order(self, request: OrderRequest) -> OrderResult:
        if not self.allow_order_placement:
            raise PermissionError(
                "KalshiAdapter constructed with allow_order_placement=False "
                "(non-live mode). Real order placement is impossible from "
                "this adapter instance — this is a mode-isolation invariant, "
                "not a configuration nicety.")
        body: dict[str, Any] = {
            "ticker": request.contract_id,
            "action": request.action.value,
            "side": request.side.value,
            "count": int(request.quantity),
            "type": "limit" if request.order_type.value != "market" else "market",
            "client_order_id": request.client_order_id or str(uuid.uuid4()),
        }
        if request.limit_price is not None:
            cents = int(request.limit_price * 100)
            if request.side == Side.YES:
                body["yes_price"] = cents
            else:
                body["no_price"] = cents
        # One attempt only: see _post_order_once (Arb.16 / D30).
        data = await self._post_order_once("/portfolio/orders", body)
        order = data.get("order", {})
        return OrderResult(
            client_order_id=body["client_order_id"],
            venue_order_id=order.get("order_id"),
            status=_map_status(order.get("status")),
            filled_quantity=Decimal(str(order.get("taker_fill_count", 0) or 0)),
            average_fill_price=None,
            fees=Decimal("0"),
            message=str(order.get("status", "")),
        )

    async def cancel_order(self, order_id: str) -> bool:
        status_code = await self._delete(f"/portfolio/orders/{order_id}")
        return status_code in (200, 204)

    async def cancel_all_orders(self) -> int:
        orders = await self.get_open_orders()
        n = 0
        for o in orders:
            oid = o.get("order_id")
            if oid and await self.cancel_order(oid):
                n += 1
        return n

    async def get_order(self, order_id: str) -> Optional[OrderResult]:
        try:
            data = await self._get(f"/portfolio/orders/{order_id}", auth=True)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        o = data.get("order", {})
        return OrderResult(
            client_order_id=o.get("client_order_id", ""),
            venue_order_id=o.get("order_id"),
            status=_map_status(o.get("status")),
            filled_quantity=Decimal(str(o.get("taker_fill_count", 0) or 0)),
            average_fill_price=None,
            fees=Decimal("0"),
        )

    async def get_fills(self) -> list[dict[str, Any]]:
        data = await self._get("/portfolio/fills", auth=True)
        return data.get("fills", [])

    # ------------------------------------------------------------- metadata
    def get_supported_order_types(self) -> OrderTypeSupport:
        return OrderTypeSupport(
            market=True, limit=True,
            ioc=True,   # via expiration_ts on limit orders
            fok=False,  # not documented → treated unsupported
            gtc=True,
            verified=True,
            source="Kalshi API docs (create-order), re-verified 2026-09-13; live observation 2026-08-30",
            verified_at=utcnow(),
        )

    def get_market_microstructure(self) -> MarketMicrostructure:
        return MarketMicrostructure(
            type=MicrostructureType.CLOB,
            has_order_book=True,
            has_pricing_function=False,
            quote_validity_ms=None,
            verified=True,
            source="Kalshi CLOB; docs re-verified 2026-09-13; last live observation 2026-08-30",
        )

    def get_api_status(self) -> APIStatus:
        # A historical public-data run captured markets and books, but the
        # current adapter revision has not had a successful live end-to-end
        # verification. This is enough for read-only collection only.
        return APIStatus.MARKET_DATA_OBSERVED

    def get_evidence(self) -> AdapterEvidence:
        return AdapterEvidence(
            documentation=EvidenceClaim(
                EvidenceState.DOCUMENTED,
                as_of="2026-09-13",
                source="docs/VENUE_RESEARCH.md K1-K5",
                detail="REST hosts, public book format, WS protocol, and rate limits reviewed."),
            connectivity=EvidenceClaim(
                EvidenceState.OBSERVED,
                as_of="2026-08-30",
                source="historical collect observation recorded in docs/AGENT_HANDOFF.md",
                detail="A prior public run reached the venue; a newer 2026-10-10 probe was unavailable."),
            usable_market_data=EvidenceClaim(
                EvidenceState.OBSERVED,
                as_of="2026-08-30",
                source="historical collect observation recorded in docs/AGENT_HANDOFF.md",
                detail="Historical collection reported 500 markets and 40 non-empty books; this is not full verification of the current adapter revision."),
            full_adapter=EvidenceClaim(
                EvidenceState.NOT_VERIFIED,
                as_of="2026-10-10T06:13:37.919175+00:00",
                source="scripts/kalshi_smoke.py and offline tests; docs/VENUE_RESEARCH.md K9",
                detail="Current public endpoints were unavailable; authenticated bulk/WS paths and current production payloads are not live-verified. Tick/minimum rules remain unknown."),
            latest_smoke=EvidenceClaim(
                EvidenceState.UNAVAILABLE,
                as_of="2026-10-10T06:13:37.919175+00:00",
                source="scripts/kalshi_smoke.py; docs/VENUE_RESEARCH.md K9",
                detail="Both https://external-api.kalshi.com/trade-api/v2 and https://api.elections.kalshi.com/trade-api/v2 failed with ConnectError: TLS/SSL connection has been closed (EOF) (_ssl.c:992) before HTTP response; no credentials or orders."),
            surfaces=("public REST discovery", "public REST order book",
                      "authenticated bulk books (offline-tested)",
                      "authenticated WebSocket (offline-tested)"),
        )


def _dedup_hosts(hosts: list[str]) -> list[str]:
    out: list[str] = []
    for h in hosts:
        if h and h not in out:
            out.append(h)
    return out or [""]


def _chunks(seq: list[str], size: int):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


def _budget_rps(refill_rate_tokens: float, cost_tokens: int,
                headroom: float) -> Optional[float]:
    """Sustained requests/second under a token budget, with headroom.
    Returns None when the tier reports no usable budget (fail closed:
    keep the configured, documented-conservative value)."""
    if refill_rate_tokens <= 0 or cost_tokens <= 0:
        return None
    return max(0.1, refill_rate_tokens * headroom / cost_tokens)


def _map_status(s: Any) -> OrderStatus:
    mapping = {
        "resting": OrderStatus.SUBMITTED,
        "pending": OrderStatus.SUBMITTED,
        "executed": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED,
        "cancelled": OrderStatus.CANCELED,
    }
    return mapping.get(str(s).lower(), OrderStatus.UNKNOWN)
