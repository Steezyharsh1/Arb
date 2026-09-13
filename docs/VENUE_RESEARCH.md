# VENUE RESEARCH — Phase 1 Checkpoint

Research date: 2026-08-30 (America/Denver). All findings below are tagged
VERIFIED / UNVERIFIED / UNAVAILABLE and carry sources. Nothing in this file
was populated from assumption; where evidence was insufficient the status is
UNKNOWN and the system treats it conservatively.

---

## HEADLINE FINDING — VENUE COLLAPSE

The four requested "venues" collapse to **two underlying exchanges**:

| Frontend            | Underlying exchange        | Clearing entity            | Relationship  | Status    |
|---------------------|----------------------------|----------------------------|---------------|-----------|
| Kalshi (direct)     | Kalshi (CFTC DCM)          | Kalshi Klear (CFTC DCO)    | —             | VERIFIED  |
| Webull event ctrts  | **Kalshi**                 | Kalshi Klear               | SAME_VENUE as Kalshi | VERIFIED (public announcements) |
| Moomoo event ctrts  | **Kalshi**                 | Kalshi Klear               | SAME_VENUE as Kalshi | VERIFIED (public announcements) |
| IBKR ForecastTrader | ForecastEx (CFTC DCM+DCO)  | ForecastEx (own DCO)       | INDEPENDENT of Kalshi | VERIFIED (CFTC registration, press releases) |

**Consequence:** the only economically independent cross-venue pair available
to this system is **Kalshi ↔ ForecastEx**. A price difference between the
Kalshi app and the Webull app (or Moomoo app) for the same contract is the
same order book seen through two frontends — it is NOT arbitrage. The
provenance registry encodes this and the independence engine blocks such
pairs (classification: SAME_VENUE).

---

## 1. KALSHI

### Venue structure
- Exchange: Kalshi (KalshiEX LLC), CFTC-designated contract market (DCM),
  approved Nov 2020.
- Clearing: Kalshi Klear LLC, CFTC-registered derivatives clearing
  organization (DCO), approved Aug 2024.
- Matching engine: Kalshi's own central limit order book (CLOB).
- Liquidity: single shared order book; broker partners (Robinhood, Webull,
  Moomoo, FOREX.com, Coinbase) route customer flow into the SAME book.
- Sources: kalshi.com; CFTC DCM/DCO registries; apidog.com/blog/kalshi-api-devolpers-guide
  (verified 2026-08-30).

### API — VERIFIED LIVE (2026-08-30)
- REST base: `https://api.elections.kalshi.com/trade-api/v2`
  (docs also list `https://api.kalshi.com/trade-api/v2`).
- WebSocket: `wss://api.elections.kalshi.com/trade-api/ws/v2`
  (authentication REQUIRED at handshake, even for market data).
- Demo environment: `https://demo-api.kalshi.co/trade-api/v2`.
- Auth: RSA key pair; sign `timestamp + METHOD + path` with
  RSA-PSS SHA256; headers `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`,
  `KALSHI-ACCESS-TIMESTAMP`.
- Verified by direct call (no auth needed for these):
  - `GET /markets?series_ticker=KXFED&status=open` → live markets returned.
  - `GET /markets/{ticker}/orderbook?depth=N` → returns
    `{"orderbook_fp": {"yes_dollars": [["0.1600","44.54"], ...], "no_dollars": [...]}}`
    **IMPORTANT: current API returns dollar-string price levels
    (`orderbook_fp`), with fractional contract quantities — not the older
    integer-cent `orderbook` format many tutorials show.** The book lists
    resting BIDS for YES and BIDS for NO (a YES ask at price p is a NO bid
    at 1−p).
  - Market payloads now carry `*_dollars` fields (`yes_bid_dollars`,
    `yes_ask_dollars`, `liquidity_dollars`), `rules_primary/rules_secondary`,
    `strike_type`, `floor_strike`, `expected_expiration_time`,
    `settlement_timer_seconds`.
- Order placement: `POST /portfolio/orders` (auth) — ticker, action
  (buy/sell), side (yes/no), count, type (limit/market), yes_price/no_price,
  client_order_id (UUID; idempotency). UNVERIFIED live (requires funded
  account); VERIFIED against official docs.
- Order types: limit, market; time_in_force supports IOC-style expiration
  (`expiration_ts`); GTC default for limit. FOK: not documented → UNKNOWN.
- Rate limits (published, basic tier): ~10 req/s reads, ~5 req/s
  order writes. Respect 429 + exponential backoff.
- FIX 4.4 available for institutional access (not used in v1).

### Fees — VERIFIED (official schedule, re-verify before live)
- Taker fee: `ceil_cents(0.07 × C × P × (1−P))` — quadratic, peaks at P=$0.50
  (e.g. 100 contracts @ $0.50 → $1.75).
- Maker fee: $0 on most markets; some markets
  `ceil_cents(0.0175 × C × P × (1−P))`.
- No membership/API fee; no per-settlement fee documented.
- Source: kalshi.com/docs/fee-schedule (schedule as of Feb 2026, gathered
  2026-08-30). Fee engine stores this versioned + timestamped; live mode
  refuses to trade if the loaded schedule is older than the configured
  max age.

### Settlement
- Cash settled, $1.00 per contract; settlement typically shortly after
  market `expected_expiration_time` (`settlement_timer_seconds` in payload).
- Resolution sources are per-series (see `contract_terms_url` per series,
  e.g. assets.kalshi.com/contract_terms/*.pdf).

### Utah eligibility — PARTIALLY VERIFIED
- Kalshi is federally regulated (CFTC); available in all US states for most
  contract classes. Sports-event contracts are contested by several state
  gaming regulators (ongoing litigation, various states). Utah status for
  sports contracts: UNKNOWN → compliance module blocks the `sports`
  category for live trading until verified.
- Automated trading via API: explicitly permitted (documented API,
  published rate limits).

## 2. WEBULL EVENT CONTRACTS

### Venue structure — SAME_VENUE as Kalshi (HIGH CONFIDENCE)
- Feb 12 2025: Webull announced partnership with Kalshi; event contracts on
  Webull are "provided by Kalshi"; Webull "plans to become a clearing
  member of Kalshi". Sources: Bloomberg 2025-02-12; PR Newswire
  2025-02-12 (verified 2026-08-30).
- Therefore: underlying exchange = Kalshi DCM, clearing = Kalshi Klear,
  order book = Kalshi's book. Webull is an FCM/frontend, not an exchange.

### API — EXISTS; event-contract endpoints VERIFIED in docs, not live-tested
- Webull OpenAPI (developer.webull.com/apis/docs) explicitly supports
  "event contracts" for market data and order placement
  (`webull-openapi-python-sdk`; verified in official docs + official
  webull-inc/webull-openapi-skills repo, 2026-08-30).
- BUT: because liquidity is Kalshi's book, Webull is modeled as an
  ALTERNATE EXECUTION ROUTE into the same venue, never as an independent
  arbitrage leg. Possible future use: fee/route optimization only.
- Adapter status: `SAME_VENUE_ROUTE` (stub in v1 — no independent
  arbitrage value; implementing it cannot create opportunities).

## 3. MOOMOO (FUTU)

### Venue structure — SAME_VENUE as Kalshi (HIGH CONFIDENCE)
- Jun 4 2026: Moomoo Financial Inc. announced Kalshi partnership; event
  contracts "through a CFTC-regulated exchange" (Kalshi); Moomoo is the FCM
  (NFA-registered), Kalshi is the venue. Sources: GlobeNewswire
  2026-06-04; CoinDesk 2026-06-04 (verified 2026-08-30).
- "Certain contracts are unavailable in select U.S. states" (their own
  disclosure) → per-category eligibility must be checked at account level.

### API — UNVERIFIED for event contracts
- Futu OpenAPI / OpenD documents stocks, options, futures, HK/US markets.
  No event-contract endpoints found in the OpenAPI docs as of 2026-08-30.
  Moomoo "API Skills" marketing mentions agentic trading but no verifiable
  event-contract API surface.
- Adapter status: `NO_API_AVAILABLE` (and SAME_VENUE anyway → no
  independent arbitrage value even if an API appears).

## 4. INTERACTIVE BROKERS / FORECASTEX

### Venue structure — INDEPENDENT of Kalshi (HIGH CONFIDENCE)
- ForecastEx LLC: CFTC-registered DCM **and** DCO (its own clearing house),
  launched 2024-08-01. Wholly owned subsidiary of Interactive Brokers
  Group. Sources: PR Newswire 2024-07-31; forecastex.com/about; CFTC
  registries (verified 2026-08-30).
- Distinct exchange, distinct matching mechanism, distinct clearing,
  distinct order book from Kalshi → INDEPENDENT.

### Microstructure — MATERIALLY DIFFERENT (verified)
- No sell orders. Only YES bids and NO bids. A trade occurs when
  YES_bid + NO_bid = $1.01 (the $0.01 is the exchange fee). Prices $0.02
  to $0.99. Winning side receives $1.00, losing side $0. Closing a
  position = buying the opposite side.
- Model as CLOB variant `PAIRED_BID` in microstructure metadata: the
  "ask" for YES at price p is synthetically 1.01 − (best NO bid).

### API — VERIFIED in docs, not live-tested
- Access via IBKR: TWS API, Client Portal Web API, FIX. Forecast
  contracts route like other IBKR instruments (IBKR docs; ForecastTrader
  product pages; verified 2026-08-30).
- TWS API is synchronous/threaded → must be wrapped with
  `asyncio.to_thread` per §9 if implemented.
- Requires funded IBKR account + running TWS/IB Gateway (or CP Web API
  session). Cannot be verified further without an account.
- Adapter status: `API_DOCUMENTED_UNTESTED` (structured stub in v1;
  order placement raises NotImplementedError until integration is tested
  against IBKR paper account).

### Fees — VERIFIED (official)
- $0.01 per contract, embedded in the pairing (YES+NO = $1.01). Zero IBKR
  commission. Interest paid on ForecastEx balances (incentive coupon,
  ~3.8% APY region, varies) — recorded as venue metadata, not netted into
  arbitrage math in v1.

### Utah eligibility — UNKNOWN (sports); economics/politics believed
available via IBKR nationally. Compliance module blocks UNKNOWN
categories for live trading.

---

## CROSS-VENUE INDEPENDENCE MATRIX (encoded in provenance registry)

|                | kalshi        | webull        | moomoo        | forecast_ex |
|----------------|---------------|---------------|---------------|-------------|
| **kalshi**     | SAME_VENUE    | SAME_VENUE    | SAME_VENUE    | INDEPENDENT |
| **webull**     | SAME_VENUE    | SAME_VENUE    | SAME_VENUE    | INDEPENDENT |
| **moomoo**     | SAME_VENUE    | SAME_VENUE    | SAME_VENUE    | INDEPENDENT |
| **forecast_ex**| INDEPENDENT   | INDEPENDENT   | INDEPENDENT   | SAME_VENUE  |

Any venue not in the registry pairs as UNKNOWN → blocked.

## CONSEQUENCES FOR THE BUILD (design decisions)

1. The only v1 arbitrage pair worth scanning is Kalshi ↔ ForecastEx.
2. ForecastEx trading is gated on an IBKR account the user must open;
   until then, live cross-venue arbitrage is IMPOSSIBLE and the system
   says so honestly rather than inventing opportunities.
3. Kalshi order-book semantics: book = YES bids + NO bids. Executable
   YES ask = 1 − best NO bid. The order-book normalizer synthesizes asks
   from the opposite side's bids.
4. ForecastEx paired-bid mechanics mean the $0.01 fee is structural; the
   fee engine models it as a per-contract exchange fee.
5. Data collection (`--mode collect`) against Kalshi works TODAY without
   credentials. That is the correct first operational step.

## VERIFIED / UNVERIFIED / UNAVAILABLE SUMMARY

| Item | Status |
|---|---|
| Kalshi REST market data, order books | VERIFIED (live calls 2026-08-30) |
| Kalshi order placement API contract | VERIFIED in docs, not live-tested |
| Kalshi WebSocket (auth required) | VERIFIED in docs, not live-tested |
| Kalshi fee formula | VERIFIED (official schedule) |
| Webull → Kalshi routing | VERIFIED (official announcements) |
| Webull OpenAPI event-contract endpoints | Documented; UNVERIFIED live |
| Moomoo → Kalshi routing | VERIFIED (official announcements) |
| Moomoo OpenAPI event-contract support | UNAVAILABLE (no documented API) |
| ForecastEx DCM+DCO independence | VERIFIED (CFTC registration) |
| ForecastEx via IBKR API | Documented; UNVERIFIED live (needs IBKR account) |
| Utah sports-contract eligibility (both venues) | UNKNOWN → blocked |

## OPEN QUESTIONS CARRIED FORWARD
1. IBKR paper account needed to verify ForecastEx contract discovery and
   order placement via Client Portal API. (Blocks Phase 10 live path for
   the only independent pair; does NOT block data collection/simulation.)
2. Kalshi WebSocket requires API key even for market data — user must
   create a (free) Kalshi account + API key to move from REST polling to
   streaming.
3. Whether Kalshi/ForecastEx list economically equivalent contracts with
   compatible resolution sources (e.g. both list Fed decisions but
   thresholds/sources must be compared per contract) — answered
   empirically by the matcher once ForecastEx data flows.
