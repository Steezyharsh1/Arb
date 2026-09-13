# ROADMAP & PHASE MAP

Compiled 2026-09-13 (America/Denver) from a full read of the repository,
a live run of the test suite and CLI, inspection of both SQLite
databases and the JSONL logs, and a re-check of venue/API sources.

This is the reference document for anyone (human or agent) continuing
the project. It describes **phases**, the **verified current state**,
the **defect register**, and the **actions only a human can perform**.

Status tags used throughout:

| Tag | Meaning |
|---|---|
| DONE | Implemented and verified |
| PARTIAL | Implemented but incomplete or unverified against a real venue |
| BROKEN | Implemented but demonstrably does not work as documented |
| NOT STARTED | Stub or absent |
| HUMAN | Blocked on an action only the account owner can take |

---

## 1. Current state snapshot (all verified locally 2026-09-13)

| Check | Result |
|---|---|
| `pytest` | **224 passed** (~23s), re-run 2026-09-13 after the Phase 2/3 completion pass: Phase 0's 107 + 11 Phase 1 fee/provenance/compliance + 26 Phase 2 (22 Kalshi host-failover/bulk/WS-streaming/rate-limit + 4 paper-streaming) + 41 Phase 3 Polymarket US + 24 exact-fee-model (`tests/test_polymarket_fees.py`) + 12 smoke-script/D14 (`tests/test_kalshi_smoke_script.py`, `tests/test_units.py::TestRateLimitCalibration`) + 3 per-fill accuracy. All offline: mock HTTP transports and a scripted local WebSocket server |
| `python -m prediction_arb.main --status` | Works: kalshi=VERIFIED, polymarket_us=API_DOCUMENTED_UNTESTED, forecast_ex=API_DOCUMENTED_UNTESTED; independence matrix correct (kalshi↔polymarket_us = INDEPENDENT) |
| `python -m prediction_arb.main --readiness` | 0 snapshots recorded in a fresh clone; **0 opportunities ever analyzed**; live NOT RECOMMENDED. (The 13,801-snapshot figure below was from the previous engineer's working copy — the DB is git-ignored, so a fresh clone starts empty.) |
| Live-capable venues | **1** (Kalshi). Polymarket US is implemented and INDEPENDENT, but stays API_DOCUMENTED_UNTESTED until the owner captures observed-payload evidence (P7) |
| `data/paper/…sqlite3` | Not present in a fresh clone (git-ignored). Previously 21,892 markets / 13,801 book snapshots spanning 2026-08-30 → 2026-09-05, tagged `mode=collect` |
| `data/historical/…sqlite3` | **Empty** (0 rows in every table) |
| `reports/historical/…json` | `{"frames": 0, "opportunities": 0}` |
| `logs/collect/collect_20260831.jsonl` | Not present in a fresh clone. Previously 32 error rows: 18× DNS failure, 14× Kalshi HTTP 500 (2026-08-31 → 2026-09-04) |
| CI | **Has run and is green**: 10 completed runs, all `success`, including run 34777759265 on `main` (`gh run list`) |
| Outbound network from this sandbox | Venues unreachable (TLS closed on `external-api.kalshi.com`, `api.elections.kalshi.com`, `gateway.polymarket.us`, `api.polymarket.us`) — venue calls must be verified on the owner's machine. **Documentation hosts ARE reachable through the research tooling**, which is how rows P8–P12 and R9 were re-verified |

**Bottom line:** the safety architecture and Phase 1 fail-closed research
registries are tested, but the opportunity pipeline has never produced a
candidate. A second implemented venue, D6 candidate generation, D9 matcher
wiring, and exact contextual fee/eligibility data remain independent blockers.

---

## 2. Phase map

| Phase | Name | Status | Primary exit criterion |
|---|---|---|---|
| 0 | Repo hygiene, packaging, runnability, spec reconstruction | **DONE 2026-09-13** | Fresh clone → `pip install -e ".[dev]"` → full suite passes → CLI runs, no manual hacks |
| 1 | Venue & API research refresh (2026) | **DONE 2026-09-13 — OWNER CHECKPOINT** | Owner selects Polymarket US, Gemini, or IBKR route before adapter work |
| 2 | Kalshi adapter refresh + streaming | PARTIAL — code complete + verification script delivered 2026-09-13 (D5, D14 fixed); **owner run pending** | 24h collect run with ≥99% cycle success; WebSocket streaming in paper |
| 3 | **Second independent venue adapter** | PARTIAL — adapter + exact fee model complete 2026-09-13 (gate 3 CLOSED); **gates 1, 2, 4 are owner-gated** | Real books from the owner-selected venue proven INDEPENDENT of Kalshi, in paper mode |
| 4 | Data lineage, replay, backtest metrics | BROKEN (D3, D4) | `--mode historical --date …` replays recorded data and reports metrics |
| 5 | Matching that can actually match | BROKEN (D6, D9) | APPROVED certificates produced from real cross-venue data |
| 6 | Paper validation & realism | PARTIAL (D11, D12) | Multi-week paper run with realistic fill/staleness modelling |
| 7 | Compliance & legal re-verification | PARTIAL — 2026-09-13 Utah/CFTC snapshot complete | Owner/legal review and venue/category eligibility evidence before live |
| 8 | Live readiness & first live path | NOT STARTED (HUMAN) | IBKR paper + Kalshi demo drills, then a capped live trade |
| 9 | v2 strategy surface (multi-leg, logical, routing) | NOT STARTED | Behind existing stubs, only after Phase 6 evidence |
| 10 | Operations (scheduler, alerting, retention) | NOT STARTED | Unattended collection + alerting |

---

## 3. Phase detail

### Phase 0 — Repo hygiene, packaging, runnability  ·  DONE 2026-09-13

**Why first:** nothing else can be verified by anyone else until a fresh
clone runs. Before this phase the repository could not even be imported.

Tasks
1. Fix the package-name mismatch (D1). Recommended: move the package
   into a `prediction_arb/` directory at the repo root, add
   `pyproject.toml` (setuptools, `pip install -e .`), and make
   `python -m prediction_arb.main` the documented entry point.
   Decide explicitly whether `config/`, `data/`, `logs/`, `reports/`
   live inside the package or at the repo root — then make
   `prediction_arb/core/config.py:ROOT`, `prediction_arb/core/logging.py:_ROOT` and
   `modes/*/reports.py` agree with that decision.
2. Add `.gitignore`; stop tracking `__pycache__/*.pyc` (60 files),
   the 17 MB SQLite databases, and scratch output. Decide and document
   a data-retention policy for recorded market data (keep locally,
   out of git).
3. Move `reconnect.py` (root-level IBKR Client Portal scratch script)
   into `scripts/` with a docstring, or delete it — and never keep
   `verify=False` in library code.
4. Normalise line endings (`.gitattributes`); several committed files
   are CRLF.
5. Reconstruct the missing spec (D8) as `docs/SPEC.md`: **73 distinct
   `§` sections are cited at 224 sites** across code, config, docs and
   tests, and no spec document existed. Derive each cited section from the code,
   tests and docs that reference it; mark each entry
   `RECONSTRUCTED-FROM-CODE` vs `RECONSTRUCTED-FROM-DOCS`; keep the
   numbering so existing citations resolve; add a table mapping every
   cited `§N` → spec heading → primary source file.
6. Add CI (GitHub Actions): install → `pytest` → `--status` smoke test.

Exit criteria: `git clone … && pip install -e ".[dev]" && pytest` → all
green; `python -m prediction_arb.main --status` prints the venue table;
`docs/SPEC.md` resolves every cited `§`. **Met 2026-09-13.**

#### Completion record (2026-09-13)

| Change | Files |
|---|---|
| Package moved into `prediction_arb/`; `config/`, `data/`, `logs/`, `reports/`, `docs/`, `tests/`, `scripts/` stay at the project root | 92 `git mv` renames |
| Packaging + entry points: `pip install -e ".[dev]"`, console script `prediction-arb`, `python -m prediction_arb`, pytest config | `pyproject.toml`, `prediction_arb/__main__.py`, `prediction_arb/__init__.py`, `requirements.txt` (runtime only; pytest moved to the `dev` extra) |
| Single path-resolution layer: project root discovered via `$PREDICTION_ARB_HOME` → marker search → loud `ProjectRootError` (never guesses) | `prediction_arb/core/paths.py` (new); `core/config.py`, `core/logging.py`, `fees/venue_fees.py`, `risk/kill_switch.py`, `modes/*/reports.py` now use it |
| Repo hygiene: artifacts untracked (60 `.pyc`, both SQLite DBs, `logs/`, `reports/`), line endings normalised, secrets excluded, env template added | `.gitignore`, `.gitattributes`, `.env.example` |
| Scratch script promoted and made safe: TLS verification is no longer silently disabled — it requires `--ca-bundle`, or `--allow-self-signed` **only** for a localhost gateway | `scripts/ibkr_gateway_session.py` (was root `reconnect.py`) |
| Spec reconstructed: all 73 cited sections, evidence-tagged `[C]/[D]/[T]/[G]/[I]`, index, uncited-number appendix, range-citation appendix, maintenance rules. Coverage verified exact: 73 cited ↔ 73 reconstructed, nothing invented | `docs/SPEC.md` (new) |
| CI: install → full test suite → four CLI smokes → honest-empty-replay check → tracked-artifact guard | `.github/workflows/ci.yml` (new) |
| Tests: 23 new layout/packaging/hygiene tests (84 → 107) | `tests/test_paths.py` (new), `tests/conftest.py` |
| Docs updated for the new layout and commands | `README.md`, `docs/API_SETUP.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`, and package-qualified module paths throughout `docs/` |

Still open from Phase 0 (deliberately not done unilaterally):
1. ~~**CI has not run**~~ **CLOSED 2026-09-13** — verified with `gh run list`:
   10 completed runs, all `success`, including run `34777759265` on `main`
   (the merge of PR #3) and the per-PR runs for Phases 0, 1, 2 and 3.
2. **Git history still contains the 17 MB SQLite blob.** Untracking stops the
   bleed; removing it from history needs a rewrite (`git filter-repo`/BFG) and
   must be the owner's decision.
3. **Data-retention policy (H5)** is still the owner's call; `.gitignore`
   documents the recommendation (keep locally, out of git).
4. **`docs/SPEC.md` §33 and §68 carry `[I]` INFERRED flags** — confirm or
   correct them.

---

### Phase 1 — Venue & API research refresh  ·  DONE 2026-09-13 / OWNER CHECKPOINT

Phase 1 re-verified Kalshi's official environments, fixed-point bid-only
order books, WebSocket sequencing, token-bucket limits, and context-dependent
fee model. It also reviewed the 2026 US landscape: IBKR routes, ForecastEx,
CME, Polymarket US, Gemini Titan/Olympus, CDNA/OG, Railbird/DKeX,
Ludlow/Novig, Rothera, and the consumer frontends operated by Robinhood,
Coinbase, Webull, Moomoo, FanDuel, and DraftKings.

Completion record:

- `docs/VENUE_RESEARCH.md` is a claim-level evidence ledger with explicit
  VERIFIED / UNVERIFIED / UNAVAILABLE / UNKNOWN status, source URL,
  verification date, and DOC-vs-LIVE distinction.
- Provenance now models direct DCMs, aliases, generic multi-route frontends,
  and contract-qualified frontend routes. Matching DCM/issuer/book identity is
  SAME_VENUE; sharing only a DCO is not dispositive; incomplete/conflicting
  routing is UNKNOWN.
- `config/fees.yaml` schema v2 records calculation completeness, required
  context, source, documentation status, and live-test status. Incomplete fees
  raise `FEE_DATA_INCOMPLETE`; only represented ForecastEx direct/IBKR routes
  are complete. Kalshi is deliberately blocked pending per-series/event and
  fill/rounding context.
- ForecastEx semantics were corrected: opposite bids pair at **$1.00** and a
  separate **$0.01 fee applies to each executed side**.
- Utah sports/proposition contracts are now RESTRICTED for every venue/route,
  based on the 2026 statute and Utah final judgment. All Utah non-sports
  categories remain UNKNOWN and therefore live-blocked.
- Focused fee, provenance-routing, shared-clearing, and Utah tests were added.
  Fresh endpoint smoke tests were UNAVAILABLE due a sandbox TLS EOF; no venue
  was falsely labelled live-tested.

Ranked conditional shortlist (five 0–5 criteria; unknowns score zero):

| Candidate | Score / 25 | Why it is conditional |
|---|---:|---|
| Polymarket US direct | 13.5 | strongest direct API candidate; Utah non-sports, account access, fee context, and live liquidity unverified |
| Gemini direct | 12.5 | strong API/sandbox documentation; same Utah, cohort-fee, and liquidity unknowns |
| IBKR ForecastEx + CME | 12.0 | two independent books through one account; local authenticated gateway/session and contract-overlap validation required |

**Recommendation:** select Polymarket US for Phase 2 account/eligibility and
live-read validation; use Gemini as the parallel fallback; select IBKR instead
if the owner prefers its existing gateway/account workflow. This is an explicit
**owner checkpoint**. No candidate adapter work is approved until the owner
selects one. Utah sports remains disabled regardless of selection.

---

### Phase 2 — Kalshi adapter refresh + streaming  ·  PARTIAL (code complete 2026-09-13; operational exit pending)

The Phase 1 owner checkpoint gated *second-venue* (Phase 3) adapter work;
the owner directed Phase 2 (Kalshi refresh) to proceed on 2026-09-13.

Implemented and test-covered (26 new tests — 22 in
`tests/test_kalshi_phase2.py` and 4 in `tests/test_paper_streaming.py`,
all offline — mock HTTP transport + scripted local WebSocket server):

- **Hosts + failover (D5).** `config/venues.yaml` now points at the
  recommended dedicated Trade API hosts
  (`https://external-api.kalshi.com/trade-api/v2`,
  `wss://external-api-ws.kalshi.com/trade-api/ws/v2`, plus the
  `external-api.*.kalshi.co` demo pair); the legacy `api.elections` /
  `demo-api` hosts remain configured as documented compatibility
  fallbacks. The adapter rotates to the next base on HTTP 5xx and
  transport failures (DNS/TLS/timeout) — the exact D5 failure modes —
  keeps 4xx sticky, and `connect()` probes all bases before starting.
  New hosts added to the official-host ledger in
  `prediction_arb/compliance/restrictions.py`.
- **`orderbook_fp` re-verified (K3).** Dollar-string prices, fractional
  quantities, YES-ask synthesis from NO bids, and the WebSocket
  `*_fp` key variants all parse through one hardened parser that drops
  (and logs) malformed / non-tradeable levels instead of guessing.
- **Streaming (K2/K4).** `subscribe_order_book` is implemented:
  authenticated WS handshake (RSA-PSS over the host-independent path
  `GET /trade-api/ws/v2`), `orderbook_delta` subscription, sequenced
  snapshot/delta application, **gap/out-of-order ⇒ recovery, never
  silent apply** (fresh snapshot via `update_subscription get_snapshot`,
  resubscribe fallback), reconnect with exponential backoff, terminal
  handling of auth (code 9) and subscription-terminal (10/25) errors.
  Paper mode now streams books into its live book cache as events
  (sub-second staleness) when credentials exist, with automatic
  per-venue fallback to REST polling; collect mode uses the
  authenticated **bulk** endpoint `GET /markets/orderbooks` (≤100
  tickers/request) when a key is configured, falling back to the public
  per-ticker endpoint otherwise. Everything degrades to the old
  unauthenticated polling path when no key is present.
- **Rate limits (K5).** Separate read/write buckets kept; on startup
  with credentials the adapter discovers the account tier
  (`GET /account/limits`) and non-default endpoint costs
  (`GET /account/endpoint_costs`) and clamps its local request budgets
  to `rate_limit_headroom` (default 80%) of the published budget —
  only ever lowering, never circumventing (§13). Without credentials the
  documented-conservative configured budgets (8/3 req/s, under the Basic
  tier) apply unchanged.

#### Completion record — verification artifacts (2026-09-13, second pass)

M2's exit criterion asked for two things: a clean collect run **and** "a
verification script the owner can run to confirm both hosts and the book
format." The script did not exist. It does now:

- **`scripts/kalshi_smoke.py` (new).** Read-only, GET-only, never
  constructs an adapter with order placement enabled. Public checks (no
  key): probes the recommended `external-api` host *and* the legacy
  `api.elections` fallback individually, confirms `exchange_active`,
  normalizes markets, and re-verifies the **book format** — fixed-point
  `orderbook_fp` envelope, `[dollar-string, quantity-string]` level pairs,
  both bid ladders present, YES asks synthesised as `1 − NO bid`, and the
  engine invariants the arbitrage core relies on (best-first ordering,
  prices inside (0,1), positive sizes, uncrossed book). With a key it
  additionally verifies tier discovery + budget clamping, endpoint costs,
  bulk `GET /markets/orderbooks` against the per-ticker book, and
  (with `--with-stream N`) the WebSocket with its sequencing counters.
  Writes sanitized evidence to `reports/evidence/kalshi_<UTC>.json`
  (git-ignored); exit 0 only when every hard check passes.
- **12 offline tests** (`tests/test_kalshi_smoke_script.py`) cover the
  script's decision functions and safety posture, since the network path
  cannot run here.
- **Run in this sandbox (2026-09-13):** it correctly probed both bases,
  recorded `TLS/SSL connection has been closed (EOF)` for each, wrote the
  evidence file, and exited `1`. That is the honest K9 result, not a
  pass — the same script on the owner's machine is what turns K9 into
  observed evidence.
- **D14 fixed** (found while wiring the script): `main.py` read
  `rate_limit_headroom` from the top level of `venues.yaml` instead of
  under its `venues:` key, so the operator's configured headroom was
  silently ignored and the hardcoded `0.8` always used. Harmless only
  while the two values matched. Fixed + 3 regression tests; verified by
  mutation (revert the fix → the test fails with `headroom=0.8`).

Still open (exit criterion — requires the owner's machine + key):

1. **H2:** Kalshi API key in `.env.paper` (needed to exercise WS, bulk
   books, and tier discovery against the real venue — and to verify the
   handshake signature against a live session).
2. **Run `python scripts/kalshi_smoke.py`** on a machine that can reach
   Kalshi (no key needed for the public checks; add `--with-stream 10`
   once H2 is done) and paste the evidence file into this section. This is
   the single step that converts K9 from UNAVAILABLE to observed.
3. A 24-hour `--mode collect` run with ≥99% successful cycles and no
   unhandled 5xx, plus a paper session confirming sub-second streaming
   staleness. Feed counters (`snapshots`, `deltas_applied`,
   `sequence_gaps`, `reconnects`) are exposed via
   `adapter.streaming_stats()` for that evidence.
4. K9 stands: no fresh live endpoint validation from this sandbox
   (TLS EOF); nothing here is labelled live-tested.

Exit: a 24-hour `--mode collect` run with ≥99% successful cycles and no
unhandled 5xx; streaming books in paper mode with sub-second staleness.

---

### Phase 3 — Second independent venue adapter  ·  IN PROGRESS

**Route selected by the owner on 2026-09-13: Polymarket US direct**
(Phase 1 rank 1, 13.5/25). The owner's stated goal is breadth — connect
to as many independent books as possible — and the agreed sequencing is
**one venue at a time**, because (a) only three of the eight researched
DCMs expose a usable public API at all (Polymarket US, Gemini, IBKR's
ForecastEx/CME routes; Railbird/DKeX, Ludlow/Novig, Rothera, and CDNA
have no verified public trading API, and reaching them would require
scraping, which §13 prohibits), and (b) pair count is not the binding
constraint — **D6** is. Until candidate generation can group differently
worded events across venues, an Nth venue adds integration surface but
still yields zero analyzed opportunities.

#### Completion record — adapter code (2026-09-13)

Implemented and test-covered (41 offline tests in
`tests/test_polymarket_us.py`: mock HTTP transport + a scripted local
WebSocket server). Evidence rows P1-P8 in `docs/VENUE_RESEARCH.md`.

- **Public market data needs no credentials (P1).** Discovery and order
  books come from `https://gateway.polymarket.us`, so paper/collect read
  this venue with an empty env file. The authenticated host
  (`https://api.polymarket.us`, Ed25519 signing per P2) is used only for
  the WebSocket and portfolio surfaces. This is a genuine structural
  difference from Kalshi, whose WS *and* bulk books both require a key.
- **True two-sided CLOB.** `bids` and `offers` are both real resting
  orders; nothing is synthesized. Contrast Kalshi (bid-only, YES ask =
  `1 − NO bid`) and ForecastEx (paired bid at $1.00).
- **Market state is enforced (P4).** `parse_order_book` returns `None`
  for any state other than `MARKET_STATE_OPEN`, so a halted, pre-open,
  or auction book can never be sized against by the arbitrage engine.
- **Per-market trading rules (P3).** `orderPriceMinTickSize` and
  `minimumTradeQty` are carried onto every `NormalizedContract`; the
  docs explicitly forbid inferring them from product type or slug.
  Missing values fall back to the *coarsest safe* grid (0.01 / 1), never
  a finer invented one.
- **Sports category preserved (§35).** Polymarket US carries heavy
  sports inventory, which is **RESTRICTED in Utah** (R1/R2). Any
  `sportsMarketType` / `gameId` signal maps to category `sports` so the
  eligibility gate fires.
- **Rate limits (P5).** Budgets are clamped to 80% of the published
  20 req/s ceiling and only ever lowered; `429` is surfaced for backoff,
  never swallowed (§13).
- **Streaming (P6).** `polymarket_us_ws.py` implements the markets
  WebSocket with reconnect/resubscribe, heartbeat accounting, and
  terminal auth handling. It contains **no sequence-gap machinery on
  purpose**: the venue pushes complete replacement books rather than
  deltas, so there is no incremental state to corrupt and no `seq` to
  check. Adding one would fabricate a guarantee the protocol does not
  offer.
- **Orders are not implemented.** `place_order` raises. Phase 3 requires
  books before orders, and the venue's fee schedule is still
  `INCOMPLETE` in `config/fees.yaml` — the cumulative aggressive-order
  cap plus banker's rounding need per-order fill state the
  `FeeCalculator` does not carry (P8).

**Status: API_DOCUMENTED_UNTESTED, and collect mode deliberately skips
the venue.** This sandbox cannot reach Polymarket US (P7, TLS EOF), so
nothing here is live-tested and the status is NOT upgraded from
documentation. To promote it, run on a machine that can reach the venue:

```bash
python scripts/polymarket_us_smoke.py          # read-only, no credentials
```

It performs public reads, checks provenance/tick/state handling, and
writes sanitized observed-payload evidence to
`reports/evidence/polymarket_us_<UTC>.json` (git-ignored). Paste that
into this section, then flip `get_api_status()` to `VERIFIED` — at which
point collect mode starts recording the venue and cross-venue matching
has real two-venue data for the first time.

#### Remaining Phase 3 gates

1. **Utah/account eligibility** for Polymarket US non-sports categories,
   resolved through an authorized workflow. Never geo-bypass. Sports
   stays RESTRICTED regardless of venue. *Re-checked 2026-09-13 (row R9):
   the venue's own trading-restrictions page says nothing about states, and
   secondary sources contradict each other about Utah — so this stays
   UNKNOWN and live-blocking. Only the venue's terms/your account
   dashboard or legal review can close it.* **OWNER.**
2. **Observed-payload evidence** via the smoke script below (gate 2).
   **OWNER** — this sandbox cannot reach the venue (P7).
3. ~~**Exact fee context**~~ **CLOSED 2026-09-13.** The cumulative
   aggressive-order cap and banker's rounding are now modelled exactly
   (P11): `rounding: half_even`, `order_fee_cap: cumulative_exact_rounded`,
   `trade_calculation_status: COMPLETE`, verified by reproducing all 198
   values of the venue's own published 100-lot fee table, plus the FAQ's
   two banker's-rounding examples and a multi-fill cap case. Volume rebates
   are recorded but deliberately never applied (weekly payout on prior-month
   volume, not a per-trade discount). Fees are charged **per fill** from the
   walked levels, which is what the cap is defined over (D13, fixed).
   **Settlement fees remain `INCOMPLETE — BLOCK`** (P12: no settlement fee
   is published anywhere reviewed, and documented-by-absence is not
   verification), so Polymarket US opportunities still fail closed until
   that is settled by the rulebook or an owner account statement.
4. **≥1 real Kalshi cross-listing** persisted as APPROVED or REVIEW,
   never forced. This is blocked behind **D6** (candidate generation)
   and **D9** (no LLM client), which is why Phase 5 should follow
   immediately rather than adding venue #3.

```bash
python scripts/polymarket_us_smoke.py          # read-only, no credentials
python scripts/kalshi_smoke.py                 # read-only, no credentials
```

---

#### Original route guidance (retained for reference)

Phase 1 ranked Polymarket US direct first, Gemini direct second, and IBKR
ForecastEx+CME third.
The rank is conditional because authenticated liquidity and Utah non-sports
account eligibility were not available for live verification.

Whichever route is selected must follow the same staged exit gates:

1. Resolve the owner's Utah/account/contract-category eligibility through an
   authorized workflow. Never geo-bypass.
2. Exercise official public/sandbox reads and save sanitized evidence of the
   observed payload/schema; do not upgrade API status from documentation alone.
3. Carry contract-qualified DCM, matcher, issuer, clearing, and settlement
   provenance through normalization. Generic frontend labels remain UNKNOWN.
4. Integrate the exact dynamic fee context and test rounding/order-state rules.
   A published base equation is insufficient.
5. Implement discovery and books before orders. Then use only paper/sandbox
   order drills behind existing mode and placement gates.
6. Identify at least one economically equivalent Kalshi cross-listing and
   produce a real certificate; never fabricate overlap or liquidity.

Route-specific cautions:

- **Polymarket US:** keep QCX/US identities and endpoints separate from the
  international platform; represent aggressive-order cumulative fee caps.
- **Gemini:** resolve listing-date fee cohorts and Titan/QC/Olympus aliases.
- **IBKR:** authenticate a local Client Portal/IB Gateway session; qualify every
  contract as `IBKR_KALSHI`, `IBKR_FORECAST_EX`, or `IBKR_CME`. For ForecastEx,
  synthesize the opposite ask at `1.00 − bid` and charge the separate $0.01 per
  executed side. Never call a user's localhost gateway directly from browser
  code; use an app/backend proxy.

Exit: paper mode fetches real books from the selected route; API status reflects
actual testing; the route is demonstrably INDEPENDENT of Kalshi for the exact
contract; fee and eligibility gates are complete; and at least one real overlap
is persisted as APPROVED or REVIEW (never forced to APPROVED).

---

### Phase 4 — Data lineage, replay, backtest metrics  ·  BROKEN

- D3: recorded data lands in the paper DB; historical mode opens an
  empty DB; `Repository._assert_mode_tag` would refuse cross-mode reads
  anyway. Decide the recorded-data store explicitly (e.g. collect
  writes `data/recorded/…` readable by historical mode) and update
  `prediction_arb/core/config.py:_EXPECTED_DB` + `_assert_mode_tag` together.
- D4: persist the **full** `NormalizedContract` (settlement time,
  cutoff, resolution source/method, threshold + operator + units,
  early-resolution rules, payout, tick size, contract version,
  underlying exchange, clearing entity) plus the raw market payload.
  Add migration `002` via `prediction_arb/database/migrations/runner.py` (forward-only)
  and reconstruct faithfully in
  `prediction_arb/modes/historical/data_provider.py::_reconstruct_contract`.
- Implement `prediction_arb/backtesting/engine.py::run_backtest` metrics: opportunity
  duration, fill probability, partial-fill rate, drawdown, capital
  utilisation, fee drag. No fabricated history, no live fallback.

Exit: `--mode historical --date <collected date>` replays real frames
and produces a report with non-zero frames and honest zero-or-more
opportunities, reproducible across runs.

#### Phase 4 readiness — verified 2026-09-13, nothing upstream blocks it

Both defects were re-derived from the current code rather than taken on
trust, so Phase 4 can start immediately:

| Defect | Evidence re-checked 2026-09-13 |
|---|---|
| D3 | `core/config.py:_EXPECTED_DB[Mode.COLLECT] = "prediction_arb_paper"` and `AppConfig.database_path` maps collect into `data/paper/`, while historical opens `data/historical/prediction_arb_historical.sqlite3`. `Repository._assert_mode_tag` permits only `collect ↔ paper` — a historical-mode open of the recorded store raises. Recorded data is therefore invisible to replay. |
| D4 | `database/models.py:MarketRow` has exactly 11 columns (`mode, market_id, venue, underlying_exchange, contract_id, description, outcome_type, category, status, created_at, updated_at`) — no settlement, cutoff, resolution source/method, threshold/operator/units, early-resolution, payout, tick size, contract version, or raw payload. `modes/historical/data_provider.py:_reconstruct_contract` consequently sets `settlement_time=None`, `resolution_source=""`, `threshold=None`, `contract_version="historical"`. |
| — | `database/migrations/runner.py:MIGRATIONS` still holds only `(1, "create initial schema", _m001_create_all)`, so migration `002` has a clean, forward-only slot. |

Decisions already made for Phase 4 (so the work starts at code, not design):

1. **Recorded store:** collect keeps writing its per-mode DB, and
   historical mode gains read access to that *recorded* store rather than
   collect being repointed — `_EXPECTED_DB` and `_assert_mode_tag` must
   change together, and the mode column stays on every row.
2. **Migration `002`:** add the full `NormalizedContract` columns plus a
   raw-payload column, forward-only through the existing runner; existing
   rows stay readable with NULLs, never back-filled with invented values.
3. **No fabrication:** `_reconstruct_contract` may only return what was
   recorded; a field that was not captured stays empty and the
   deterministic matcher keeps rejecting on it.

Phase 4's *exit* criterion still needs real recorded data, i.e. the
owner's collect run (Phase 2 step 2/3 above). The code work does not.

---

### Phase 5 — Matching that can actually match  ·  BROKEN

- D6: `prediction_arb/normalization/contracts.py::normalized_event_key` groups by
  *exact canonical title + settlement date*. Two venues phrase the same
  event differently, so cross-venue pairs will essentially never land in
  the same group — the matcher never even sees them. Replace with real
  candidate generation (blocking): settlement window ± tolerance,
  category/product code, threshold + operator + units, numeric tokens
  extracted from titles, then fuzzy/embedding similarity, then the LLM.
- D9: no LLM client is configured, so every ambiguous pair ends in
  REVIEW and `search_event` (which requires APPROVED certificates)
  evaluates nothing. Wire a real client in `main.py` per
  `docs/API_SETUP.md`, keep schema validation and
  "malformed ⇒ no match", and add caching + prompt/model versioning.
- Keep Principle B intact: deterministic REJECT is final; the LLM can
  never authorise a trade.

Exit: on real two-venue data the pipeline emits candidate pairs,
certificates with statuses, and a measurable false-match rate reviewed
by a human.

---

### Phase 6 — Paper validation & realism  ·  PARTIAL

- D11: `OrderBookAggregator` is only referenced by tests; the paper
  engine keeps its own dict and applies **no** freshness gate, while
  `settings.yaml` declares `maximum_book_age_ms: 1000`. Enforce
  staleness in paper (reject or degrade), and use the aggregator.
- D12: `simulated_fill_probability_at_touch` (0.9 in `paper.yaml`) is
  stored but never used — paper fills are always 100% at touch, which
  flatters the strategy. Implement the fill model or delete the config
  key and say so.
- Add queue-position/partial-fill realism, latency per venue, and
  opportunity-lifetime bookkeeping (`§68`).

Exit: a multi-week paper run whose fill assumptions are documented and
defensible, with per-cycle metrics persisted.

---

### Phase 7 — Compliance & legal re-verification  ·  PARTIAL

Phase 1 captured the 2026-09-13 Utah/CFTC snapshot: Utah's proposition-bet
statute and final district judgment support a wildcard RESTRICTED sports
record; Third/Ninth Circuit decisions show a live split; and the CFTC's June
publication is still an NPRM. Non-sports eligibility remains UNKNOWN across
venues and therefore live-blocked.

Before any live step, obtain owner/legal review and re-verify the chosen route's
account/category eligibility, automation terms, statutes, litigation, effective
CFTC rules, and exact fee context. Negative search results are not permanent
legal conclusions. Nothing here is legal advice.

---

### Phase 8 — Live readiness & first live path  ·  NOT STARTED (HUMAN)

Sequence: owner-selected venue **paper/sandbox** → Kalshi **demo**
(`external-api.demo.kalshi.co`) → only then a separately approved live path
with hard caps. Rehearse: kill
switch, crash recovery reconciliation, one-leg-filled recovery,
firewall rejection, fee-freshness refusal. `main.py --readiness`
currently hardcodes "two live-capable independent venues = False"; make
it compute from adapter status + provenance instead. Live always
requires typing `I UNDERSTAND LIVE TRADING`; no flag may bypass it.

---

### Phase 9 — v2 strategy surface  ·  NOT STARTED

Behind the existing stable stubs, and only after Phase 6 evidence:
`prediction_arb/arbitrage/multi_leg.py`, `prediction_arb/arbitrage/logical.py` (TYPE B, research-only
with a test battery first), `prediction_arb/arbitrage/graph.py` cycle search, and
fee/route optimisation across SAME_VENUE frontends (the one legitimate
future use for the Webull/Moomoo stubs).

### Phase 10 — Operations  ·  NOT STARTED

Unattended collection (systemd/launchd/Docker), alert sinks
(`prediction_arb/monitoring/alerts.py` email/webhook stubs), the web dashboard stub,
DB growth/retention (the paper DB is already 17 MB after one day of
collection), and log rotation.

---

## 4. Defect register

| ID | Defect | Evidence | Phase |
|---|---|---|---|
| D1 | ~~Package unimportable as committed~~ **FIXED 2026-09-13**: package moved to `prediction_arb/`, `pyproject.toml` added, three working entry points | Was: `import prediction_arb` → ModuleNotFoundError on a fresh clone. Now: `pip install -e ".[dev]"` → 118 tests pass | 0 |
| D2 | ~~No `.gitignore`; artifacts tracked~~ **FIXED 2026-09-13**: `.gitignore` + `.gitattributes` added, 60 `.pyc` + both DBs + `logs/` + `reports/` untracked (files kept on disk), `reconnect.py` → `scripts/ibkr_gateway_session.py` with safe TLS handling. *Caveat: the 17 MB blob remains in git **history**; purging it needs a history rewrite (owner's call)* | `git ls-files \| grep -c pycache` → 0; CI step guards against regressions | 0 |
| D3 | Collect data is invisible to historical replay | collect → `data/paper/…` (tagged `mode=collect`); historical → `data/historical/…` (0 rows); report shows `frames: 0` | 4 |
| D4 | Recorded markets lose the metadata matching needs | `MarketRow` has no settlement/rules/threshold/payout/version columns; `_reconstruct_contract` sets `settlement_time=None`, `resolution_source=""` → `deterministic_match` rejects on both | 4 |
| D5 | ~~Kalshi on legacy host, intermittent failures, no failover~~ **FIXED IN CODE 2026-09-13**: recommended `external-api` hosts configured, legacy hosts kept as fallbacks, 5xx/transport failover + connect-time probing, bulk books, WS streaming, tier-aware budget clamping (26 tests). *Operational verification (24h run on the owner's machine) still pending — see Phase 2 section* | Was: `config/venues.yaml` → `api.elections.kalshi.com`; collect log: 18 DNS failures + 14 HTTP 500s | 2 |
| D6 | Cross-venue candidate generation cannot produce candidates | `normalized_event_key` = exact canonical title + settlement date | 5 |
| D7 | ~~Venue research stale~~ **FIXED 2026-09-13**: evidence ledger now covers direct DCMs, aliases, multi-route frontends, fees, Utah/CFTC context, and ranked candidates; old “only one independent pair” premise removed | `docs/VENUE_RESEARCH.md`; provenance/fee/compliance registries; focused tests | 1 |
| D8 | ~~Spec document missing~~ **FIXED 2026-09-13**: `docs/SPEC.md` reconstructs all 73 cited sections from code/docs/tests/config evidence, with `[I]` flags on the two thin ones (§33, §68) and an appendix of the 22 numbers that have no surviving citation | Was: 73 distinct `§` sections cited at 224 sites with no spec file. Now: `grep -c "^### §" docs/SPEC.md` → 73 | 0 |
| D9 | No LLM configured ⇒ no APPROVED certificates ⇒ nothing is ever evaluated | `main.py`: `LLMMatcher(client=None)`; `optimizer.search_event` skips pairs without approval | 5 |
| D10 | Live mode is monitor-only; readiness check hardcodes the venue count | `prediction_arb/modes/live/engine.py::run`; `main.py::cmd_readiness` (`False` literal) | 8 |
| D11 | No book-freshness enforcement in paper; `OrderBookAggregator` is dead code | aggregator referenced only in tests; `maximum_book_age_ms` enforced solely by the live firewall | 6 |
| D12 | Configured fill probability is ignored | `fill_probability_at_touch` stored in `prediction_arb/execution/simulated.py` and never read | 6 |
| D13 | ~~Trade fees priced at a walk's weighted-average price instead of per fill~~ **FIXED 2026-09-13**: `FeeCalculator.trade_fee_fills()` charges each walked level, applies the venue's documented rounding, and honours an order-total cap; `two_leg.py` and `execution/simulated.py` now pass the real `levels_consumed`. The old shortcut is not what any venue bills: it overstates a concave taker charge and would understate a maker credit, and it ignores the cumulative cap entirely | Was: `two_leg.py:139` → `trade_fee(venue, yes_walk.weighted_average_price, qty)`. Now: per-fill; `tests/test_polymarket_fees.py::TestPerFillAccuracy` proves the engine charges walked levels and that the two numbers differ | 3 |
| D14 | ~~Configured rate-limit headroom silently ignored~~ **FIXED 2026-09-13**: `main.py` read `rate_limit_headroom` from the top level of `venues.yaml` instead of under its `venues:` key, so the hardcoded `0.8` default always won. Harmless only while config and default matched; an operator tightening headroom would have had no effect | Was: `config.venues.get("kalshi", {})` → `{}`. Now unwrapped like `build_adapters`; `tests/test_units.py::TestRateLimitCalibration` (verified by mutation: reverting the fix fails the test with `headroom=0.8`) | 2 |

---

## 5. Invariants that survive every phase

1. No real order can leave a non-live mode. Simulation engines hold no
   adapter reference; `KalshiAdapter.place_order` refuses unless
   constructed with `allow_order_placement=True`, which `main.py` sets
   only in live mode; the firewall re-verifies every order.
2. UNKNOWN never means yes — not for independence, eligibility, fees,
   settlement lockup, or book freshness.
3. Never fabricate data. Stubs raise `NotImplementedError` with exact
   instructions; historical mode never falls back to live data.
4. Every external claim carries a status (VERIFIED / UNVERIFIED /
   UNAVAILABLE / UNKNOWN), a source, and a verification date.
5. Deterministic filters are final; an LLM may narrow, never widen.
6. Mode isolation is architectural, not a UI flag; per-mode DBs, logs,
   reports, env files, and a `mode` column on every row.
7. No scraping, no geo-bypass, no rate-limit circumvention, no ToS
   violations (`prediction_arb/compliance/restrictions.py`).
8. Credentials are never logged and never committed.

---

## 6. Human action checklist (beginner-friendly)

Do these in a terminal. Copy each block, press Enter, and read what it
prints. If a step fails, stop and paste the exact output back.

**H1 — Set up the project (one time)**
```bash
cd <wherever you cloned the repo>
python3 -m venv .venv                      # create a private Python environment
source .venv/bin/activate                  # macOS/Linux  (Windows: .venv\Scripts\activate)
pip install -e ".[dev]"                    # install the package + pytest
pytest                                     # should finish with all tests passing
python -m prediction_arb.main --status     # venue table; needs no network
```
If `pytest` fails with `ModuleNotFoundError: No module named
'prediction_arb'`, re-run `pip install -e ".[dev]"` from the repository root
(the directory that contains `pyproject.toml`).

**H2 — Kalshi API key (needed for WebSocket streaming and the demo env)**
1. Sign in at kalshi.com → account settings → API → create a key.
2. You get a **Key ID** and a downloadable **RSA private key** (shown once).
3. Put them in `.env.paper` at the repo root (never in `.env.live`
   unless you intend live trading; live-marked keys are refused outside
   live mode):
   ```
   KALSHI_API_KEY_ID=your-key-id
   KALSHI_PRIVATE_KEY_PEM="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"
   ```
4. `.env.*` is git-ignored; `cp .env.example .env.paper` gives you a
   commented template. Never commit or paste a real key anywhere.

**H3 — IBKR Client Portal Gateway (needed for Route A of Phase 3)**
1. In IBKR Client Portal: Settings → Trading Permissions → enable
   **Forecast Contracts / Event Contracts** (may require acknowledging
   risk disclosures; eligibility is 21+ for IB LLC). Request a **paper
   account** if you do not have one.
2. Download the Client Portal Gateway from Interactive Brokers, unzip
   it, and start it:
   ```bash
   cd <gateway folder>
   bin/run.sh root/conf.yaml                # macOS/Linux  (Windows: bin\run.bat)
   ```
3. A browser page opens at `https://localhost:5000`. Log in with your
   IBKR credentials and complete the 2FA prompt. Keep the terminal open —
   the gateway must stay running.
4. Verify the session from a second terminal:
   ```bash
   curl -k --max-time 10 https://localhost:5000/v1/api/iserver/auth/status
   ```
   `"authenticated": true` means you are connected. (`-k` skips the
   self-signed-certificate check for this local test only.)
5. Sessions expire. Re-run the authentication flow when they do;
   `scripts/ibkr_gateway_session.py` provides a guarded local session check.

**H4 — ~~Choose the Phase 2/3 route~~ DONE 2026-09-13: Polymarket US**
The owner selected the Polymarket US direct route. Remaining owner-side
steps for that route:
1. Verify the selected product and non-sports categories are available to
   the owner's Utah account. Do not bypass geographic or product
   restrictions. Utah **sports** stays RESTRICTED at every venue (R1/R2).
2. Run the read-only evidence capture from a machine that can reach the
   venue — **no credentials or account needed**, it uses the public
   gateway:
   ```bash
   python scripts/polymarket_us_smoke.py
   ```
   Exit code 0 = all checks passed. It writes sanitized observed-payload
   evidence to `reports/evidence/polymarket_us_<UTC>.json` (git-ignored).
3. Paste that evidence into the Phase 3 completion record, then the
   adapter's `get_api_status()` can be promoted from
   `API_DOCUMENTED_UNTESTED` to `VERIFIED` — which is what makes collect
   mode start recording the venue.
4. *(Optional, streaming only.)* Create an API key at
   polymarket.us/developer and put `POLYMARKET_KEY_ID` /
   `POLYMARKET_SECRET_KEY` in `.env.paper`. Requires the iOS app plus
   identity verification. Books work without it. Keep credentials local;
   never paste them into chat or commit them.

**H5 — Decide the data policy**
Recorded market data is already 17 MB for one day of collection. Decide
how long to retain it and whether any of it belongs in git (recommended:
none; keep it local or in external storage).

**H6 — Say the word before anything live**
Nothing in Phases 0–7 places a real order. Live work starts only in
Phase 8, only after paper/demo drills, and only with your typed
confirmation phrase.
