# AGENT HANDOFF — prediction_arb

**Read this file first, before any other document or any code.** It is the
permanent entry point for a fresh agent session with no memory of prior
conversations. It tells you what the project is, what is true *right now*,
what is broken, what you may work on, and what you must not touch.

- **Last updated:** 2026-10-10 (UTC) on the fixed Arena branch
  `arena/2ac1509e-arb` (base `86bd003ee00e9962f1cae219446a170471986230`).
  Arb.12 adds an 11-test offline synthetic path from paper-allowed contract
  eligibility through the production matching/certificate, fresh-book scan,
  walked fee/settlement evaluation, risk coordinator and simulated paper
  fills/recovery. Synthetic LLM, fee and ForecastEx timing inputs are explicitly
  test-only; no adapter, live placement method, or real opportunity is used.
  Exact coverage and boundaries are in §6, §7c and §12.
- **Previous update (D27, 2026-10-10, merge commit `86bd003`):**
  audited adapter evidence, public-only smoke posture, collection admission,
  order-book validity, paper data gating and readiness; recorded both latest
  live attempts as `UNAVAILABLE` (TLS EOF), without calling either adapter
  broken or verified. Current details are in §§5–9 and §7e.
- **Earlier update (Arb.10, 2026-10-10, branch `arena/dbb1a2ee-arb`):**
  audited all YAML configuration keys and the production call graph; removed
  ignored settings rather than presenting them as runtime protections; added
  the configuration/module audit in §7d and deferred prototypes to D26. No
  active risk behavior or live-order path was added.
- **Previous update (Arb.9, 2026-10-10, branch `arena/a5c3486d-arb`):**
  semantic-matching budget and deduplication — deterministic prioritization by
  independence/shared-keys/settlement-proximity, cache keys sensitive to
  contract versions + matcher/prompt/model versions, match_id includes
  versions and is sorted, REVIEW deduplication prevents per-cycle bloat, and
  budget-exhausted pairs remain eligible (not cached).
- **Arb.8 candidate retrieval (2026-10-10, branch `arena/732e107e-arb`, base
  `91d46df`):** cross-venue candidate generation (§96) is now pairwise,
  bounded and non-transitive. On the 1,000-contract benchmark it proposes
  **3,262 pairs instead of 333,296**, keeps **256/256** equivalent pairs the
  matcher can pass, and cuts the largest component from **1,000 to 51**. Recall
  on the 50-listing reference set is **15/16**, and the lost pair is a known
  paraphrase miss (§7c, owner decision §9 of `docs/CANDIDATE_RETRIEVAL.md`).
  Full design, numbers and lost matches: **`docs/CANDIDATE_RETRIEVAL.md`**.
  New defect **D25** (operator/units vocabulary) is recorded, not fixed.
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

**Immediate objective.** Establish exactly which read-only public market-data
surfaces have evidence in this environment, then resume the longer goal of
getting two genuinely independent venues to produce real, costed paper
candidates. No real money is traded in this engagement under any
circumstances. On 2026-10-10, the latest bounded, credential-free smoke probes
for Kalshi and Polymarket US both ended before HTTP response with TLS EOF and
are recorded `UNAVAILABLE`. Kalshi remains `MARKET_DATA_OBSERVED` only because
a historical public collection on 2026-08-30 captured 500 markets and 40
non-empty books; this is not full verification of the current adapter. No
Polymarket US market payload is observed, so it remains
`API_DOCUMENTED_UNTESTED`; ForecastEx remains a stub. No current adapter is
fully verified. Collection may admit only public-data evidence, records only
usable two-sided books, and never executes; paper WebSocket streaming requires
full verification. The local smoke failure does not mark an adapter broken.

The fee audit remains unchanged: three enabled venue pairs are
provenance-independent, but **0/3 are end-to-end cost-evaluable**; all active
trade and settlement schedules are `INCOMPLETE`. Do not turn documented rates
into estimates. Settlement timing (capital lockup) is evidenced only for
Kalshi (DOCUMENTED per-market bound); Polymarket US and ForecastEx are
`UNKNOWN` and block (D18). Fee-schedule freshness is an enforced gate (D19,
§98). `--readiness` derives every verdict from observed state (§99); live is
NOT READY for computed reasons, and enabling live remains out of scope.

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
| Working branch (fixed for this Arena session) | `arena/2ac1509e-arb` |
| Baseline commit | `86bd003ee00e9962f1cae219446a170471986230` — Arena session base (D27 merge) |
| Working tree at continuation | clean; no pre-existing changes. Baseline suite re-run before edits: 632 passed in 28.86 s; Arb.12 result is recorded in §6. |
| Clone depth | **shallow (depth 1)**: `git log` shows a single commit. Run `git fetch --unshallow` if you need real history (e.g. to blame a regression) |
| Merged PRs to date | #1 Phase 0, #2 Phase 1, #3 Phases 2+3, #4 Phase 2/3 gaps + Phase 4, #5 Phase 5, #6 D16 baseline recovery, #7 deterministic settlement fixtures, #8 D17 mandatory-cost fail-closed repair, #9 fee-readiness review, #10 D18 settlement timing, #11 D10/D19 readiness + fee freshness |
| Arb.8 candidate-retrieval session | Branch `arena/732e107e-arb`, branched from `91d46df` (`main` HEAD). Baseline before its change: `.venv/bin/pytest -o addopts=""` **549 passed in 27.72 s**. After it: **582 passed in 27.75 s** (549 + 31 new in `tests/test_candidate_retrieval.py` + 2 new settlement/threshold regressions). Not pushed (session policy). |
| Arb.9 matching budget & dedup session | Branch `arena/a5c3486d-arb`, branched from `0b99106` (`main` HEAD). After: **597 passed in 29.23 s** (582 + 15 new in `tests/test_matching_arb9.py`). |
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
4. `prediction_arb/normalization/contracts.py` — `blocking_keys()`,
   `candidate_pairs()`, `groups_from_pairs()`. Candidate **proposal** (§96,
   Arb.8): a pair is proposed only if it is cross-venue, both settle within the
   ±6 h `SETTLEMENT_TOLERANCE` of each other, and it shares a `thr`/`num`/`tok`
   key that is not dense (≤ 20 holders settle within the window) at that
   moment. Nothing is transitive; components only group the scan. Recall-oriented
   on purpose; it never decides equivalence. Design, benchmark and lost matches:
   `docs/CANDIDATE_RETRIEVAL.md`.
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
adapter reference) → `execution/coordinator.py`; live → `execution/live.py` +
`execution/firewall.py` (15 checks, all must pass). Paper startup now requires
positive `paper_starting_cash` for every enabled venue (Kalshi $1,000,
Polymarket US $750, ForecastEx $750), and the simulator independently rejects
an unknown venue by name. The coordinator is the runtime risk boundary: it
re-checks executable/mode/kill state, checks and reserves all capital/position/
order limits, prospectively checks first-leg naked risk, and checks actual
exposure after each leg, before each retry/unwind, and after recovery. Failed
hedge + failed unwind persists exposure and engages the kill switch; a breach
still permits the in-progress risk-reducing recovery. Exact status/caveats for
all 12 limits: `docs/RISK_MANAGEMENT.md` (D20–D24). Today no production code
constructs a `FirewallContext` because live mode is monitor-only — only tests
exercise the firewall; live risk wiring remains D24 and no real order was
enabled.

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

**Fastest offline core-path check (Arb.12):**
`.venv/bin/pytest -o addopts='' tests/test_synthetic_opportunity_e2e.py::TestSyntheticOpportunityEndToEnd::test_approved_pair_walked_costs_and_paper_fills -q`
(differently phrased paper-research pair → production candidate/matcher →
APPROVED synthetic certificate → fresh walked books → cost ledger → risk
coordinator → `PaperExecutionEngine` fills). The entire new refusal/partial/failure
battery is `.venv/bin/pytest -o addopts='' tests/test_synthetic_opportunity_e2e.py`.
The earlier `tests/test_candidate_generation.py::TestPipelineEmitsCandidates`
remains the shorter matching-to-evaluation regression.

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
| `collect` | `--mode collect` | Records usable two-sided order-book snapshots to `data/paper/*.sqlite3` (tagged `mode=collect`); no execution engine/path | Admits only `MARKET_DATA_OBSERVED` or `VERIFIED`; current Kalshi admission is based on the historical 2026-08-30 public capture. Rejects invalid/crossed books and skips Polymarket US (`API_DOCUMENTED_UNTESTED`). No fresh live snapshot observed in this sandbox. |
| `paper` (default) | `--mode paper` | REST market discovery for adapters with usable market-data evidence; simulated pipeline and simulated execution only | WebSocket streaming still requires full `VERIFIED`; current public probes are UNAVAILABLE. With no LLM configured ambiguous pairs remain REVIEW, never auto-approved. |
| `historical` | `--mode historical --date YYYY-MM-DD` | Read-only replay of the recorded store (SQLite `mode=ro`) into its own DB + metrics | Refuses to invent data; a missing store is reported, never created |
| `live` | `--mode live` | **Monitor-only by construction** — requires the typed confirmation phrase and does not scan to execute | Requires full adapter verification/order support; current live-capable venue count is 0. No flag bypasses the phrase; kill switch blocks live entirely |
| utility | `--status`, `--readiness`, `--kill`, `--unkill` | Venue/provenance table, claim-level evidence table, per-mode derived readiness report, persistent kill switch | Advisory only: report enables nothing. Documentation, connectivity, usable market data and full adapter verification are distinct; latest smoke failures are UNAVAILABLE, not adapter failure. |

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
| `.venv/bin/pytest -o addopts=""` (Arb.8 branch, after the candidate-retrieval change) | **582 passed in 27.75 s**, 2026-10-10 on `arena/732e107e-arb`. `test_candidate_generation.py` 29 + `test_candidate_retrieval.py` 31 = 60 of those. |
| `.venv/bin/pytest -o addopts=""` (Arb.9 branch, after matching budget & dedup) | **597 passed in 29.23 s**, 2026-10-10 on `arena/a5c3486d-arb`. `test_matching_arb9.py` adds 15: deterministic ordering, budget exhaustion, cache invalidation, duplicate persistence, repeated cycles. |
| `.venv/bin/python -m benchmarks.candidate_retrieval.run --source reference --json …` and `--source synthetic --contracts 1000 --seed 20261010 [--skew 2.5]` | **Run 2026-10-10**, outputs committed under `benchmarks/candidate_retrieval/results/`. Synthetic 1,000: candidates 333,296 → 3,262; eq∩det-pass recall 1.0; largest component 1,000 → 51; median candidate-generation time 0.186 s → 0.040 s; peak heap 44.62 MB → 2.28 MB. Reference 50 listings: 36 candidates; eq∩det 15/16. Timings are sandbox-only and cover candidate generation, not matching. |
| Mutation checks on the retrieval code (2026-10-10, restored and verified with `diff`) | Settlement window 400 days → 10 failed. No density bound (`MAX_LOCAL_DENSITY = 10**9`) → 2 failed. Global per-key cap 40 → 1 failed. Transitive closure of proposed pairs (legacy grouping) → 4 failed. Two fixture defects were found by these checks and fixed before they were counted. |
| CLI smokes re-run after the change: `--status`, `--readiness`, `--mode historical --date 2020-01-01` | **Exit 0**, grep checks pass, 2026-10-10 on `arena/732e107e-arb`. The smokes created empty git-ignored `data/`, `logs/`, `reports/`; these were removed afterwards. |
| `benchmarks/candidate_retrieval/fallback_window.py` | **Run 2026-10-10.** A 60-min cross-venue fallback is not adopted. Reference: 15/16 → 16/16 at 36 → 42 pairs. Synthetic: 3,262 → 5,112 pairs (+57 %), det-pass 720 → 796, eq∩det unchanged 256/256. Output `results/fallback_60min.json`. |
| `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` | **Succeeded 2026-10-10**; Python 3.11.2, including pytest 9.1.1, PyYAML 6.0.3, Rich 15.0.0 and project runtime dependencies |
| `.venv/bin/pytest -o addopts=""` | D27 baseline: **632 passed in 28.86 s**, re-run 2026-10-10 before Arb.12 edits on `arena/2ac1509e-arb`; Python 3.11.2, all offline. |
| `.venv/bin/pytest -o addopts="" tests/test_synthetic_opportunity_e2e.py` | **11 passed in 0.28 s**, 2026-10-10; the candidate → certificate → fresh books → cost → risk → paper-execution core path plus mismatch, same-venue, unknown-cost, liquidity, staleness, capital, partial-fill and execution-failure refusals. All inputs synthetic and offline. |
| `.venv/bin/pytest -o addopts=""` | **643 collected, 643 passed in 28.04 s**, 2026-10-10 after Arb.12 on `arena/2ac1509e-arb`; Python 3.11.2, all offline. |

| `.venv/bin/pytest --collect-only -q` | **643 tests:** arbitrage_engine 10, candidate_generation 29, candidate_retrieval 31, collect_mode 8, config_audit 2, failure_modes 13, kalshi_phase2 23, kalshi_smoke_script 14, llm_client 20, matching_arb9 15, paper_realism 15, paper_streaming 5, paths 23, phase4_recording 27, polymarket_fees 25, polymarket_us 50, polymarket_us_smoke_script 5, readiness 140, risk_enforcement 16, safety 26, settlement_timing 44, spec_cases 13, **synthetic_opportunity_e2e 11**, unavailable_costs 35, units 43. |

| Paper risk/exposure fault injection | **16/16 passed 2026-10-10** in `tests/test_risk_enforcement.py` (15 existing lifecycle tests + Arb.10 LIMIT-request regression). Verifies explicit capital for all enabled venues and missing-capital refusal; all 12 configured names; seven pre-leg-A capital/state breaches with zero submissions; exact state update then a blocked second trade; aggregate/per-incident prospective exposure and aged exposure; successful partial-fill retry; failed hedge + failed unwind → retained exposure + persistent kill; timeout breach → kill while recovery remains allowed; simulated unwind really removes the position; coordinator requests are priced LIMIT; concession cap; recovery loss feeds the daily gate. `FaultEngine` is simulation-only and holds no adapter. Caveats D21–D24 are not promoted to enforced |
| D18 baseline reproduction and mutation checks | **Run 2026-10-10.** In a worktree at `caa57c8`, a past-dated Kalshi/ForecastEx pair with synthetic complete fees returned `TRADE`, `settlement_delay_cost=0.0000`, `net_profit=7.3200`; same script on this branch → `BLOCK`, `None`. Eight mutations (Kalshi back to expected+0, past clamp, Polymarket US 0-day, ForecastEx 0-day, ignoring `evaluation_time` in the evaluator, half-even rounding, compounded cost, dropping it in `run_arbitrage_scan`) fail 6 / 5 / 3 / 2 / 1 / 1 / 12 / 1 tests respectively (counts for the first seven taken before the 44th test was added); source restored and diff-checked afterwards |
| Focused YAML load + `FeeCalculator.from_config` | **Succeeded 2026-10-10**. Kalshi, Polymarket US, ForecastEx, and IBKR/ForecastEx parse; all active trade and settlement statuses are `INCOMPLETE`; references are respectively `0.07/0.0175`, `0.0695/-0.0125`, and `$0.01` per executed side. Kalshi's source gives no global effective date, so its reference snapshot date is kept separate and calculations block |
| `.venv/bin/python -m prediction_arb.main --status` | **Exit 0, re-run 2026-10-10 after D27.** kalshi=`MARKET_DATA_OBSERVED`; polymarket_us=`API_DOCUMENTED_UNTESTED`; forecast_ex=`API_DOCUMENTED_UNTESTED` (stub). Evidence table reports documentation/connectivity/data/full adapter/latest smoke separately; latest Kalshi and Polymarket public probes are UNAVAILABLE, no venue is fully verified, all three provenance pairs remain INDEPENDENT; kill switch disengaged. |

| D17 offline reproduction and mutation checks | **Inherited from the D17/PR #8 verification:** original positive-profit-on-BLOCK reproduced, zero-substitution mutation failed 20 tests, zero-lockup mutation failed 2 tests. The full current suite above re-runs all D17 regressions; standalone mutation checks were not repeated in this fee review |
| `.venv/bin/python -m prediction_arb.main --readiness` | **Exit 0, re-run 2026-10-10 after D27.** `COLLECT: READY` (1 of 3 `MARKET_DATA_OBSERVED`/`VERIFIED`), `HISTORICAL: NOT READY — 1 blocker(s)`, `PAPER: NOT READY — 5 blocker(s)`, `LIVE: NOT READY — 8 blocker(s)`, `LIVE MODE: NOT RECOMMENDED YET`. Live blockers include 0 fully verified live-capable venues, no independent pair, no matcher, 0/3 complete fee schedules, UNKNOWN settlement for polymarket_us/forecast_ex, no recorded volume, eligibility UNKNOWN ×3, and no `.env.live`; fee freshness 3/3, staleness gates, kill switch and configuration-only risk fields pass. Report remains advisory and creates no runtime data. |

| `.venv/bin/python -m prediction_arb.main --status` and `--readiness` | Both re-run 2026-10-10 after D27, exit 0. Status distinguishes all evidence claims; readiness names 0 live-capable venues and 1 data-admitted venue. `--readiness` creates no store; the full suite's empty `data/paper/` was retained only as ignored test output. |

| `.venv/bin/python -m prediction_arb.main --mode historical --date 2020-01-01` | **Exit 0, re-run 2026-10-10 after D18:** honestly empty ("No recorded historical order-book data … will NOT fabricate data"); `data/`, `logs/`, `reports/` deleted afterwards |
| Public venue smoke scripts | Re-run 2026-10-10 with `--limit 1`; both exit 1 as `UNAVAILABLE`, no credentials/orders. Kalshi: TLS EOF before HTTP response on both configured hosts at 06:13:37.919175 UTC. Polymarket US: TLS EOF before HTTP response at `gateway.polymarket.us` at 06:13:41.226497 UTC. Neither is an adapter failure or verification; exact records K9/P7 in `docs/VENUE_RESEARCH.md`. |

| Frozen-wall-clock sweep (2026-10-14 / 2027-10-09 / 2031-01-02) | **INHERITED from the D10/D19 session: 534 passed at each of the three dates** (and at the real clock). The 15 D20 tests were run at the real 2026-10-10 clock, not in that inherited sweep. The fee clock was frozen by patching only `prediction_arb.fees.calculator.utcnow` and `prediction_arb.fees.freshness.utcnow` via a throwaway pytest plugin; this is what exposed the fixture date bomb fixed below |
| Readiness mutation checks | **Run 2026-10-10, all eight caught, source restored and diff-checked afterwards.** `freshness_enforced` always False → 9 failures; PAPER policy downgraded to RESEARCH → 9; `ModeReadiness.ready` hardcoded `False` → 7; `order_types_verified` ignored → 3; readiness probe creating the recorded store → 2; historical engine dropping the research label → 1; fee-freshness criterion always passing → 5; a stale RESEARCH schedule silently tolerated → 1 |
| GitHub Actions CI | **Not run for the Arb.10 working tree** (not pushed at verification time). The complete 601-test local suite and `--status`/`--readiness` smokes passed. Do not describe inherited CI runs as verification of this audit |

**Environment limitations a fresh agent must assume:**

- **Latest public attempts did not reach a venue HTTP response.** On
  2026-10-10, Kalshi and Polymarket US public probes terminated TLS with EOF
  (K9/P7); this describes those attempts only and does not establish an
  outage or adapter defect. Do not claim current connectivity or usable data
  without a successful response. Outbound access is limited to `github.com`,
  `api.github.com`, `codeload.github.com`, `registry.npmjs.org`, `pypi.org`,
  `files.pythonhosted.org`.
- **No credentials exist here.** There is no `.env.paper`/`.env.live`; no
  Kalshi key, no Polymarket key, no LLM key. The LLM-less path is what you
  will always observe locally: every ambiguous pair ends in REVIEW.
- **The databases start empty.** `data/`, `logs/`, `reports/` are git-ignored
  and created on demand. `--readiness` is side-effect free and does **not**
  create the recorded store (D10); a paper/collect command does create
  `data/paper/prediction_arb_paper.sqlite3`, after which a later historical run
  can see the store even when it has zero rows. Delete `data/` to reproduce a
  true fresh clone.
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
| **D20** | **Paper capital/risk was not connected end-to-end:** enabled Polymarket US had no paper allocation; exposure breach checks had no caller; coordinator lacked limits/state/kill checks; open-order state never changed; success-only/even-split accounting and simulated sells made recovery unsafe | Confirmed by source trace on 2026-10-10. `ExposureTracker.breaches` had zero callers; only `PaperEngine._maybe_execute` called seven state checks; the simulator used missing cash as zero and credited sells without reducing/validating holdings | **FIXED FOR PAPER 2026-10-10:** explicit venue-local cash + startup/order validation; single canonical risk YAML; coordinator precheck/reservation; exact venue costs; prospective and lifecycle exposure checkpoints; bounded retry; persistent kill on unresolved risk; FIFO simulated unwind. Fifteen new fault-injection/regression tests in `tests/test_risk_enforcement.py`; all simulated, no adapters. Full matrix: `docs/RISK_MANAGEMENT.md` |
| **D21** | **`max_daily_loss` lacks a complete realized-P&L source** | Coordinator can observe and post recovery concession/unwind loss, but paper has no settlement/position-close or UTC day-rollover lifecycle | **OPEN / EXPLICITLY PARTIAL.** The next trade is blocked for observed losses at the threshold. Do not claim settlement-day loss coverage until the ledger/reset dependency is implemented and tested |
| **D22** | **`max_emergency_loss` bounds retry concession, not unknown unwind loss/fees** | Current generic engine interface has no freshness-gated recovery quote/fee preflight; the executable bid and fee are observed only after submit | **OPEN / EXPLICITLY PARTIAL.** Over-budget chase is stopped; realized unwind loss is reported and posted; unresolved exposure kills new trades. Do not broaden the claim or auto-disable unwind without preflight/failure tests |
| **D23** | **Configured liquidity-ratio and execution-latency settings were not runtime gates** | Arb.10 confirmed no production comparison: the book walker is bounded by displayed depth, and the coordinator only records `time_between_legs_ms` | **RESOLVED AS CONFIG-HONESTY CLEANUP 2026-10-10, NOT FEATURE IMPLEMENTATION:** removed `minimum_liquidity_ratio` and both execution-latency keys from `settings.yaml`; neither policy is implemented. Re-add only after a defined ratio denominator, post-leg-A recovery semantics, and fault-injection tests. |
| **D24** | **Live risk and generic emergency behavior remain disconnected** | Live is monitor-only; no production `FirewallContext`; `EmergencyManager` is not constructed and does not cancel/flatten | **OPEN / INTENTIONAL SAFETY BLOCKER.** No real-order bypass exists because no live coordinator exists. Do not enable real orders or the disconnected emergency manager before demo fault/reconciliation tests |
| **D25** | **Threshold-operator vocabulary and units are not normalized across venues (candidate, not fixed)** | Reproduced 2026-10-10 with `prediction_arb/matching/deterministic_matcher.py` and the adapters: identical event, threshold `5.0`, equal settlement; Kalshi emits `threshold_operator = strike_type` (raw `greater`), Polymarket US emits `None` ⇒ `threshold_operator` check FAILS. With both `>` the pair passes. Units are `None` on both adapters, so the units check passes vacuously and a real units mismatch (for example a percent versus a point value) cannot be detected. Retrieval proposes such pairs and the deterministic matcher rejects them on vocabulary alone, so each rejection is a false negative, not a finding about the events. Not in the reference set, which uses normalized operators. Fix belongs to the adapters (a shared operator vocabulary, units populated from the market) and must not weaken the matcher. Next free ID after D24 — verified 2026-10-10. | OPEN — candidate defect (not fixed). |
| **D26** | **Orphaned capital and hedge helpers could be mistaken for production controls** | Arb.10 source trace: `risk/capital.py` and `execution/hedging.py` have no production callers. The former consumes caller-supplied balances/demand; the latter omits fees and an independence gate. `execution/emergency.py` also has no production constructor and does not cancel/flatten (D24) | **DEFERRED 2026-10-10.** Keep the helpers disconnected. First add account/capital provenance, validated venue/fee semantics, and fault tests for quote failure, no fill, cancel/flatten failure, and alert/kill-write failure. Roadmap Phase 8/9; no protection is implied. |
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
| D6 | Cross-venue candidate generation by blocking (§96). Revised 2026-10-10 (Arb.8): pairwise, bounded, non-transitive retrieval; see `docs/CANDIDATE_RETRIEVAL.md` | `normalization/contracts.py::candidate_pairs`, `modes/shared.py::run_matching` |
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
- **Recorded recall limitation from Arb.8 (not a registered defect, owner
  decision pending).** The reference paraphrase `KX-APPROP-OCT01-2026` ↔
  `federal-funds-law-sep30` is labelled equivalent and passes the deterministic
  matcher, but shares no content word or number, so no key proposes it. The pair
  is kept in the reference set as a known miss and pinned by a test. Recovery
  options, and the cost of the 60-minute settlement fallback that would recover
  it, are in `docs/CANDIDATE_RETRIEVAL.md` §9. The same document records
  `ref-cpi-mom-03-oct26` (labelled equivalent, settles 6 h 01 m apart) as a
  deterministic reject that no retrieval design can approve.
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
- **Arb.12 synthetic E2E scope (2026-10-10).**
  `tests/test_synthetic_opportunity_e2e.py` exercises `check_contract_eligibility`
  in PAPER (the synthetic non-sports contracts are UNKNOWN but research-allowed,
  not live-eligible), candidate generation + deterministic matching + a
  schema-validated scripted LLM + certificate/provenance gates,
  `order_book_problems` + `OrderBookAggregator` freshness, shared scan/optimizer,
  order-book walks, complete TEST-ONLY venue-specific trade/settlement fees,
  the test-only ForecastEx timing estimator plus production Kalshi timing,
  `evaluate_two_leg`, `ExecutionCoordinator` risk/exposure/recovery and
  `PaperExecutionEngine`. Eleven offline tests cover mismatch, SAME_VENUE,
  incomplete production costs, thin/stale books, risk/cash refusal, partial
  recovery and unresolved failure. No production adapter or live order method
  is constructed/called. `PaperEngine._cycle`/discovery, network/data adapters,
  DB persistence/reporting, real LLM/API, production-complete fees, real
  ForecastEx settlement timing, confirmed Utah/account eligibility, and any
  real fills or multi-week paper run remain untested. No missing seam required
  a production refactor: the shared stages, freshness aggregator, coordinator
  and simulator already compose via injectable inputs.

### 7d. Arb.10 configuration and runtime call-graph audit — VERIFIED 2026-10-10

Method: parsed all seven `config/*.yaml` files, exact-key searched the Python
source tree, then checked dynamic `.get()`/`from_config()` access and production
callers. The cleanup below removes only keys that the running code never read;
it does not change a decision. Regression coverage pins the fixed behaviors.

| Setting or module | Intended behavior / claim | Actual call sites | Runtime decision / status |
|---|---|---|---|
| `settings.arbitrage.guaranteed_only` (**removed**) | Make strategy eligibility configurable as guaranteed-only | `PaperEngine._cycle` → `run_arbitrage_scan` → `optimizer.search_event` → `evaluate_two_leg`; scan constructs only `TYPE_A_GUARANTEED`. TYPE B (`logical.py`) and N-leg (`multi_leg.py`) functions are stubs and not called | No config effect. Guaranteed TYPE A is the only production strategy; regression pins the opportunity type |
| `settings.compliance.block_unknown_venue_relationship` (**removed**) | Block unknown/same-venue pairs | `rules_validator.validate_match` approves only `Independence.INDEPENDENT`; `optimizer.search_event` repeats the check; `evaluate_two_leg` blocks other values | No config effect. Unknown is hard-blocked in the production arbitrage path, even if an approval tuple is supplied |
| `settings.compliance.block_unknown_status` (**removed**) | Block unknown jurisdiction/category eligibility | `PaperEngine._discover` passes jurisdiction + mode to `check_contract_eligibility`; it never passes this YAML key. The helper blocks UNKNOWN for `Mode.LIVE` and permits research in supported non-live modes, regardless of the legacy keyword. `LiveEngine` has no discovery/eligibility scan path, so this helper is not a live production gate | No YAML effect; helper policy is fixed in code and tested with both legacy keyword values. Do not imply live enforcement; D24 still blocks live scanning/execution |
| `settings.execution.preferred_order_type`, `allow_market_orders` (**removed**) | Prefer LIMIT / disable market orders | `ExecutionCoordinator._execute_locked` always creates priced `OrderType.LIMIT` requests; `LiveOrderFirewall.verify` rejects a missing limit price | No config effect. LIMIT-only behavior remains fixed and regression-tested |
| `settings.arbitrage.minimum_liquidity_ratio`, `arbitrage.maximum_execution_latency_ms`, `execution.max_execution_latency_ms` (**removed; D23**) | Minimum displayed-liquidity ratio / maximum execution latency | No production comparisons existed. `two_leg` sizes by walking available asks; coordinator only records `time_between_legs_ms` | No gate existed; keys were removed rather than pretending the gate was active. A ratio and latency policy remain **unimplemented**; define the denominator and post-leg-A recovery semantics before re-adding |
| `risk_limits.limits.max_capital_per_trade: 100` (**kept**) | Cap per-opportunity capital | `RiskLimits.from_config` → `ExecutionCoordinator._execute_locked` → `check_trade_against_limits` | **Enforced in paper before leg A.** Live does not use a production coordinator. The old mismatched `max_trade_capital` duplicate was removed in D20; do not confuse the names |
| `risk/exposure.py`, `risk/limits.py`, `risk/kill_switch.py` (**active**) | Track paper naked exposure, capital/position/order bounds, persistent stop | `PaperEngine` constructs `RiskLimits`, `RiskState`, `ExposureTracker`; coordinator checks/reserves before A, after legs, before recovery and after recovery; `main.py` owns manual kill/unkill | Affect paper order decisions. Historical/backtest use limits only for reporting/metrics; readiness's “configured” check is not live enforcement |
| `risk/capital.py` (**deferred, D26**) | Size against venue-local capital and offer transfer advice | No production import/caller; tests only. Functions use caller-supplied balances/demand; no account or simulator lifecycle feeds them | No runtime effect. Not integrated until verified balance inputs and capital lifecycle exist |
| `execution/hedging.py` (**deferred, D26**) | Choose a low-cost hedge/unwind after a partial fill | No production caller; `PartialFillHandler` uses its own bounded retry/unwind flow | Prototype only: price-only (no fees), no independence check, and `naked_side` is not used. Do not wire into recovery without semantic/failure tests |
| `execution/emergency.py` (**deferred, D24**) | Flatten/cancel and stop after an emergency | No production construction. `EmergencyManager.engage_kill_switch` writes the switch, reads positions and emits an alert | It does **not** cancel or flatten; switch-write or alert failures can propagate. Not an emergency recovery path |
| `execution/coordinator.py`, `partial_fills.py`, `simulated.py` (**active paper path**) | Execute only validated two-leg opportunities and recover bounded partials | `PaperEngine._maybe_execute` → `ExecutionCoordinator.execute` → risk precheck/reservation → leg A → exposure check → leg B → `PartialFillHandler` recovery through `PaperExecutionEngine` | Active for paper simulation only; no venue adapter in the simulator. D21/D22 remain partial limits; see `docs/RISK_MANAGEMENT.md` |
| `execution/live.py`, `firewall.py` (**not an active order route**) | Guard live order transmission | `LiveEngine.startup` may construct `LiveExecutionEngine` after typed confirmation and runs reconciliation; `LiveEngine.run` is monitor-only. No production `FirewallContext` is built and no production caller submits an order | No live scan/order decision path today. D24 remains a hard blocker; no live enablement |
| `arbitrage/graph.py`, `logical.py`, `multi_leg.py` (**deferred, Phase 9**) | Graph cycles, TYPE B logical arb, N-leg arb | `graph.two_leg_candidates` is test-only; no scanner imports the graph. `find_cycles`, logical arb, and multi-leg arb raise `NotImplementedError` | No runtime effect; left as explicit prototypes/stubs |
| Other YAML inventory (not selected this session) | `schema_version`, mode `data_dir`, `venues.*.adapter`, Kalshi `demo_rest_base*` look like metadata/routing settings | `schema_version` is not validated; `AppConfig` derives data paths from mode; `main.build_adapters` dispatches by hard-coded venue keys and does not read `adapter` or demo-base fields | No runtime effect. Keep on the config-schema backlog; do not assume configurable data roots, adapter swapping, or a demo route |
| Fee-ledger metadata | Preserve evidence and published-reference context | `FeeCalculator.from_config`/freshness/readiness consume schedules, statuses, rules and verification fields. The exact-token misses `reference_snapshot_date`, `published_settlement_scope` (and `simple_yes_no`, `scalar_subcent`, `execution_use`), `published_reference` (and its rate/formula/treatment subkeys), `volume_definition`, `accelerated_tier_placement`, and `route_scope` are human-evidence only; nested prose such as `fee_context.account_or_fill_state` / `clearing_route` is stored generically, not used to price a trade. `rebate_policy`/volume tiers are recorded, never applied as per-order discounts | Intentional evidence-vs-runtime split. Every enabled production schedule remains `INCOMPLETE`, so published rates are not used as execution estimates (see `docs/FEE_READINESS.md`) |

**Actual running call graph.** `main.async_main` selects `PaperEngine`,
`HistoricalEngine`, or `LiveEngine`. Paper performs discovery/eligibility →
`run_matching` (candidate pairs, deterministic filter, LLM, final certificate)
→ fresh books → `run_arbitrage_scan` → `optimizer.search_event` (APPROVED
certificate **and** INDEPENDENT required) → `evaluate_two_leg` (walked depth,
fees, settlement/capital cost) → `Opportunity.is_executable` → coordinator risk
reservation and simulated execution. Historical runs matching + the same scan
and persists a replay report; it does **not** execute. Live startup confirms and
reconciles, then `run()` prints monitor-only status; no production firewall
context/order loop exists. `docs/RISK_MANAGEMENT.md` and Phase 8/9 of
`docs/ROADMAP.md` record the corresponding safety boundary and deferrals.

The Arb.10 scope was deliberately limited to removing the inert settings above
and documenting the active/future boundaries. Remaining config and module
backlog is not silently considered implemented.

### 7e. Public adapter evidence, smoke posture and collection gates — 2026-10-10

| Adapter / surface | Current evidence | Consequence |
|---|---|---|
| Kalshi public REST | `MARKET_DATA_OBSERVED` from historical public collection (2026-08-30: 500 markets, 40 non-empty books); latest public-only probe at 2026-10-10 06:13:37.919175 UTC is `UNAVAILABLE` on both configured hosts with `ConnectError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)` before HTTP response | Read-only collection may admit it, but no current connectivity or current payload is established; not `VERIFIED`. Authenticated bulk/WS not live-verified. |
| Polymarket US public REST | Docs reviewed (2026-09-13); latest public-only probe at 2026-10-10 06:13:41.226497 UTC is `UNAVAILABLE` at `https://gateway.polymarket.us` with `ConnectionError: TLS/SSL connection has been closed (EOF) (_ssl.c:992)` before HTTP response | No usable live payload observed; stays `API_DOCUMENTED_UNTESTED`, collect/paper skip it; not `VERIFIED`. |
| ForecastEx | Adapter is a structured stub; route/API connectivity and usable market data not implemented | No collection admission or promotion. |
| Smoke scope | `scripts/kalshi_smoke.py --limit 1` and `scripts/polymarket_us_smoke.py --limit 1` are bounded public GET-only, do not load credentials, do not call account/authenticated/WS/order surfaces, and place no orders | `UNAVAILABLE` means the attempt could not reach an HTTP response, not adapter failure or verification. Successful public evidence is only a manual `MARKET_DATA_OBSERVED` candidate. |
| Offline behavior | Tests cover malformed/nonfinite payloads, market state, missing/invalid tick/minimum values, timestamps/staleness, codec round-trip, book invariants, collect admission/no execution, Kalshi sequence-gap recovery and REST fallback | Offline tests prove code behavior only, not live endpoints, account authorization, or full adapter verification. |

Collection now admits only `MARKET_DATA_OBSERVED`/`VERIFIED` adapters and saves
only usable two-sided, sorted, uncrossed finite books with positive sizes and
in-range prices. The collect engine has no execution path. Paper REST can use
observed data, but paper WebSocket streaming and live use still require full
adapter verification. Missing per-market tick/minimum rules remain unknown.
The complete updated evidence ledger is in `docs/VENUE_RESEARCH.md`; offline
test coverage is summarized in `docs/TESTING.md`. Roadmap defect D27 tracks
the repaired status conflation and collection/book-admission gaps.

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
0d. ~~**Connect paper capital, risk, exposure, partial-fill recovery and kill
   activation (D20).**~~ **DONE FOR PAPER 2026-10-10** — all enabled venues
   have explicit venue-local cash; all 12 configured limits have tested runtime
   behavior or an explicit partial defect; checks occur at order-lifecycle
   decision points. **Still open:** complete daily realized P&L (D21), a broad
   emergency-loss preflight cap (D22), any liquidity-ratio/latency gate
   (D23; its inert config keys were removed, but the feature remains absent),
   live coordinator/emergency behavior (D24), and validated capital/hedging
   helpers (D26). Do not resolve these by implying they are active.
0e. ~~**Reduce candidate-generation workload without a silent cap (Arb.8).**~~
   **DONE 2026-10-10 on `arena/732e107e-arb`** — pairwise, non-transitive,
   settlement-windowed retrieval with local density bounds. 333,296 → 3,262
   candidates on the 1,000-contract benchmark, eq∩det recall 256/256, largest
   component 1,000 → 51. One **owner decision** is open (not adopted):
   accept the reference paraphrase miss (15/16), or adopt the 60-minute settlement
   fallback (+57 % synthetic candidates for no synthetic recall gain, recovers
   the one paraphrase). Also open: D25, the threshold-operator vocabulary, which
   belongs to the adapters. Full record: `docs/CANDIDATE_RETRIEVAL.md`.
0f. **Arb.12 synthetic offline pipeline proof DONE 2026-10-10** — 11 tests
   drive a pair through actual matching/certification, fresh-book validation,
   walked/costed evaluation, paper risk and the simulator, with failure/refusal
   cases. Exact scope and gaps: §7c and `docs/TESTING.md`. **This does not
   change external evidence:** no LLM endpoint, real fees/books, confirmed
   eligibility, ForecastEx settlement evidence, live adapter, or multi-week
   paper run was tested.
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
3. **Fresh public payload evidence** — the 2026-10-10 bounded public smoke
   attempts for Kalshi and Polymarket US were `UNAVAILABLE` before HTTP
   response (K9/P7). If an authorized network later yields valid open,
   two-sided books and per-market rules, review the exact payload and consider
   manual `MARKET_DATA_OBSERVED` admission. A connection alone is insufficient;
   public smoke never establishes full adapter verification. No credential is
   needed for these probes. *Owner-side only if a reachable environment exists.*
4. **Owner supplies authorized fee/account evidence** — complete the Kalshi
   endpoint/rounding and settlement steps, Polymarket fee-family/fill and
   settlement reports, and ForecastEx/IBKR account route and settlement
   evidence in `docs/FEE_READINESS.md`. No credentials or unredacted statements
   in chat or Git. *Owner/venue/broker; agent integrates after receipt.*
   The same settled-position records should show resolution time and the
   time the payout reached cash, which is the evidence Polymarket US and
   ForecastEx settlement timing needs (`docs/TRADING_LOGIC.md`, D18).
5. **Owner runs a 24 h `--mode collect`** — supplies real recorded data for
   Phase 4. Public REST collection requires no key; a key is only optionally
   needed for the separately documented authenticated read-only bulk/WS
   surfaces. Do not request or provide credentials for the public smoke.
6. **Owner adds an LLM key (H7)** — without it no certificate is APPROVED,
   so real data cannot reach the arbitrage evaluator. *Owner.*
7. **Phase 5/6 exits on real two-venue data** — approved/reviewed candidate
   pairs, false-match review, and the multi-week paper realism run. *Agent,
   blocked on 3–6.*
8. **Phase 8 live readiness** — D10 (derived verdicts) and D19 (mandatory fee
   freshness) are done and the report names the real blockers. Full adapter
   verification currently supports 0 live-capable venues; remaining gates
   include at least two fully verified independent venues, complete fee
   schedules, evidenced settlement timing, recorded volume, resolved
   legal/account eligibility, `.env.live`, and explicit owner go-ahead. Not
   before Phases 2–7 evidence exists; live orders remain prohibited in this
   session.

Phase-by-phase detail, exit criteria and completion records: `docs/ROADMAP.md`
§2 and §3.

---

## 9. Outstanding external evidence, credentials, and operator actions

Beginner-friendly instructions for every item are in `docs/ROADMAP.md` §6
(H1–H7) and `docs/API_SETUP.md`. Never ask for a secret in chat. Do not ask
for trading credentials for the public smoke or public REST collection; a key
is only potentially needed for separately documented authenticated read-only
surfaces (bulk books/WS/tier), and must stay in the owner's local environment.
Use the mandatory request format in `docs/AGENT_KICKOFF.md` ("HOW TO ASK THE
OWNER FOR HELP").

| Item | What it unblocks | Status |
|---|---|---|
| H2 — Optional Kalshi API key in local `.env.paper` | Only separately documented authenticated read-only WebSocket, bulk-book, and tier surfaces; not required for public smoke or collection | OPEN / optional; do not request for this audit |
| H4(2) — Polymarket US public smoke | Latest 2026-10-10 result P7 is UNAVAILABLE (TLS EOF); a future usable payload may support manually reviewed `MARKET_DATA_OBSERVED`, not `VERIFIED` | UNAVAILABLE here; no credential needed |
| Phase 2(2) — Kalshi public smoke | Latest 2026-10-10 result K9 is UNAVAILABLE (TLS EOF on both configured hosts); historical market data still supports MARKET_DATA_OBSERVED only | UNAVAILABLE here; no credential needed |
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
  optimisation. `graph.two_leg_candidates()` is test-only and is not called by
  the active optimizer; keep all of these prototypes/stubs disconnected until
  Phase 6 evidence and the required regression batteries exist.
- **Unwired risk/execution prototypes (D24/D26):**
  `risk/capital.py` has no production caller and receives no account balances;
  `execution/hedging.py` prices supplied books only, without fees or a proven
  independent venue; `execution/emergency.py` only persists kill + reports
  positions and does not cancel or flatten. Do not connect any of these until
  their data inputs, venue/fee semantics, and all failure outcomes are tested.
  In particular, do not use the emergency or hedge helpers to hide the D21/D22
  gaps.
- **Live order path (D24):** `FirewallContext` has no production constructor,
  LiveEngine is monitor-only, and no running mode calls
  `submit_order_with_context`. Do not build this path or enable real orders in
  this session.
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
python -m prediction_arb.main --mode paper --cycles 1               # reaches public adapters; not an offline-only test. Latest smoke probes were UNAVAILABLE; never interpret a network failure as adapter failure
python scripts/kalshi_smoke.py --limit 1; python scripts/polymarket_us_smoke.py --limit 1  # bounded public-only; latest 2026-10-10 was UNAVAILABLE
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
you *proved* wrong, add a defect you *reproduced* (next free ID; **D26 is
taken**, so the next free one is **D27**),
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

000000. **2026-10-10 — Arb.12 offline synthetic opportunity validation
   (branch `arena/2ac1509e-arb`, base `86bd003`).** Added
   `tests/test_synthetic_opportunity_e2e.py` (11 tests): research-mode
   eligibility → candidate retrieval → deterministic match + schema-validated
   scripted LLM → APPROVED independent certificate → validated fresh books →
   production walk/evaluation + explicit synthetic per-venue trade/settlement
   fees + known test settlement timing → coordinator risk checks →
   `PaperExecutionEngine`. The successful path calculates fees `$0.58`,
   lockup cost `$0.0019`, capital `$4.58`, and net `$3.4181` for 8 synthetic
   contracts, then fills both simulated legs. Refusal/regression cases cover
   payout mismatch, SAME_VENUE even with forged approval, production
   incomplete fees/UNKNOWN ForecastEx timing (net profit stays `None`), thin
   and stale books, risk and venue-cash limits, partial recovery and unresolved
   failure/kill. Synthetic LLM/fees/timing/books are not external evidence;
   paper eligibility is UNKNOWN/research-only; no adapter or live placement
   method is constructed/called. `PaperEngine._cycle`, discovery/persistence,
   real accounts/fees/settlement/LLM, resolved legal eligibility, live paths,
   and multi-week paper remain untested. No production refactor was needed.
   Focused: **11 passed in 0.28 s**; full suite: **643 passed in 28.04 s**.
   Docs: `docs/TESTING.md`, `docs/ROADMAP.md`, and this handoff.
00000. **2026-10-10 — D27 adapter evidence and collection audit (branch
   `arena/e7d80655-arb`, base `e0f8e4a58ad2f5a50dc654088e45b208342df3c1`).**
   Separated documentation, connectivity, usable market data, full adapter
   verification and latest smoke claims in an explicit evidence record/status
   table. Kalshi is `MARKET_DATA_OBSERVED` from the historical 2026-08-30
   public capture only (500 markets, 40 non-empty books); Polymarket US remains
   `API_DOCUMENTED_UNTESTED`; ForecastEx remains a stub; no adapter is fully
   verified. Re-ran bounded public-only, no-credential/no-order probes:
   Kalshi returned `UNAVAILABLE` on both configured hosts at 06:13:37.919175
   UTC and Polymarket US returned `UNAVAILABLE` at gateway.polymarket.us at
   06:13:41.226497 UTC (TLS EOF before HTTP response); neither outcome is an
   adapter failure. Collection admits only observed/verified market-data
   status, validates usable books, and has no execution path; paper REST and
   WebSocket gates remain distinct. Added 31 evidence/collection/smoke tests
   or regressions; `.venv/bin/pytest -o addopts=""`: **632 passed in 28.75 s**.
   `--status` and `--readiness` exit 0; readiness shows 1 data-admitted and 0
   live-capable venues. Docs: SPEC §101, VENUE_RESEARCH K9/K11/P7, TESTING,
   ROADMAP D27, FEE_READINESS, MODE_SEPARATION, API_SETUP and README.
00000. **2026-10-10 — Arb.10 configuration/runtime audit (branch
   `arena/dbb1a2ee-arb`, base `219c8ba`).** Parsed all seven YAML files,
   searched the source key-by-key, and traced paper/historical/live risk,
   execution, and arbitrage routes. Removed eight ignored settings rather than
   implying runtime protections; behavior remains fixed in code. Confirmed
   `max_capital_per_trade` is enforced in paper, and documented the unimplemented
   liquidity-ratio/latency policies without reintroducing their dead keys.
   Deferred `risk/capital.py`, `execution/hedging.py`, and
   `execution/emergency.py` (D24/D26); corrected the graph prototype's false
   claim that production scanning consumes it. Added four regression tests plus
   an assertion for TYPE A output. **601 passed in 27.64 s**; `--status` and
   `--readiness` both exited 0. Audit table/backlog: §7d, `docs/ROADMAP.md`
   D23/D26, and `docs/RISK_MANAGEMENT.md`.
00000. **2026-10-10 — Arb.9 semantic-matching budget & deduplication (branch
   `arena/a5c3486d-arb`, base `0b99106`).** Verified current budget consumption:
   per-cycle counter reset in `run_matching`, cache hits never consume budget,
   budget-exhausted pairs return REVIEW with error and are deliberately NOT cached
   so they remain eligible. Candidate ordering was discovery-order (sorted index
   pairs). Replaced with deterministic prioritization by defensible pre-LLM
   evidence: venue independence (INDEPENDENT 100, SAME_VENUE -200), shared blocking
   keys (thr=20, num=5, tok=1), settlement proximity (smaller delta first), with
   lexical tie-breakers. No gross edge or unverified match used for ordering;
   deterministic REJECT remains final; semantic matching cannot approve a pair
   that fails independence. Fixed cache keys to include contract versions +
   matcher/prompt/model versions for correctness; fixed match_id to include
   contract versions + model version and to be sorted (deterministic regardless
   of input order), so version changes create new rows preserving history.
   Fixed persistence deduplication: `save_match_certificate` now skips writes
   when meaningful fields (status, score, independence, versions) are identical,
   preventing unbounded duplicate REVIEW records on repeated scans; meaningful
   changes (verdict, version) still update. Documented smallest viable migration
   for full verdict-change history (audit log table) in
   `docs/MATCHING_DEDUP_MIGRATION.md` rather than rewriting schema. Added 15
   tests in `tests/test_matching_arb9.py` covering deterministic ordering,
   budget exhaustion, cache invalidation, duplicate persistence, repeated cycles.
   Full suite: **597 passed in 29.23 s**.
0000. **2026-10-10 — Arb.8 candidate-retrieval reduction (branch
   `arena/732e107e-arb`, base `91d46df`).** Measured the inherited behaviour
   first. The giant component (1,000 contracts → 1 component) reproduces; the
   inherited 2,111 deterministic-pass figure does not, on the current generator
   (986; the source generator is not in the repo). Built a reproducible multi-venue
   benchmark (`benchmarks/candidate_retrieval/`) with a 50-listing hand-labelled
   reference set and exhaustive ground truth. Replaced the settlement-bucket
   union-find with pairwise retrieval: settlement as a ±6 h window tied to the
   matcher's own tolerance, `thr`/`num`/`tok` keys, a local density cap of 20 (a
   global cap was rejected), components used for grouping only. The deterministic
   matcher, its rules, and LLM authority are unchanged. Results and every lost
   match are in `docs/CANDIDATE_RETRIEVAL.md`; the lost reference pair is
   recorded in §7c. Owner decisions open: §9 of that document. Tests: 582 passed
   in 27.75 s. Defect D25 (operator vocabulary) recorded as a candidate, not fixed.
   Nothing pushed.
000. **2026-10-10 — D20 paper capital/risk enforcement.** Confirmed the
   inherited issues: enabled Polymarket US had no paper allocation;
   unhedged breach checks had no caller; state limits sat outside the
   coordinator and several state fields were never updated. Added the $750
   paper allocation and fail-loud startup/order validation, made
   `config/risk_limits.yaml` the single risk source, moved precheck/reservation
   and exact venue capital into the coordinator, connected prospective and
   post-fill exposure checks at every recovery decision, persisted a kill on
   unresolved exposure, and made simulated unwind actually reduce holdings.
   Fifteen offline fault-injection regressions cover every configured field,
   partial/failing hedge paths, kill activation, and recovery after a breach;
   **549 passed in 26.52 s**. D21–D24 explicitly retain the incomplete daily
   P&L source, broad emergency-loss preflight, liquidity/latency gates, and
   live/emergency wiring; none is implied active. Full matrix:
   `docs/RISK_MANAGEMENT.md`.
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

**Recommended next task (one):** D20's paper enforcement is complete; do not
wire the disconnected live `EmergencyManager` or enable orders to address
D21–D24. The agent-side fee/source audit, D18 settlement-timing review,
readiness derivation (D10/D19), and Arb.12 offline synthetic core-path proof
are complete. The latter does **not** make a production fee or settlement
schedule calculable and does not count as venue evidence. Request the redacted
owner evidence listed in
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
