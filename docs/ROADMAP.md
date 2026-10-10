# ROADMAP & PHASE MAP

Originally compiled 2026-09-13 (America/Denver) from a full read of the
repository, test suite/CLI, databases/logs, and venue/API sources. Updated
2026-10-10 for the direct fee-source re-review and pair-readiness audit
(`docs/FEE_READINESS.md`), the D18 settlement-timing evidence review
(`docs/TRADING_LOGIC.md`, "Settlement timing and capital lockup"), D20 paper
capital/risk enforcement (`docs/RISK_MANAGEMENT.md`), the Arb.10 runtime
configuration/module audit (`docs/AGENT_HANDOFF.md` §7d), D27 adapter
evidence/collection audit (claim-level status, public-only smoke, snapshot
validation; see `docs/VENUE_RESEARCH.md` K9/K11/P7 and handoff §7e), and Arb.12
offline synthetic end-to-end validation of the matching → costed paper scan →
simulated execution core path (see §3 Phase 6 and `docs/TESTING.md`). Older
verification dates remain attached to unrevisited claims.

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

## 1. Current state snapshot (re-verified 2026-10-10 where noted; broad baseline 2026-09-13)

| Check | Result |
|---|---|
| `pytest` | `.venv/bin/pytest -o addopts=''`: **643 collected, 643 passed in 28.04 s**, Python 3.11.2, re-run 2026-10-10 on `arena/2ac1509e-arb`; all tests are offline. Arb.12 adds 11 synthetic end-to-end/refusal tests; prior D27 additions are included. `pytest --collect-only -q` is represented by the full run's 643 collected items. |
| Settlement-timing readiness | **Kalshi DOCUMENTED** (per market: `latest_expiration_time` + `settlement_timer_seconds`; not observed), **Polymarket US UNKNOWN**, **ForecastEx UNKNOWN** — so every enabled pair still blocks on lockup, independently of the fee gate (D18, 2026-10-10). Evidence table: `docs/TRADING_LOGIC.md` |
| `python -m prediction_arb.main --status` | Re-run 2026-10-10, exit 0: kalshi=`MARKET_DATA_OBSERVED` (historical public data only), polymarket_us=`API_DOCUMENTED_UNTESTED`, forecast_ex=`API_DOCUMENTED_UNTESTED` (structured stub). The claim-level table separately shows both latest public probes `UNAVAILABLE` (TLS EOF), no full adapter verification, and all three enabled unordered pairs `INDEPENDENT`; provenance is not fee/book readiness. |
| `python -m prediction_arb.main --readiness` | Re-run 2026-10-10, exit 0: four derived per-mode reports; `COLLECT: READY` (1 of 3 market-data-evidenced adapters), `HISTORICAL: NOT READY — 1 blocker(s)` (no recorded store), `PAPER: NOT READY — 5 blocker(s)` (only one market-data adapter, no independent pair, no matcher, incomplete fees, unknown settlement), `LIVE: NOT READY — 8 blocker(s)` (0 fully verified live-capable venues, no pair, no matcher, 0/3 complete fee schedules, settlement UNKNOWN for polymarket_us + forecast_ex, 0 recorded snapshots/opportunities, eligibility UNKNOWN ×3, no `.env.live`). Fee freshness passes 3/3 (0 days old); 12 risk values and staleness gates are configured, but risk row is configuration-only. `LIVE MODE: NOT RECOMMENDED YET`; report is advisory and side-effect-free. |
| Live-capable venues | **0**. Kalshi is `MARKET_DATA_OBSERVED`, not full `VERIFIED`; Polymarket US has no observed market payload; ForecastEx is a stub. |
| Fee/pair readiness | **0 of 3** enabled provenance-independent pairs are end-to-end cost-evaluable. All three enabled venues have incomplete trade/settlement gates. Kalshi alone is admitted for market-data collection based on its historical public observation; no fresh live snapshot was observed. Direct fee audit and pair matrix: `docs/FEE_READINESS.md` (2026-10-10). |
| `data/paper/…sqlite3` | Not present in a fresh clone (git-ignored). Previously 21,892 markets / 13,801 book snapshots spanning 2026-08-30 → 2026-09-05, tagged `mode=collect` |
| `data/historical/…sqlite3` | **Empty** (0 rows in every table) |
| `reports/historical/…json` | `{"frames": 0, "opportunities": 0}` |
| `logs/collect/collect_20260831.jsonl` | Not present in a fresh clone. Previously 32 error rows: 18× DNS failure, 14× Kalshi HTTP 500 (2026-08-31 → 2026-09-04) |
| CI | **Green for the D17 work on PR #8** (2026-10-10) — `pull_request` runs `38012787059` and `38012962141` both `success` (47s each: install, full suite, four CLI smokes, honest-empty replay, artifact guard); the newest run on the PR head supersedes those listed. Predecessors: `796abec` (merge of PR #7) green as run `38009643994`; the original D16 repair green as run `37999166331` on `f74c728` (2026-10-09, 49s). *2026-10-10: the workflow gained a fifth smoke (`--readiness`, grepping for the derived policy line, the live verdict and the advisory footer) plus a guard that the report leaves the runtime tree unchanged. The first version of that guard asserted `data/` was absent, which the test suite itself contradicts — `tests/test_paths.py` and `tests/test_phase4_recording.py` leave an empty `data/paper/` behind — so push run `38021724449` failed on the guard, not on the product. It now snapshots `data/ logs/ reports/` before and after the run and fails on any delta* |
| Outbound network from this sandbox | Latest bounded public-only probes on 2026-10-10: Kalshi (06:13:37.919175 UTC) returned `UNAVAILABLE` before HTTP response with `ConnectError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)` on both `external-api.kalshi.com` and `api.elections.kalshi.com`; Polymarket US (06:13:41.226497 UTC) returned the same TLS EOF at `gateway.polymarket.us`. No credentials/orders; no current market payload observed. These attempts do not establish an outage or adapter defect. Details: K9/P7 in `docs/VENUE_RESEARCH.md`. |

**Updated 2026-10-10 — D20 paper risk:** the inherited capital/risk report was
confirmed. Polymarket US was enabled without paper cash, unhedged breach checks
had no production caller, and multiple configured state limits were outside the
coordinator or had no lifecycle updates. Paper now has explicit venue-local
cash for all enabled venues, fail-loud missing-capital behavior, coordinator
precheck/reservation, exact per-venue capital, prospective/actual exposure
checkpoints, bounded recovery, persistent kill on unresolved exposure, and a
position-reducing simulated unwind. Fifteen offline fault-injection regressions
bring the suite to **549 passed in 26.52 s**. D21–D24 explicitly record the
remaining partial/disconnected limits; no live or generic emergency path was
enabled. See `docs/RISK_MANAGEMENT.md`.

**Updated 2026-10-10 — Arb.9 matching budget & deduplication:** verified
per-cycle budget consumption (counter reset in `run_matching`, cache hits never
consume, budget-exhausted REVIEW not cached so remains eligible). Candidate
ordering was discovery-order (sorted index pairs); replaced with deterministic
prioritization by independence (INDEPENDENT first), shared blocking keys
(thr>num>tok), settlement proximity, with lexical tie-breakers. No gross edge or
unverified LLM output used for ordering; deterministic REJECT final; semantic
matching cannot approve a pair that fails independence. Fixed cache keys to
include contract versions + matcher/prompt/model versions; fixed match_id to
include versions and to be sorted, so version changes create new rows preserving
history. Fixed persistence deduplication: `save_match_certificate` skips writes
when meaningful fields unchanged, preventing unbounded duplicate REVIEW records;
meaningful changes still update. Documented smallest viable migration for full
verdict-change history (audit log table) in `docs/MATCHING_DEDUP_MIGRATION.md`.
Added 15 tests in `tests/test_matching_arb9.py`; **597 passed in 29.23 s**.

**Bottom line:** the D17 zero-substitution defect is fixed, but **the fee
readiness gate is reopened** after the current Polymarket US schedule and
Kalshi/ForecastEx settlement scopes were re-reviewed on 2026-10-10. Three
enabled pairs are provenance-independent; none is end-to-end cost-evaluable.
As of D27, Kalshi is the only adapter admitted to public-data collection
(`MARKET_DATA_OBSERVED` from historical evidence), but no adapter has current
full verification. Matching needs an owner-supplied LLM key for an `APPROVED`
certificate. D6 candidate
generation and D15 provenance gating are implemented, but no first real
opportunity can be costed until external book/account evidence and the fee
context in `docs/FEE_READINESS.md` are resolved.

**Updated 2026-10-09:** the inherited D16 failure was a stale test fixture,
not a production settlement defect. The follow-up freezes the settlement
clock in tests, uses future-dated fixtures relative to that clock, and asserts
positive delay cost in both unit and end-to-end candidate tests. **313 tests
pass in 25.01s**, and the suite also passes at three frozen future wall-clock
dates. The remaining critical-path items are owner-gated — see
`docs/AGENT_HANDOFF.md` §8 and §9.

**Updated 2026-10-10:** the inherited D17 report was real and reproduced
offline: a BLOCKed opportunity carried a *positive* net profit
(`status=BLOCK`, `net_profit=9.9630`, fees fabricated as `0`) because
`evaluate_two_leg` replaced unavailable fees and an unknown settlement
lockup with zero. Net profit is now `None` (UNVERIFIED) whenever any
mandatory cost cannot be computed, unavailable costs are represented as
typed `CostComponent`/`CostLedger` values rather than numbers, gross edge is
preserved as a clearly separate research figure, and every reporting surface
(dashboard, CLI summaries, database, daily reports) shows `n/a` / NULL
instead of a figure. **348 tests pass in 26.29s** (313 baseline + 35 new in
`tests/test_unavailable_costs.py`), and the repair is mutation-verified. No
change to the fail-closed fee engine for D17.

**Updated 2026-10-10 — fee-readiness review:** direct official fee sources
were re-read for Kalshi, Polymarket US, and ForecastEx/IBKR. The prior
Polymarket US `0.06` formula is superseded by the schedule effective
2026-10-07; Table Tennis and combo variants were not represented by the old
single-family schedule. Kalshi's zero settlement fee is limited to simple
yes/no determinations. ForecastEx's `$0.01` exchange component remains
published, but no universal route commission or zero settlement charge was
established. All active production trade/settlement statuses are now
`INCOMPLETE`; no estimates are exposed. Three configured unordered pairs
are provenance-independent, but zero are end-to-end cost-evaluable with the
current API gates. Four focused regressions were added, and the full offline
suite now passes **352 tests in 28.29 s** (Python 3.11.2). See
`docs/FEE_READINESS.md` and updated `config/fees.yaml`.

**Updated 2026-10-10 — D18 settlement timing:** the inherited report that
Polymarket US was missing from `_EXPECTED_SETTLEMENT_DELAY_DAYS` was
confirmed, but the table itself was the defect: bare 0-day constants for
Kalshi and ForecastEx with no meaning or source, added to Kalshi's
*expected* expiration, a passed time clamped to zero lockup (reproduced:
a past-dated pair with synthetic complete fees came back `TRADE`,
`settlement_delay_cost=0.0000`, `net=7.32`), and replay measured lockup
against the wall clock. The table is replaced by an evidence registry with
explicit meaning/status/sources: Kalshi uses its documented per-market bound,
Polymarket US and ForecastEx are explicit `UNKNOWN` and keep blocking. No
delay was changed from unknown to zero. **396 tests pass in 25.99 s**;
eight mutation checks each fail the new tests.

---

## 2. Phase map

| Phase | Name | Status | Primary exit criterion |
|---|---|---|---|
| 0 | Repo hygiene, packaging, runnability, spec reconstruction | **DONE 2026-09-13** | Fresh clone → `pip install -e ".[dev]"` → full suite passes → CLI runs, no manual hacks |
| 1 | Venue & API research refresh (2026) | **DONE 2026-09-13 — OWNER CHECKPOINT** | Owner selects Polymarket US, Gemini, or IBKR route before adapter work |
| 2 | Kalshi adapter refresh + streaming | PARTIAL — code complete + verification script delivered 2026-09-13 (D5, D14 fixed); **owner run pending** | 24h collect run with ≥99% cycle success; WebSocket streaming in paper |
| 3 | **Second independent venue adapter** | PARTIAL — Polymarket US adapter is implemented, but fee gate was **REOPENED 2026-10-10** after the 2026-10-07 schedule change; market-family/settlement context remains blocked; observed payload and eligibility are owner-gated | Real books from the owner-selected venue proven INDEPENDENT of Kalshi, with complete all-in cost in paper mode |
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
  raise `FEE_DATA_INCOMPLETE`; the 2026-10-10 audit leaves all three enabled
  venues' trade and settlement components `INCOMPLETE`. The earlier ForecastEx
  all-in/zero-settlement completion claim was too broad; see
  `docs/FEE_READINESS.md` for route and rulebook gaps.
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

#### Completion record — adapter evidence and public smoke (reviewed 2026-10-10)

`KalshiAdapter` is `MARKET_DATA_OBSERVED` based only on the historical
public collection from 2026-08-30 (500 markets, 40 non-empty books). This
supports read-only data collection; it is not current full-adapter verification.
Current public smoke checks are intentionally bounded and credential-free:
`python scripts/kalshi_smoke.py --limit 1` (default limit 1, maximum 10) uses
only documented public GETs for configured exchange-status hosts, market
discovery and a single public book. It does not load mode env files, inspect
account/tier, call authenticated bulk books or WebSocket, or place orders.
Its offline tests cover output classification and safety but cannot establish
current live behavior. Successful public evidence would be a manual
`MARKET_DATA_OBSERVED` candidate only; it would not verify authenticated or
streaming surfaces or promote `VERIFIED` automatically.

- **Latest sandbox attempt (2026-10-10 06:13:37.919175 UTC):** `UNAVAILABLE`
  before HTTP response. Both
  `https://external-api.kalshi.com/trade-api/v2` and
  `https://api.elections.kalshi.com/trade-api/v2` raised
  `ConnectError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)`.
  No credentials or orders; exit 1. This does not establish venue outage or
  adapter defect. Full payload evidence is in VENUE_RESEARCH K9.
- **Offline tests:** `tests/test_kalshi_smoke_script.py` covers host result
  classification, public-only operation and the evidence-state distinctions.
  `tests/test_kalshi_phase2.py` separately covers failover, fixed-point
  payloads, authenticated bulk parsing, account-limit logic, and WebSocket
  sequence-gap recovery/reconnect with local mocks. These tests are not live
  connectivity evidence.
- **D14 fixed** (found while wiring the script): `main.py` read
  `rate_limit_headroom` from the top level of `venues.yaml` instead of under
  its `venues:` key. Fixed with three regression tests and mutation-verified.

Still open (exit criterion — owner-side evidence required):

1. A successful bounded public smoke from an authorized environment that can
   reach Kalshi; review the sanitized observed payload manually. This can
   establish usable public market-data evidence, not full adapter verification.
2. **Optional authenticated read-only evidence:** only if the owner wants to
   verify the separately documented account-tier, bulk-book or WebSocket
   surfaces. These calls require the documented API key and should be run
   locally; never supply credentials to this session or smoke script.
3. A 24-hour `--mode collect` run with ≥99% successful cycles and no
   unhandled 5xx, plus a paper session confirming sub-second streaming
   staleness. Feed counters (`snapshots`, `deltas_applied`,
   `sequence_gaps`, `reconnects`) are exposed via
   `adapter.streaming_stats()` for that evidence.

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

- **Public market data is documented as credential-free (P1).** The adapter
  implements discovery/books via `https://gateway.polymarket.us` without
  credentials; offline tests exercise that route. The 2026-10-10 sandbox
  probe got no HTTP response (P7), so live public connectivity and current
  payload behavior remain unobserved. The authenticated host
  (`https://api.polymarket.us`, Ed25519 signing per P2) is used for the
  documented authenticated surfaces; this smoke does not touch them.
- **True two-sided CLOB.** `bids` and `offers` are both real resting
  orders; nothing is synthesized. Contrast Kalshi (bid-only, YES ask =
  `1 − NO bid`) and ForecastEx (paired bid at $1.00).
- **Market state is enforced (P4).** `parse_order_book` returns `None`
  for any state other than `MARKET_STATE_OPEN`, so a halted, pre-open,
  or auction book can never be sized against by the arbitrage engine.
- **Per-market trading rules (P3).** `orderPriceMinTickSize` and
  `minimumTradeQty` are optional fields carried only when observed and
  valid. Missing or malformed values remain unknown (`None`); they are
  never inferred from product type or slug. A smoke result cannot count
  as usable rule evidence without positive finite values for both.
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

**Status: `API_DOCUMENTED_UNTESTED`; collect mode deliberately skips the
venue.** The bounded public-only probe on 2026-10-10 06:13:41.226497 UTC was
`UNAVAILABLE` before HTTP response: `https://gateway.polymarket.us` raised
`ConnectionError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)`.
No credentials or orders were used. No live market or book payload is
observed; the adapter is neither labelled broken nor verified (P7,
`docs/VENUE_RESEARCH.md`).

On an authorized network, use the bounded public-only probe (default one
market, maximum ten):

```bash
python scripts/polymarket_us_smoke.py --limit 1
```

It writes a sanitized report to
`reports/evidence/polymarket_us_<UTC>.json` (git-ignored). Review evidence
manually. A successful public read can support `MARKET_DATA_OBSERVED` for
usable, open, two-sided books with valid per-market tick/minimum rules; it
must **not** promote the adapter to `VERIFIED`. Full adapter verification
needs additional evidence for required surfaces. Until an observed public
market payload has been reviewed and recorded, collect/paper continue to skip
this venue.

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
3. **Fee readiness reopened 2026-10-10 — INCOMPLETE / BLOCK.** The current
   2026-10-07 schedule supersedes the prior `0.06` rate and includes a higher
   Table Tennis rate plus a separate combo curve. The arithmetic regression
   now uses a test-only synthetic standard schedule and does not qualify the
   production calculator. Market-family selection, combo support, account/fill
   reconciliation, and customer settlement treatment are unresolved. Weekly
   volume rebates remain prior-month account evidence, paid weekly, not per-
   trade discounts. See `docs/FEE_READINESS.md` for official sources, exact
   gaps, and required owner evidence. Keep both production statuses
   `INCOMPLETE`; do not expose estimates.
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
  international platform. The 2026-10-07 schedule has standard, Table Tennis,
  and combo fee families; selection, order/fill state, and settlement remain
  blocked (`docs/FEE_READINESS.md`). Do not promote the standard formula to a
  production estimate.
- **Kalshi:** resolve series/event overrides and account/order rounding state;
  keep zero settlement limited to the documented simple yes/no scope.
- **ForecastEx / IBKR:** authenticate a local Client Portal/IB Gateway session;
  qualify each route. Opposite bids pair at `1.00` and the exchange separately
  charges `$0.01` per executed side, but all-in commissions and settlement
  remain account/rulebook gated. Never call a user's localhost gateway directly
  from browser code; use an app/backend proxy.
- **Gemini:** resolve listing-date fee cohorts and Titan/QC/Olympus aliases.

Exit: paper mode fetches real books from the selected route; API status reflects
actual testing; the route is demonstrably INDEPENDENT of Kalshi for the exact
contract; every mandatory trade/settlement cost and eligibility gate is
supported by current evidence; and at least one real overlap is persisted as
APPROVED or REVIEW (never forced to APPROVED).

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
  _Superseded 2026-10-10 (Arb.8): the settlement-bucket union-find below is
  no longer used for proposal; see `docs/CANDIDATE_RETRIEVAL.md`._
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
by a human. Offline coverage now spans two levels: the existing
`tests/test_candidate_generation.py::TestPipelineEmitsCandidates` exercises
candidate → deterministic pass → APPROVED certificate → evaluated
opportunity; Arb.12's `tests/test_synthetic_opportunity_e2e.py` carries an
explicit synthetic pair through the same production matching/certification,
fresh-book scan and cost evaluator into risk-gated `PaperExecutionEngine`
fills, and covers mismatch/SAME_VENUE and refusal/failure paths. Its test-only
LLM is not an endpoint and proves no real match. **Still owner-gated:**
(a) an LLM key in `.env.paper` (H7) so real ambiguous matches can be reviewed
and approved; (b) real two-venue data — the Polymarket US smoke run + Kalshi
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

**Arb.12 offline integration addition (2026-10-10; not a Phase 6 exit).**
`tests/test_synthetic_opportunity_e2e.py` (11 tests) composes existing
production seams: PAPER eligibility decision, pair proposal/matching and
certificate, registry-based independence, normalized book validation and
`OrderBookAggregator` freshness, `run_arbitrage_scan`/optimizer,
`evaluate_two_leg`/walked levels/fee ledger/settlement lockup, risk reservation
and exposure checks, partial-fill recovery, and `PaperExecutionEngine`. It
proves one all-gates-pass simulated two-leg fill plus mismatch, same-venue,
unknown-cost, thin/stale-book, risk/cash and partial/unresolved-failure
refusals. Synthetic test LLM/fee/ForecastEx-timing evidence is never loaded
outside the fixture; the production fee ledger and production ForecastEx
UNKNOWN timing are separately shown to block with `net_profit=None`. No
production refactor was needed because these components already compose.
The synthetic categories remain UNKNOWN for Utah but are research-allowed in
PAPER; this is not a legal eligibility claim.

**Exact boundary:** the test does not call `PaperEngine._cycle`, discover
through venue adapters, read REST/WS or account data, persist matches or
opportunities, run a live LLM, compute production-complete fees, establish
ForecastEx capital-release timing, exercise a real-account eligibility
verdict, or model settlement/real fills. No real order-placement method or
adapter is involved. These owner/data-gated and multi-week Phase 6 exits remain
open.

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
with hard caps. Rehearse: kill switch, crash recovery reconciliation,
one-leg-filled recovery, firewall rejection, fee-freshness refusal. ~~`main.py
--readiness` currently hardcodes "two live-capable independent venues = False";
make it compute from adapter status + provenance instead.~~ **DONE 2026-10-10
(D10):** `prediction_arb/monitoring/readiness.py` computes every criterion
from adapter status **and** verified order types, provenance, matcher, fee
completeness and freshness, settlement evidence, recorded volume, eligibility,
kill switch, risk limits and book-staleness gates (§99), and the fee-freshness
rehearsal item above is now a real runtime gate rather than a document (D19,
§98). Live always requires typing `I UNDERSTAND LIVE TRADING`; no flag may
bypass it, and live mode remains monitor-only by construction (§60). D24 still
blocks live coordinator construction: no production `FirewallContext` is
built, and the emergency scaffold does not cancel or flatten. Keep both
unwired until demo fault/reconciliation tests prove the path; remaining work
is owner-gated evidence plus those safety tests, not live enablement.

---

### Phase 9 — v2 strategy surface  ·  NOT STARTED

Behind the existing stable stubs, and only after Phase 6 evidence:
`prediction_arb/arbitrage/multi_leg.py`, `prediction_arb/arbitrage/logical.py`
(TYPE B, research-only with a test battery first), `prediction_arb/arbitrage/graph.py`
cycle search, and fee/route optimisation across SAME_VENUE frontends (the one
legitimate future use for the Webull/Moomoo stubs). Arb.10 confirmed that the
graph's `two_leg_candidates()` is not consumed by production scanning; the
active two-leg path is `modes/shared.py` → `arbitrage/optimizer.py` →
`arbitrage/two_leg.py`.

Also explicitly deferred from production wiring (D26): `risk/capital.py`
operates only on caller-supplied balances and demand, while
`execution/hedging.py` is a price-only helper that does not validate
independence or include fees. Neither has a production caller. Do not connect
either until account/capital inputs, venue/fee semantics, and failure cases
have a tested lifecycle. `execution/emergency.py` remains the separate D24
blocker: it only engages the kill switch and reports positions; it cannot
cancel or flatten.

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
| D6 | ~~Cross-venue candidate generation cannot produce candidates~~ **FIXED IN CODE 2026-09-14; RETRIEVAL REVISED 2026-10-10 (Arb.8)**: the settlement-bucket union-find described below chained the 1,000-contract benchmark into one component. Arb.8 replaced it with pairwise, bounded, non-transitive retrieval (cross-venue, settlement within ±6h, shared `thr`/`num`/`tok` key, local density cap 20). Benchmark: 333,296 → 3,262 candidates, eq∩det recall 256/256 on synthetic and 15/16 on the reference set (one known paraphrase miss). Design and lost matches: `docs/CANDIDATE_RETRIEVAL.md`. Original 2026-09-14 record follows: **FIXED IN CODE 2026-09-14**: `group_by_event` now unions contracts over five deterministic blocking-key families (settlement 24h buckets ±1, category, threshold+operator+units, canonical numeric tokens, plural-folded content words) instead of exact canonical title + settlement date; recall-oriented by design, and the deterministic matcher stays the only authority (§18/§96). 24 tests in `tests/test_candidate_generation.py`, mutation-verified. *Exit evidence on real two-venue data remains owner-gated (Phases 2/3)* | Was: `normalized_event_key` = exact canonical title + settlement date → cross-venue pairs never candidates. Now: `tests/test_candidate_generation.py` (e.g. `test_differently_phrased_same_event_group_together`, `test_pipeline_finds_cross_venue_opportunity`) | 5 |
| D7 | ~~Venue research stale~~ **FIXED 2026-09-13**: evidence ledger now covers direct DCMs, aliases, multi-route frontends, fees, Utah/CFTC context, and ranked candidates; old “only one independent pair” premise removed | `docs/VENUE_RESEARCH.md`; provenance/fee/compliance registries; focused tests | 1 |
| D8 | ~~Spec document missing~~ **FIXED 2026-09-13; maintained 2026-10-10:** `docs/SPEC.md` records reconstructed requirements with `[I]` flags on the two thin ones (§33, §68), an appendix of unresolved historical gaps, and the requested claim-level evidence requirement §101. | Current spec has 79 section entries; 451 citation sites were counted on 2026-10-10, excluding the spec, roadmap, and kickoff docs. `grep -c "^### §" docs/SPEC.md` → 79 | 0 |
| D9 | ~~No LLM configured ⇒ no APPROVED certificates ⇒ nothing is ever evaluated~~ **FIXED IN CODE 2026-09-14**: `main.py` builds the matcher from the mode env file via `matching/llm_client.py` (§97 — OpenAI-compatible `/chat/completions`; `LLM_API_BASE`/`LLM_API_KEY`/`LLM_MODEL`/`LLM_TIMEOUT_SECONDS` in `.env.paper`). Transport only: schema validation (§88), outage/malformed ⇒ no-match, in-memory caching (§87) and model/prompt versioning unchanged; no config is still the conservative default (REVIEW, never auto-APPROVED); partial config refuses startup; fail-closed per-cycle comparison budget (`llm.max_comparisons_per_cycle`, default 200; excess pairs stay REVIEW and are not cached; `run_matching` resets the window). 20 tests in `tests/test_llm_client.py`, mutation-verified. *Operational verification on a real endpoint is owner-gated (H7)* | Was: `main.py`: `LLMMatcher(client=None)` hardcoded; `optimizer.search_event` skips pairs without approval. Now: wired + `tests/test_llm_client.py` (config paths, transport errors, budget; live wiring checks of the three startup paths — none/partial/full — run 2026-09-14) | 5 |
| D10 | ~~Live mode is monitor-only; readiness check hardcodes the venue count~~ **READINESS SIDE FIXED 2026-10-10** (live stays monitor-only by construction, §60, which is not a defect): `cmd_readiness` derives criteria from observed state, a mode is ready iff no applicable criterion is BLOCKED, and the read-only probe reports a missing store without creating it. The full-verification + verified-order-type threshold for live remains intact. At the pre-D27 snapshot there was 1 live-capable venue; **D27's current evidence audit downgrades that to 0**, because Kalshi is `MARKET_DATA_OBSERVED`, not full `VERIFIED`. Current readiness remains NOT READY: no independent live pair, 0/3 complete fee schedules, UNKNOWN settlement on 2 venues, 0 recorded rows, eligibility UNKNOWN ×3, no `.env.live` | Was: `--readiness` printed "NOT YET: Two live-capable independent venues = 1" from a literal `False`, so the check could never pass even with two verified venues, and running it created the paper data directory. Now: `tests/test_readiness.py` (140) — a synthetic all-pass input yields `live.ready is True`; the fake adapter reports APIStatus.VERIFIED *and* verified order types. 16 single-gate mutations (one per gate) each flip exactly the expected criteria; hardcoding `ModeReadiness.ready = False` fails 7 tests; the readiness probe creating the store fails 2; `cmd_readiness` on a `tmp_path` copy of the real config asserts no `data/` afterwards | 8 |
| D11 | ~~No book-freshness enforcement in paper; `OrderBookAggregator` is dead code~~ **FIXED 2026-09-13**: the paper engine now holds an `OrderBookAggregator` and both the scan and the simulated execution engine read through it. The gate is the stricter of `execution.max_book_age_ms` and `arbitrage.maximum_book_age_ms`; excluded books are counted and printed rather than silently dropped. Streamed books get the same sequence guard and staleness accounting as polled ones | Was: aggregator referenced only in tests; `maximum_book_age_ms` enforced solely by the live firewall, so a paper book could age forever and still be traded against. Now: `tests/test_paper_realism.py::TestPaperFreshnessEndToEnd`, verified by mutation — reverting `fresh_snapshot()` → `snapshot()` fails `test_stale_polled_book_never_reaches_the_scan` | 6 |
| D12 | ~~Configured fill probability is ignored~~ **FIXED 2026-09-13**: `fill_probability_at_touch` now scales the portion of an order executing at the touch (levels beyond the touch are taker fills and stay whole). Expected-fill rather than a coin flip, so paper remains reproducible, and it rounds down — rounding up would reintroduce the optimism being removed | Was: stored in `prediction_arb/execution/simulated.py` and never read, so every paper fill was 100% at the touch while `paper.yaml` said 0.9. Now: `tests/test_paper_realism.py::TestFillProbabilityAtTouch`, verified by mutation (replacing the check with `if False:` fails 4 tests, including one asserting `paper.yaml`'s 0.9 actually reaches the engine) | 6 |
| D13 | ~~Trade fees priced at a walk's weighted-average price instead of per fill~~ **FIXED 2026-09-13**: `FeeCalculator.trade_fee_fills()` charges each walked level, applies the venue's documented rounding, and honours an order-total cap; `two_leg.py` and `execution/simulated.py` now pass the real `levels_consumed`. The old shortcut is not what any venue bills: it overstates a concave taker charge and would understate a maker credit, and it ignores the cumulative cap entirely | Was: `two_leg.py:139` → `trade_fee(venue, yes_walk.weighted_average_price, qty)`. Now: per-fill; `tests/test_polymarket_fees.py::TestPerFillAccuracy` proves the engine charges walked levels and that the two numbers differ | 3 |
| D14 | ~~Configured rate-limit headroom silently ignored~~ **FIXED 2026-09-13**: `main.py` read `rate_limit_headroom` from the top level of `venues.yaml` instead of under its `venues:` key, so the hardcoded `0.8` default always won. Harmless only while config and default matched; an operator tightening headroom would have had no effect | Was: `config.venues.get("kalshi", {})` → `{}`. Now unwrapped like `build_adapters`; `tests/test_units.py::TestRateLimitCalibration` (verified by mutation: reverting the fix fails the test with `headroom=0.8`) | 2 |
| D15 | ~~`validate_match` can issue an APPROVED certificate for a SAME_VENUE pair~~ **FOUND + FIXED 2026-09-14** (found while re-deriving the matching pipeline for D6; fixed as its own reviewable commit): the provenance gate now routes every relationship that is not proven `INDEPENDENT` — SAME_VENUE, LIKELY_SAME_VENUE, LIKELY_INDEPENDENT, UNKNOWN — to REVIEW, never APPROVED (§33 requires an INDEPENDENT relationship for any trade). `search_event` already refused to evaluate non-INDEPENDENT pairs, so no fake trade could execute; the fix closes the certificate side, which would otherwise poison the human false-match review. 3 tests in `tests/test_candidate_generation.py::TestSameVenueNeverApproved`, mutation-verified (reverting the gate fails the 2 same-venue tests) | Was: only `Independence.UNKNOWN` was downgraded to REVIEW in `matching/rules_validator.py`; kalshi↔webull (SAME_VENUE via `provenance/independence.py`) with a deterministic pass + a good LLM verdict reproduced `APPROVED`. Now: `TestSameVenueNeverApproved` | 5 |
| D16 | ~~Test suite red: an expired date fixture~~ **FOUND + FIXED 2026-10-09**: the old `tests/conftest.py` date was `2026-09-15T20:00Z`; after it passed, `capital_lockup_days()` correctly clamped lockup to zero, so `TestSettlementTiming::test_lockup_and_cost` failed and other tests silently stopped charging delay cost. **Production code was never implicated.** *(2026-10-10, D18: the zero clamp D16 treated as correct was itself replaced — a passed capital-release time for a contract still being priced is now UNKNOWN and blocks, and replay measures from the frame time.)* Tests now use a fixed `SETTLEMENT_TEST_NOW`; the default fixture settles three days later, and candidate-generation's fixture settles one day later. The unit regression pins a 3-day lockup and `$0.0411` cost; the end-to-end scan asserts positive delay cost. The prior report's 310/311 failure is not present at the current baseline (target passed before edits); an explicit calculation confirms the old date yields `0.0` after 2026-09-15. | Current `pytest` baseline: **313 passed in 25.01s**; 313 also pass at frozen wall clocks 2026-10-14 / 2027-10-09 / 2031-01-02. Mutation checks: restoring the old `conftest.py` date fails `test_lockup_and_cost`; restoring the old candidate-generation date fails the end-to-end positive-cost assertion. Last-leg arithmetic and the intentional past-settlement clamp remain covered by explicit-`now` tests. Tests only; no production changes | 0 |
| D17 | ~~A BLOCKed opportunity could report a positive "verified" net profit~~ **FOUND + FIXED 2026-10-10**: `evaluate_two_leg` caught `FeeDataError` and substituted `fees = Decimal("0")`, and substituted `delay_cost = Decimal("0")` for an unknown settlement lockup. The profit test then ran on fabricated free costs, so an opportunity blocked for `FEE_DATA_INCOMPLETE` came back with `status=BLOCK` **and** `net_profit=+9.9630`, which the dashboard printed as `$9.96` next to `$0.00` fees and the database stored verbatim. A positive gross edge was therefore indistinguishable from a profitable executable opportunity. **Fix (production, not a display patch):** every mandatory cost is now its own `core.types.CostComponent` inside a `CostLedger` that has *no total* while any component is missing; `net_profit`, `net_profit_per_contract`, `return_on_capital`, `total_fees`, `settlement_delay_cost` and `capital_required` become `None` (UNVERIFIED) instead of a number, each component is evaluated independently so all failures are reported, and `gross_edge` / `known_costs` are kept as clearly-labelled pre-cost research figures. Cost-of-capital is unavailable when fees are (its base includes fees). Downstream: `optimizer.score()` refuses to rank an unpriced opportunity and `rank()` appends unpriced ones last, `Opportunity.is_executable` gates paper execution on a verified positive net profit, metrics withhold a fee-drag ratio over a partially costed set, the dashboard/CLI render `n/a`, `OpportunityRow` stores SQL NULL + `unavailable_costs` (`SCHEMA_VERSION` 2 → 3, migration 003), and the paper/live daily reports count `opportunities_without_verified_net_profit`. The fail-closed fee engine itself is untouched | Was: `arbitrage/two_leg.py` `except FeeDataError: fees = Decimal("0")`; reproduced offline 2026-10-10 with a fee calculator missing one venue — `status=BLOCK`, `total_fees=0`, `net_profit=9.9630`, dashboard `$9.96`, DB `net_profit='9.9630'`. Now: `tests/test_unavailable_costs.py` (35 tests: incomplete trade fees, incomplete settlement fees, context-required fees, missing/expired/not-yet-effective/stale/unverified schedules, missing settlement time, venue with no settlement-delay model, ledger semantics, ranking, dashboard `n/a`, NULL persistence, migration 003, report counts). Mutation-verified: restoring the zero fee substitution fails 20 of the 35 (20 failed / 15 passed); restoring the zero delay-cost substitution fails the 2 settlement-timing tests (2 failed / 33 passed) | 3 |
| D18 | ~~Settlement-delay values had no meaning or evidence, and passed or long-dated settlement could be costed as (near-)immediate~~ **FOUND + FIXED 2026-10-10**: the inherited review reported Polymarket US absent from `_EXPECTED_SETTLEMENT_DELAY_DAYS` (confirmed). Re-verification found the whole table unsupported: `kalshi: 0` and `forecast_ex: 0` carried no source; Kalshi documents that markets "typically settle shortly after expiration, but timing can vary", and ForecastEx terms document settlement "no later than the day following Expiration Time" to the Member, not to a customer. The 0 was added to `settlement_time`, which for Kalshi is `expected_expiration_time` (an expectation, not a bound), so a market whose documented `latest_expiration_time` was a year out was costed for days. `capital_lockup_days` clamped a passed time to `0` for a contract still being priced off a live book, and historical/backtest replay measured lockup from the wall clock, so every recorded frame looked already settled. Day counts went through `float`. **Fix:** `normalization/settlement.py` now holds `PRODUCTION_SETTLEMENT_TIMING`, one `SettlementTimingSource` per venue with release meaning, resolution basis, post-resolution delay, evidence status, sources, review date and gaps; an UNKNOWN entry cannot carry an estimator. Kalshi = DOCUMENTED per-market `latest_expiration_time + settlement_timer_seconds` read from the preserved payload (round-trips the recorded store); Polymarket US = explicit UNKNOWN (no published resolution bound or settlement delay; `endDate` undefined and markets "stay open until the outcome is known"); ForecastEx = UNKNOWN (0-day value withdrawn). A release at or before evaluation time is UNKNOWN, not zero. `evaluate_two_leg` takes `evaluation_time`; historical and backtest pass the frame time. The lockup reason is reported even when fees are also unavailable. Day arithmetic is Decimal; the simple annual cost `capital × rate × days / 365` is unchanged but rounds UP to $0.0001 and rejects negative inputs | Baseline reproduction 2026-10-10 (worktree at `caa57c8`): registry `{'kalshi': 0, 'forecast_ex': 0}`; past-dated pair lockup `0.0`; with synthetic complete fees `status=TRADE`, `settlement_delay_cost=0.0000`, `net_profit=7.3200`; polymarket_us lockup `None`. After: same script → `BLOCK`, cost/net `None`. `tests/test_settlement_timing.py` (44: registry evidence, missing timing, past timestamps, replay frame time incl. the `run_arbitrage_scan` entry point, long-duration lockups, leg spread, capital-cost formula, adapter + codec round trip). Mutation-verified: Kalshi back to expected+0 → 6 fail; past clamp → 5; Polymarket US 0-day → 3; ForecastEx 0-day → 2; ignoring `evaluation_time` → 1; half-even rounding → 1; compounded cost → 12; dropping `evaluation_time` in `run_arbitrage_scan` → 1. The historical/backtest call sites passing the frame time are checked by inspection, not by a dedicated engine test. Kalshi timing is documented, not observed (K9) | 3 |
| D19 | ~~Mandatory fee freshness was documented but never enforced — `fees.max_schedule_age_days: 30` was dead config~~ **FOUND + FIXED 2026-10-10**: `FeeCalculator.schedule_for()` could always enforce the age limit, but only behind a `strict_freshness` flag **no production caller passed** — `evaluate_two_leg()` and `run_arbitrage_scan()` defaulted it to `False` and no mode overrode it — so a schedule verified years ago, or carrying no `verified_at` at all, priced paper decisions exactly like one verified yesterday, while `config/settings.yaml` promised "refuse live trading on stale fee data" and `--readiness` reported freshness as a hardcoded pass. **Fix:** one rule in `prediction_arb/fees/freshness.py` derives strictness from the mode — LIVE/PAPER **MANDATORY**, HISTORICAL **RESEARCH**, COLLECT **NOT_APPLICABLE**, an unrecognized mode fails closed to MANDATORY, and an explicit override wins in both directions. `strict_fee_freshness=None` now means "derive it from the mode" and is threaded through `arbitrage/two_leg.py`, `modes/shared.py` and `execution/simulated.py`. Under MANDATORY a stale, undated or unverified schedule raises `FeeDataError("FEE_DATA_INCOMPLETE: <venue>: … Re-verify before live execution")` — the same refusal as a missing schedule, never a stale number and never zero. Under RESEARCH the replay is still costed (history is evidence) and is labelled `RESEARCH USE ONLY: fee schedule(s) older than …` in the console, the run summary (`fee_freshness`) and `reports/historical/`, with the policy note printed on every replay so silence is never ambiguous. `verified_at: null` became representable instead of a config-loader `ValueError`, because "age unknown" must block with a reason. Freshness is not completeness: all three production schedules are fresh **and** `INCOMPLETE`, so they still block (§24) | Was: `grep -rn "strict_freshness=True" prediction_arb/` → nothing outside tests; a 45-day-old COMPLETE schedule returned a price in PAPER. Now: `tests/test_readiness.py` (138) covers fresh / stale / exact-boundary / stricter-limit / undated / missing / unverified schedules at every layer that takes a fee-derived decision (`schedule_for`, `evaluate_two_leg`, `run_arbitrage_scan`, simulated paper execution, historical execution, a full replay) and asserts the operator-facing message names the venue exactly once. Mutation-verified: `freshness_enforced` always False → 9 fail; PAPER downgraded to RESEARCH → 9; the fee-freshness criterion always passing → 5; the historical engine dropping the research label → 1; a stale RESEARCH schedule silently tolerated → 1 | 8 |
| D20 | ~~Paper capital and configured risk checks were disconnected from the execution lifecycle~~ **FOUND + FIXED 2026-10-10 (paper path):** confirmed `polymarket_us` was enabled but absent from `paper_starting_cash`; `ExposureTracker.breaches()` had no production caller; `ExecutionCoordinator` did not receive `RiskLimits`/`RiskState` or check the kill switch; paper checked seven capital/state limits outside the coordinator, never updated `open_orders`, updated deployment only on reported success, divided venue capital evenly instead of using leg costs, and did not test projected position count. Missing simulator cash was read as zero for buys (generic insufficient-cash message), while sells could create cash without a holding; unwind credited cash but never removed the position, so recovery could report success while the naked simulated position remained. **Fix:** explicit `$750` Polymarket US rehearsal allocation; startup requires positive cash for every enabled venue and the simulator names a missing venue. `config/risk_limits.yaml` is now the sole risk source (the dead `settings.yaml` duplicate and mismatched `max_trade_capital` key were removed). Coordinator precheck/reservation enforces capital/trade/event/venue/loss/position/order limits; venue capital is walked cost + that venue's fees; all prospective and actual unhedged limits run before A, after each leg, before every recovery order, and after recovery. Reservations release in `finally`; unresolved exposure is retained and engages the persistent kill switch while risk-reducing recovery remains allowed. Simulator sells cannot exceed holdings and reduce positions FIFO. | `tests/test_risk_enforcement.py`: checked-in capital coverage; missing-capital startup/order rejection; all 12 configured field names; seven pre-trade capital/state breaches with zero submissions; state update followed by a blocked second event trade; coordinator kill check; prospective aggregate/per-incident dollar/contract and aged-exposure blocks; simulated unwind position clearing; partial retry recovery; failed hedge + failed unwind kill activation; timeout breach with recovery still allowed; concession cap; observed recovery loss feeding the daily gate. All use `PaperExecutionEngine` or in-memory `FaultEngine`; no adapter/real-order path. Full trace: `docs/RISK_MANAGEMENT.md`. | 6 |
| D21 | **OPEN — `max_daily_loss` has no complete daily realized-P&L source.** The coordinator now posts observed paper hedge-concession and unwind loss and blocks the next trade at the configured threshold. Paper positions never settle and there is no day-boundary/settlement close lifecycle, so settlement-day realized P&L cannot be posted or reset honestly. | Do not call readiness's “12 limits configured” line enforcement evidence. Add a settlement/position-close ledger with UTC trading-day rollover and reconciliation tests before claiming full daily-loss protection. Current partial behavior and regression are documented in `docs/RISK_MANAGEMENT.md`. | 6 |
| D22 | **OPEN — `max_emergency_loss` bounds hedge price concession, not every possible recovery loss.** This matches the original §32 text and is now enforced before each retry, but the unwind's executable bid and exit fee are known only after `submit_order()` through the current generic engine interface. Pretending the cap covers those would be unsafe. | Add an engine-agnostic, freshness-gated recovery quote/fee preflight and test quote-to-fill deterioration, fee failure, no-bid, and cap exhaustion before broadening the limit. Until then, an over-budget chase is stopped, unwind loss is reported/posted, unresolved exposure kills new trades, and docs name the limitation. | 6 |
| D23 | ~~**OPEN — configured liquidity-ratio and execution-latency values are not gates.**~~ **RESOLVED AS CONFIG-HONESTY CLEANUP 2026-10-10, NOT AS FEATURE IMPLEMENTATION.** `arbitrage.minimum_liquidity_ratio`, `arbitrage.maximum_execution_latency_ms`, and `execution.max_execution_latency_ms` had no production comparison and were removed from `settings.yaml`. The walker still stops at displayed liquidity (no ratio check); the coordinator still records time between legs (no latency threshold). Neither protection is implemented. | `tests/test_config_audit.py` guards the removed keys; existing sizing/recovery tests cover the distinct displayed-depth and recovery behavior. Reintroduce only with a defined denominator and post-leg-A recovery policy plus fault-injection tests. | 6 |
| D24 | **OPEN — live risk wiring remains intentionally absent.** The live engine is monitor-only and no production code constructs a `FirewallContext`, so configured values are not supplied as live `risk_violations`. `EmergencyManager` is also disconnected and does not actually cancel/flatten. | This creates no current real-order bypass because live scanning/execution is not built. Before Phase 8, build and test a live coordinator with demo/sandbox fault injection, reconciliation, cancellation failure, orphaned fill, and firewall context generation. Do not enable `EmergencyManager` or real orders first. | 8 |
| D25 | **OPEN (candidate, 2026-10-10) — threshold-operator vocabulary and units are not normalized across venues.** Kalshi emits the raw `strike_type` as `threshold_operator`; Polymarket US emits `None`; both emit `units=None`. Identical events with the same threshold therefore fail the deterministic `threshold_operator` check; a units mismatch cannot be detected at all. Reproduced 2026-10-10. Not fixed by Arb.8: the fix is in the adapters and must not weaken the matcher. Details: `docs/AGENT_HANDOFF.md` §7a, `docs/CANDIDATE_RETRIEVAL.md` §11. | Reproduction in the handoff §7a row; no regression test pins the defect. | — |
| D26 | **DEFERRED — standalone capital/recovery prototypes are not production controls.** `risk/capital.py` has no production caller and consumes caller-supplied balances/demand; `execution/hedging.py` has no production caller, prices alternatives without fees or an independence gate, and is not used by `PartialFillHandler`. `execution/emergency.py` is covered by D24: it only engages the kill switch and reports positions, with no cancellation/flattening. | Source call-graph audit 2026-10-10; no integration is claimed. Keep capital sizing, hedge enumeration, and emergency response out of production until inputs, venue/fee/route semantics, cancellation/flattening outcomes, and failure handling have dedicated fault-injection tests. | 8–9 |
| D27 | **RESOLVED 2026-10-10 — adapter evidence was overclaimed and collection admitted invalid/unverified books.** Kalshi was unconditionally `VERIFIED`; a historical public observation was not separated from current endpoint connectivity/full adapter coverage. Collection's blocklist could admit other unverified states and saved non-empty books without validating crossed/non-finite levels. Smoke scripts mixed credentialed/authenticated surfaces with public checks and treated some network failures as hard failures. | Added claim-level documentation/connectivity/usable-data/full-verification/latest-smoke evidence; Kalshi is `MARKET_DATA_OBSERVED` from the 2026-08-30 public capture only, Polymarket US stays `API_DOCUMENTED_UNTESTED`, ForecastEx remains a stub. Smoke probes are bounded public GET-only/no credentials/no orders; latest 2026-10-10 attempts are explicitly `UNAVAILABLE` with exact TLS EOF details (K9/P7), not adapter failures. Collection now admits only MARKET_DATA_OBSERVED/VERIFIED and records usable books without an execution path; paper REST and streaming gates are distinct. Unknown Polymarket tick/minimum rules remain unknown. Offline regressions cover collection, parsers, timestamps, books, status and Kalshi sequence-gap/REST recovery; full current endpoint verification remains unavailable and is not claimed. | 2–3 |

---

## 5. Invariants that survive every phase

1. No real order can leave a non-live mode. Simulation engines hold no
   adapter reference; `KalshiAdapter.place_order` refuses unless
   constructed with `allow_order_placement=True`, which `main.py` sets
   only in live mode; the firewall re-verifies every order.
2. UNKNOWN never means yes — not for independence, eligibility, fees,
   settlement lockup, book freshness, or a fee schedule's verification age
   (an undated schedule is unknown-age and blocks where freshness is
   mandatory).
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
9. Readiness is derived, never asserted (§98–§100). Every gate reports the
   observation behind its verdict and the reason it is unmet; a mode is ready
   only when nothing applicable is BLOCKED; a gate that cannot be evaluated
   blocks; the report enables nothing and creates nothing; and a rejection is
   explained to the operator where they are looking, not only stored.

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
2. If desired, run the bounded public read-only evidence capture from an
   authorized network (default 1 market, max 10; **no credentials, account,
   or orders**):
   ```bash
   python scripts/polymarket_us_smoke.py --limit 1
   ```
   The latest sandbox attempt (2026-10-10 06:13:41.226497 UTC) was
   `UNAVAILABLE` due to TLS EOF before HTTP response (P7), which is neither
   adapter failure nor verification.
3. Manually review any future successful payload. It may support
   `MARKET_DATA_OBSERVED` for collection only; it does not promote
   `get_api_status()` to `VERIFIED`. Record current evidence in Phase 3 and
   `docs/VENUE_RESEARCH.md` before changing status.
4. *(Optional; only to verify authenticated streaming/account surfaces.)*
   The documented key flow and required scope are described in
   `docs/API_SETUP.md`. Keep credentials local; never paste them into chat or
   commit them. Basic public REST collection does not require a key.

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
confirmation phrase. Before that conversation, run
`python -m prediction_arb.main --readiness`: it prints one block per mode and,
for every criterion, the value it observed plus the reason anything is unmet —
so "what exactly is missing before live?" has a written answer instead of an
opinion. It is advisory: it enables nothing and creates no files.
