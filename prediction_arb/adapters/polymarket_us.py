"""Polymarket US (QCX LLC) adapter — Phase 3 second independent venue.

Venue identity (docs/VENUE_RESEARCH.md V2, VERIFIED / DOC / 2026-09-13):
QCX LLC d/b/a **Polymarket US** is a CFTC-designated contract market
clearing through QC Clearing LLC d/b/a Polymarket Clearing. It is a
DIFFERENT legal entity, DCM, and order book from the international
Polymarket platform, and it is independent of Kalshi's DCM/book.

API surface (docs.polymarket.us, VERIFIED / DOC / 2026-09-13, rows
P1-P6):

* **Two bases.** Unlike Kalshi, market data lives on a separate
  *public* host that needs no credentials:
    - public  ``https://gateway.polymarket.us`` — markets, books, BBO,
      events, series, settlement (no API key, 20 req/s per IP);
    - authed  ``https://api.polymarket.us`` — orders, portfolio,
      account, and both WebSockets (Ed25519 key, 20 req/s per key).
  Discovery and order books therefore work with **no credentials at
  all**, which is how paper/collect mode runs by default.
* **True two-sided CLOB.** ``GET /v1/markets/{slug}/book`` returns real
  ``bids`` AND ``offers``. Nothing is synthesized here — contrast
  Kalshi (bid-only, YES ask = 1 - NO bid) and ForecastEx (paired bid).
  Prices are ``{"value": "0.555", "currency": "USD"}`` decimal strings;
  quantities are decimal strings and MAY be fractional.
* **Market state is load-bearing.** A book is only tradeable when
  ``state == MARKET_STATE_OPEN``. PREOPEN/SUSPENDED/HALTED/EXPIRED/
  TERMINATED/auction books are returned by the API but must never be
  treated as executable, so ``get_order_book`` returns None for them
  rather than handing the engine a book it could size against.
* **Per-market trading rules.** ``orderPriceMinTickSize`` and
  ``minimumTradeQty`` are per-market and the docs explicitly say NOT to
  infer them from product type or slug. They are carried onto the
  NormalizedContract instead of hardcoding $0.01/1 contract.

Order placement is deliberately NOT implemented. Phase 3's staged exit
gates require discovery and books first, and the Polymarket US fee
schedule is still INCOMPLETE in config/fees.yaml (banker's rounding plus
a cumulative aggressive-order fee cap need order/fill state the
calculator does not yet carry). ``place_order`` therefore raises rather
than transmitting anything.

Live status: the sandbox that wrote this file could not reach
gateway.polymarket.us (TLS EOF — evidence row P7, UNAVAILABLE / LIVE).
Everything here is documentation-verified and tested offline against a
mock transport and a local WebSocket server. api_status stays
API_DOCUMENTED_UNTESTED until the owner exercises it for real.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
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
    OrderTypeSupport,
    OutcomeType,
    PriceLevel,
)
from .base import OrderBookCallback, PredictionMarketAdapter
from .polymarket_us_ws import (
    PolymarketWebSocketFeed,
    PolymarketWSAuthError,
    PolymarketWSUnavailable,
)
from .ratelimit import RetryPolicy, TokenBucket

log = get_logger("adapters.polymarket_us")

_RETRYABLE = (httpx.TransportError, httpx.ReadTimeout)

#: Only this market state is executable (P4). Everything else is a book
#: that exists but must not be sized against.
TRADEABLE_STATE = "MARKET_STATE_OPEN"

#: Documented cap of markets per WebSocket subscription (P6).
WS_MAX_MARKETS_PER_SUBSCRIPTION = 100

#: Documented published limits (P5): 20 req/s public per IP and 20 req/s
#: authenticated per API key. Config supplies the budget actually used;
#: these are the ceilings we refuse to exceed.
PUBLISHED_PUBLIC_RPS = 20.0
PUBLISHED_AUTHED_RPS = 20.0


class PolymarketAuthError(Exception):
    """Credentials missing or rejected for an authenticated surface."""


class PolymarketOrderPlacementDisabled(Exception):
    """Order placement is not implemented for this venue (Phase 3 gate)."""


@dataclass(frozen=True)
class PolymarketMarketRules:
    """Per-market trading rules the docs say never to infer (P3)."""
    tick_size: Decimal
    min_order_size: Decimal


class PolymarketUSAdapter(PredictionMarketAdapter):
    venue = "polymarket_us"
    supports_streaming = True

    def __init__(self,
                 public_base: str = "https://gateway.polymarket.us",
                 authed_base: str = "https://api.polymarket.us",
                 key_id: str | None = None,
                 secret_key: str | None = None,
                 reads_per_sec: float = 8.0,
                 writes_per_sec: float = 3.0,
                 allow_order_placement: bool = False,
                 ws_url: str | None = None,
                 rate_limit_headroom: float = 0.8):
        self.public_base = public_base.rstrip("/")
        self.authed_base = authed_base.rstrip("/")
        self.key_id = key_id
        self.secret_key = secret_key
        # Kept for interface parity with other adapters. Polymarket US
        # order placement is not implemented at all in Phase 3, so this
        # flag cannot enable transmission on its own.
        self.allow_order_placement = allow_order_placement
        self.ws_url = ws_url or "wss://api.polymarket.us/v1/ws/markets"
        self._client: Optional[httpx.AsyncClient] = None
        # §13: clamp to the published ceiling, never above it.
        self._read_bucket = TokenBucket(
            _clamped(reads_per_sec, PUBLISHED_PUBLIC_RPS, rate_limit_headroom))
        self._write_bucket = TokenBucket(
            _clamped(writes_per_sec, PUBLISHED_AUTHED_RPS, rate_limit_headroom))
        self._retry = RetryPolicy()
        self._private_key = None
        self._feed: Optional[PolymarketWebSocketFeed] = None
        self._streamed_markets: set[str] = set()
        self._market_rules: dict[str, PolymarketMarketRules] = {}

    # ------------------------------------------------------------- props
    @property
    def has_credentials(self) -> bool:
        return bool(self.key_id and self.secret_key)

    def effective_reads_per_sec(self) -> float:
        return self._read_bucket.rate

    def effective_writes_per_sec(self) -> float:
        return self._write_bucket.rate

    # --------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        # Idempotent: tests inject their own transport; disconnect() nulls it.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=15.0)
        # Probe the PUBLIC base — market data is what paper/collect need,
        # and it requires no credentials. A missing key is not an error.
        try:
            await self._get_public("/v1/markets", params={"limit": 1})
        except Exception as exc:
            raise ConnectionError(
                f"Polymarket US public API unreachable at "
                f"{self.public_base}: {exc}") from exc

    async def disconnect(self) -> None:
        if self._feed is not None:
            await self._feed.close()
            self._feed = None
            self._streamed_markets.clear()
        if self._client:
            await self._client.aclose()
            self._client = None

    # ----------------------------------------------------------- signing
    def _load_private_key(self):
        """Ed25519 key from the base64 secret (P2).

        The documented client takes the first 32 bytes of the decoded
        secret as the Ed25519 seed.
        """
        if self._private_key is None:
            if not self.secret_key:
                raise PolymarketAuthError(
                    "Polymarket US secret key not configured. Authenticated "
                    "endpoints require POLYMARKET_KEY_ID and "
                    "POLYMARKET_SECRET_KEY in the mode env file.")
            from cryptography.hazmat.primitives.asymmetric import ed25519
            try:
                raw = base64.b64decode(self.secret_key)
            except Exception as exc:
                raise PolymarketAuthError(
                    f"POLYMARKET_SECRET_KEY is not valid base64: {exc}"
                ) from exc
            if len(raw) < 32:
                raise PolymarketAuthError(
                    "POLYMARKET_SECRET_KEY decoded to fewer than 32 bytes; "
                    "it is not a valid Ed25519 secret key.")
            self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
                raw[:32])
        return self._private_key

    def auth_headers(self, method: str, path: str) -> dict[str, str]:
        """Signed headers for the authenticated API (P2).

        message = timestamp_ms + METHOD + path (no query string).
        Timestamps must be within 30 seconds of server time.
        """
        if not self.key_id:
            raise PolymarketAuthError(
                "Polymarket US key id not configured; cannot call an "
                "authenticated endpoint. Set POLYMARKET_KEY_ID and "
                "POLYMARKET_SECRET_KEY in the mode env file. Note that "
                "market discovery and order books need NO credentials — "
                "they use the public gateway host.")
        key = self._load_private_key()
        ts = str(int(time.time() * 1000))
        message = f"{ts}{method.upper()}{path}".encode()
        sig = key.sign(message)
        return {
            "X-PM-Access-Key": self.key_id,
            "X-PM-Timestamp": ts,
            "X-PM-Signature": base64.b64encode(sig).decode(),
            "Content-Type": "application/json",
        }

    # -------------------------------------------------------------- http
    async def _get_public(self, path: str,
                          params: dict[str, Any] | None = None
                          ) -> dict[str, Any]:
        """Unauthenticated read against gateway.polymarket.us."""
        return await self._retry.run(
            lambda: self._request("GET", self.public_base, path,
                                  params=params, auth=False),
            _RETRYABLE)

    async def _get_authed(self, path: str,
                          params: dict[str, Any] | None = None
                          ) -> dict[str, Any]:
        """Authenticated read against api.polymarket.us."""
        return await self._retry.run(
            lambda: self._request("GET", self.authed_base, path,
                                  params=params, auth=True),
            _RETRYABLE)

    async def _request(self, method: str, base: str, path: str, *,
                       params: dict[str, Any] | None = None,
                       body: dict[str, Any] | None = None,
                       auth: bool = False) -> dict[str, Any]:
        assert self._client is not None, "connect() first"
        bucket = self._read_bucket if method == "GET" else self._write_bucket
        await bucket.acquire()
        headers = self.auth_headers(method, path) if auth else {}
        url = base + path
        if method == "GET":
            resp = await self._client.get(url, params=params, headers=headers)
        elif method == "POST":
            resp = await self._client.post(url, json=body, headers=headers)
        elif method == "DELETE":
            resp = await self._client.delete(url, headers=headers)
        else:
            raise ValueError(f"unsupported method {method}")
        if resp.status_code == 429:
            # Documented remedy (P5): stop, wait >=1s, exponential backoff.
            # Surfaced as a transport error so RetryPolicy backs off.
            raise httpx.TransportError(
                f"rate limited (429) from {urlparse(base).netloc}")
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------- market data
    async def get_markets(self, limit: int = 200) -> list[NormalizedContract]:
        """Public market discovery — no credentials required (P1)."""
        out: list[NormalizedContract] = []
        offset = 0
        page = min(limit, 100)
        while len(out) < limit:
            data = await self._get_public("/v1/markets", params={
                "limit": page,
                "offset": offset,
                "active": True,
                "closed": False,
            })
            markets = data.get("markets") or []
            if not markets:
                break
            for m in markets:
                nc = self._normalize_market(m)
                if nc is not None:
                    out.append(nc)
                if len(out) >= limit:
                    break
            offset += len(markets)
            if len(markets) < page:
                break
        return out

    async def get_market(self, market_id: str) -> Optional[NormalizedContract]:
        try:
            data = await self._get_public(f"/v1/market/slug/{market_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        m = data.get("market") or data
        return self._normalize_market(m) if m else None

    def _normalize_market(self, m: dict[str, Any]
                          ) -> Optional[NormalizedContract]:
        slug = m.get("slug")
        if not slug:
            # Without a slug we cannot fetch a book or place an order.
            return None
        if m.get("closed") or m.get("archived"):
            return None

        rules = self._rules_for(m)
        self._market_rules[slug] = rules

        settlement_time = parse_iso_utc(m.get("endDate"))
        start_time = parse_iso_utc(m.get("startDate"))

        # Category drives the Utah eligibility gate (§35). Polymarket US
        # carries heavy sports inventory, which is RESTRICTED in Utah, so
        # any sports signal must be preserved rather than flattened into
        # a generic label.
        category = self._category_for(m)

        return NormalizedContract(
            venue=self.venue,
            underlying_exchange="qcx_dcm",
            clearing_entity="qc_clearing",
            market_id=slug,
            contract_id=slug,
            event_description=m.get("question") or "",
            normalized_event="",          # filled by the normalization layer
            outcome_type=OutcomeType.BINARY,
            outcome="YES",
            normalized_outcome="",
            outcome_count=2,
            all_outcomes=("YES", "NO"),
            settlement_time=settlement_time,
            timezone="UTC",
            cutoff_time=settlement_time,
            resolution_source=(m.get("description") or "")[:500],
            resolution_method="",
            threshold=_opt_decimal(m.get("line")),
            threshold_operator=None,
            units=None,
            payout=Decimal("1.00"),
            currency="USD",
            early_resolution_rules="",
            cancellation_rules="",
            contract_version=str(m.get("updatedAt") or ""),
            tick_size=rules.tick_size,
            min_order_size=rules.min_order_size,
            max_order_size=None,
            jurisdiction_requirements="US",
            category=category,
            market_microstructure=self.get_market_microstructure(),
            supported_order_types=self.get_supported_order_types(),
            raw={**m, "_start_date": start_time.isoformat()
                 if start_time else None},
        )

    @staticmethod
    def _rules_for(m: dict[str, Any]) -> PolymarketMarketRules:
        """Read per-market tick/min-qty, never infer them (P3).

        The docs are explicit: do not infer tick size or minimum quantity
        from product type, symbol, or slug. When a market omits them we
        fall back to the coarsest SAFE values (a 1-cent tick and a
        1-contract minimum) and say so, rather than inventing a finer
        grid that could produce an unplaceable price.
        """
        tick = _opt_decimal(m.get("orderPriceMinTickSize"))
        min_qty = _opt_decimal(m.get("minimumTradeQty"))
        if tick is None or tick <= 0:
            tick = Decimal("0.01")
        if min_qty is None or min_qty <= 0:
            min_qty = Decimal("1")
        return PolymarketMarketRules(tick_size=tick, min_order_size=min_qty)

    @staticmethod
    def _category_for(m: dict[str, Any]) -> str:
        """Preserve sports signals for the eligibility gate (§35)."""
        if m.get("sportsMarketType") or m.get("sportsMarketTypeV2") \
                or m.get("gameId"):
            return "sports"
        category = (m.get("category") or "").strip()
        subcategory = (m.get("subcategory") or "").strip()
        if category and subcategory:
            return f"{category}/{subcategory}"
        return category or subcategory or "*"

    async def get_order_book(self, market_id: str) -> Optional[OrderBook]:
        """Public two-sided order book — no credentials required (P1).

        Returns None when the market is not in a tradeable state (P4):
        a halted or pre-open book is real data but is NOT executable, and
        handing it to the arbitrage engine would let it size a trade it
        could never fill.
        """
        try:
            data = await self._get_public(f"/v1/markets/{market_id}/book")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return self.parse_order_book(market_id, data)

    def parse_order_book(self, market_id: str,
                         data: dict[str, Any]) -> Optional[OrderBook]:
        """Parse a Polymarket US book payload (P1/P4).

        Accepts either the REST envelope ``{"marketData": {...}}`` or a
        bare ``MarketData`` object (the WebSocket delivers the latter).
        Both sides are genuine resting orders; nothing is synthesized.
        Malformed or non-tradeable levels are dropped and logged.
        """
        md = data.get("marketData") or data.get("market_data") or data
        if not isinstance(md, dict):
            log.warning("polymarket_us: unparseable book payload for %s",
                        market_id)
            return None

        state = md.get("state") or md.get("marketState")
        if state is not None and state != TRADEABLE_STATE:
            log.debug("polymarket_us: %s not tradeable (state=%s)",
                      market_id, state)
            return None

        bids = _levels(md.get("bids"), "bid", market_id)
        offers = _levels(md.get("offers") or md.get("asks"), "offer",
                         market_id)

        # Docs say levels arrive best-first, but sorting is cheap and makes
        # the invariant the engine relies on explicit rather than assumed.
        bids.sort(key=lambda level: -level.price)
        offers.sort(key=lambda level: level.price)

        timestamp = parse_iso_utc(md.get("transactTime")
                                  or md.get("transact_time")) or utcnow()

        return OrderBook(
            venue=self.venue,
            contract_id=md.get("marketSlug") or md.get("market_slug")
            or market_id,
            bids=bids,
            asks=offers,
            timestamp=timestamp,
            sequence_number=None,   # venue sends full snapshots, not deltas
            microstructure_type=MicrostructureType.CLOB,
            raw=data,
        )

    async def get_order_books(self,
                              market_ids: list[str]) -> dict[str, OrderBook]:
        """Per-market books.

        Polymarket US documents no bulk multi-market book endpoint, so
        this loops the public per-market endpoint under the shared rate
        limiter. It exists so collect mode's bulk-preferring path works
        uniformly across venues; it is NOT a batched request and claims
        no efficiency Kalshi's bulk endpoint has.
        """
        out: dict[str, OrderBook] = {}
        for slug in market_ids:
            try:
                book = await self.get_order_book(slug)
            except Exception as exc:
                log.warning("polymarket_us: book fetch failed for %s: %s",
                            slug, exc)
                continue
            if book is not None:
                out[slug] = book
        return out

    # --------------------------------------------------------- streaming
    async def subscribe_order_book(self, market_id: str,
                                   callback: OrderBookCallback) -> None:
        """Stream a market's book over the authenticated WebSocket (P6).

        Requires credentials: both Polymarket US WebSockets authenticate
        in the handshake even for public market data. Without a key,
        callers should poll the public REST book instead — which needs no
        credentials at all.
        """
        if not self.has_credentials:
            raise PolymarketAuthError(
                "Polymarket US WebSocket requires an API key even for market "
                "data (docs.polymarket.us/api-reference/websocket/overview, "
                "2026-09-13). Configure POLYMARKET_KEY_ID and "
                "POLYMARKET_SECRET_KEY, or poll the public REST book "
                "(get_order_book), which needs no credentials.")
        if self._feed is None:
            self._feed = PolymarketWebSocketFeed(
                venue=self.venue,
                ws_url=self.ws_url,
                auth_headers_factory=self.auth_headers,
                book_parser=self.parse_order_book)
        await self._feed.subscribe(market_id, callback)
        self._streamed_markets.add(market_id)

    async def unsubscribe_order_book(self, market_id: str) -> None:
        if self._feed is None:
            return
        await self._feed.unsubscribe(market_id)
        self._streamed_markets.discard(market_id)

    def streaming_stats(self) -> dict[str, Any]:
        if self._feed is None:
            return {"connected": False, "markets": [], "stats": None}
        return {"connected": self._feed.connected,
                "markets": self._feed.markets(),
                "stats": self._feed.stats.__dict__}

    # ----------------------------------------------------------- account
    async def get_account(self) -> dict[str, Any]:
        return await self._get_authed("/v1/account/balances")

    async def get_balances(self) -> dict[str, Decimal]:
        data = await self.get_account()
        out: dict[str, Decimal] = {}
        for bal in data.get("balances") or []:
            currency = bal.get("currency") or "USD"
            value = bal.get("currentBalance")
            if value is not None:
                out[currency] = Decimal(str(value))
        return out

    async def get_positions(self) -> list[dict[str, Any]]:
        data = await self._get_authed("/v1/portfolio/positions")
        return data.get("positions") or []

    async def get_open_orders(self) -> list[dict[str, Any]]:
        data = await self._get_authed("/v1/orders/open")
        return data.get("orders") or []

    # ------------------------------------------------------------ orders
    async def place_order(self, request: OrderRequest) -> OrderResult:
        raise PolymarketOrderPlacementDisabled(
            "Polymarket US order placement is not implemented. Phase 3 "
            "requires discovery and books before orders, and the venue's "
            "fee schedule is still INCOMPLETE in config/fees.yaml "
            "(banker's-rounded per-fill fees plus a cumulative "
            "aggressive-order cap need order/fill state the FeeCalculator "
            "does not carry). See docs/ROADMAP.md Phase 3.")

    async def cancel_order(self, order_id: str) -> bool:
        raise PolymarketOrderPlacementDisabled(
            "Polymarket US order placement is not implemented, so there is "
            "nothing to cancel.")

    async def cancel_all_orders(self) -> int:
        # Safe to answer honestly: this adapter never places an order, so
        # it can never have one outstanding to cancel.
        return 0

    async def get_order(self, order_id: str) -> Optional[OrderResult]:
        raise PolymarketOrderPlacementDisabled(
            "Polymarket US order placement is not implemented.")

    async def get_fills(self) -> list[dict[str, Any]]:
        data = await self._get_authed("/v1/portfolio/activities")
        return data.get("activities") or []

    # ---------------------------------------------------------- metadata
    def get_supported_order_types(self) -> OrderTypeSupport:
        return OrderTypeSupport(
            market=False,   # docs model marketable LIMIT orders, not market
            limit=True,
            ioc=True,
            fok=True,
            gtc=True,
            verified=False,  # documented only; no live order was placed
            source="https://docs.polymarket.us/api-reference/orders/overview "
                   "(documentation only; not live-tested)",
            verified_at=utcnow(),
        )

    def get_market_microstructure(self) -> MarketMicrostructure:
        return MarketMicrostructure(
            type=MicrostructureType.CLOB,
            has_order_book=True,
            has_pricing_function=False,
            quote_validity_ms=None,
            verified=False,
            source="https://docs.polymarket.us/concepts/market-data "
                   "(central limit order book, two-sided; DOC 2026-09-13)",
        )

    def get_api_status(self) -> APIStatus:
        # Documentation is verified; no live call has succeeded from here
        # (P7, UNAVAILABLE / LIVE). Never upgrade this from docs alone.
        return APIStatus.API_DOCUMENTED_UNTESTED


# ------------------------------------------------------------- helpers

def _clamped(configured: float, published_ceiling: float,
             headroom: float) -> float:
    """Never exceed ``headroom`` of the published ceiling (§13)."""
    if not 0.0 < headroom <= 1.0:
        raise ValueError("headroom must be in (0, 1]")
    return min(float(configured), published_ceiling * headroom)


def _opt_decimal(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _amount(value: Any) -> Optional[Decimal]:
    """Decode a ``{"value": "0.55", "currency": "USD"}`` Amount (P1)."""
    if isinstance(value, dict):
        value = value.get("value")
    return _opt_decimal(value)


def _levels(raw: Any, what: str, market_id: str) -> list[PriceLevel]:
    """Parse book levels, dropping anything non-tradeable.

    A price outside (0, 1) or a non-positive quantity is not something to
    guess about — it is dropped and logged, exactly as the Kalshi parser
    does, so a malformed feed can never inflate an apparent edge.
    """
    out: list[PriceLevel] = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            log.warning("polymarket_us: skipping malformed %s level %r on %s",
                        what, entry, market_id)
            continue
        price = _amount(entry.get("px") or entry.get("price"))
        qty = _opt_decimal(entry.get("qty") or entry.get("quantity"))
        if price is None or qty is None:
            log.warning("polymarket_us: skipping malformed %s level %r on %s",
                        what, entry, market_id)
            continue
        if not (Decimal(0) < price < Decimal(1)) or qty <= 0:
            log.warning(
                "polymarket_us: skipping non-tradeable %s level %r on %s",
                what, entry, market_id)
            continue
        out.append(PriceLevel(price=price, quantity=qty))
    return out
