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

**Paper capital and risk enforcement** (`test_risk_enforcement.py`, D20–D24,
2026-10-10 — all offline): checked-in paper cash covers every enabled venue;
missing startup/order capital is explicit; the configured 12-field inventory
matches `RiskLimits`; every capital/trade/event/venue/daily-loss/position/order
breach blocks before leg A with zero submissions; successful fills update state
and block a later over-limit trade; aggregate/per-incident exposure is checked
prospectively and by age/contracts/dollars. Fault-injected simulation covers a
successful hedge retry, failed retries plus failed unwind, timeout breach,
persistent kill activation, recovery continuing after kill, exposure clearing,
concession-cap exhaustion, and observed recovery loss feeding the next daily
loss check. A real `PaperExecutionEngine` regression proves a sell cannot
create a short and removes the unwound position. 15 tests; no adapter or real
order path.

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

**Unavailable mandatory costs** (`test_unavailable_costs.py`, D17,
2026-10-10 — all offline, synthetic fee schedules): an opportunity whose
fees or settlement timing cannot be computed has **no** net profit rather
than a flattering one. Incomplete trade fees, incomplete settlement fees,
context-required fees, missing / expired / not-yet-effective / stale
(strict) / unverified schedules, a missing settlement time, and a venue
with no settlement-delay model each yield `status=BLOCK`,
`net_profit=None`, `total_fees=None`, `capital_required=None` and a named
unavailable `CostComponent`; gross edge and the costs that *were*
computable stay available and separate. Also pins: `CostLedger` has no
total while a component is missing (an empty ledger is not complete);
`score()` refuses an unpriced opportunity and `rank()` puts a huge
unpriced gross edge last; the dashboard renders `n/a`; the database stores
NULL plus `unavailable_costs`; migration 003 relaxes the cost columns on a
v2 database without touching its rows; and the paper report / historical
summary count unpriced opportunities instead of implying they are
profitable. 35 tests. Verified by mutation: restoring the zero fee substitution fails
20 of them (20 failed / 15 passed) and restoring the zero delay-cost
substitution fails 2 (the `TestUnknownSettlementTiming` pair).

**Settlement timing and capital lockup** (`test_settlement_timing.py`, D18,
2026-10-10 — offline): every production timing entry states meaning,
evidence status, `https` sources and review date, and an UNKNOWN entry
cannot carry an estimator; Polymarket US and ForecastEx are explicit
UNKNOWN and block; Kalshi uses `latest_expiration_time +
settlement_timer_seconds` from the payload, survives the adapter and the
recorded-store codec, and blocks when either field is missing/invalid or
inconsistent; a release time at or before evaluation time is UNKNOWN, not
zero; replay measures from the frame time; long-dated bounds are costed in
full (a three-year pair loses its TRADE verdict); lockup follows the last
leg and spread compares release times; the simple annual cost is exact,
linear, rounded up and rejects negative inputs; the scan entry point
threads the frame time. 44 tests. Engine-math tests
use a TEST-ONLY ForecastEx timing entry from `tests/conftest.py`; the
`production_settlement_timing` fixture restores production. Mutation-verified
(each of eight reverts fails at least one test; see ROADMAP D18).

**Readiness and mandatory fee freshness** (`test_readiness.py`, D10 + D19,
2026-10-10 — all offline: stub adapters, synthetic fee schedules, a
`tmp_path` copy of the real `config/`, an isolated kill-switch file):
*one freshness policy, derived from the mode* (§98). Live and paper refuse a
stale, undated, unverified or missing schedule (`FeeDataError` — never a stale
number, never zero); historical still costs it and is labelled
`RESEARCH USE ONLY: …`; collect never calculates a fee; an unrecognized mode
fails closed; an explicit `strict_fee_freshness` override wins in both
directions. Enforcement is proved at every layer that takes a fee-derived
decision — `schedule_for`, `evaluate_two_leg`, `run_arbitrage_scan`, simulated
paper execution, historical execution, and a full historical replay — plus the
age boundary (`max_schedule_age_days` exactly, and a stricter configured
limit). *Readiness verdicts are computed, never hardcoded* (§99): every
criterion is shown passing **and** failing on its own observation (claim-level
documentation/connectivity/usable-market-data/full-verification evidence,
`APIStatus` and verified order types, provenance independence including
UNKNOWN, matcher configuration, fee completeness and freshness, settlement
timing, recorded-evidence volume, eligibility, kill switch, risk limits,
book-staleness gates, `.env.live` existence), a mode is ready only when no
applicable criterion is BLOCKED, a synthetic all-pass input yields
`live.ready is True` (so no verdict can be a constant), and producing the
report does not create `data/`. The CLI is asserted end to end: exit 0, the
per-mode policy line, thresholds read from `config/settings.yaml`, every real
live blocker named with its reason, and the advisory footer that enables
nothing. `print_block_reasons` (§100) is checked for deduplication, counts,
most-common-first ordering, the bounded "and N further distinct reason(s)"
tail, markup escaping, and silence when nothing was blocked. 138 tests.
Mutation-verified (eight reverts, each caught, source restored and
diff-checked): `freshness_enforced` always False → 9 failures; paper
downgraded to RESEARCH → 9; `ModeReadiness.ready` hardcoded `False` → 7;
`order_types_verified` ignored → 3; the readiness probe creating the recorded
store → 2; the fee-freshness criterion always passing → 5; the historical
engine dropping the research label → 1; a stale RESEARCH schedule silently
tolerated → 1. The whole suite also passes with the fee clock frozen at
2026-10-14 / 2027-10-09 / 2031-01-02 — that sweep is what exposed a fixed
`verified_at` in the shared fixtures, now derived from the fee engine's own
clock (`conftest.fresh_verification_date()`).

**Candidate retrieval (§96, Arb.8)** (`test_candidate_retrieval.py`, 31 tests; `test_candidate_generation.py`, 29 tests): settlement window and day boundary; numeric normalization (`100k` = `100,000`); different wording; generic words that must not link; non-transitivity; proposal is not approval; labelled reference set including the pinned known miss (K22/P18); synthetic recall at 300 contracts (pinned); the frozen 1,000-contract baseline (1 component, pinned). The 1,000-contract recall figures are in committed artifacts, not in a test. Offline. Benchmark and results: `docs/CANDIDATE_RETRIEVAL.md`.

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
  malformed/nonfinite/non-tradeable levels dropped and logged; missing
  market tick/minimum rules stay unknown rather than receiving defaults;
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
* per-market rules — valid `orderPriceMinTickSize` /
  `minimumTradeQty` carried from the payload; missing, malformed, nonfinite,
  or out-of-range values remain unknown and are never inferred; closed/archived
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

**Evidence-scoped adapter and collection validation** (`test_collect_mode.py`,
`test_kalshi_smoke_script.py`, `test_polymarket_us_smoke_script.py`,
2026-10-10 — all offline):
- collection admits only `MARKET_DATA_OBSERVED` / `VERIFIED`, records valid
  two-sided uncrossed snapshots, rejects empty/invalid/crossed/non-finite
  books, and never constructs or invokes an execution engine;
- Kalshi and Polymarket US public smoke decision logic keeps
  documentation/connectivity/usable-market-data/full-verification distinct,
  classifies TLS/transport/5xx/429 as `UNAVAILABLE` rather than adapter
  failure, and does not load credentials or place orders;
- a successful public smoke is marked as a manual market-data candidate only;
  unknown per-market rules cannot be filled from defaults.
These tests assert script behavior and status policy, not live connectivity.

**Paper streaming** (`test_paper_streaming.py`, all offline, fake adapters): a
streamed contract is no longer REST-polled and pushed books land in the live
cache; a streaming failure falls back to polling exactly once per venue per
run (never a per-cycle retry storm); with no credentials the engine polls
only; a `MARKET_DATA_OBSERVED` adapter can serve REST reads but cannot subscribe
to the WebSocket; streamed books are validated before caching; and a market
that leaves discovery is unsubscribed and evicted from the book cache (even
when discovery returns nothing at all).

## Integration-testing record

On 2026-08-30, a prior run exercised `--mode collect --cycles 1` against the
Kalshi compatibility host (500 markets and 40 non-empty books), paper mode,
historical no-data behavior, kill-switch refusal, and startup isolation.

On 2026-10-10, the current bounded public-only probes were run with no
credentials and no orders: `scripts/kalshi_smoke.py --limit 1` exited 1 as
`UNAVAILABLE` before HTTP response because both configured bases
(`external-api.kalshi.com` and `api.elections.kalshi.com`) raised
`ConnectError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)`;
`scripts/polymarket_us_smoke.py --limit 1` exited 1 as `UNAVAILABLE` before
HTTP response because `gateway.polymarket.us` raised
`ConnectionError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)`.
Neither outcome establishes an outage or adapter failure; neither established
usable current market data or full adapter verification.

Historical D10/D19 readiness result (2026-10-10, before D27):
`python -m prediction_arb.main --readiness` exited 0 with four per-mode blocks
and derived criteria
(`COLLECT: READY`, `HISTORICAL: NOT READY — 1 blocker(s)`,
`PAPER (this run's mode): NOT READY — 5 blocker(s)`,
`LIVE: NOT READY — 8 blocker(s)`), `LIVE MODE: NOT RECOMMENDED YET`, and **no**
`data/`, `logs/` or `reports/` directory created by the run — CI asserts the
runtime tree is unchanged by it (a before/after delta, because the suite
itself leaves an empty `data/paper/` behind). `--mode historical --date 2020-01-01` still exits 0 with the honest
empty-replay message, and the prior `--mode paper --cycles 1` smoke exited 0
reporting no active-venue markets — with no opportunities there are no block
reasons, and `print_block_reasons` correctly prints nothing rather than an
empty table; generated artifacts were deleted afterwards.

**D27 verification (2026-10-10):** the full suite re-ran with
`.venv/bin/pytest -o addopts=''`: **632 passed in 28.75 s**. The current
`--status` output reports Kalshi `MARKET_DATA_OBSERVED`, Polymarket US and
ForecastEx `API_DOCUMENTED_UNTESTED`, and no fully verified adapter; both
latest smoke attempts remain `UNAVAILABLE` (exact details above and in
`docs/VENUE_RESEARCH.md`). `--readiness` exits 0 with `COLLECT: READY` (one
market-data-evidenced adapter), `HISTORICAL: NOT READY — 1 blocker(s)`,
`PAPER: NOT READY — 5 blocker(s)`, and `LIVE: NOT READY — 8 blocker(s)`
(0 live-capable venues). The report creates no runtime data. Network failures
are attempt evidence only, not adapter failure or verification.
