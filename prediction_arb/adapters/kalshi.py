"""Kalshi adapter — the one fully implemented adapter in v1.

VERIFIED live 2026-08-30 against https://api.elections.kalshi.com/trade-api/v2:
  * GET /markets, GET /markets/{ticker}, GET /markets/{ticker}/orderbook,
    GET /series/{series}, GET /exchange/status — public, no auth.
  * Order book format is the CURRENT `orderbook_fp` dollar-string format:
        {"orderbook_fp": {"yes_dollars": [["0.16","44.54"], ...],
                          "no_dollars":  [["0.62","95.00"], ...]}}
    Both sides are resting BIDS. The executable YES ask at quantity q is
    synthesized from NO bids: ask_price = 1 - no_bid_price.

Authenticated endpoints (portfolio, orders) implement Kalshi's documented
RSA-PSS signing scheme. They are exercised only in live mode via the
firewall; in paper/collect modes credentials may be absent and every
account method raises a descriptive error rather than fabricating data.
"""
from __future__ import annotations

import asyncio
import base64
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any, Optional

import httpx

from ..core.clock import parse_iso_utc, utcnow
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
from .ratelimit import RetryPolicy, TokenBucket

_RETRYABLE = (httpx.TransportError, httpx.ReadTimeout)


class KalshiAuthError(Exception):
    pass


class KalshiAdapter(PredictionMarketAdapter):
    venue = "kalshi"

    def __init__(self, rest_base: str,
                 api_key_id: str | None = None,
                 private_key_pem: str | None = None,
                 reads_per_sec: float = 8.0,
                 writes_per_sec: float = 3.0,
                 allow_order_placement: bool = False):
        self.rest_base = rest_base.rstrip("/")
        self.api_key_id = api_key_id
        self.private_key_pem = private_key_pem
        self.allow_order_placement = allow_order_placement
        self._client: Optional[httpx.AsyncClient] = None
        self._read_bucket = TokenBucket(reads_per_sec)
        self._write_bucket = TokenBucket(writes_per_sec)
        self._retry = RetryPolicy()
        self._private_key = None  # lazily loaded cryptography key object
        self._ws_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------- lifecycle
    async def connect(self) -> None:
        self._client = httpx.AsyncClient(timeout=15.0)
        # verify exchange reachability
        r = await self._get("/exchange/status")
        if not r.get("exchange_active", False):
            raise ConnectionError("Kalshi exchange reports inactive")

    async def disconnect(self) -> None:
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
        if self._client:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------- signing
    def _load_private_key(self):
        if self._private_key is None:
            if not self.private_key_pem:
                raise KalshiAuthError(
                    "Kalshi private key not configured. Authenticated "
                    "endpoints require KALSHI_API_KEY_ID and "
                    "KALSHI_PRIVATE_KEY_PEM in the mode env file.")
            from cryptography.hazmat.primitives import hashes, serialization
            self._private_key = serialization.load_pem_private_key(
                self.private_key_pem.encode(), password=None)
        return self._private_key

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        if not self.api_key_id:
            raise KalshiAuthError(
                "Kalshi API key id not configured; cannot call "
                "authenticated endpoint.")
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        key = self._load_private_key()
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method.upper()}/trade-api/v2{path}".encode()
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
    async def _get(self, path: str, params: dict[str, Any] | None = None,
                   auth: bool = False) -> dict[str, Any]:
        assert self._client is not None, "connect() first"
        await self._read_bucket.acquire()

        async def do() -> dict[str, Any]:
            headers = self._auth_headers("GET", path) if auth else {}
            resp = await self._client.get(self.rest_base + path,
                                          params=params, headers=headers)
            if resp.status_code == 429:
                raise httpx.TransportError("rate limited (429)")
            resp.raise_for_status()
            return resp.json()

        return await self._retry.run(do, _RETRYABLE)

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert self._client is not None, "connect() first"
        await self._write_bucket.acquire()
        headers = self._auth_headers("POST", path)
        resp = await self._client.post(self.rest_base + path, json=body,
                                       headers=headers)
        resp.raise_for_status()
        return resp.json()

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
        threshold = Decimal(str(floor_strike)) if floor_strike is not None else None

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
            tick_size=Decimal("0.01"),
            min_order_size=Decimal("1"),
            max_order_size=None,
            jurisdiction_requirements="US",
            category=series,
            market_microstructure=self.get_market_microstructure(),
            supported_order_types=self.get_supported_order_types(),
            raw=m,
        )

    async def get_order_book(self, market_id: str) -> Optional[OrderBook]:
        try:
            data = await self._get(f"/markets/{market_id}/orderbook",
                                   params={"depth": 32})
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return self.parse_order_book(market_id, data)

    def parse_order_book(self, market_id: str,
                         data: dict[str, Any]) -> OrderBook:
        """Parse Kalshi's current `orderbook_fp` format.

        yes_dollars = resting YES bids; no_dollars = resting NO bids.
        YES asks are synthesized: a NO bid at p is willingness to sell YES
        at (1 - p). Book is expressed from the YES perspective.
        """
        ob = data.get("orderbook_fp") or data.get("orderbook") or {}
        yes_raw = ob.get("yes_dollars") or []
        no_raw = ob.get("no_dollars") or []

        def lv(pair: list[Any]) -> PriceLevel:
            return PriceLevel(price=Decimal(str(pair[0])),
                              quantity=Decimal(str(pair[1])))

        yes_bids = sorted((lv(p) for p in yes_raw), key=lambda l: -l.price)
        no_bids = sorted((lv(p) for p in no_raw), key=lambda l: -l.price)
        yes_asks = sorted(
            (PriceLevel(price=Decimal("1") - l.price, quantity=l.quantity)
             for l in no_bids),
            key=lambda l: l.price)

        return OrderBook(
            venue=self.venue,
            contract_id=market_id,
            bids=yes_bids,
            asks=yes_asks,
            timestamp=utcnow(),
            sequence_number=None,
            microstructure_type=MicrostructureType.CLOB,
            raw=data,
        )

    async def subscribe_order_book(self, market_id: str,
                                   callback: OrderBookCallback) -> None:
        """Kalshi WebSocket requires authentication even for market data.
        v1 uses REST polling when no API key is configured."""
        if not (self.api_key_id and self.private_key_pem):
            raise KalshiAuthError(
                "Kalshi WebSocket requires an API key even for market data "
                "(verified in docs 2026-08-30). Configure KALSHI_API_KEY_ID "
                "and KALSHI_PRIVATE_KEY_PEM, or use REST polling "
                "(get_order_book) which needs no credentials.")
        raise NotImplementedError(
            "Kalshi WebSocket subscription is scheduled for the streaming "
            "milestone; v1 uses REST polling. The wire format "
            "(orderbook_snapshot + orderbook_delta on "
            "wss://api.elections.kalshi.com/trade-api/ws/v2) is documented "
            "in docs/VENUE_RESEARCH.md.")

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
        data = await self._post("/portfolio/orders", body)
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
        assert self._client is not None
        await self._write_bucket.acquire()
        headers = self._auth_headers("DELETE", f"/portfolio/orders/{order_id}")
        resp = await self._client.delete(
            f"{self.rest_base}/portfolio/orders/{order_id}", headers=headers)
        return resp.status_code in (200, 204)

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
            source="Kalshi API docs (create-order), verified 2026-08-30",
            verified_at=utcnow(),
        )

    def get_market_microstructure(self) -> MarketMicrostructure:
        return MarketMicrostructure(
            type=MicrostructureType.CLOB,
            has_order_book=True,
            has_pricing_function=False,
            quote_validity_ms=None,
            verified=True,
            source="Kalshi CLOB; order book verified live 2026-08-30",
        )

    def get_api_status(self) -> APIStatus:
        return APIStatus.VERIFIED


def _map_status(s: Any) -> OrderStatus:
    mapping = {
        "resting": OrderStatus.SUBMITTED,
        "pending": OrderStatus.SUBMITTED,
        "executed": OrderStatus.FILLED,
        "canceled": OrderStatus.CANCELED,
        "cancelled": OrderStatus.CANCELED,
    }
    return mapping.get(str(s).lower(), OrderStatus.UNKNOWN)
