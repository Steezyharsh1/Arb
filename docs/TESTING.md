# TESTING

```bash
pytest                        # full suite (configured in pyproject.toml)
python -m pytest tests/       # equivalent
```

## Coverage map

**Spec §75 mandatory cases** (`tests/test_spec_cases.py`):
1. Fees exceed $0.01 gross → NO TRADE ✅
2. 5 available vs 100 requested → sized to 5 ✅
3. Same underlying exchange → NO ARBITRAGE ✅
4. UNKNOWN relationship → BLOCK ✅
5. LLM says match, deterministic disagrees → REJECT ✅
6. 2% displayed, 2.2% real costs → NO TRADE ✅
7. One leg fills → recovery procedure runs ✅
8. Crash between legs → intents found & reconciled ✅
9. T+0 vs T+3 settlement delay costed (unknown → None, blocking) ✅
10. paper config + LiveExecutionEngine → startup refused ✅
    (also verified against the real CLI)

**Layout & packaging** (`test_paths.py`, Phase 0): project-root discovery
(marker search, `$PREDICTION_ARB_HOME` override, loud failure instead of
guessing), per-mode `data/`/`logs/`/`reports/` separation, global (not
per-mode) kill-switch file, no runtime artifacts inside the importable
package, `pyproject.toml` console script / dependency wiring, `.gitignore`
coverage, and a credentials-free `.env.example`.

**Unit** (`test_units.py`): book walking, staleness, Kalshi
`orderbook_fp` parsing (ask synthesis from NO bids), isolated synthetic
formula math, production fee-context failures, ForecastEx $1.00 pairing and
separate per-side fees, route-qualified provenance, shared-DCO independence,
Utah category/event-text sports restrictions, deterministic matcher rejections,
LLM schema validation, risk limits, settlement lockup, metadata, normalization,
and aliases.

**Safety** (`test_safety.py`): paper engine holds no adapters; adapter
refuses orders outside live; mode-mismatch rejection; confirmation
phrase; firewall — every one of its checks individually blocks
(parameterized); kill switch; stub honesty (NotImplementedError, never
fabricated data); live-credential leakage into paper env refused; DB
mode-tag enforcement.

**Failure** (`test_failure_modes.py`): stale/out-of-order books hidden;
FEE_DATA_INCOMPLETE blocks (never zero); strict fee freshness;
insufficient sim cash; limit-price respected; historical no-data
honesty; backtest stub raises; capital non-fungibility.

**Arbitrage** (`test_arbitrage_engine.py`): marginal sizing stops at
payout; profitable path yields TRADE; reproducibility (identical inputs
→ identical outputs); search requires APPROVED certificates; SAME_VENUE
pairs never evaluated; graph candidates; full matching pipeline with a
mock LLM (approve) and without one (REVIEW only).

**Kalshi Phase 2** (`test_kalshi_phase2.py`, 2026-09-13 — all offline:
mock HTTP transport + a scripted **local WebSocket server**):
* host failover — 5xx rotation to the fallback base, 404/429 never
  rotate, DNS/transport failure rotation, all-bases-5xx raises, sticky
  host kept, connect-time probing of every base, refusal when all dead;
* bulk `GET /markets/orderbooks` — auth required, 100-ticker batching,
  auth headers present, parsed books;
* rate-limit discovery — `GET /account/limits` tier parsing,
  `GET /account/endpoint_costs` cost map + default cost, budget
  clamping that only ever LOWERS the configured read/write budgets
  (headroom 80%), zero-budget tier keeps configured values;
* parser hardening — WebSocket `*_fp` key names, sequence passthrough,
  malformed / non-tradeable levels dropped and logged;
* WebSocket feed against the local server — handshake signature over the
  host-independent `GET /trade-api/ws/v2` path (verified with a
  generated RSA key), snapshot + in-order delta application with ask
  synthesis, sid-based unsubscribe, **sequence gap ⇒ recovery snapshot,
  never silent apply**, clean-close reconnect with resubscribe/rebase,
  and terminal handling of WS error code 9 (auth).

**Polymarket US Phase 3** (`test_polymarket_us.py`, 2026-09-13 — all
offline: mock HTTP transport + a scripted **local WebSocket server**):
* public market data — discovery and books succeed with **no
  credentials**, every request goes to the public gateway host and
  carries no auth header;
* books are genuinely two-sided (real `offers`, nothing synthesized as
  `1 − bid`), levels sorted best-first, malformed and non-tradeable
  levels dropped;
* market-state gating — each of the six non-`OPEN` states yields **no
  book** (parameterized), so a halted market can never be sized against;
* per-market rules — `orderPriceMinTickSize` / `minimumTradeQty` carried
  from the payload, coarsest-safe fallback when absent, closed/archived
  markets skipped, sports signals preserved for the Utah gate;
* Ed25519 signing verified against a generated keypair over
  `timestamp + METHOD + path`, authenticated calls hit the authed host,
  malformed/short secret keys rejected clearly;
* rate limits — budgets clamped below the published 20 req/s ceiling,
  `429` surfaced for backoff rather than swallowed;
* safety posture — order placement refused even with
  `allow_order_placement=True` in live mode, API status never upgraded
  from documentation, independence from Kalshi, official-host guard;
* WebSocket feed against the local server — handshake signature over
  `/v1/ws/markets`, documented subscribe frame, **full-snapshot
  replacement** (stale levels vanish), non-tradeable streamed books
  dropped, heartbeats counted, subscription errors fail fast, auth
  errors terminal, clean-close reconnect + resubscribe, unsubscribe
  stops delivery.

**Paper streaming** (`test_paper_streaming.py`, 2026-09-13 — all offline,
fake adapters): a streamed contract is no longer REST-polled and pushed
books land in the live cache; a streaming failure falls back to polling
exactly once per venue per run (never a per-cycle retry storm); with no
credentials the engine polls only; and a market that leaves discovery is
unsubscribed and evicted from the book cache (even when discovery
returns nothing at all).

## Integration-testing record

On 2026-08-30, a prior run exercised `--mode collect --cycles 1` against the
Kalshi compatibility host (500 markets and 40 non-empty books), paper mode,
historical no-data behavior, kill-switch refusal, and startup isolation.

On 2026-09-13, Phase 1 re-verified Kalshi/Polymarket US/Gemini documentation,
but fresh sandbox HTTP smoke tests ended in a TLS EOF before any HTTP response.
Those fresh network results are **UNAVAILABLE**, not successful live tests.
`--status` and the full suite remain local verification gates.
