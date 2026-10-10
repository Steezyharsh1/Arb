# AGENT HANDOFF — prediction_arb

**Read this file first, before any other document or any code.** It is the
permanent entry point for a fresh agent session with no memory of prior
conversations. It tells you what the project is, what is true *right now*,
what is broken, what you may work on, and what you must not touch.

- **Last updated:** 2026-10-10 (UTC) by the readiness-derivation work on branch
  `arena/c1d8507b-arb` — mandatory fee freshness is now enforced per mode and
  every readiness verdict is computed from observed state (**D10 and D19
  resolved**). It carries forward the D18 settlement-timing review, the
  fee-readiness review and the D17 mandatory-cost repair.
- **Evidence discipline:** verification dates are per claim. Active-route fee
  documents and venue settlement-timing documents were opened directly on
  2026-10-10; no venue API, account, fill,
  or settlement statement was available. Test and CLI results below are marked
  with their actual command/date. Anything inherited and not re-run is
  labelled INHERITED; never promote it to VERIFIED without evidence.
- This document does **not** restate the other docs. Each subject links to the
  one authoritative document for it. If this file and another document
  disagree about *status*, re-verify and fix both in the same change.

---

## 1. Mission and immediate objective

**Mission.** Detect *guaranteed* (TYPE A) two-leg arbitrage across genuinely
independent US prediction-market venues: buy YES on venue A and NO on venue B
for the same economic contract, such that every settlement outcome is
profitable after real walked costs, fees, and the cost of locked capital.
Safety, honesty about UNKNOWN, and mode isolation outrank features.

**Immediate objective (unchanged).** Get from *"architecturally sound but has
never seen real two-venue data"* to *"two genuinely independent venues
producing real, costed arbitrage candidates in paper mode."* No real money is
traded in this engagement under any circumstances. Current fee audit:
three enabled venue pairs are provenance-independent, but **0/3 are
end-to-end cost-evaluable**; all active trade and settlement schedules are
`INCOMPLETE`. Do not turn the documented rates into estimates. Settlement
timing (capital lockup) is evidenced only for Kalshi (DOCUMENTED per-market
bound); Polymarket US and ForecastEx are `UNKNOWN` and block (D18). Fee-schedule
freshness **is** an enforced gate now (D19, §98): live and paper refuse a
schedule older than `fees.max_schedule_age_days` (30), one with no
`verified_at`, or an unverified one, while historical replay still costs it and
is labelled `RESEARCH USE ONLY`. `--readiness` derives every verdict from
observed state (§99): live is NOT READY because of the criteria it prints, not
because a literal says so — and enabling live is still out of scope.

Authoritative context: `README.md` (what works / what is stubbed),
`docs/ROADMAP.md` (phase map + defect register),
`docs/FEE_READINESS.md` (pair matrix, current fee evidence and operator steps),
`docs/SPEC.md` (every `§N` cited in the code), `docs/AGENT_KICKOFF.md`
(role, milestones, how to ask the owner for help).

---

## 2. Repository, branch, baseline

| Item | Value |
|---|---|
| Repo | `Steezyharsh1/Arb` (package name `prediction_arb`) |
| Working branch (fixed for this Arena session) | `arena/c1d8507b-arb` |
| Baseline commit | `e75abef44a97109bfabfd1651bc194313487acff` — merge commit for PR #10 (D18 settlement timing); this readiness-derivation session branched from it |
| Working tree at session start | clean (`git status --porcelain` empty) — VERIFIED 2026-10-10 before this session's edits; baseline `pytest` **396 passed** |
| Clone depth | **shallow (depth 1)**: `git log` shows a single commit. Run `git fetch --unshallow` if you need real history (e.g. to blame a regression) |
| Merged PRs to date | #1 Phase 0, #2 Phase 1, #3 Phases 2+3, #4 Phase 2/3 gaps + Phase 4, #5 Phase 5, #6 D16 baseline recovery, #7 deterministic settlement fixtures, #8 D17 mandatory-cost fail-closed repair, #9 fee-readiness review, #10 D18 settlement timing |
| CI | `.github/workflows/ci.yml`: install → `pytest` → 5 CLI smokes (status, readiness, collect, paper, historical) → honest-empty-replay grep → readiness side-effect guard → tracked-artifact guard |

---

## 3. Architecture, and the minimum path to evaluate a two-leg opportunity

Layer map and the security boundary: `docs/ARCHITECTURE.md`. Mode enforcement
inventory: `docs/MODE_SEPARATION.md`. Trading rules and the profit test:
`docs/TRADING_LOGIC.md`.

**The minimum path.** If you only read nine files, read these in this order —
this is the complete route from "a market exists" to "an opportunity is
scored", and every defect that has ever mattered lived on it:

1. `prediction_arb/main.py` — `main()` → `load_config()` → `build_adapters()`
   → mode engine. The only place `allow_order_placement=True` can be set
   (live only), and the only place the LLM matcher is constructed.
2. `prediction_arb/core/config.py` — mode/engine/database/env consistency
   (`validate_mode_consistency`, `load_mode_env`); refuses startup on any
   mismatch. `core/types.py` holds every dataclass/enum (`NormalizedContract`,
   `OrderBook`, `Opportunity`, …); money is always `Decimal`.
3. `prediction_arb/modes/paper/engine.py` — the reference cycle: discovery →
   eligibility filter → matching → books (streamed or polled, through
   `OrderBookAggregator` with a staleness gate) → scan → simulated execution.
   Collect/historical/live engines differ only in data provider and execution
   engine (`prediction_arb/modes/*`).
4. `prediction_arb/normalization/contracts.py` — `with_normalized_keys()`,
   `blocking_keys()`, `group_by_event()`. Candidate **generation** by blocking
   (§96): settlement 24h buckets ±1, category, threshold+operator+units,
   numeric title tokens, plural-folded content words, unioned with union-find.
   Recall-oriented on purpose; it never decides equivalence.
5. `prediction_arb/matching/deterministic_matcher.py` — the hard filter (§18).
   **A deterministic REJECT is final.**
6. `prediction_arb/matching/llm_matcher.py` + `matching/llm_client.py` — the
   semantic narrowing stage (§19, §88, §97). Transport is env-configured;
   malformed/outage ⇒ no match; a per-cycle comparison budget bounds spend.
7. `prediction_arb/matching/rules_validator.py` → `match_certificates.py` —
   `validate_match()` issues APPROVED **only** for a proven-INDEPENDENT pair
   (§33, defect D15); everything else is REVIEW/REJECTED.
8. `prediction_arb/modes/shared.py` — `run_matching()` then
   `run_arbitrage_scan()`; identical across modes (§45, §57).
   → `prediction_arb/arbitrage/optimizer.py::search_event()` evaluates only
   pairs holding an APPROVED certificate **and** `Independence.INDEPENDENT`.
9. `prediction_arb/arbitrage/two_leg.py::evaluate_two_leg()` — **the core.**
   `find_max_profit_quantity()` walks both ask ladders to the marginal unit,
   `orderbooks/orderbook.py::walk_buy()` produces the real consumed levels,
   `fees/calculator.py::trade_fee_fills()/settlement_fee()` can calculate
   supplied fill slices and raise `FeeDataError` when context is incomplete;
   all current active production fee schedules are incomplete, and a book walk
   is not an actual account fill/fee reconciliation. `normalization/settlement.py`
   costs the capital lockup to the last leg's evidenced *capital-release
   time* from `PRODUCTION_SETTLEMENT_TIMING` (each venue entry has meaning,
   status, sources, review date; UNKNOWN or passed ⇒ BLOCK, D18); the result
   is TRADE / WATCH / BLOCK with explicit `block_reasons`.
   Each mandatory cost is its own `core.types.CostComponent` inside a
   `CostLedger` (D17): a component that cannot be computed is `amount=None`
   with a reason, the ledger has **no total** while any component is missing,
   and `net_profit` / `return_on_capital` / `capital_required` /
   `total_fees` / `settlement_delay_cost` are then `None` — UNVERIFIED, never
   zero and never a positive figure. `gross_edge` (payout×q − walked cost)
   stays available as a pre-cost research figure and is not profit.

Execution after that point: paper → `execution/simulated.py` (holds **no**
adapter reference); live → `execution/live.py` + `execution/firewall.py`
(15 checks, all must pass). Today no production code constructs a
`FirewallContext` because live mode is monitor-only — only `tests/test_safety.py`
exercises the firewall (VERIFIED by grep 2026-10-09).

**Fee freshness and readiness (added 2026-10-10, §98–§100).**
`prediction_arb/fees/freshness.py` turns a schedule's `verified_at` into a
per-mode gate: `policy_for_mode()` gives LIVE and PAPER **MANDATORY**,
HISTORICAL **RESEARCH**, COLLECT **NOT_APPLICABLE**; `freshness_enforced(mode,
override)` fails closed for an unknown mode and lets an explicit override win
in both directions; under MANDATORY a stale, undated or unverified schedule
makes `FeeCalculator.schedule_for(..., strict_freshness=True)` raise
`FeeDataError("FEE_DATA_INCOMPLETE: … Re-verify before live execution")`, and
under RESEARCH the same schedule still prices the replay but the run is
labelled `RESEARCH USE ONLY: fee schedule(s) older than …`.
`prediction_arb/monitoring/readiness.py::evaluate_all()` derives every
criterion from observed adapter, provenance, recorded-data, fee, settlement,
compliance and risk state; a mode is `ready` only when no criterion is
`BLOCKED`, and each unmet criterion carries the reason. `--readiness` prints
that report and enables nothing (no files, no directories, no orders).

**Fastest offline way to watch the whole path run:**
`pytest tests/test_candidate_generation.py::TestPipelineEmitsCandidates -q`
(differently-phrased cross-venue pair → candidate → deterministic pass →
APPROVED certificate → evaluated opportunity, with a stub LLM).

---

## 4. Safety and compliance invariants that must never be weakened

The full list with rationale is `docs/ROADMAP.md` §5 "Invariants that survive
every phase" and `docs/AGENT_KICKOFF.md` "HARD SAFETY INVARIANTS". The short
form — if a change touches any of these, stop and re-read those sections:

1. **No real order, ever, in this engagement.** Do not set
   `allow_live_orders`, do not construct an adapter with
   `allow_order_placement=True` outside live mode, do not weaken
   `execution/firewall.py`, do not remove the typed phrase
   `I UNDERSTAND LIVE TRADING`.
2. **Mode isolation is architectural**, not a flag: simulation engines hold no
   adapter reference; per-mode DBs/logs/reports/env files; `mode` column on
   every row; `validate_mode_consistency` refuses mismatches.
3. **UNKNOWN never means yes** — independence, eligibility, fees, settlement
   lockup, book freshness. Missing data BLOCKS and is never defaulted to zero.
4. **Never fabricate data.** Stubs raise `NotImplementedError` with
   instructions; historical mode never falls back to live data; reconstruction
   that cannot be done faithfully returns `None` and is counted.
5. **Every external claim carries** VERIFIED / UNVERIFIED / UNAVAILABLE /
   UNKNOWN + source URL + date, and distinguishes *documented* from
   *live-tested* (`docs/VENUE_RESEARCH.md` is the model).
6. **Deterministic filters are final**; an LLM may narrow, never widen, and
   never authorises a trade.
7. **No scraping, geo-bypass, rate-limit circumvention, or ToS violations**
   (`compliance/restrictions.py`); budgets stay below published limits.
8. **Secrets** are never logged, never committed, never requested in chat
   (`core/logging.py` redaction filter; `.env.*` is git-ignored).
9. **Provenance discipline:** a frontend routing into an existing exchange is
   `SAME_VENUE` (e.g. Kalshi via Webull/IBKR), not a new venue.

Compliance posture (Utah/US-UT, sports RESTRICTED, non-sports UNKNOWN and
live-blocking): `docs/COMPLIANCE.md`, evidence rows R1–R9 in
`docs/VENUE_RESEARCH.md`. Nothing in this repository is legal advice.

---

## 5. Operating modes and their current restrictions

| Mode | Command | What it does today | Restriction |
|---|---|---|---|
| `collect` | `--mode collect` | Records public Kalshi markets + books into `data/paper/*.sqlite3` (tagged `mode=collect`) | **Skips** any venue whose `APIStatus` is `API_DOCUMENTED_UNTESTED` / `NO_API_AVAILABLE` / `SAME_VENUE_ROUTE` — so Polymarket US is *not* recorded until its status is promoted (`modes/collect/engine.py`) |
| `paper` (default) | `--mode paper` | Full pipeline, simulated execution, real data | Needs venue reachability; with no LLM configured nothing is ever APPROVED, so no opportunity is evaluated |
| `historical` | `--mode historical --date YYYY-MM-DD` | Read-only replay of the recorded store (SQLite `mode=ro`) into its own DB + metrics | Refuses to invent data; a missing store is reported, never created |
| `live` | `--mode live` | **Monitor-only by construction** — requires the typed confirmation phrase, then reports that fewer than two live-capable independent venues exist and does not scan to execute | No flag bypasses the phrase; kill switch blocks live entirely |
| utility | `--status`, `--readiness`, `--kill`, `--unkill` | Venue/provenance table, per-mode derived readiness report (COLLECT / HISTORICAL / PAPER / LIVE, each criterion PASS / WARN / BLOCKED with its reason), persistent kill switch | Advisory only: the report enables nothing. Live stays NOT READY because the *derived* criteria fail (1 live-capable venue, 0/3 complete fee schedules, UNKNOWN settlement, no `.env.live`), not because a literal `False` is printed — D10 resolved |

---

## 6. Verified test results and environment limitations (2026-10-10)

Environment: Debian sandbox, **Python 3.11.2**, fresh `.venv`,
`pip install -e ".[dev]"` succeeded. `requirements.txt` pins no upper bounds,
so this run used pytest 9.1.1, pytest-asyncio 1.4.0, httpx 0.28.1,
pydantic 2.14.0, SQLAlchemy 2.1.4, websockets 17.2, rich 15.0.0,
cryptography 50.0.2, PyYAML 6.0.3, python-dotenv 1.2.4 (re-read from
`pip list` 2026-10-10 — identical to the 2026-10-09 session).

| Check (exact command) | Result / recency |
|---|---|
| `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` | **Succeeded 2026-10-10**; Python 3.11.2, including pytest 9.1.1, PyYAML 6.0.3, Rich 15.0.0 and project runtime dependencies |
| `.venv/bin/pytest -o addopts=""` | **534 collected, 534 passed in ~26 s**, re-run after the readiness changes on 2026-10-10 (baseline at `e75abef` before edits: **396 passed**; `tests/test_readiness.py` adds 138). All offline. Note: the project's `addopts = "-q"` plus `-q` on the command line suppresses the summary line; use `-o addopts=""` to see the count |
| `.venv/bin/pytest --collect-only -q` | **534 tests:** arbitrage_engine 9, candidate_generation 27, failure_modes 13, kalshi_phase2 22, kalshi_smoke_script 12, llm_client 20, paper_realism 14, paper_streaming 4, paths 23, phase4_recording 25, polymarket_fees 25, polymarket_us 41, readiness 138, safety 26, settlement_timing 44, spec_cases 13, unavailable_costs 35, units 43 |
| D18 baseline reproduction and mutation checks | **Run 2026-10-10.** In a worktree at `caa57c8`, a past-dated Kalshi/ForecastEx pair with synthetic complete fees returned `TRADE`, `settlement_delay_cost=0.0000`, `net_profit=7.3200`; same script on this branch → `BLOCK`, `None`. Eight mutations (Kalshi back to expected+0, past clamp, Polymarket US 0-day, ForecastEx 0-day, ignoring `evaluation_time` in the evaluator, half-even rounding, compounded cost, dropping it in `run_arbitrage_scan`) fail 6 / 5 / 3 / 2 / 1 / 1 / 12 / 1 tests respectively (counts for the first seven taken before the 44th test was added); source restored and diff-checked afterwards |
| Focused YAML load + `FeeCalculator.from_config` | **Succeeded 2026-10-10**. Kalshi, Polymarket US, ForecastEx, and IBKR/ForecastEx parse; all active trade and settlement statuses are `INCOMPLETE`; references are respectively `0.07/0.0175`, `0.0695/-0.0125`, and `$0.01` per executed side. Kalshi's source gives no global effective date, so its reference snapshot date is kept separate and calculations block |
| `.venv/bin/python -m prediction_arb.main --status` | **Exit 0, re-run 2026-10-10 after D18.** kalshi=VERIFIED; polymarket_us=API_DOCUMENTED_UNTESTED; forecast_ex=API_DOCUMENTED_UNTESTED. Matrix reports Kalshi↔Polymarket US, Kalshi↔ForecastEx, and Polymarket US↔ForecastEx all INDEPENDENT; kill switch disengaged |
| D17 offline reproduction and mutation checks | **Inherited from the D17/PR #8 verification:** original positive-profit-on-BLOCK reproduced, zero-substitution mutation failed 20 tests, zero-lockup mutation failed 2 tests. The full current suite above re-runs all D17 regressions; standalone mutation checks were not repeated in this fee review |
| `.venv/bin/python -m prediction_arb.main --readiness` | **Exit 0, re-run 2026-10-10 after the readiness work.** Header names the sources (§58, §98–§99); policy line `live=MANDATORY, paper=MANDATORY, historical=RESEARCH, collect=NOT_APPLICABLE`; thresholds read from `config/settings.yaml → readiness`. Blocks: `COLLECT: READY`, `HISTORICAL: NOT READY — 1 blocker(s)` (no recorded store), `PAPER (this run's mode): NOT READY — 5 blocker(s)`, `LIVE: NOT READY — 8 blocker(s)`, then `LIVE MODE: NOT RECOMMENDED YET` and the advisory footer. LIVE blockers: live-capable venues 1/2, no independent pair, matcher unconfigured, fee completeness 0/3, settlement UNKNOWN (polymarket_us, forecast_ex), recorded evidence 0/1000 + 0/50, eligibility UNKNOWN ×3, no `.env.live`. LIVE passes: fee freshness 3/3 (0 days old), book-staleness gates, kill switch disengaged, 12 risk limits configured. the run leaves the runtime tree unchanged (asserted in `tests/test_readiness.py`, and CI compares `data/ logs/ reports/` before and after — the suite itself leaves an empty `data/paper/`, see §7c) |
| `.venv/bin/python -m prediction_arb.main --mode historical --date 2020-01-01` | **Exit 0, re-run 2026-10-10 after D18:** honestly empty ("No recorded historical order-book data … will NOT fabricate data"); `data/`, `logs/`, `reports/` deleted afterwards |
| Paper CLI smoke, tracked-artifact guard, and venue smoke scripts | **Inherited from the D17 handoff, not re-run in the fee or D18 reviews.** Prior recorded results: empty fresh-clone data store; venue API TLS EOF; see date-specific rows in `docs/ROADMAP.md` / `docs/VENUE_RESEARCH.md` |
| Frozen-wall-clock sweep (2026-10-14 / 2027-10-09 / 2031-01-02) | **Re-run 2026-10-10 for this session: 534 passed at each of the three dates** (and at the real clock). The fee clock was frozen by patching only `prediction_arb.fees.calculator.utcnow` and `prediction_arb.fees.freshness.utcnow` via a throwaway pytest plugin; this is what exposed the fixture date bomb fixed below |
| Readiness mutation checks | **Run 2026-10-10, all eight caught, source restored and diff-checked afterwards.** `freshness_enforced` always False → 9 failures; PAPER policy downgraded to RESEARCH → 9; `ModeReadiness.ready` hardcoded `False` → 7; `order_types_verified` ignored → 3; readiness probe creating the recorded store → 2; historical engine dropping the research label → 1; fee-freshness criterion always passing → 5; a stale RESEARCH schedule silently tolerated → 1 |
| GitHub Actions CI | **Not run for the D18 working tree.** Inherited PR #8 result: `pull_request` runs `38012787059` and `38012962141` passed; not a CI run for this fee-review working tree. Baseline `796abec` was green as run `38009643994` |

**Environment limitations a fresh agent must assume:**

- **No venue is reachable from this sandbox.** Kalshi, Polymarket US (and by
  extension IBKR) terminate TLS with EOF. Every claim requiring a live venue
  call must be written as a script for the owner to run, never asserted.
  Outbound access is limited to `github.com`, `api.github.com`,
  `codeload.github.com`, `registry.npmjs.org`, `pypi.org`,
  `files.pythonhosted.org`.
- **No credentials exist here.** There is no `.env.paper`/`.env.live`; no
  Kalshi key, no Polymarket key, no LLM key. The LLM-less path is what you
  will always observe locally: every ambiguous pair ends in REVIEW.
- **The databases start empty.** `data/`, `logs/`, `reports/` are git-ignored
  and created on demand. Note that running `--readiness` (or any paper
  command) *creates* `data/paper/prediction_arb_paper.sqlite3`, after which a
  later historical run prints "Recorded store (read-only): …" even though it
  holds zero rows. Delete `data/` to reproduce a true fresh clone.
- The test suite is fully offline (mock HTTP transports + a scripted local
  WebSocket server). Keep it that way: a test that needs the network is a test
  that cannot gate anything here.

---

## 7. Known defects

### 7a. Reproduced in a recent session

| ID | Defect | Reproduction / evidence | State |
|---|---|---|---|
| **D18** | **Settlement-delay inputs had no meaning or evidence; passed and long-dated settlement could be costed as (near-)immediate** (inherited report: Polymarket US missing from `_EXPECTED_SETTLEMENT_DELAY_DAYS` — confirmed 2026-10-10) | The table held unsourced `kalshi: 0`, `forecast_ex: 0`, added to `settlement_time` (Kalshi's *expected* expiration, not a bound). A passed time clamped to zero lockup: past-dated pair with synthetic complete fees → `TRADE`, delay cost `0.0000`, net `7.32` (worktree at `caa57c8`). Historical/backtest measured lockup from the wall clock. Days via `float` | **FIXED 2026-10-10** (production + tests): `PRODUCTION_SETTLEMENT_TIMING` evidence registry (meaning, basis, delay, status, sources, review date, gaps; UNKNOWN cannot carry an estimator). Kalshi DOCUMENTED per market (`latest_expiration_time + settlement_timer_seconds` from the payload; round-trips the store); Polymarket US present as explicit UNKNOWN; ForecastEx 0-day withdrawn → UNKNOWN. Passed release ⇒ UNKNOWN; `evaluation_time` threaded, replay uses the frame time; lockup reason always reported; Decimal days; cost rounds up. 44 tests, 8 mutation checks. Evidence table: `docs/TRADING_LOGIC.md` |
| **D17** | **A BLOCKed opportunity could expose a positive "verified" net profit** (inherited review, reproduced 2026-10-10). `evaluate_two_leg` caught `FeeDataError` and substituted `fees = Decimal("0")`, and substituted `delay_cost = Decimal("0")` for an unknown settlement lockup, so the profit test ran on fabricated free costs | Offline synthetic scan with a fee calculator missing one venue returned `status=BLOCK`, `total_fees=0`, `net_profit=+9.9630`, `return_on_capital=0.1107`; the dashboard printed `Net $9.96` beside `Fees $0.00`; `Repository.save_opportunity` stored `net_profit='9.9630'`, `fees='0'`. Only the *first* fee failure was reported, because one `FeeDataError` aborted the whole fee sum | **FIXED 2026-10-10** (production + tests): each mandatory cost is now a `core.types.CostComponent` in a `CostLedger` with no total while any component is missing; `net_profit`, `net_profit_per_contract`, `return_on_capital`, `total_fees`, `settlement_delay_cost` and `capital_required` become `None` (UNVERIFIED); components are evaluated independently so every failure is reported; `gross_edge`/`known_costs` stay available as pre-cost research figures; cost-of-capital is unavailable when fees are. Downstream: `optimizer.score()` refuses unpriced opportunities and `rank()` sorts them last, `Opportunity.is_executable` gates paper execution, metrics withhold a fee-drag ratio over a partially costed set, dashboard/CLI print `n/a`, `OpportunityRow` stores NULL + `unavailable_costs` (`SCHEMA_VERSION` 2 → 3, migration 003), paper/live reports count `opportunities_without_verified_net_profit`. 35 new tests, mutation-verified (20 fail on the fee mutation, 2 on the delay mutation). Fee engine untouched |
| **D16** | **The inherited review reported one expired settlement fixture.** The old `tests/conftest.py` date was `2026-09-15T20:00Z`; after it passed, `capital_lockup_days()` correctly clamped lockup to zero, so `TestSettlementTiming::test_lockup_and_cost` failed and other tests silently stopped charging delay cost. Production behavior was correct — an already-settled contract locks no capital | Prior handoff recorded `pytest` → `310 passed, 1 failed`. In this session the reported test passed at the baseline commit; an explicit calculation with settlement `2026-09-15T20:00Z` and test clock `2026-10-09T00:00Z` returned `0.0`, confirming the cause | **FIXED / RE-VERIFIED 2026-10-09** (tests only): replaced the wall-clock-relative default with frozen `SETTLEMENT_TEST_NOW` and a default settlement three days later; `test_lockup_and_cost` pins the exact 3-day lockup and `$0.0411` cost. Found and fixed the same silent failure in the candidate-generation scan: its expired 2026-09 fixture had produced zero delay cost without failing; it now settles after the frozen test clock and asserts positive cost. Four settlement-timing tests and 27 candidate tests pass; full suite **313 passed in 25.01 s** and at each of 2026-10-14 / 2027-10-09 / 2031-01-02 under a frozen wall clock. Mutation-verified: restoring either stale fixture makes its regression test fail; last-leg and past-settlement clamp tests still pin the intended arithmetic. No production code changed. *Superseded in part by D18 (2026-10-10): the zero clamp itself was replaced — a passed release time is now UNKNOWN and blocks* |
| **D10** | Live mode is monitor-only and `--readiness` hardcoded the two-venue check | `main.py::cmd_readiness` contained the literal `False`; `--readiness` printed "NOT YET: Two live-capable independent venues = 1"; the probe also *created* `data/paper/` as a side effect | **FIXED 2026-10-10** (production + tests): `monitoring/readiness.py` derives every criterion (live-capable venues, venue independence, matcher, fee completeness *and* freshness, settlement timing, recorded evidence, eligibility, live credentials, kill switch, risk limits, book-staleness gates); a mode is ready iff nothing is BLOCKED, and `TestModeVerdictsAreComputed` proves a synthetic all-pass input yields `live.ready is True`, so no verdict is a constant. The recorded-store probe opens read-only and never creates a directory (mutation-checked, and CI asserts `data/` is absent after `--readiness`). Live remains monitor-only by construction and is still NOT READY on real state |
| **D19** | **Mandatory fee freshness was documented but never enforced** — `fees.max_schedule_age_days: 30` was dead config | `strict_fee_freshness` existed as a parameter but every caller defaulted it to `False` and no mode passed `True` (re-confirmed 2026-10-10 by grep and by reading `modes/shared.py`, `arbitrage/two_leg.py`, `execution/simulated.py`); a schedule verified years ago, or with no `verified_at` at all, priced live/paper decisions identically to one verified yesterday, and `--readiness` reported freshness as a hardcoded pass | **FIXED 2026-10-10** (production + tests): one policy rule in `fees/freshness.py` (`policy_for_mode`, `freshness_enforced`) drives `schedule_for(..., strict_freshness=…)`; LIVE and PAPER are MANDATORY, HISTORICAL is RESEARCH (a stale schedule still prices the replay and the run is labelled `RESEARCH USE ONLY: …`), COLLECT takes no fee-derived decision. MANDATORY stale/undated/unverified ⇒ `FeeDataError("FEE_DATA_INCOMPLETE: … Re-verify before live execution")`; unknown modes fail closed. 138 tests in `tests/test_readiness.py` cover fresh / stale / exact-boundary / undated / missing / unverified schedules, each readiness gate passing and failing, and the CLI report |

### 7b. Inherited as FIXED — regression tests pass, but exit evidence is still owner-gated

These were fixed by earlier sessions. This session re-read each fix site and
confirmed the corresponding tests pass in the 313-test suite, **but did not
re-run the mutation checks** the original sessions describe, and none of them
can be validated against a real venue here. Details and evidence columns:
`docs/ROADMAP.md` §4 defect register.

| ID | Subject | Where it lives now |
|---|---|---|
| D1, D2, D8 | Packaging, repo hygiene, reconstructed spec | `pyproject.toml`, `.gitignore`, `docs/SPEC.md` |
| D3, D4 | Recorded store readable by replay; full contract persisted | `database/repository.py` (`read_only`), `database/contract_codec.py`, migration `002` |
| D5, D14 | Kalshi hosts/failover/streaming; rate-limit headroom actually read | `adapters/kalshi*.py`, `main.py::maybe_calibrate_kalshi_rate_limits` |
| D6 | Cross-venue candidate generation by blocking (§96) | `normalization/contracts.py` |
| D9 | LLM matcher wired from the mode env file (§97) | `main.py`, `matching/llm_client.py` |
| D11, D12 | Paper book-freshness gate; fill probability actually applied | `modes/paper/engine.py`, `execution/simulated.py` |
| D13 | Fee arithmetic accepts supplied per-fill slices; current production schedules remain context-incomplete, and an aggregated book walk is not account fee reconciliation | `fees/calculator.py::trade_fee_fills`, `arbitrage/two_leg.py`, `docs/FEE_READINESS.md` |
| D15 | Only proven-INDEPENDENT pairs may be APPROVED (§33) | `matching/rules_validator.py` |
| D7 | Venue research refreshed | `docs/VENUE_RESEARCH.md` |

### 7c. Observations from this session — NOT yet registered defects

Recorded here so they are not rediscovered, deliberately **not** added to the
ROADMAP register because their impact needs an owner/agent decision first:

- ~~**`strict_fee_freshness` is not enabled end-to-end.**~~ **Resolved
  2026-10-10 as D19**: the flag is now derived from the mode by
  `fees/freshness.py` and passed by `modes/shared.py`, `arbitrage/two_leg.py`
  and `execution/simulated.py`; `fees.max_schedule_age_days: 30` is an active
  gate for LIVE and PAPER. See `docs/FEE_READINESS.md` "System-level gates".
- **Verification dates now differ by route — and strict checking IS enabled.**
  Kalshi, Polymarket US, ForecastEx, and IBKR/ForecastEx were directly
  re-reviewed and dated `2026-10-10`; inactive/unreviewed schedules retain
  `2026-09-13` and exceed the configured 30-day age window after 2026-10-13.
  Only schedules reachable from an *enabled adapter* are checked, so those
  inactive routes do not block anything today, but a route promoted to an
  enabled venue must be re-verified first or it will raise
  `FEE_DATA_INCOMPLETE` in LIVE/PAPER. Current active calculation statuses
  remain `INCOMPLETE`.
- **Any test helper that computes "N days ago" must read the fee engine's
  clock.** `tests/conftest.py::fresh_verification_date()` and
  `tests/test_readiness.py::_fee_today()` both call
  `prediction_arb.fees.calculator.utcnow()`, not `core.clock.utcnow` — the
  modules import the clock by value, so mixing the two produced 7 / 72 / 70
  spurious failures in the frozen-clock sweep. Never put a fixed
  `verified_at` in a fixture reachable through `Mode.PAPER` or
  `PaperExecutionEngine`: PAPER is MANDATORY-strict now, so the fixture
  becomes a date bomb (this one broke 37 tests at a frozen 2026-10-14).
- **Two pre-existing test files create `data/paper/` in the repo root.**
  `tests/test_paths.py` and `tests/test_phase4_recording.py` resolve real
  per-mode paths, so a full `pytest` run leaves an empty `data/paper/` behind
  (git-ignored, no files, harmless). Consequence for CI and for any local
  check: you cannot assert "the working copy has no `data/`" *after* the
  suite — the readiness smoke therefore compares the runtime tree before and
  after the command it is testing. An earlier version of that guard asserted
  absence and failed for this reason alone (run `38021724449`).
- **Cosmetic, pre-existing, deliberately not fixed:** `modes/paper/engine.py`
  prints `f"[cycle {cycle}] no active-venue markets"` and Rich swallows
  `[cycle 1]` as markup, so the operator sees ` no active-venue markets`.
  Out of scope here; the new `print_block_reasons()` avoids it by
  `escape()`-ing its label and reasons and printing with `soft_wrap=True`.
- **Settlement fixtures are now tied to a deterministic test clock.**
  `tests/conftest.py` freezes only `normalization.settlement.utcnow` at
  `SETTLEMENT_TEST_NOW` and places its default settlement three days later;
  `tests/test_candidate_generation.py` uses a settlement one day later and
  asserts the end-to-end scan charges a positive delay cost. The absolute
  2026-09-25 instant in `tests/test_phase4_recording.py` is persistence/replay
  data only (no time-sensitive lockup path). The whole suite also passes with
  the wall clock frozen at 2026-10-14 / 2027-10-09 / 2031-01-02. New timing
  tests should keep using an explicit `now=` or a frozen settlement clock.
- **Tests run against a TEST-ONLY ForecastEx settlement-timing entry (D18).**
  The autouse `synthetic_settlement_timing` fixture in `tests/conftest.py`
  swaps `normalization.settlement._ACTIVE_TIMING` for the production registry
  plus a labelled synthetic ForecastEx entry, the same way engine tests use
  synthetic fee schedules; Kalshi fixtures carry synthetic
  `latest_expiration_time`/`settlement_timer_seconds=0` payload fields. Use
  the `production_settlement_timing` fixture or
  `sources=PRODUCTION_SETTLEMENT_TIMING` when a test is about production
  evidence.
- **Kalshi's lockup bound is conservative and may be long.** It uses the
  documented *latest* expiration, which can be far later than the expected
  one; that lengthens lockup cost and the leg spread (5-day limit), so some
  pairs will block or lose margin by design. Disputed/amended markets can
  still settle later than the bound (not modelled). Observed payload values
  (K9) have not been checked.

---

## 8. Current critical path (ordered by dependency, then impact)

0. ~~**Stop a BLOCKed opportunity from advertising a positive net profit
   (D17).**~~ **DONE 2026-10-10** — unavailable mandatory costs are typed as
   unavailable, net profit is `None`/`n/a`, and the calculation/reporting
   layers are regression-tested. The inherited baseline was **348 passed in
   26.29 s**; the fee-readiness change adds four focused regressions (current
   full count: **352 passed in 28.29 s**, see §12).
0b. ~~**Make settlement timing evidence-backed (D18).**~~ **DONE 2026-10-10**
   — Kalshi DOCUMENTED per-market bound; Polymarket US and ForecastEx
   explicit UNKNOWN (still blocking); passed/long-dated timing no longer
   costed as immediate; **396 passed in 25.99 s**.
0c. ~~**Derive readiness from verified state and enforce mandatory fee
   freshness (D10, D19).**~~ **DONE 2026-10-10** — one freshness policy per
   mode, every readiness criterion computed with an explicit reason, block
   reasons surfaced to the operator, live still NOT READY on real state;
   **534 passed** at the real clock and at three frozen future dates.
1. ~~**Restore a green baseline (D16).**~~ **DONE 2026-10-09** — 313 passed
   in 25.01 s with a fixed settlement clock and at three future wall-clock
   dates; stale fixtures fail mutation checks.
2. **Fee evidence and readiness (new audit, 2026-10-10).** All three enabled
   unordered pairs are independent, but 0/3 are end-to-end cost-evaluable.
   The production schedules for Kalshi, Polymarket US, and ForecastEx have
   incomplete trade and settlement components. Owner actions, direct sources,
   and exact missing account/market data are in `docs/FEE_READINESS.md`; do not
   promote a documented reference rate. Agent implementation/tests can follow
   once the required evidence is provided.
3. **Owner captures venue reachability evidence** — run
   `scripts/kalshi_smoke.py` and `scripts/polymarket_us_smoke.py` from a
   reachable machine. This changes K9/P7 from UNAVAILABLE only if observed,
   and can qualify Polymarket US for collection; it does not close fee or
   eligibility gates. *Owner (H4 step 2 / Phase 2 item 2).*
4. **Owner supplies authorized fee/account evidence** — complete the Kalshi
   endpoint/rounding and settlement steps, Polymarket fee-family/fill and
   settlement reports, and ForecastEx/IBKR account route and settlement
   evidence in `docs/FEE_READINESS.md`. No credentials or unredacted statements
   in chat or Git. *Owner/venue/broker; agent integrates after receipt.*
   The same settled-position records should show resolution time and the
   time the payout reached cash, which is the evidence Polymarket US and
   ForecastEx settlement timing needs (`docs/TRADING_LOGIC.md`, D18).
5. **Owner adds the Kalshi key (H2) and runs a 24 h `--mode collect`** —
   supplies real recorded data for Phase 4. *Owner.*
6. **Owner adds an LLM key (H7)** — without it no certificate is APPROVED,
   so real data cannot reach the arbitrage evaluator. *Owner.*
7. **Phase 5/6 exits on real two-venue data** — approved/reviewed candidate
   pairs, false-match review, and the multi-week paper realism run. *Agent,
   blocked on 3–6.*
8. **Phase 8 live readiness** — D10 (derived verdicts) and D19 (mandatory fee
   freshness) are done and the report names the real blockers; what remains is
   owner-gated evidence: a second live-capable venue, complete fee schedules,
   evidenced settlement timing, recorded volume, resolved legal/account
   eligibility, `.env.live`, and an explicit owner go-ahead. Not before
   Phases 2–7 evidence exists; live orders remain prohibited in this session.

Phase-by-phase detail, exit criteria and completion records: `docs/ROADMAP.md`
§2 and §3.

---

## 9. Outstanding external evidence, credentials, and operator actions

Beginner-friendly instructions for every item are in `docs/ROADMAP.md` §6
(H1–H7) and `docs/API_SETUP.md`. Never ask for a secret in chat; ask the owner
to place it in `.env.paper` and tell you it is done. Use the mandatory
request format in `docs/AGENT_KICKOFF.md` ("HOW TO ASK THE OWNER FOR HELP").

| Item | What it unblocks | Status |
|---|---|---|
| H2 — Kalshi API key in `.env.paper` | WebSocket streaming, bulk books, tier discovery, the 24 h collect run | OPEN |
| H4(2) — run `scripts/polymarket_us_smoke.py` | Observed-payload evidence (P7) → promote venue status → collect records venue #2 | OPEN |
| Phase 2(2) — run `scripts/kalshi_smoke.py` | Converts K9 to observed; confirms both hosts + book format | OPEN |
| H7 — LLM key in `.env.paper` | APPROVED certificates; the only way an opportunity is ever evaluated | OPEN |
| H3 — IBKR Client Portal Gateway | Only if the owner later selects the IBKR route (not the current route) | OPEN, not on the critical path |
| H5 — data-retention policy | Recorded data is ~17 MB/day; `.gitignore` keeps it out of git | OPEN (owner decision) |
| H6 — explicit go-ahead before anything live | Phase 8 | OPEN (and must stay closed in this engagement) |
| Fee/account/settlement evidence — Kalshi, Polymarket US, ForecastEx/IBKR | All active production fee components; exact owner actions in `docs/FEE_READINESS.md` | OPEN — no account statements/reports or live fee reconciliation |
| R9 — Utah/account eligibility for Polymarket US non-sports | Phase 3 gate 1; live eligibility generally | UNKNOWN — primary sources silent, secondary sources conflict |
| Settlement timing — Polymarket US and ForecastEx (D18) | Capital-lockup cost for every pair involving them; both are UNKNOWN and block | UNKNOWN — no published resolution bound or settlement delay (Polymarket US); exchange-level next-day schedule only, customer crediting undocumented (ForecastEx). Needs contract terms/API field or redacted settled-position records |
| P12 — Polymarket US customer settlement-fee treatment | Phase 3; mandatory cost remains blocked | UNVERIFIED — reviewed public material does not establish customer amount |
| SPEC §33 and §68 | Two reconstructed sections flagged `[I]` INFERRED | Need owner confirmation |
| Git history still contains a 17 MB SQLite blob | Repo size; needs a history rewrite | Owner decision (Phase 0 leftover) |

---

## 10. Deferred work — do NOT start these

- **Any live order, live-order enablement, or firewall/kill-switch/mode-gate
  weakening.** Forbidden outright.
- **New venue integrations** (Gemini, IBKR ForecastEx/CME, CDNA, Railbird/DKeX,
  Ludlow/Novig, Rothera, any frontend). The owner selected Polymarket US and
  the agreed sequencing is one venue at a time; venue count is not the binding
  constraint — evidence is.
- **Phase 9 v2 strategy surface**: `arbitrage/multi_leg.py`,
  `arbitrage/logical.py` (TYPE B), `arbitrage/graph.py` cycle search, fee/route
  optimisation. Stubs must stay honest stubs until Phase 6 evidence exists
  (§3 scope).
- **Phase 10 operations**: scheduler/daemon, email/webhook alert sinks, web
  dashboard, log rotation.
- **Wiring `HistoricalExecutionEngine`.** Deliberately unwired: replay measures
  opportunities, it does not simulate fills. Wiring it would imply execution
  evidence that does not exist.
- **Broad refactoring, renaming, reformatting, or renumbering `§` sections.**
  New spec requirements get new numbers (§98+); 224 citation sites depend on
  the existing numbering.
- **Committing recorded data, logs, reports, databases, `.env*`, or keys.**

---

## 11. Instructions for every future agent session

**A. Inspect (always, before touching anything)**

```bash
git status --porcelain && git log --oneline -5      # never discard unrelated working-tree changes
sed -n '1,80p' docs/AGENT_HANDOFF.md                # this file, then ROADMAP §1 and §4
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

**B. Test and record exact results (never paraphrase, never predict)**

```bash
pytest                                              # record collected/passed/failed + duration
python -m prediction_arb.main --status
python -m prediction_arb.main --readiness
python -m prediction_arb.main --mode historical --date 2020-01-01   # must stay honestly empty
python -m prediction_arb.main --mode paper --cycles 1               # offline: expect TLS EOF, no markets
python scripts/kalshi_smoke.py; python scripts/polymarket_us_smoke.py   # expect exit 1 in this sandbox
```

If a result differs from §6 of this file, that difference *is* the news: find
out why before doing anything else, and update §6 in the same change. Never
copy a prior session's numbers forward — re-run them. A command you could not
run is UNAVAILABLE, not a pass.

**C. Update this handoff — mandatory, in the same commit as your work**

Keep it current and keep it short. Specifically: §2 baseline commit, §6
verified results + date, §7 defects (move items between 7a/7b/7c as evidence
changes, never silently delete one), §8 critical path, §12 recent changes and
the single next task. Add new items with the same evidence discipline:
status + source + date. If you did not verify it, say so.

**D. Update `docs/ROADMAP.md` when — and only when — it is demonstrably stale**

The ROADMAP is the register of record for phases and defects. Correct a status
you *proved* wrong, add a defect you *reproduced* (next free ID; **D19 is
taken**, so the next free one is **D20**),
record a completion with its evidence. Do not redesign it, do not add
aspirational plans, do not mark anything DONE whose exit criterion is
owner-gated — say "code complete, exit owner-gated" instead. Also update
`README.md` ("what works / what is stubbed") and any doc whose claim your
change invalidated — same commit.

**E. Commit**

```bash
git add -A && git commit -m "<scope>: <what changed and why>"
git push origin <your session branch>   # e.g. arena/c1d8507b-arb for the readiness session
```

Small, reviewable, single-purpose commits. Never commit secrets, databases,
logs, reports, or `.pyc`. Branch discipline: work only on the branch your
session was given; never switch, create, or push another branch.

**F. Report back** — what changed (files), what you verified and how (command
+ exact output), what is still UNVERIFIED and what would verify it, test
counts, doc updates, and the next action. Milestone-end reporting format:
`docs/AGENT_KICKOFF.md` "REPORTING".

---

## 12. Most recent meaningful changes, and the recommended next task

**Most recent meaningful changes** (newest first):

00. **2026-10-10 — D10 + D19: readiness derived, mandatory fee freshness
   enforced.** `--readiness` no longer prints a hardcoded `False`: new
   `prediction_arb/monitoring/readiness.py` evaluates the gates that apply to
   each mode (COLLECT 3, HISTORICAL 6, PAPER 9, LIVE 12 — 13 distinct
   criteria) from observed adapter/provenance/recorded-data/fee/settlement/
   compliance/risk state, each with `PASS`/`WARN`/`BLOCKED`, a value, a reason, and a
   mode is ready only when nothing is BLOCKED (a synthetic all-pass input
   proves `live.ready is True`). New `prediction_arb/fees/freshness.py` makes
   `fees.max_schedule_age_days` a real gate: LIVE/PAPER MANDATORY (stale,
   undated or unverified ⇒ `FeeDataError`), HISTORICAL RESEARCH (prices the
   replay, labels it `RESEARCH USE ONLY: …` in the console, the run summary
   and the report), COLLECT NOT APPLICABLE. New
   `monitoring/dashboard.py::print_block_reasons()` tells the operator *why*
   candidates were rejected instead of only how many. Thresholds moved to
   `config/settings.yaml → readiness`. 138 new tests
   (`tests/test_readiness.py`), fixture date bomb fixed, CI gained a
   readiness smoke; **534 passed** at the real clock and at frozen
   2026-10-14 / 2027-10-09 / 2031-01-02; eight mutation checks all caught.
   Docs: SPEC §58 pointer + §98–§100, FEE_READINESS, TRADING_LOGIC,
   MODE_SEPARATION, TESTING, ROADMAP, README.
0. **2026-10-10 — D18: settlement timing made evidence-backed.** Confirmed
   the reported missing Polymarket US delay entry, and found the whole
   delay table unsupported (unsourced 0-day Kalshi/ForecastEx constants on
   Kalshi's *expected* expiration, passed times clamped to zero lockup,
   replay measured from the wall clock). Replaced it with
   `PRODUCTION_SETTLEMENT_TIMING`: Kalshi DOCUMENTED per market
   (`latest_expiration_time + settlement_timer_seconds`), Polymarket US and
   ForecastEx explicit UNKNOWN with sources and missing evidence recorded.
   No unknown delay became zero. `evaluate_two_leg(evaluation_time=…)`;
   historical/backtest pass frame time. 44 new tests
   (`tests/test_settlement_timing.py`), D16 clamp test replaced; **396
   passed in 25.99 s**; eight mutation checks. Docs: TRADING_LOGIC
   (evidence table), ROADMAP D18, SPEC §28 evidence, TESTING, README,
   FEE_READINESS note.
1. **2026-10-10 — fee readiness and pair audit.** Direct official sources
   re-reviewed; Polymarket US's 2026-10-07 schedule replaced the stale `0.06`
   reference, Kalshi's zero-settlement scope was narrowed to simple yes/no,
   and ForecastEx all-in/settlement completeness was withdrawn. Active
   production trade and settlement schedules are all `INCOMPLETE`; the
   three enabled pairs are independent but 0/3 are end-to-end cost-evaluable.
   Added `docs/FEE_READINESS.md`, updated fee config/tests and current venue,
   roadmap, trading, README, and handoff claims. Four focused regressions
   were added; **352 passed in 28.29 s** on Python 3.11.2, and `--status`
   exited 0. No account/live evidence was available; exact operator actions
   are documented in the fee-readiness page.
2. **2026-10-10 — D17: unavailable mandatory costs are no longer zeros.**
   `evaluate_two_leg` had allowed a BLOCK row to advertise `+$9.96` after
   substituting free fees and lockup. Net profit is now `None` whenever any
   mandatory cost is unavailable; typed `CostComponent`/`CostLedger` values,
   gross edge separation, dashboard/CLI `n/a`, SQL NULL + `unavailable_costs`,
   and daily report counts are regression-covered. Migration 003
   (`SCHEMA_VERSION` 3); the inherited baseline was **348 passed in 26.29 s**.
3. **2026-10-09 — D16 fixtures made repeatable and re-verified.** The
   settlement test clock is fixed, default and candidate fixtures settle in
   its future, and assertions require positive lockup/cost. The full suite was
   **313 passed in 25.01 s** and passed at three future frozen wall-clock
   dates; mutation checks make stale fixtures fail.
4. **2026-10-09 — original D16 repair and this handoff created.** Added
   `docs/AGENT_HANDOFF.md`, registered **D16**, and re-verified CI recency and
   sandbox venue reachability (both smoke scripts returned TLS EOF; K9/P7
   remain open).
5. **2026-09-14 — Phase 5 (PR #5).** D6 candidate blocking (§96), D9 LLM
   client wiring (§97) with a per-cycle budget, D15 provenance gate on
   certificates (§33). 47 new tests.
6. **2026-09-13 — Phase 2/3 gaps + Phase 4 (PR #4).** The former Polymarket
   standard fee arithmetic table and per-fill engine work, `scripts/kalshi_smoke.py`
   + D14, full-contract recording, read-only replay, backtest metrics, and
   paper realism work.

**Recommended next task (one):** the agent-side fee/source audit, the D18
settlement-timing review, the readiness derivation (D10/D19), and their
focused regressions are complete; request the redacted owner evidence listed in
`docs/FEE_READINESS.md` (plus the two read-only smoke-script outputs). This
sandbox cannot produce account route, actual fee-report, settlement-statement,
or venue API evidence. No production fee schedule should be promoted before
those records are reviewed and reconciled. Reachability commands:

```bash
python scripts/polymarket_us_smoke.py    # read-only, no key, no account
python scripts/kalshi_smoke.py           # read-only, no key needed
```

Both write sanitized evidence to `reports/evidence/` (git-ignored). Use the
mandatory beginner-friendly request format in `docs/AGENT_KICKOFF.md` ("HOW TO
ASK THE OWNER FOR HELP"), one task per message, and state the success
condition. Bundle the key requests (H2 Kalshi, H7 LLM) only *after* this
returns, so the owner is never handed five setup steps at once.

**Recommended next task — owner evidence for the selected Kalshi ↔ Polymarket US pair.** Polymarket US is the owner-selected second venue and the only enabled non-Kalshi route with an implemented adapter; its paper API status and both venues' production fee gates still block the pair. From a network-reachable machine, run the two read-only smoke scripts above, then follow the Kalshi steps 1–3 and Polymarket US steps 4–5 in `docs/FEE_READINESS.md` using existing account reports and settled positions. Save only redacted evidence under ignored `reports/evidence/`; never send credentials or create a new live order. If representative account evidence is unavailable, leave both schedules `INCOMPLETE` and record the specific blocker. Ask for the settled-position records to show both the market's resolution time and the time the payout reached cash: Polymarket US settlement timing stays `UNKNOWN` (and blocks the pair) until such evidence or a documented per-contract settlement date exists. The agent can integrate verified context and regression tests after evidence arrives. ForecastEx remains a separate stub route; do not treat it as a usable alternate pair yet.
