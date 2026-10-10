# Phase 1 venue research and selection checkpoint

**Evidence cut-off:** 2026-09-13 (UTC) for the broad venue/regulatory snapshot.

**Fee-source re-review:** 2026-10-10 (UTC); current fee readiness and exact gaps are in [`docs/FEE_READINESS.md`](FEE_READINESS.md).

**Adapter evidence review:** 2026-10-10 (UTC); bounded public-only Kalshi and Polymarket US smoke attempts were both `UNAVAILABLE` before an HTTP response. This changes neither adapter's status to broken or fully verified.

**Milestone:** M1 / Phase 1 research only — no new adapter was implemented

**Jurisdiction configured by this repository:** US-UT (Utah)
**Not legal, financial, or trading advice.**

## 0. How to read this document

External facts are recorded in the evidence ledgers below. Every ledger row has:

- **VERIFIED** — the cited primary/official material was reviewed;
- **UNVERIFIED** — only secondary evidence, a conflicting source, or an
  operational claim not established by the primary material was available;
- **UNAVAILABLE** — a live/account test could not be performed; or
- **UNKNOWN** — evidence was insufficient to decide the claim.

`DOC` means documentation/regulatory verification. `LIVE` means an endpoint or
account was actually exercised. A documented API is not a live-tested API. Matrix
scores are repository analysis, not external facts; each score names the evidence
rows used.

## 1. Executive result

The old premise that ForecastEx was the only exchange independent of Kalshi is
obsolete. Phase 1 found multiple distinct DCM/matching stacks: ForecastEx,
Polymarket US (QCX), Gemini Titan, CDNA, CME, Railbird/DKeX, Ludlow/Novig, and
Rothera. A different app name does **not** establish a different venue; frontend
routes must be contract-qualified.

### Conditional shortlist

1. **Polymarket US direct API** — best first direct-adapter candidate: distinct
   DCM/CLOB and a purpose-built REST/WebSocket surface. Exact overlap, live
   liquidity, account access, and Utah non-sports eligibility remain untested.
2. **Gemini Predictions direct API** — similarly strong documented API and demo
   environment. Cohort-dependent fees and exact Kalshi overlap remain blockers.
3. **IBKR multi-venue route** — strongest operational choice *if the owner already
   has or wants an IBKR account*: one authenticated integration exposes Kalshi,
   ForecastEx, and CME. Kalshi-routed contracts are not independent of direct
   Kalshi; only route-qualified ForecastEx/CME legs can be independent.

No candidate is approved for live execution. Utah sports contracts are
**RESTRICTED**, Utah non-sports eligibility is **UNKNOWN**, authenticated liquidity
was not measured, and most contextual fee schedules remain execution-blocking.
Owner agreement is required before Phase 2/3 adapter work.

## 2. Kalshi re-verification ledger

| ID | Claim | Status / test | Source | Verified |
|---|---|---|---|---|
| K1 | Recommended production REST is `https://external-api.kalshi.com/trade-api/v2`; `https://api.elections.kalshi.com/trade-api/v2` remains supported for compatibility. Recommended demo REST is `https://external-api.demo.kalshi.co/trade-api/v2`; the legacy demo host also remains supported. | VERIFIED / DOC | https://docs.kalshi.com/getting_started/api_environments | 2026-09-13 |
| K2 | Recommended production WebSocket is `wss://external-api-ws.kalshi.com/trade-api/ws/v2`; recommended demo WebSocket is `wss://external-api-ws.demo.kalshi.co/trade-api/ws/v2`; legacy WS hosts remain supported. | VERIFIED / DOC | https://docs.kalshi.com/getting_started/api_environments | 2026-09-13 |
| K3 | `GET /markets/{ticker}/orderbook` is public and returns bid-only fixed-point strings in `orderbook_fp.yes_dollars` and `.no_dollars`; asks are complements (`1.00 - opposite bid`). | VERIFIED / DOC | https://docs.kalshi.com/getting_started/orderbook_responses | 2026-09-13 |
| K4 | WebSocket snapshots/deltas carry `seq`; snapshot uses `yes_dollars_fp`/`no_dollars_fp`, and deltas carry side, price, and `delta_fp`. Sequence gaps/out-of-order updates must trigger recovery rather than silent application. | VERIFIED / DOC | https://docs.kalshi.com/websockets/orderbook-updates | 2026-09-13 |
| K5 | Authenticated limits are token buckets with separate read/write budgets. Default endpoint cost is 10 tokens; official tier budgets run Basic 200 read/100 write through Prestige 10,000 read/8,000 write tokens per second. Endpoint-specific costs come from `GET /account/endpoint_costs`; 429 has no `Retry-After`. | VERIFIED / DOC | https://docs.kalshi.com/getting_started/rate_limits | 2026-09-13 |
| K6 | Series metadata carries `fee_type` and `fee_multiplier`; event responses carry `fee_type_override` and `fee_multiplier_override`; series and event fee-change endpoints expose scheduled changes. A cleared/null event override must not be retained as a stale selector. A venue-wide coefficient is insufficient. | VERIFIED / DOC | https://docs.kalshi.com/api-reference/market/get-series ; https://docs.kalshi.com/api-reference/exchange/get-series-fee-changes ; https://docs.kalshi.com/api-reference/events/get-event ; https://docs.kalshi.com/api-reference/events/get-event-fee-changes ; https://docs.kalshi.com/llms.txt | 2026-10-10 |
| K7 | Standard taker reference is `0.07 × M × C × P × (1-P)`; maker-enabled series use `0.0175 × M × C × P × (1-P)`. The fee page lists no upcoming changes but gives no global effective date; fee type and multiplier remain series/event contextual, so these are not a venue-wide execution schedule. | VERIFIED / DOC | https://kalshi.com/fee-schedule ; https://docs.kalshi.com/api-reference/market/get-series ; https://docs.kalshi.com/api-reference/events/get-event | 2026-10-10 |
| K8 | Per-fill fee rounding uses six-decimal model-fee precision, member-specific balance precision (direct member `$0.0001`, non-direct `$0.01`), an order accumulator spanning maker/taker fills, and balance-alignment adjustments/rebates. | VERIFIED / DOC | https://docs.kalshi.com/getting_started/fee_rounding | 2026-10-10 |
| K9 | Bounded unauthenticated smoke (`scripts/kalshi_smoke.py --limit 1`, 2026-10-10 06:13:37.919175 UTC) failed before HTTP response on both configured public bases: `https://external-api.kalshi.com/trade-api/v2` and `https://api.elections.kalshi.com/trade-api/v2`; each raised `ConnectError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)`. No credentials or orders; 1-market limit. This does **not** establish a Kalshi outage, adapter defect, or usable current payload. Arb.15 re-run at about 17:29 UTC (local records were written to the git-ignored `reports/evidence/` and deleted at the end of the session; they are not committed, and the outcome is summarized here) gave the same result. This sandbox's egress policy allows only `github.com`, `api.github.com`, `codeload.github.com`, `registry.npmjs.org`, `pypi.org` and `files.pythonhosted.org`, and neither venue host is on that list, so the TLS EOF is consistent with the egress restriction (an inference, not separately tested). It must not be bypassed; the probe is meaningful only from an authorized network. | UNAVAILABLE / LIVE | `/exchange/status` on both configured bases (attempted by public-only smoke) | 2026-10-10 |
| K10 | Settlement guidance limits the zero-settlement-fee statement to simple yes/no determinations; sub-cent scalar settlement may incur a fee. The current evaluator has no settlement-type guard, so the production settlement schedule must not be globally zero. | VERIFIED / DOC; runtime scope incomplete | https://docs.kalshi.com/getting_started/market_settlement | 2026-10-10 |
| K11 | Historical public collection on 2026-08-30 recorded 500 markets and 40 non-empty books, supporting a `MARKET_DATA_OBSERVED` claim only. It is not evidence that the current adapter revision, authenticated bulk books, WebSocket, or order paths are fully verified. The latest smoke remains K9 (`UNAVAILABLE`). | `MARKET_DATA_OBSERVED` (historical public collection only); **not** live-capable | `docs/TESTING.md` integration-testing record; `tests/test_collect_mode.py` and current offline adapter tests cover code behavior only | 2026-08-30; re-reviewed 2026-10-10 (Arb.15) |

**Code consequence (adapter evidence reviewed 2026-10-10):** `config/venues.yaml`
uses the documented recommended `external-api` hosts from K1/K2 and keeps
legacy hosts as fallbacks. `KalshiAdapter` is `MARKET_DATA_OBSERVED` from the
historical public collection (K11), not `VERIFIED`; current network access is
`UNAVAILABLE` (K9). Offline tests cover 5xx/transport failover, fixed-point
REST and WebSocket parsing, sequence-gap recovery, reconnect behavior,
authenticated bulk-book parsing, and account-tier budget clamping, but none
of those offline tests establishes live payload validity or authenticated
connectivity. The current bounded smoke is deliberately public-only: it does
not load credentials, query account/tier, call bulk books or WebSocket, or
place orders. No full current-adapter verification is claimed. Kalshi's
production fee/settlement schedules remain `INCOMPLETE` (K6-K8, K10 and
`docs/FEE_READINESS.md`).

## 2b. Polymarket US API ledger (Phase 3, added 2026-09-13)

Wire-level facts for the Phase 3 adapter. General API sources were reviewed
2026-09-13; fee, market-family, combo, settlement-reporting sources were
re-read 2026-10-10. Venue *identity* evidence stays in row V2 below.

| ID | Claim | Status / test | Source | Verified |
|---|---|---|---|---|
| P1 | The API is split across two hosts. **Public** `https://gateway.polymarket.us` serves markets, events, series, order books, BBO, and settlement with **no API key** (`GET /v1/markets`, `GET /v1/markets/{slug}/book`, `GET /v1/markets/{slug}/bbo`). **Authenticated** `https://api.polymarket.us` serves orders, portfolio, account, and both WebSockets. | VERIFIED / DOC | https://docs.polymarket.us/api-reference/introduction ; https://docs.polymarket.us/api-reference/market/overview | 2026-09-13 |
| P2 | Authenticated requests use **Ed25519** signatures with headers `X-PM-Access-Key` / `X-PM-Timestamp` / `X-PM-Signature`. The signed message is `timestamp + METHOD + path`; the secret's first 32 decoded bytes are the Ed25519 seed; timestamps must be within **30 seconds** of server time. | VERIFIED / DOC | https://docs.polymarket.us/api-reference/authentication | 2026-09-13 |
| P3 | `orderPriceMinTickSize` and `minimumTradeQty` are **per-market** and the documentation explicitly instructs integrators **not** to infer tick size or minimum quantity from product type, symbol, or slug. Quantities may be fractional. | VERIFIED / DOC | https://docs.polymarket.us/api-reference/market/overview | 2026-09-13 |
| P4 | Books carry a `state` enum. Only `MARKET_STATE_OPEN` is trading; `PREOPEN`, `SUSPENDED`, `HALTED`, `EXPIRED`, `TERMINATED`, and `MATCH_AND_CLOSE_AUCTION` are returned but are not executable. | VERIFIED / DOC | https://docs.polymarket.us/api-reference/markets/get-market-book ; https://docs.polymarket.us/concepts/market-data | 2026-09-13 |
| P5 | Rate limits are **20 requests/second** for authenticated endpoints (per API key) and 20 requests/second public (per IP). `429` carries no `Retry-After`; the documented remedy is to stop, wait ≥1 s, and back off exponentially. A separate 5-second **latency stopgap** rejects unprocessed inbound orders with the message `Global Rate Limit Exceeded`; that message is explicitly **not** a rate limit and must not trigger throttling. | VERIFIED / DOC | https://docs.polymarket.us/api-reference/rate-limits | 2026-09-13 |
| P6 | The markets WebSocket is `wss://api.polymarket.us/v1/ws/markets` and **requires API-key authentication in the handshake** even for public market data. Subscribe with `{"subscribe":{"request_id","subscription_type","market_slugs"}}`; `subscription_type` 1 = `MARKET_DATA` (full book), 2 = `MARKET_DATA_LITE`, 3 = `TRADE`. Responses carry a **complete** `market_data` book (`bids`/`offers`), not incremental deltas, so there is no sequence number to gap-check. Server sends `{"heartbeat":{}}`; errors arrive as `{"request_id", "error"}`; max **100 markets per subscription**. | VERIFIED / DOC | https://docs.polymarket.us/api-reference/websocket/overview ; https://docs.polymarket.us/api-reference/websocket/markets | 2026-09-13 |
| P7 | Bounded unauthenticated smoke (`scripts/polymarket_us_smoke.py --limit 1`, 2026-10-10 06:13:41.226497 UTC) failed before HTTP response: `https://gateway.polymarket.us` raised `ConnectionError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)`. No credentials or orders; 1-market limit. This does **not** establish a Polymarket US outage, adapter defect, or usable current payload. Arb.15 re-run at about 17:29 UTC (local records were written to the git-ignored `reports/evidence/` and deleted at the end of the session; they are not committed, and the outcome is summarized here) gave the same result. This sandbox's egress policy allows only `github.com`, `api.github.com`, `codeload.github.com`, `registry.npmjs.org`, `pypi.org` and `files.pythonhosted.org`, and neither venue host is on that list, so the TLS EOF is consistent with the egress restriction (an inference, not separately tested). It must not be bypassed; the probe is meaningful only from an authorized network. | UNAVAILABLE / LIVE | Public gateway `/v1/markets` connection probe (attempted) | 2026-10-10 |
| P8 | Current schedule effective **2026-10-07 10:00 ET**: standard taker `Θ = 0.0695`, Table Tennis taker `Θ = 0.10`, and maker rebate `Θ = -0.0125` under `Θ × C × p × (1-p)`. Fees use nearest-cent **banker's rounding**; aggressive-order fills are capped at the banker's rounding of cumulative exact fee (adjustment can only reduce a charge); maker rebates are independently rounded per fill and not capped. Prior-calendar-month taker amount-at-risk tiers (10/25/50% at `$250k`/`$1m`/`$10m`) are paid weekly, not discounted per trade; the page also permits accelerated placement using verifiable trailing-30-day external volume. This is not one universal family: Table Tennis differs, and combos use a separate curve (P13). | VERIFIED / DOC | https://docs.polymarket.us/fees | 2026-10-10 |
| P9 | Recurring **weekly maintenance Thursday 02:00–06:00 ET** (effective 2026-07-09; previously 06:00–08:00). All open orders are cancelled before the window, **all API requests return `503 Service Unavailable`** during it, and books **reopen empty** because every order was cancelled. Markets move `SUSPENDED → OPEN` when it ends. | VERIFIED / DOC | https://docs.polymarket.us/faqs/general-faqs | 2026-09-13 |
| P10 | Fractional-contract support is **documented inconsistently**. The retail/learn docs state "Polymarket US does not support fractional contracts. All trades are executed in **whole event contracts**", while the API docs describe `minimumTradeQty: 0.01` as "the market supports 1% contract increments" and the institutional data model carries a per-instrument `fractionalQtyScale`. Resolution adopted in code: the per-market `minimumTradeQty` from the payload is authoritative and is **never inferred**; the engine's own sizing stays whole-contract, so the conflict cannot make it size a fractional order. | VERIFIED / DOC (conflict) | https://docs.polymarket.us/learn/trading/basics/fractional-shares ; https://docs.polymarket.us/api-reference/market/overview ; https://docs.polymarket.us/faqs/general-faqs | 2026-09-13 |
| P11 | Offline arithmetic tests reproduce the **current standard** 99-row 100-lot table in both taker and maker columns (198 published values), and exercise half-even rounding, aggressive-order cap, maker rebates, and volume-rebate non-application. Tests use a **test-only synthetic trade schedule**; the production schedule is `INCOMPLETE` for trade and settlement because fee-family and account/order context are not integrated. No claim of a production all-market fee calculation or live reconciliation. | VERIFIED / DOC + offline arithmetic test; UNAVAILABLE / LIVE | https://docs.polymarket.us/fees#standard-fee-schedule-by-price ; `tests/test_polymarket_fees.py` | 2026-10-10 |
| P12 | Customer settlement-fee treatment is **not established**. Public settlement materials describe `$1.00`/`$0.00` payouts; the Payments API exposes `TRANSACTION_TYPE_SETTLEMENT_FEE`, and the balance-ledger API lists `LEDGER_ENTRY_TYPE_BALANCE_SETTLEMENT_FEE` but says suppressed entry types are filtered out. That does not establish whether, when, or how much a customer is charged; `settlement_calculation_status` stays **INCOMPLETE**. | UNVERIFIED / DOC (customer treatment unresolved) | https://docs.polymarket.us/learn/markets/contract-settlement ; https://docs.polymarket.us/institutional/funding/overview ; https://docs.polymarket.us/api-reference/funding/get-account-balance-ledger ; https://docs.polymarket.us/institutional/payments/overview ; https://docs.polymarket.us/trader-guide/reporting | 2026-10-10 |
| P13 | Combo instruments are available through the Retail API; the fee page gives a separate taker formula `C × p × [0.0695 × (1-p) + 0.06 × (1-p)^4]`, applied to the combo as a whole, with **no maker rebate**. The current adapter/evaluator does not model combo instruments. | VERIFIED / DOC; runtime UNSUPPORTED | https://docs.polymarket.us/changelog ; https://docs.polymarket.us/api-reference/combos/overview ; https://docs.polymarket.us/fees#combo-taker-fees | 2026-10-10 |

**Code consequence (adapter evidence reviewed 2026-10-10):**
`prediction_arb/adapters/polymarket_us.py` implements public-gateway discovery
and books without credentials (P1); the Ed25519 signer (P2) is for authenticated
surfaces, not required by this smoke. Offline tests cover those code paths, but
current connectivity and payload behavior are **not observed**: the bounded
public probe on 2026-10-10 returned TLS EOF before HTTP response (P7). Therefore
`api_status` remains `API_DOCUMENTED_UNTESTED`, and collect/paper do not admit
this venue. Tick size and minimum quantity are optional per-market values:
provided values are parsed and validated; missing/malformed values remain
unknown (`None`) and are never inferred (P3). `parse_order_book` rejects a
missing/unknown or non-`MARKET_STATE_OPEN` state (P4); the smoke also requires a
usable two-sided uncrossed book and valid tick/minimum rules before it can
report market-data evidence. Request budgets are clamped to 80% of the
published 20 req/s ceiling and `429` is surfaced for backoff (P5, §13).
`polymarket_us_ws.py` implements full-snapshot streaming with
reconnect/resubscribe and terminal auth handling; it deliberately has no
sequence-gap machinery because the documented protocol sends replacement
books rather than deltas (P6). Neither offline mocks nor an unavailable public
probe establish full adapter verification.

**Fees (re-reviewed 2026-10-10; Phase 3 fee gate REOPENED):** the prior
`0.06` schedule effective 2026-07-01 was superseded by the current
2026-10-07 10:00 ET schedule. `config/fees.yaml` records standard
`Θ=0.0695`, Table Tennis `Θ=0.10`, maker rebate `Θ=-0.0125`, and the combo
curve as published references, but sets both production calculation statuses
`INCOMPLETE`. The arithmetic regressions use a test-only synthetic standard
schedule; they do not establish the live market-family selector, account/fill
reconciliation, combo support, or settlement-fee treatment. The older claim
that the full production fee model was complete is withdrawn. Weekly volume
rebates remain evidence-only and are never applied per order. See
`docs/FEE_READINESS.md` for direct sources, exact gaps, and operator steps.
`api_status` remains `API_DOCUMENTED_UNTESTED`; collect mode skips the venue.
A future successful public smoke would provide a manual
`MARKET_DATA_OBSERVED` candidate only, not full adapter verification; the
2026-10-10 attempt is `UNAVAILABLE` (P7).

**Operational note (P9):** Thursday 02:00–06:00 ET the venue returns `503`
for every request and reopens with **empty** books. `503` is a 5xx, so the
adapter's failover rotates hosts and then surfaces the failure rather than
silently trading on a stale book; paper-mode realism work (Phase 6) should
model the weekly flat-book window instead of treating it as an outage.

## 3. Direct exchange / DCM evidence ledger

| ID | Venue identity and finding | Independence from Kalshi | API evidence | Fee evidence | Source | Status / date |
|---|---|---|---|---|---|---|
| V1 | **ForecastEx LLC** is both a CFTC DCM and DCO. Opposite bids pair at **$1.00**; a separate **$0.01 per executed contract per side** fee applies under the schedule effective 2026-03-31. Older IBKR educational pages still saying `$1.01` are stale relative to the 2026 rule filings. | VERIFIED distinct DCM, matching, clearing | Retail automation is routed through authenticated IBKR surfaces; adapter is a stub and no account was live-tested. | Exchange component is documented; all-in IBKR/FCM commission and settlement treatment are **INCOMPLETE**. IBKR publishes `$0.00` commission only for its documented direct-client route. | https://www.cftc.gov/PressRoom/PressReleases/8926-24 ; https://www.cftc.gov/filings/orgrules/rules03022640215.pdf ; https://www.cftc.gov/filings/orgrules/rules03242641956.pdf ; https://www.cftc.gov/filings/orgrules/rules09032623612.pdf ; https://forecastex.com/faq ; https://www.interactivebrokers.com/en/pricing/commissions-events.php | VERIFIED / DOC; UNAVAILABLE / LIVE / 2026-10-10 |
| V2 | **Polymarket US** is QCX LLC d/b/a Polymarket US, a CFTC DCM, clearing through QC Clearing LLC d/b/a Polymarket Clearing. It is not the international Polymarket venue. | VERIFIED distinct DCM/CLOB | Official REST/WebSocket/auth docs exist. Sandbox TLS smoke test produced no HTTP response. | Current schedule effective 2026-10-07 10:00 ET: standard taker `0.0695`, Table Tennis taker `0.10`, maker rebate `-0.0125`, separate combo curve, half-even rounding, aggressive-order cap, weekly volume rebates. Production trade and settlement statuses are **INCOMPLETE**. | https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/49571 ; https://docs.polymarket.us/ ; https://docs.polymarket.us/fees ; https://docs.polymarket.us/api-reference/combos/overview | VERIFIED / DOC; UNAVAILABLE / LIVE / 2026-10-10 |
| V3 | **Gemini Titan LLC** is a CFTC DCM. Titan contracts may clear through QC Clearing or registered DCO **Gemini Olympus LLC**; common clearing does not merge Titan’s book with QCX’s book. | VERIFIED distinct DCM/CLOB | Official public REST, authenticated WS trading, API keys, and sandbox are documented. Sandbox TLS smoke test produced no HTTP response. | Listings on/after 2026-03-18 use quadratic maker/taker rates; older listings use a different `min(P,1-P)` cohort formula. Cohort metadata is absent from current fee calls. | https://www.cftc.gov/csl/26-12/download ; https://www.cftc.gov/IndustryOversight/IndustryFilings/ClearingOrganizations/59189 ; https://developer.gemini.com/products/prediction-markets ; https://www.gemini.com/fees/predictions-2026-03-18 | VERIFIED / DOC; UNAVAILABLE / LIVE / 2026-09-13 |
| V4 | **North American Derivatives Exchange, Inc. d/b/a Crypto.com Derivatives North America (CDNA)** is both DCM and DCO. OG Prediction Markets is a product/listing brand/partner, not evidence of a separate book. | VERIFIED distinct DCM/matching identity | No complete public automated event-contract trading API was verified. | Official material gives an effective-2026-06-30 taker range of `$0.01-$0.0175` per `$1` contract and zero maker/settlement fees, but the exact price function was not verified. | https://www.cftc.gov/sites/default/files/filings/orgrules/25/03/rules03102516984.pdf ; https://help.crypto.com/en/articles/10748690-prediction-markets-trading-fees | VERIFIED identity/range; UNVERIFIED exact formula / 2026-09-13 |
| V5 | **CME event contracts** are `$1` exchange-listed contracts on CME and cleared by CME Clearing. | VERIFIED distinct DCM/Globex | Institutional APIs exist; the retail route evaluated here is through FCMs such as IBKR/FanDuel, not a direct retail adapter. | 2026 schedule shows `$0.01` exchange fee; route commission must also be modeled. | https://www.cmegroup.com/markets/event-contracts.html ; https://www.cmegroup.com/company/files/2026-fee-schedule.pdf | VERIFIED / DOC / 2026-09-13 |
| V6 | **Railbird Exchange LLC** is a CFTC DCM. CFTC Letter 26-13 authorized a QC Clearing-to-Bitnomial Clearinghouse transition and third-party clearing members. | VERIFIED distinct DCM/CLOB | No public trading API was verified. | Complete current schedule unavailable. | https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/50090 ; https://www.cftc.gov/csl/26-13/download | VERIFIED registration/authorization; UNVERIFIED completed cutover / 2026-09-13 |
| V7 | **DKeX** launched 2026-06-26 as DraftKings’ proprietary exchange and uses acquired Railbird technology/CFTC license. DKeX is therefore route-qualified to Railbird’s DCM, not merely a generic CME/CDNA frontend and not independent of Railbird. | VERIFIED same venue as Railbird; distinct from Kalshi | No public trading API was verified. | Complete public schedule unavailable. | https://ir.aboutdraftkings.com/news/news-details/2026/DraftKings-Launches-Proprietary-Exchange-to-Bolster-Differentiated-Predictions-Experience/default.aspx ; https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/50090 | VERIFIED / DOC / 2026-09-13 |
| V8 | **Ludlow Exchange LLC** was designated 2026-06-16 and is owned by Novig Betting, Inc.; application material describes a CLOB and a third-party registered DCO without publicly naming that DCO in the reviewed filing. | VERIFIED distinct DCM/CLOB | No public DCM API was verified. | Complete route/status-dependent schedule unavailable. | https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/59390 ; https://www.cftc.gov/filings/documents/2026/orgdcmlexexhibitg3260122.pdf | VERIFIED DCM/ownership; UNKNOWN DCO; UNAVAILABLE API / 2026-09-13 |
| V9 | Secondary reports say **Novig’s Ludlow-regulated product launched 2026-08-04** and describe pre-game versus in-play fee differences. No primary launch/API/fee document was obtained in this review. | Same DCM as Ludlow if contract is a Ludlow contract | No verified public API. | Not promoted into executable config. | https://ftw.usatoday.com/story/sports/2026/08/09/what-is-novig-august-9/91170923007/ ; https://www.novig.us/ | UNVERIFIED / 2026-09-13 |
| V10 | **Rothera Exchange and Clearing LLC** is the renamed LedgerX/MIAXdx legal entity and remains a registered DCM and DCO. Robinhood documents live routing of selected contracts to it. | VERIFIED distinct DCM/matching/clearing | No direct public retail trading API was verified. | Rothera exchange formula plus frontend commission is contextual. | https://www.cftc.gov/IndustryOversight/IndustryFilings/TradingOrganizations/40473 ; https://www.cftc.gov/IndustryOversight/IndustryFilings/ClearingOrganizations/30998 ; https://robinhood.com/us/en/newsroom/the-world-cup-is-now-trading-on-robinhood-and-rothera/ | VERIFIED / DOC / 2026-09-13 |

## 4. Frontend and FCM route ledger

A route ID means “contracts in this app that the cited disclosure assigns to this
DCM.” It does not mean every contract in the app uses that route.

| ID | Frontend route | Provenance result | API result | Source | Status / date |
|---|---|---|---|---|---|
| F1 | `ibkr_kalshi` | Kalshi DCM/book: SAME_VENUE with direct Kalshi. Generic `ibkr` is UNKNOWN until the contract route is identified. | IBKR automation documented; authenticated product mapping untested. | https://www.interactivebrokers.com/predictionmarkets/en/home.php | VERIFIED / DOC; UNAVAILABLE / LIVE / 2026-09-13 |
| F2 | `ibkr_forecastex` | ForecastEx DCM/book: independent of Kalshi. | Same authenticated IBKR stack. | https://www.interactivebrokers.com/predictionmarkets/en/home.php | VERIFIED / DOC; UNAVAILABLE / LIVE / 2026-09-13 |
| F3 | `ibkr_cme` | CME DCM/Globex: independent of Kalshi. | Same authenticated IBKR stack. | https://www.interactivebrokers.com/predictionmarkets/en/home.php | VERIFIED / DOC; UNAVAILABLE / LIVE / 2026-09-13 |
| F4 | IBKR's published **direct-client** event-contract prices list ForecastEx `$0.00` IBKR commission + `$0.01` exchange fee; Kalshi `$0.01 + $0.01`; CME `$0.01 + $0.01`. This is not a price guarantee for introduced/advisor/other FCM routes or every account. | Documentation only; account statement reconciliation unavailable. | https://www.interactivebrokers.com/en/pricing/commissions-events.php | VERIFIED / DOC; UNAVAILABLE / LIVE / 2026-10-10 |
| F5 | `coinbase` | Coinbase states its prediction markets are operated by Kalshi and launch flow came from Kalshi: SAME_VENUE with direct Kalshi. | No event-contract automation surface verified. | https://www.coinbase.com/blog/system-update-the-future-of-finance-is-on-coinbase ; https://help.coinbase.com/en/coinbase/trading-and-funding/prediction-markets/intro | VERIFIED / DOC / 2026-09-13 |
| F6 | `webull` | Disclosed Kalshi route: SAME_VENUE with direct Kalshi. | Webull OpenAPI exists, but event-contract endpoints/order support were not verified. | https://www.webull.com/news-detail/12263811258663936 ; https://developer.webull.com/ | VERIFIED route; UNAVAILABLE event API / 2026-09-13 |
| F7 | `moomoo` | Disclosed Kalshi route: SAME_VENUE with direct Kalshi. | No event-contract endpoints found in reviewed OpenAPI material. | https://www.moomoo.com/us/newsroom/moomoo-launches-prediction-markets ; https://openapi.moomoo.com/ | VERIFIED route; UNAVAILABLE event API / 2026-09-13 |
| F8 | `robinhood_*` | Robinhood disclosures identify Kalshi, ForecastEx, Rothera, and CDNA routes. Generic `robinhood` is therefore UNKNOWN; each contract must identify its underlying DCM. | No public customer trading API verified. | https://robinhood.com/us/en/newsroom/the-world-cup-is-now-trading-on-robinhood-and-rothera/ ; https://robinhood.com/us/en/support/articles/prediction-markets/ | VERIFIED / DOC / 2026-09-13 |
| F9 | `fanduel_cme` and `fanduel_cdna` | FanDuel documents CME contracts plus sports/entertainment contracts listed by OG Prediction Markets/CDNA. Generic `fanduel` is UNKNOWN. | No public trading API verified. | https://www.cmegroup.com/media-room/press-releases/2025/11/12/cme_group_and_fanduelannouncelaunchofnewpredictionmarketsapp.html ; https://www.fanduel.com/about/news/fanduel-predicts-to-expand-event-contract-offering-through-partnership-with-crypto-com-and-og-prediction-markets | VERIFIED / DOC / 2026-09-13 |
| F10 | `draftkings_cme`, `draftkings_cdna`, `draftkings_dkex` | DraftKings began with third-party routes and now also has proprietary DKeX/Railbird. Generic `draftkings` is UNKNOWN. | No public trading API verified. | https://www.draftkings.com/draftkings-debuts-predictions-app-entering-prediction-markets ; https://ir.aboutdraftkings.com/news/news-details/2026/DraftKings-Launches-Proprietary-Exchange-to-Bolster-Differentiated-Predictions-Experience/default.aspx | VERIFIED / DOC / 2026-09-13 |

## 5. Independence rules adopted in code

The following are repository policy conclusions based on V1-V10 and F1-F10:

1. Equal **DCM**, contract issuer, matching system, or settlement authority means
   `SAME_VENUE`.
2. Sharing only an unaffiliated/third-party **DCO** is non-dispositive. For example,
   a QC-cleared Gemini Titan contract and a QCX/Polymarket US contract retain
   separate DCMs and books and can be `INDEPENDENT`.
3. A generic multi-route frontend is `UNKNOWN` until its contract route is known.
4. Missing or conflicting critical provenance is `UNKNOWN`; only fully verified
   `INDEPENDENT` reaches guaranteed-arbitrage logic.
5. Aliases collapse: DKeX = Railbird DCM; Novig/Ludlow contracts = Ludlow DCM.

These rules are encoded in
`prediction_arb/provenance/exchange_registry.py` and
`prediction_arb/provenance/independence.py`.

## 6. Utah and federal regulatory ledger

| ID | Claim | Status / test | Source | Verified |
|---|---|---|---|---|
| R1 | Utah Code § 76-9-1401’s 2026 text expressly includes a wager on an individual athlete’s performance (“proposition bet”) in its gambling definitions. | VERIFIED / DOC | https://le.utah.gov/xcode/Title76/Chapter9/C76-9-S1401_2026050620260506.pdf | 2026-09-13 |
| R2 | On 2026-08-04, the District of Utah entered final judgment for Utah in `KalshiEX LLC v. Cox`, holding the CEA did not preempt Utah enforcement against Kalshi sports contracts. | VERIFIED / DOC | https://www.courthousenews.com/wp-content/uploads/2026/08/kalshi-utah-shelby-decision.pdf ; https://www.courtlistener.com/docket/72422603/kalshiex-llc-v-cox/ | 2026-09-13 |
| R3 | The Third Circuit’s 2026 preliminary-injunction decision in `KalshiEX v. Flaherty` reached the opposite preemption result for New Jersey. | VERIFIED / DOC | https://www2.ca3.uscourts.gov/opinarch/251922p.pdf | 2026-09-13 |
| R4 | The Ninth Circuit’s 2026 published opinion held Kalshi had not shown likely CEA preemption against Nevada sports-gaming enforcement. | VERIFIED / DOC | https://cdn.ca9.uscourts.gov/datastore/opinions/2026/08/28/25-7516.pdf | 2026-09-13 |
| R5 | Secondary reporting says the Tenth Circuit denied Kalshi an injunction pending appeal in September 2026 while the merits appeal remained pending. A primary appellate order was not obtained. | UNVERIFIED / DOC | https://sbcamericas.com/2026/09/08/kalshi-denied-tenth-circuit-appeal/ | 2026-09-13 |
| R6 | The CFTC published a prediction-market ANPRM on 2026-03-16. | VERIFIED / DOC | https://www.federalregister.gov/documents/2026/03/16/2026-05105/prediction-markets | 2026-09-13 |
| R7 | The CFTC published a proposed Rule 40.11 framework on 2026-06-12, with comments closing 2026-07-27. It is a proposed rule, not an effective final rule. | VERIFIED / DOC | https://www.federalregister.gov/documents/2026/06/12/2026-11854/prediction-markets-public-interest-determinations | 2026-09-13 |
| R8 | No effective 2026 final Rule 40.11 amendment was identified in the official materials reviewed through the cut-off. This negative search result is not a guarantee that no later/unindexed action exists. | UNKNOWN / DOC | https://www.federalregister.gov/agencies/commodity-futures-trading-commission | 2026-09-13 |
| R9 | Polymarket US's own **Trading Restrictions** page addresses who should not trade (sell-side/buy-side employees, MNPI holders, firm compliance) and says **nothing about state availability**. Secondary sources disagree about Utah: one lists Utah as available with only Nevada excluded, another lists ~11 restricted states, and a third says state restrictions are unpublished. No primary source establishes Utah eligibility, and the sources conflict with each other. | UNKNOWN / DOC (conflicting secondary sources) | https://docs.polymarket.us/learn/trading/access-and-limits/trading-restrictions ; https://oddsassist.com/prediction-markets/polymarket-us-states/ ; https://predscope.com/guide/polymarket-us ; https://www.saturdaydownsouth.com/prediction-markets/polymarket-promo-code/legal-states/ | 2026-09-13 |

**R9 consequence:** Utah eligibility for Polymarket US stays **UNKNOWN**.
Secondary blogs that contradict each other are not evidence, and a venue
marketing page loading is not eligibility. The `check_contract_eligibility`
helper rejects UNKNOWN when called with `Mode.LIVE`; `PaperEngine` calls the
helper for paper discovery. However, `LiveEngine` is monitor-only: no production
eligibility scan or caller to the context-gated live submit method exists, so
the helper's live policy is not an end-to-end live gate. The only acceptable
evidence resolutions are (a) the venue's own terms or
account dashboard stating Utah availability for the specific non-sports
categories, or (b) owner/legal review. Utah **sports** stays RESTRICTED
regardless (R1/R2), which matters for Polymarket US specifically because it
carries heavy sports inventory.

### Eligibility policy and current runtime path

- **Utah sports, every venue:** `RESTRICTED` based on R1-R2. This is broader
  than Kalshi because the statute is venue-neutral; frontend routing does not
  avoid it. `check_contract_eligibility` rejects it, and `PaperEngine` applies
  that helper during discovery.
- **Utah non-sports, every venue:** `UNKNOWN`. Federal registration, national
  marketing, or an app loading in another state is not proof of Utah eligibility.
- The helper returns a block for both `RESTRICTED` and `UNKNOWN` when called
  with `Mode.LIVE`; its UNKNOWN result is research-allowed for supported
  non-live modes. `LiveEngine` is monitor-only: no production eligibility scan
  or caller to the context-gated live submit method exists, and historical
  replay does not call this eligibility helper. Therefore this is a
  classification/helper policy, not an end-to-end live control. No result is a
  legal conclusion.

## 7. Fee representation and execution gates

### Exact facts captured

| Venue/route | Verified reference | Runtime status | Why |
|---|---|---|---|
| ForecastEx / IBKR-ForecastEx | `$0.01` exchange component per executed side; opposite bids pair at `$1.00`; IBKR publishes `$0.00` commission for the documented direct-client route (V1, F4) | **INCOMPLETE — BLOCK** for trade and settlement | Account/FCM route and all-in commission are not established; accessible current rulebook extraction did not verify settlement Rules 416/417/603. The prior global zero-settlement claim is withdrawn. |
| Kalshi direct | Standard `0.07` taker / `0.0175` maker-enabled reference formulas plus dynamic series/event overrides (K6-K8) | **INCOMPLETE — BLOCK** for trade and settlement | Series/event metadata, member precision, actual order accumulator/fills, and a simple-binary-only settlement guard are absent. |
| Polymarket US | Current standard `0.0695` taker / `-0.0125` maker, Table Tennis `0.10` taker, separate combo curve, banker's rounding, aggressive-order cap, weekly volume rebates (P8, P11-P13) | **INCOMPLETE — BLOCK** for trade and settlement | Fee-family selection, combo handling, account/fill reconciliation, and customer settlement treatment are not established. Published arithmetic tests use a test-only synthetic schedule; they do not promote runtime status. |
| Gemini | Pre/post-2026-03-18 cohorts (V3) | **INCOMPLETE — BLOCK** | Listing cohort and contract/clearing context absent. |
| CDNA/OG | Published range only (V4) | **INCOMPLETE — BLOCK** | Exact function and route treatment unverified. |
| CME / IBKR routes | Exchange and FCM charges documented (V5, F4) | **INCOMPLETE — BLOCK** | Product/route/account and settlement treatment not integrated or live-reconciled. |
| Railbird/DKeX, Ludlow/Novig, Rothera/frontends | No complete verified route schedule | **INCOMPLETE — BLOCK** | Contract-, status-, route-, or account-dependent data missing. |

`config/fees.yaml` schema v2 records calculation status, source, verification
provenance, required context, rounding/cap rules, and route/market context.
`FeeCalculator` rejects schedules marked `INCOMPLETE`; it has no execution-facing
“estimate” mode. The three enabled primary venue schedules all block. See
`docs/FEE_READINESS.md` for the current pair-readiness matrix, documented versus
live status, exact customer/account gaps, and operator actions. Synthetic complete
schedules remain isolated to generic arithmetic tests.

## 8. Ranked candidate matrix

Scoring is an internal triage tool, not a factual claim or permission to trade.
Each dimension is 0-5. `U` is zero unless Utah non-sports access is verified; sports
cannot score because it is restricted. `L` is zero where authenticated/live liquidity
was not measured. Fee incompleteness is a separate hard gate and cannot be offset by
points.

| Rank | Candidate integration | Independence `I` | API quality `A` | Utah `U` | likely Kalshi overlap `O` | measured liquidity `L` | Total / 25 | Evidence and disposition |
|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | Polymarket US direct | 5 | 4.5 | 0 | 4 | 0 | **13.5** | V2, R1-R2. **Recommend first direct-adapter due diligence**: owner account/Utah check, authenticated catalog+book test, then exact fee-state design. |
| 2 | Gemini direct | 5 | 4.5 | 0 | 3 | 0 | **12.5** | V3, R1-R2. **Recommend second direct candidate**; excellent documented sandbox/WS, but cohort fees must be solved. |
| 3 | IBKR ForecastEx + CME routes | 5 | 3.5 | 0 | 3.5 | 0 | **12.0** | V1, V5, F1-F4. **Recommend operational option if owner has IBKR**. Session/gateway complexity and route qualification lower API score. |
| 4 | Rothera (currently Robinhood-routed) | 5 | 0.5 | 0 | 3 | 0 | **8.5** | V10, F8. Hold: distinct live exchange, but no direct public adapter path. |
| 5 | CDNA / OG | 5 | 0.5 | 0 | 3 | 0 | **8.5** | V4, F8-F10. Hold: strong category overlap, no verified complete API/fee path. |
| 6 | Railbird / DKeX | 5 | 0 | 0 | 3 | 0 | **8.0** | V6-V7, F10. Hold: live proprietary venue, but sports is restricted and API/fees/cutover are unresolved. |
| 7 | Ludlow / Novig | 5 | 0 | 0 | 3 | 0 | **8.0** | V8-V9. Hold: sports-only value is unusable in Utah and operational claims lack primary API/fee evidence. |
| 8 | Direct/other CME FCM | 5 | 1 | 0 | 2 | 0 | **8.0** | V5. Prefer the better-documented IBKR route rather than inventing a direct retail path. |
| — | Coinbase/Webull/Moomoo Kalshi routes | 0 | 0-1 | 0 | 5 | n/a | **not candidates** | F5-F7. SAME_VENUE with Kalshi; useful only for future route/fee comparison. |
| — | Generic IBKR/Robinhood/FanDuel/DraftKings brand | ? | 0 | 0 | ? | n/a | **blocked** | F1-F3, F8-F10. Route UNKNOWN until each contract identifies its DCM. |

“Likely overlap” only scores documented category breadth; exact contract identity,
settlement terms, and simultaneous availability must still pass deterministic + LLM
matching. No score treats app-level volume as exchange-book liquidity.

## 9. Owner checkpoint — stop before adapter work

Phase 1 recommendation:

- **Default:** choose **Polymarket US** for Phase 2 account/eligibility and live-read
  validation, then adapter implementation only if those gates pass.
- **Parallel fallback:** validate **Gemini** sandbox/account and cohort metadata.
- **Operational alternative:** choose **IBKR** if one integration across ForecastEx
  and CME is more valuable than a direct venue API and the owner accepts a local
  authenticated gateway/session dependency.

Before Phase 2/3, the owner must select one route and confirm willingness to open/use
that account. No credentials should be supplied in chat. Regardless of selection,
Utah sports stays disabled and live trading remains off until a specific non-sports
eligibility record, contextual fees, provenance, matching, and authenticated books
all pass.
