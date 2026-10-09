# ROADMAP & PHASE MAP

Compiled 2026-09-13 (America/Denver) from a full read of the repository,
a live run of the test suite and CLI, inspection of both SQLite
databases and the JSONL logs, and a re-check of venue/API sources.

This is the reference document for anyone (human or agent) continuing
the project. It describes **phases**, the **verified current state**,
the **defect register**, and the **actions only a human can perform**.

> **Fresh agent session?** Read `docs/AGENT_HANDOFF.md` first — it is the
> permanent entry point (current baseline, verified test results, environment
> limits, critical path, and what not to start). This roadmap stays the
> register of record for phases and defects.

Status tags used throughout:

| Tag | Meaning |
|---|---|
| DONE | Implemented and verified |
| PARTIAL | Implemented but incomplete or unverified against a real venue |
| BROKEN | Implemented but demonstrably does not work as documented |
| NOT STARTED | Stub or absent |
| HUMAN | Blocked on an action only the account owner can take |

---

## 1. Current state snapshot (verified locally 2026-09-13; re-verified 2026-10-09 where the row says so)

| Check | Result |
|---|---|
| `pytest` | **313 passed** (~25s) — 2026-10-09 on Python 3.11.2, after fixing **D16** (an expired absolute date in `tests/conftest.py` had made `TestSettlementTiming::test_lockup_and_cost` fail; the fix makes the fixture settlement relative, strengthens the assertion and adds two deterministic settlement-timing tests, 311 → 313). Re-verified under a frozen future clock at 2026-10-14, 2027-10-09 and 2031-01-02: **313 passed at every date**, so no date-dependent test remains. Earlier composition (2026-09-14, 311 tests): the 264 from 2026-09-13 (Phase 0's 107 + 11 Phase 1 + 26 Phase 2 + 41 Phase 3 + 24 exact-fee-model + 12 smoke-script/D14 + 3 per-fill accuracy + 25 Phase 4 + 14 Phase 6) + **24 candidate-generation tests** (`tests/test_candidate_generation.py`: blocking families, grouping, D15 provenance gate, end-to-end candidate→certificate→opportunity — mutation-verified) + **20 LLM-client tests** (`tests/test_llm_client.py`: config paths, transport, budget — mutation-verified). All offline: mock HTTP transports and a scripted local WebSocket server |
| `python -m prediction_arb.main --status` | Works (re-verified 2026-10-09, exit 0): kalshi=VERIFIED, polymarket_us=API_DOCUMENTED_UNTESTED, forecast_ex=API_DOCUMENTED_UNTESTED; independence matrix correct (kalshi↔polymarket_us = INDEPENDENT) |
| `python -m prediction_arb.main --readiness` | Re-verified 2026-10-09: 0 snapshots recorded in a fresh clone; **0 opportunities ever analyzed**; live NOT RECOMMENDED. (The 13,801-snapshot figure below was from the previous engineer's working copy — the DB is git-ignored, so a fresh clone starts empty.) |
| Live-capable venues | **1** (Kalshi). Polymarket US is implemented and INDEPENDENT, but stays API_DOCUMENTED_UNTESTED until the owner captures observed-payload evidence (P7) |
| `data/paper/…sqlite3` | Not present in a fresh clone (git-ignored). Previously 21,892 markets / 13,801 book snapshots spanning 2026-08-30 → 2026-09-05, tagged `mode=collect` |
| `data/historical/…sqlite3` | **Empty** (0 rows in every table) |
| `reports/historical/…json` | `{"frames": 0, "opportunities": 0}` |
| `logs/collect/collect_20260831.jsonl` | Not present in a fresh clone. Previously 32 error rows: 18× DNS failure, 14× Kalshi HTTP 500 (2026-08-31 → 2026-09-04) |
| CI | Has run and was green through 2026-09-15: latest run `34917308199` (`gh run list`, checked 2026-10-09) `success`, as were all runs before it including `34804088486` on `main`. **Caveat: no CI run has happened since the suite went red (D16)** — the green status predates the regression and does not describe today's suite |
| Outbound network from this sandbox | Venues unreachable — re-confirmed 2026-10-09 by running both smoke scripts (`scripts/kalshi_smoke.py`, `scripts/polymarket_us_smoke.py`), each exiting `1` with `TLS/SSL connection has been closed (EOF)` on `external-api.kalshi.com`, `api.elections.kalshi.com` and `gateway.polymarket.us`. Rows K9 and P7 therefore still stand; venue calls must be verified on the owner's machine. **Documentation hosts ARE reachable through the research tooling**, which is how rows P8–P12 and R9 were re-verified |

**Bottom line:** the safety architecture and Phase 1 fail-closed research
registries are tested. The opportunity pipeline still has never seen real
two-venue data (neither venue is reachable from the agent sandbox), but the
in-code blockers are now closed: D6 candidate generation produces
cross-venue candidates from differently-phrased titles, D9 wires a real
LLM matcher from the env file (no LLM ⇒ REVIEW, never auto-approve), and
D15 keeps same-venue pairs out of APPROVED. What remains for the first
real opportunity is owner-gated: venue reachability evidence (smoke
scripts), the Kalshi key (H2) for collected data, and an LLM key (H7)
for approval semantics.

**Added 2026-10-09:** the suite briefly went red when a date-dependent fixture
expired (**D16**, no production code implicated); it is **fixed and green
again at 313 tests**, and now survives a frozen clock years into the future.
With that closed, every remaining critical-path item is owner-gated — see
`docs/AGENT_HANDOFF.md` §8 and §9.

---

## 2. Phase map

| Phase | Name | Status | Primary exit criterion |
|---|---|---|---|
| 0 | Repo hygiene, packaging, runnability, spec reconstruction | **DONE 2026-09-13** | Fresh clone → `pip install -e ".[dev]"` → full suite passes → CLI runs, no manual hacks |
| 1 | Venue & API research refresh (2026) | **DONE 2026-09-13 — OWNER CHECKPOINT** | Owner selects Polymarket US, Gemini, or IBKR route before adapter work |
| 2 | Kalshi adapter refresh + streaming | PARTIAL — code complete + verification script delivered 2026-09-13 (D5, D14 fixed); **owner run pending** | 24h collect run with ≥99% cycle success; WebSocket streaming in paper |
| 3 | **Second independent venue adapter** | PARTIAL — adapter + exact fee model complete 2026-09-13 (gate 3 CLOSED); **gates 1, 2, 4 are owner-gated** | Real books from the owner-selected venue proven INDEPENDENT of Kalshi, in paper mode |
| 4 | Data lineage, replay, backtest metrics | CODE COMPLETE 2026-09-13 (D3, D4 fixed); **exit needs owner's real collected data** | `--mode historical --date …` replays recorded data and reports metrics |
| 5 | Matching that can actually match | CODE COMPLETE 2026-09-14 (D6 blocking §96, D9 LLM wiring §97, D15 provenance gate); **exit needs owner's real two-venue data + LLM key (H7)** | APPROVED certificates produced from real cross-venue data |
| 6 | Paper validation & realism | PARTIAL — D11, D12 fixed 2026-09-13; **owner's multi-week run pending** | Multi-week paper run with realistic fill/staleness modelling |
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
  both bid ladders present, YES asks synthesised as `1 − NO bid`, and Read-only, GET-only, never
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

### Phase 4 — Data lineage, replay, backtest metrics  ·  CODE COMPLETE 2026-09-13 (exit needs owner data)

- ~~D3: recorded data lands in the paper DB; historical mode opens an
  empty DB; `Repository._assert_mode_tag` would refuse cross-mode reads
  anyway.~~ **FIXED 2026-09-13.** `Repository` gained a `read_only`
  handle and `_assert_mode_tag` a narrow allowance (historical +
  read-only + a `collect`/`paper` tag). `AppConfig.recorded_store_path`
  is derived from the same constants collect writes with, so the two
  cannot drift. Historical mode now reads the recorded store read-only
  and writes only its own database.
- ~~D4: persist the **full** `NormalizedContract` … plus the raw market
  payload.~~ **FIXED 2026-09-13.** `MarketRow` gained 26 nullable
  columns (`SCHEMA_VERSION` 1 → 2), migration `002` is forward-only and
  idempotent, and `database/contract_codec.py` owns both directions so a
  field written at collect time cannot be dropped at replay time.
- ~~Implement `backtesting/engine.py::run_backtest` metrics.~~ **DONE
  2026-09-13** in `backtesting/metrics.py`: opportunity duration, fill
  probability, partial-fill rate, drawdown, capital utilisation, fee
  drag. No fabricated history, no live fallback.

Exit: `--mode historical --date <collected date>` replays real frames
and produces a report with non-zero frames and honest zero-or-more
opportunities, reproducible across runs. **Still needs the owner's real
collected data** — the sandbox cannot reach either venue, so no genuine
frames exist to replay. The code path is exercised end to end in
`tests/test_phase4_recording.py` instead.

#### Phase 4 — what was built, and the honesty rules it holds to

**D3 — recorded store is now readable by replay.** Historical mode keeps
its own writable database for replay output and opens the collect/paper
store separately, read-only:

- `Repository(db_path, mode, read_only=True)` opens via
  `sqlite3.connect("file:…?mode=ro", uri=True)`. Writes are refused by
  SQLite itself, not by a convention the code could bypass. Migrations
  are skipped and the mode tag is never written.
- It refuses a missing file rather than creating an empty database
  (`FileNotFoundError`), and still refuses a `live`-tagged store.
- The allowance is deliberately narrow: `read_only=True` **and**
  `mode == HISTORICAL` **and** tag in `{collect, paper}`. Anything else
  still raises "Refusing to mix mode data (§49)".

**D4 — recording is now faithful, and gaps stay gaps.**

- `record_contract()` persists all 37 `NormalizedContract` fields plus
  the raw venue payload. Verified: a rich contract round-trips with
  **zero** field differences.
- Decimals are stored as text (matching the existing convention),
  datetimes as `DateTime(timezone=True)`, microstructure / order-type /
  raw payload as JSON text.
- Migration `002` adds each column only if absent, so a fresh database
  (which already gets the columns from `create_all`) does not fail with
  "duplicate column name". A genuine v1 database is upgraded in place
  with its existing rows preserved and the new columns NULL.
- **Decode never invents.** `decode_contract` returns `None` when a row
  lacks the fields a contract needs. `HistoricalDataProvider` counts and
  reports those rows (`under_recorded`) and the replay says how many were
  excluded. The previous behaviour — substituting `payout=1.00`,
  `tick_size=0.01`, `min_order_size=1`, `contract_version="historical"` —
  was fabrication, and it is gone.

**Backtest metrics — measured vs modelled stays separated.**

- *Measured* from the recording: opportunity duration, fill statistics,
  fee drag, peak capital required.
- *Modelled*, and labelled as such in every result: the equity curve and
  drawdown assume every executable opportunity filled in full at the
  recorded prices. `BacktestResult.assumptions` carries that in plain
  text so a report can never be read as a forecast.
- Opportunity duration keys on the **sorted leg pair**, not
  `opportunity_id`: each `evaluate_two_leg` call mints a fresh `uuid4`,
  so the id can never express persistence across frames. With the leg
  pair, `first_seen == last_seen` no longer holds by construction.
- Fill statistics come from recorded book depth. When no walk was
  recorded they are `None` — never an assumed 100% (that assumption is
  defect D12, and it is not repeated here).
- `run_backtest` refuses to run without a `FeeCalculator`: cost is what
  separates a real trade from a fantasy one.
- **The battery is reachable from the CLI.** `--mode historical` runs
  `summarize_frames` over the opportunities that replay just produced —
  it does not re-scan, so the numbers describe exactly the run that was
  persisted — and the full battery lands in
  `reports/historical/historical_replay_*.json` under `metrics`. This
  was a real gap in the first cut: the battery existed but only
  `BacktestEngine` could reach it, so the exit criterion ("replays
  recorded data **and reports metrics**") was not met by the command the
  criterion names. Unavailable metrics print as `n/a`, never as a bare
  `None` (reads like a bug) or `0` (reads like a measurement of nothing).

**One thing this phase deliberately did not do:** `HistoricalExecutionEngine`
is still unwired — replay counts and measures opportunities, it does not
simulate executions against them. That belongs to Phase 6 (fill realism,
D12), not here, and wiring it now would have implied execution evidence
that does not exist.

#### Phase 4 — verification record (2026-09-13)

The three decisions recorded before the work started were all followed,
and each is now pinned by a test in `tests/test_phase4_recording.py`
(25 tests, all passing):

| Decision | Where it landed | Test that pins it |
|---|---|---|
| Historical reads the recorded store; collect is not repointed | `Repository(read_only=True)` + narrow `_assert_mode_tag` allowance + `AppConfig.recorded_store_path` | `test_historical_reads_collect_store_read_only`, `test_read_only_still_refuses_a_live_tagged_store`, `test_config_recorded_store_matches_where_collect_writes` |
| Migration `002` forward-only; old rows keep NULLs | `migrations/runner.py:_m002_market_contract_fields`, guarded by `PRAGMA table_info` | `test_migration_002_is_registered_and_idempotent`, `test_migration_002_upgrades_a_v1_database` |
| No fabrication in reconstruction | `contract_codec.decode_contract` returns `None` when under-recorded | `test_decode_never_invents_for_under_recorded_rows`, `test_provider_counts_unreconstructable_instead_of_dropping` |

Also verified rather than assumed:

- **Round trip is lossless.** `test_record_contract_round_trips_every_field`
  walks all 37 `NormalizedContract` fields and asserts the difference
  list is empty. A silently dropped field is exactly how replay starts
  inventing, so this is checked field by field, not spot-checked.
- **Read-only is enforced by SQLite, not by convention.** The test
  attempts a write through the read-only handle and asserts it raises.
- **A missing recorded store is never created.**
  `test_read_only_never_creates_a_missing_store` asserts both the
  `FileNotFoundError` and that the file still does not exist afterwards.
- **The metric battery is not permanently empty.** One test pins the
  honest zero (no LLM ⇒ pair is REVIEW ⇒ nothing approved, even with a
  profitable-looking spread); a second approves the pair and asserts the
  metrics actually populate. Without both, a green metric test could pass
  on an unwired pipeline.

**What still blocks the phase exit:** real recorded data. Neither venue
is reachable from this environment, so no genuine frames exist to replay
and the exit criterion cannot be met here. The owner's 24h collect run
(Phase 2) produces it; `--mode historical --date <that date>` is then the
acceptance run.

---

### Phase 5 — Matching that can actually match  ·  CODE COMPLETE 2026-09-14 (D6, D9, D15 fixed; exit needs owner's real two-venue data + LLM key)

- ~~D6: `prediction_arb/normalization/contracts.py::normalized_event_key` groups by
  *exact canonical title + settlement date*. Two venues phrase the same
  event differently, so cross-venue pairs will essentially never land in
  the same group — the matcher never even sees them.~~ **FIXED
  2026-09-14.** Grouping is now real blocking (§96):
  `blocking_keys()` emits per-contract keys from five deterministic
  families — 24h UTC settlement buckets (own bucket ±1, so a 23:00 vs
  01:00-next-day timestamp disagreement still groups), category/product
  code, threshold + operator + units (number-canonicalized so `10000.00`
  and `1E+4` block together), canonical numeric tokens extracted from
  titles (`$100k` and `$100,000` block together), and plural-folded,
  stopword-filtered content words. `group_by_event` returns the connected
  components of the shared-key graph (union-find, stable component ids).
  Blocking is candidate generation only and deliberately recall-oriented:
  the deterministic matcher (§18, REJECT final) still decides everything;
  blocking can only widen what it sees, never approve. 24 tests in
  `tests/test_candidate_generation.py`, **mutation-verified** (reverting
  `group_by_event` to exact-text grouping fails 6 of them, including the
  end-to-end one). Fuzzy/embedding similarity is deliberately NOT a
  separate stage: content-word blocking is the deterministic fuzzy net
  here, and no embedding provider has been verified — the semantic stage
  is the (schema-validated) LLM, per §89.
- ~~D9: no LLM client is configured, so every ambiguous pair ends in
  REVIEW and `search_event` (which requires APPROVED certificates)
  evaluates nothing.~~ **FIXED IN CODE 2026-09-14.** `main.py` now builds
  the matcher from the mode env file via `matching/llm_client.py` (§97):
  any OpenAI-compatible `/chat/completions` endpoint
  (`LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL`, optional
  `LLM_TIMEOUT_SECONDS`; docs/API_SETUP.md). The client is transport
  only — schema validation (§88), "malformed ⇒ no match", outage ⇒
  no-match, in-memory caching (§87) and prompt/model versioning on
  certificates (§20) are unchanged, and no client is still the
  conservative default (no LLM ⇒ REVIEW, never auto-APPROVED). Partial
  configuration refuses startup (a half-configured matcher must never be
  mistaken for a working one). Added a fail-closed per-cycle comparison
  budget (`llm.max_comparisons_per_cycle`, default 200): pairs beyond it
  stay REVIEW and are deliberately not cached; cache hits never consume
  budget; `run_matching` resets the window each cycle. 20 tests in
  `tests/test_llm_client.py`, **mutation-verified** (removing the
  `begin_cycle()` reset fails the budget-cycle test; silently ignoring
  partial config fails the refusal tests). The key is never logged
  (verified: no key material in any artifact after live wiring checks).
- ~~**D15**: `validate_match` could stamp a SAME_VENUE pair APPROVED~~
  **FOUND + FIXED 2026-09-14** — see the defect register: only a
  proven-INDEPENDENT relationship is APPROVED; everything else is REVIEW
  (§33).
- Keep Principle B intact: deterministic REJECT is final; the LLM can
  never authorise a trade.

Exit: on real two-venue data the pipeline emits candidate pairs,
certificates with statuses, and a measurable false-match rate reviewed
by a human. The pipeline side is exercised end-to-end offline
(`tests/test_candidate_generation.py::TestPipelineEmitsCandidates`:
differently-phrased cross-venue pair → candidate → deterministic pass →
APPROVED certificate → evaluated opportunity). **Still owner-gated:**
(a) an LLM key in `.env.paper` (H7) so matching can APPROVE rather than
REVIEW; (b) real two-venue data — the Polymarket US smoke run + Kalshi
key (H2) and a collect run, exactly the gates Phases 2/3 already list.
Certificates persist per cycle in paper mode
(`save_match_certificate`), which is what makes the false-match rate
human-reviewable.

---

### Phase 6 — Paper validation & realism  ·  PARTIAL (D11, D12 fixed 2026-09-13)

- ~~D11: `OrderBookAggregator` is only referenced by tests; the paper
  engine keeps its own dict and applies **no** freshness gate, while
  `settings.yaml` declares `maximum_book_age_ms: 1000`.~~ **FIXED
  2026-09-13.** The paper engine now holds an `OrderBookAggregator`; both
  the scan and the simulated execution engine read through it. The limit
  is the stricter of `execution.max_book_age_ms` and
  `arbitrage.maximum_book_age_ms`, so a book stale enough to fail either
  check is neither scanned nor filled against. Excluded books are counted
  and printed, never silently dropped — a scan that quietly sees nothing
  is indistinguishable from a quiet market.
- ~~D12: `simulated_fill_probability_at_touch` (0.9 in `paper.yaml`) is
  stored but never used — paper fills are always 100% at touch.~~
  **FIXED 2026-09-13.** The configured probability is now applied to the
  portion of an order executing *at the touch*; levels consumed beyond the
  touch are taker executions against displayed liquidity and fill in full.
  It is an expected-fill model, not a coin flip, so paper stays
  reproducible run to run, and it rounds **down** — rounding up would
  reintroduce the exact optimism the fix removes.
- Add queue-position/partial-fill realism, latency per venue, and
  opportunity-lifetime bookkeeping (`§68`).

Exit: a multi-week paper run whose fill assumptions are documented and
defensible, with per-cycle metrics persisted. **Still needs the owner's
multi-week run**; the code that would produce defensible fill assumptions
now exists.

#### Phase 6 — why these two were fixed together

Both defects pushed paper results in the *optimistic* direction, which is
the dangerous one: a strategy that validates in paper and then fails live
is worse than one that never validated. Fixing only one would have left
the other quietly inflating the same numbers.

Verified by mutation, not just by passing tests. Reverting the fill model
(`if False:` in place of the probability check) fails 4 tests; reverting
the freshness gate (`snapshot()` for `fresh_snapshot()`) fails the
end-to-end test that feeds the scan a 5-second-old book and asserts it
does not arrive. The first draft of the D11 tests only exercised the
aggregator's helpers and caught the revert **not at all** — testing that a
helper works is not testing that it is used. `tests/test_paper_realism.py`
(14 tests) exists because of that miss.

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
| D3 | ~~Collect data is invisible to historical replay~~ **FIXED 2026-09-13**: `Repository` gained a `read_only` handle (SQLite `mode=ro`, so writes are refused by the engine, not by convention) and `_assert_mode_tag` a narrow allowance — `read_only` **and** `mode=HISTORICAL` **and** a `collect`/`paper` tag. `AppConfig.recorded_store_path` is derived from the same constants collect writes with, so replay and the writer cannot drift. A missing store raises instead of creating an empty DB; a `live`-tagged store is still refused | Was: collect → `data/paper/…` (tagged `mode=collect`); historical → `data/historical/…` (0 rows); report showed `frames: 0`. Now: `tests/test_phase4_recording.py::TestRecordedStoreAccess` (6 tests) | 4 |
| D4 | ~~Recorded markets lose the metadata matching needs~~ **FIXED 2026-09-13**: `MarketRow` gained 26 nullable columns (`SCHEMA_VERSION` 1 → 2) and migration `002` is forward-only and `PRAGMA`-guarded, so a fresh DB does not die on "duplicate column name" and a real v1 DB upgrades with its rows intact. `record_contract()` persists all 37 `NormalizedContract` fields plus the raw payload; `database/contract_codec.py` owns both directions. **Decode never invents** — an under-recorded row returns `None` and is counted, replacing the old invented `payout=1.00` / `tick_size=0.01` / `contract_version="historical"` | Was: `MarketRow` had 11 columns; `_reconstruct_contract` set `settlement_time=None`, `resolution_source=""` → `deterministic_match` rejected on both. Now: `test_record_contract_round_trips_every_field` asserts an empty difference list across all 37 fields | 4 |
| D5 | ~~Kalshi on legacy host, intermittent failures, no failover~~ **FIXED IN CODE 2026-09-13**: recommended `external-api` hosts configured, legacy hosts kept as fallbacks, 5xx/transport failover + connect-time probing, bulk books, WS streaming, tier-aware budget clamping (26 tests). *Operational verification (24h run on the owner's machine) still pending — see Phase 2 section* | Was: `config/venues.yaml` → `api.elections.kalshi.com`; collect log: 18 DNS failures + 14 HTTP 500s | 2 |
| D6 | ~~Cross-venue candidate generation cannot produce candidates~~ **FIXED IN CODE 2026-09-14**: `group_by_event` now unions contracts over five deterministic blocking-key families (settlement 24h buckets ±1, category, threshold+operator+units, canonical numeric tokens, plural-folded content words) instead of exact canonical title + settlement date; recall-oriented by design, and the deterministic matcher stays the only authority (§18/§96). 24 tests in `tests/test_candidate_generation.py`, mutation-verified. *Exit evidence on real two-venue data remains owner-gated (Phases 2/3)* | Was: `normalized_event_key` = exact canonical title + settlement date → cross-venue pairs never candidates. Now: `tests/test_candidate_generation.py` (e.g. `test_differently_phrased_same_event_group_together`, `test_pipeline_finds_cross_venue_opportunity`) | 5 |
| D7 | ~~Venue research stale~~ **FIXED 2026-09-13**: evidence ledger now covers direct DCMs, aliases, multi-route frontends, fees, Utah/CFTC context, and ranked candidates; old “only one independent pair” premise removed | `docs/VENUE_RESEARCH.md`; provenance/fee/compliance registries; focused tests | 1 |
| D8 | ~~Spec document missing~~ **FIXED 2026-09-13**: `docs/SPEC.md` reconstructs all 73 cited sections from code/docs/tests/config evidence, with `[I]` flags on the two thin ones (§33, §68) and an appendix of the 22 numbers that have no surviving citation | Was: 73 distinct `§` sections cited at 224 sites with no spec file. Now: `grep -c "^### §" docs/SPEC.md` → 73 | 0 |
| D9 | ~~No LLM configured ⇒ no APPROVED certificates ⇒ nothing is ever evaluated~~ **FIXED IN CODE 2026-09-14**: `main.py` builds the matcher from the mode env file via `matching/llm_client.py` (§97 — OpenAI-compatible `/chat/completions`; `LLM_API_BASE`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_TIMEOUT_SECONDS` in `.env.paper`). Transport only: schema validation (§88), outage/malformed ⇒ no-match, in-memory caching (§87) and model/prompt versioning unchanged; no config is still the conservative default (REVIEW, never auto-APPROVED); partial config refuses startup; fail-closed per-cycle comparison budget (`llm.max_comparisons_per_cycle`, default 200; excess pairs stay REVIEW and are not cached; `run_matching` resets the window). 20 tests in `tests/test_llm_client.py`, mutation-verified. *Operational verification on a real endpoint is owner-gated (H7)* | Was: `main.py`: `LLMMatcher(client=None)` hardcoded; `optimizer.search_event` skips pairs without approval. Now: wired + `tests/test_llm_client.py` (config paths, transport errors, budget; live wiring checks of the three startup paths — none/partial/full — run 2026-09-14) | 5 |
| D10 | Live mode is monitor-only; readiness check hardcodes the venue count | `prediction_arb/modes/live/engine.py::run`; `main.py::cmd_readiness` (`False` literal) | 8 |
| D11 | ~~No book-freshness enforcement in paper; `OrderBookAggregator` is dead code~~ **FIXED 2026-09-13**: the paper engine now holds an `OrderBookAggregator` and both the scan and the simulated execution engine read through it. The gate is the stricter of `execution.max_book_age_ms` and `arbitrage.maximum_book_age_ms`; excluded books are counted and printed rather than silently dropped. Streamed books get the same sequence guard and staleness accounting as polled ones | Was: aggregator referenced only in tests; `maximum_book_age_ms` enforced solely by the live firewall, so a paper book could age forever and still be traded against. Now: `tests/test_paper_realism.py::TestPaperFreshnessEndToEnd`, verified by mutation — reverting `fresh_snapshot()` → `snapshot()` fails `test_stale_polled_book_never_reaches_the_scan` | 6 |
| D12 | ~~Configured fill probability is ignored~~ **FIXED 2026-09-13**: `fill_probability_at_touch` now scales the portion of an order executing at the touch (levels beyond the touch are taker fills and stay whole). Expected-fill rather than a coin flip, so paper remains reproducible, and it rounds down — rounding up would reintroduce the optimism being removed | Was: stored in `prediction_arb/execution/simulated.py` and never read, so every paper fill was 100% at the touch while `paper.yaml` said 0.9. Now: `tests/test_paper_realism.py::TestFillProbabilityAtTouch`, verified by mutation (replacing the check with `if False:` fails 4 tests, including one asserting `paper.yaml`'s 0.9 actually reaches the engine) | 6 |
| D13 | ~~Trade fees priced at a walk's weighted-average price instead of per fill~~ **FIXED 2026-09-13**: `FeeCalculator.trade_fee_fills()` charges each walked level, applies the venue's documented rounding, and honours an order-total cap; `two_leg.py` and `execution/simulated.py` now pass the real `levels_consumed`. The old shortcut is not what any venue bills: it overstates a concave taker charge and would understate a maker credit, and it ignores the cumulative cap entirely | Was: `two_leg.py:139` → `trade_fee(venue, yes_walk.weighted_average_price, qty)`. Now: per-fill; `tests/test_polymarket_fees.py::TestPerFillAccuracy` proves the engine charges walked levels and that the two numbers differ | 3 |
| D14 | ~~Configured rate-limit headroom silently ignored~~ **FIXED 2026-09-13**: `main.py` read `rate_limit_headroom` from the top level of `venues.yaml` instead of under its `venues:` key, so the hardcoded `0.8` default always won. Harmless only while config and default matched; an operator tightening headroom would have had no effect | Was: `config.venues.get("kalshi", {})` → `{}`. Now unwrapped like `build_adapters`; `tests/test_units.py::TestRateLimitCalibration` (verified by mutation: reverting the fix fails the test with `headroom=0.8`) | 2 |
| D15 | ~~`validate_match` can issue an APPROVED certificate for a SAME_VENUE pair~~ **FOUND + FIXED 2026-09-14** (found while re-deriving the matching pipeline for D6; fixed as its own reviewable commit): the provenance gate now routes every relationship that is not proven `INDEPENDENT` — SAME_VENUE, LIKELY_SAME_VENUE, LIKELY_INDEPENDENT, UNKNOWN — to REVIEW, never APPROVED (§33 requires an INDEPENDENT relationship for any trade). `search_event` already refused to evaluate non-INDEPENDENT pairs, so no fake trade could execute; the fix closes the certificate side, which would otherwise poison the human false-match review. 3 tests in `tests/test_candidate_generation.py::TestSameVenueNeverApproved`, mutation-verified (reverting the gate fails the 2 same-venue tests) | Was: only `Independence.UNKNOWN` was downgraded to REVIEW in `matching/rules_validator.py`; kalshi↔webull (SAME_VENUE via `provenance/independence.py`) with a deterministic pass + a good LLM verdict reproduced `APPROVED`. Now: `TestSameVenueNeverApproved` | 5 |
| D16 | ~~Test suite red: an expired date fixture~~ **FOUND + FIXED 2026-10-09**: `tests/conftest.py` pinned `SETTLE = 2026-09-15T20:00Z`, and `normalization/settlement.py::capital_lockup_days` correctly clamps a past settlement to `0`, so `TestSettlementTiming::test_lockup_and_cost`'s `assert lockup > 0` failed for every run after that instant — and every other test using the fixture had quietly stopped exercising a non-zero settlement-delay cost. **Production code was never implicated.** The fix is tests-only: `make_contract`'s default settlement is now a relative near-future instant (cannot expire), the assertion is strengthened to `cost > 0` (which the clamped-to-zero state cannot satisfy), and two deterministic explicit-`now` tests were added — exact last-leg-decides arithmetic, and the past-settlement clamp pinned as intentional rather than a bug | Was: `pytest` → `310 passed, 1 failed`; `capital_lockup_days([a,b], now=2026-09-14)` → `1.8333…` vs `now()` → `0.0` (date-dependent, not environmental). Now: **313 passed**, and 313 passed again under a frozen clock at 2026-10-14 / 2027-10-09 / 2031-01-02. Mutation-verified three ways: restoring the absolute date fails `test_lockup_and_cost`; returning the first leg instead of the last fails `test_lockup_tracks_the_last_leg_to_settle`; removing the `max(0, …)` clamp fails `test_already_settled_contract_locks_no_capital` | 0 |

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

**H7 — LLM API key (needed for Phase 5's exit: APPROVED certificates)**
1. Choose any OpenAI-compatible provider (OpenAI, Groq, OpenRouter, or a
   local server) and create an API key on that provider's site.
2. Put the three values in `.env.paper` at the repo root (see
   `docs/API_SETUP.md`; `.env.example` has a commented template):
   ```
   LLM_API_BASE=https://api.openai.com/v1
   LLM_API_KEY=your-key
   LLM_MODEL=gpt-4o-mini
   ```
3. Tell the agent it's done — never paste the key into chat. A paper run
   will then print `LLM matching enabled` at startup instead of the yellow
   `not configured` notice, and cross-venue pairs that pass the
   deterministic filter can be APPROVED (budget-capped per cycle).

**H5 — Decide the data policy**
Recorded market data is already 17 MB for one day of collection. Decide
how long to retain it and whether any of it belongs in git (recommended:
none; keep it local or in external storage).

**H6 — Say the word before anything live**
Nothing in Phases 0–7 places a real order. Live work starts only in
Phase 8, only after paper/demo drills, and only with your typed
confirmation phrase.
