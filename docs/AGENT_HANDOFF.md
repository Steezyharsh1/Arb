# AGENT HANDOFF — prediction_arb

**Read this file first, before any other document or any code.** It is the
permanent entry point for a fresh agent session with no memory of prior
conversations. It tells you what the project is, what is true *right now*,
what is broken, what you may work on, and what you must not touch.

- **Last updated:** 2026-10-10 (UTC) by the mandatory-cost session on branch
  `arena/321f09ff-arb` (D17: an unavailable mandatory cost is never a zero).
- **Everything below marked VERIFIED was executed in this sandbox on
  2026-10-10** (rows dated 2026-10-09 were executed then and are labelled
  as such). Anything inherited from earlier sessions and not re-run is
  labelled INHERITED. Never promote INHERITED to VERIFIED without running it.
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
traded in this engagement under any circumstances.

Authoritative context: `README.md` (what works / what is stubbed),
`docs/ROADMAP.md` (phase map + defect register), `docs/SPEC.md` (every `§N`
cited in the code), `docs/AGENT_KICKOFF.md` (role, milestones, how to ask the
owner for help).

---

## 2. Repository, branch, baseline

| Item | Value |
|---|---|
| Repo | `Steezyharsh1/Arb` (package name `prediction_arb`) |
| Working branch (fixed for this session lineage) | `arena/321f09ff-arb` |
| Baseline commit | `796abec767ec962bd2bb9a8ff49de42480f12cdb` — *"Merge pull request #7 from Steezyharsh1/arena/fc6eca21-arb"* (merged 2026-10-10T00:34Z; CI run `38009643994` green) |
| Working tree at session start | clean (`git status --porcelain` empty) — VERIFIED 2026-10-10 before edits |
| Clone depth | **shallow (depth 1)**: `git log` shows a single commit. Run `git fetch --unshallow` if you need real history (e.g. to blame a regression) |
| Merged PRs to date | #1 Phase 0, #2 Phase 1, #3 Phases 2+3, #4 Phase 2/3 gaps + Phase 4, #5 Phase 5, #6 D16 baseline recovery, #7 *"test: make settlement fixtures deterministic"* (merged 2026-10-10T00:34Z — baseline HEAD is this merge) |
| CI | `.github/workflows/ci.yml`: install → `pytest` → 4 CLI smokes → honest-empty-replay grep → tracked-artifact guard |

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
   `fees/calculator.py::trade_fee_fills()/settlement_fee()` charge **per fill**
   (D13) and raise `FeeDataError` when context is incomplete,
   `normalization/settlement.py` costs the capital lockup (UNKNOWN ⇒ BLOCK),
   and the result is TRADE / WATCH / BLOCK with explicit `block_reasons`.
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
| utility | `--status`, `--readiness`, `--kill`, `--unkill` | Venue/provenance table, advisory readiness, persistent kill switch | `--readiness` currently hardcodes the two-venue check to `False` (defect D10) |

---

## 6. Verified test results and environment limitations (2026-10-10)

Environment: Debian sandbox, **Python 3.11.2**, fresh `.venv`,
`pip install -e ".[dev]"` succeeded. `requirements.txt` pins no upper bounds,
so this run used pytest 9.1.1, pytest-asyncio 1.4.0, httpx 0.28.1,
pydantic 2.14.0, SQLAlchemy 2.1.4, websockets 17.2, rich 15.0.0,
cryptography 50.0.2, PyYAML 6.0.3, python-dotenv 1.2.4 (re-read from
`pip list` 2026-10-10 — identical to the 2026-10-09 session).

| Check (exact command) | Result — VERIFIED 2026-10-10 |
|---|---|
| `.venv/bin/pytest` | **348 collected, 348 passed in 26.29 s** — 313 at the 2026-10-09 baseline + the 35 new D17 mandatory-cost tests. All offline |
| `.venv/bin/pytest --collect-only -q` | 348 tests: arbitrage_engine 9, candidate_generation 27, failure_modes 13, kalshi_phase2 22, kalshi_smoke_script 12, llm_client 20, paper_realism 14, paper_streaming 4, paths 23, phase4_recording 25, polymarket_fees 24, polymarket_us 41, safety 26, spec_cases 13, **unavailable_costs 35**, units 40 |
| `.venv/bin/pytest tests/test_unavailable_costs.py` | **35 passed** |
| `.venv/bin/pytest tests/test_phase4_recording.py -q` | **25 passed** (schema-version assertion moved 2 → 3; migration 002 tests unchanged) |
| D17 offline reproduction (fee calculator missing one venue) | Before the fix: `status=BLOCK`, `total_fees=0`, `net_profit=+9.9630`, dashboard `Net $9.96` / `Fees $0.00`, DB `net_profit='9.9630'`. After: `net_profit=None`, `total_fees=None`, `capital_required=None`, dashboard `n/a`, DB `NULL` + `unavailable_costs=["kalshi:trade_fee","settlement_delay"]` |
| Mutation check — restore `fees = Decimal("0")` on `FeeDataError` | **20 failed, 15 passed** of the 35 D17 tests (calculation, ranking, dashboard, persistence and report layers all fail) |
| Mutation check — restore `delay_cost = Decimal("0")` on unknown lockup | **2 failed, 33 passed** — the two `TestUnknownSettlementTiming` cases |
| `python -m prediction_arb.main --status` | exit 0. kalshi=VERIFIED, polymarket_us=API_DOCUMENTED_UNTESTED, forecast_ex=API_DOCUMENTED_UNTESTED; independence matrix kalshi↔polymarket_us = INDEPENDENT; kill switch disengaged |
| `python -m prediction_arb --status` / `prediction-arb --status` | both exit 0 |
| `python -m prediction_arb.main --readiness` | exit 0. `0` snapshots, `0` opportunities, "Two live-capable independent venues = 1" → **LIVE MODE: NOT RECOMMENDED YET** |
| `python -m prediction_arb.main --mode historical --date 2020-01-01` | exit 0, prints "No recorded historical order-book data…", writes an honest empty report — the CI grep still holds |
| `python -m prediction_arb.main --mode paper --cycles 1` | exit 0; both venues fail to connect (TLS EOF), host rotation behaves as designed, cycle ends "no active-venue markets" |
| CI tracked-artifact guard (`git ls-files` piped through the `grep -E` filter in `.github/workflows/ci.yml`) | OK — no artifacts, databases, logs or env files tracked |
| `python scripts/kalshi_smoke.py` | **exit 1** — `TLS/SSL connection has been closed (EOF)` on *both* `external-api.kalshi.com` and `api.elections.kalshi.com`. Evidence row **K9 still stands** |
| `python scripts/polymarket_us_smoke.py` | **exit 1** — same TLS EOF on `gateway.polymarket.us`. Evidence row **P7 still stands** |
| Frozen-wall-clock sweep (2026-10-14 / 2027-10-09 / 2031-01-02) | **NOT RE-RUN this session** — INHERITED from 2026-10-09 (313/313 at each date, via a temporary `freezegun` in the ignored `.venv`). The new tests avoid wall-clock dependence by construction: `verified_at` staleness uses 2020-01-01, expiry uses 2020-01-01, effectiveness uses 2099-01-01, and settlement uses the frozen test clock |
| GitHub Actions CI | **Green for this branch's D17 work on PR #8** — `pull_request` runs `38012787059` and `38012962141` both `success` (47 s each; `tests + CLI smoke` pass), matching `push` runs `38012367502` / `38012959534`. Each run is install → full suite → four CLI smokes → honest-empty replay → artifact guard. The newest run on the PR head supersedes the ones listed. Baseline `796abec` was green as run `38009643994` |

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
| **D17** | **A BLOCKed opportunity could expose a positive "verified" net profit** (inherited review, reproduced 2026-10-10). `evaluate_two_leg` caught `FeeDataError` and substituted `fees = Decimal("0")`, and substituted `delay_cost = Decimal("0")` for an unknown settlement lockup, so the profit test ran on fabricated free costs | Offline synthetic scan with a fee calculator missing one venue returned `status=BLOCK`, `total_fees=0`, `net_profit=+9.9630`, `return_on_capital=0.1107`; the dashboard printed `Net $9.96` beside `Fees $0.00`; `Repository.save_opportunity` stored `net_profit='9.9630'`, `fees='0'`. Only the *first* fee failure was reported, because one `FeeDataError` aborted the whole fee sum | **FIXED 2026-10-10** (production + tests): each mandatory cost is now a `core.types.CostComponent` in a `CostLedger` with no total while any component is missing; `net_profit`, `net_profit_per_contract`, `return_on_capital`, `total_fees`, `settlement_delay_cost` and `capital_required` become `None` (UNVERIFIED); components are evaluated independently so every failure is reported; `gross_edge`/`known_costs` stay available as pre-cost research figures; cost-of-capital is unavailable when fees are. Downstream: `optimizer.score()` refuses unpriced opportunities and `rank()` sorts them last, `Opportunity.is_executable` gates paper execution, metrics withhold a fee-drag ratio over a partially costed set, dashboard/CLI print `n/a`, `OpportunityRow` stores NULL + `unavailable_costs` (`SCHEMA_VERSION` 2 → 3, migration 003), paper/live reports count `opportunities_without_verified_net_profit`. 35 new tests, mutation-verified (20 fail on the fee mutation, 2 on the delay mutation). Fee engine untouched |
| **D16** | **The inherited review reported one expired settlement fixture.** The old `tests/conftest.py` date was `2026-09-15T20:00Z`; after it passed, `capital_lockup_days()` correctly clamped lockup to zero, so `TestSettlementTiming::test_lockup_and_cost` failed and other tests silently stopped charging delay cost. Production behavior was correct — an already-settled contract locks no capital | Prior handoff recorded `pytest` → `310 passed, 1 failed`. In this session the reported test passed at the baseline commit; an explicit calculation with settlement `2026-09-15T20:00Z` and test clock `2026-10-09T00:00Z` returned `0.0`, confirming the cause | **FIXED / RE-VERIFIED 2026-10-09** (tests only): replaced the wall-clock-relative default with frozen `SETTLEMENT_TEST_NOW` and a default settlement three days later; `test_lockup_and_cost` pins the exact 3-day lockup and `$0.0411` cost. Found and fixed the same silent failure in the candidate-generation scan: its expired 2026-09 fixture had produced zero delay cost without failing; it now settles after the frozen test clock and asserts positive cost. Four settlement-timing tests and 27 candidate tests pass; full suite **313 passed in 25.01 s** and at each of 2026-10-14 / 2027-10-09 / 2031-01-02 under a frozen wall clock. Mutation-verified: restoring either stale fixture makes its regression test fail; last-leg and past-settlement clamp tests still pin the intended arithmetic. No production code changed |
| **D10** (open, inherited and re-confirmed) | Live mode is monitor-only and `--readiness` hardcodes the two-venue check | `main.py::cmd_readiness` contains the literal `False`; `--readiness` printed "NOT YET: Two live-capable independent venues = 1"; `modes/live/engine.py::run` refuses to scan | **OPEN.** Correct-but-dishonest: the check cannot ever pass, even when two verified venues exist. Phase 8 work |

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
| D13 | Fees charged per fill from walked levels | `fees/calculator.py::trade_fee_fills`, `arbitrage/two_leg.py` |
| D15 | Only proven-INDEPENDENT pairs may be APPROVED (§33) | `matching/rules_validator.py` |
| D7 | Venue research refreshed | `docs/VENUE_RESEARCH.md` |

### 7c. Observations from this session — NOT yet registered defects

Recorded here so they are not rediscovered, deliberately **not** added to the
ROADMAP register because their impact needs an owner/agent decision first:

- **`strict_fee_freshness` is never enabled by any caller.** `grep -rn
  "strict_freshness\|strict_fee_freshness" prediction_arb/` shows the flag
  threaded from `modes/shared.py` into `arbitrage/two_leg.py` and
  `fees/calculator.py`, but no engine ever passes `True`. Consequently
  `settings.yaml: fees.max_schedule_age_days: 30` is inert at runtime today
  (only `tests/test_failure_modes.py` exercises it directly). Harmless while
  live mode is monitor-only; it becomes a real gap the moment live scanning
  exists (Phase 8). Same family as D12/D14 ("configured value silently
  ignored").
- **All fee schedules in `config/fees.yaml` carry `verified_at: 2026-09-13`.**
  Under a 30-day window they fall out of strict freshness on **2026-10-14**.
  Nothing breaks today (see the previous bullet), but any live-path work must
  re-verify the schedules first.
- **Settlement fixtures are now tied to a deterministic test clock.**
  `tests/conftest.py` freezes only `normalization.settlement.utcnow` at
  `SETTLEMENT_TEST_NOW` and places its default settlement three days later;
  `tests/test_candidate_generation.py` uses a settlement one day later and
  asserts the end-to-end scan charges a positive delay cost. The absolute
  2026-09-25 instant in `tests/test_phase4_recording.py` is persistence/replay
  data only (no time-sensitive lockup path). The whole suite also passes with
  the wall clock frozen at 2026-10-14 / 2027-10-09 / 2031-01-02. New timing
  tests should keep using an explicit `now=` or a frozen settlement clock.

---

## 8. Current critical path (ordered by dependency, then impact)

0. ~~**Stop a BLOCKed opportunity from advertising a positive net profit
   (D17).**~~ **DONE 2026-10-10** — unavailable mandatory costs are now
   typed as unavailable, net profit is `None`/`n/a` instead of a fabricated
   figure, and 34 regression tests pin the calculation, ranking, dashboard,
   persistence and report layers. **348 passed in 26.29 s.**
1. ~~**Restore a green baseline (D16).**~~ **DONE 2026-10-09** — 313 passed
   in 25.01 s with a fixed settlement clock, and at three future wall-clock
   dates. The reported failure is not present at baseline; the stale default
   and pipeline fixtures now fail explicit mutation checks. With that closed,
   **every remaining item on the critical path needs the owner**;
   nothing further can be verified here without venue access or keys.
2. **Owner captures venue reachability evidence** — `scripts/kalshi_smoke.py`
   and `scripts/polymarket_us_smoke.py` from a machine that can reach the
   venues (no keys needed for the public checks). This converts K9/P7 from
   UNAVAILABLE to observed and is the gate for promoting Polymarket US out of
   `API_DOCUMENTED_UNTESTED` — which is what makes collect mode record a
   second venue at all. *Owner (H4 step 2 / Phase 2 item 2).*
3. **Owner adds the Kalshi key (H2) and runs a 24 h `--mode collect`** —
   produces the first real recorded data, closing the Phase 2 operational exit
   and supplying Phase 4's replay acceptance run. *Owner.*
4. **Owner adds an LLM key (H7)** — without it no certificate is ever
   APPROVED, so the arbitrage evaluator is unreachable on real data.
   *Owner.*
5. **Phase 5 exit on real two-venue data** — candidate pairs, certificates
   with statuses, and a human-reviewable false-match rate. *Agent, blocked on
   2–4.*
6. **Phase 3 remaining gates** — Utah/account eligibility (R9, UNKNOWN) and
   the unpublished Polymarket US settlement fee (P12, `INCOMPLETE — BLOCK`).
   Both are evidence problems, not code problems. *Owner/legal.*
7. **Phase 6 realism evidence** — the owner's multi-week paper run.
8. **Phase 8 live readiness** — including D10 (compute the live-capable venue
   count instead of hardcoding it) and the strict-fee-freshness observation in
   §7c. Not before Phases 2–7 evidence exists.

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
| R9 — Utah/account eligibility for Polymarket US non-sports | Phase 3 gate 1; live eligibility generally | UNKNOWN — primary sources silent, secondary sources conflict |
| P12 — Polymarket US settlement fee | Phase 3; opportunities fail closed until settled | UNVERIFIED (documented by absence only) |
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
you *proved* wrong, add a defect you *reproduced* (next free ID; D17 is
taken),
record a completion with its evidence. Do not redesign it, do not add
aspirational plans, do not mark anything DONE whose exit criterion is
owner-gated — say "code complete, exit owner-gated" instead. Also update
`README.md` ("what works / what is stubbed") and any doc whose claim your
change invalidated — same commit.

**E. Commit**

```bash
git add -A && git commit -m "<scope>: <what changed and why>"
git push origin <your-session-branch>    # e.g. arena/321f09ff-arb; each session
                                         # lineage is fixed to its own branch
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

1. **2026-10-10 — D17: unavailable mandatory costs are no longer zeros.**
   `evaluate_two_leg` costed a blocked opportunity with fabricated free
   fees (and a free capital lockup), so a `BLOCK` row could advertise
   `+$9.96` of "net profit" on the dashboard and in the database. Net profit
   is now `None` whenever any mandatory cost is unavailable, costs are
   represented as `CostComponent`/`CostLedger` values, gross edge and known
   costs survive as clearly-labelled research figures, and every reporting
   surface (dashboard, CLI summaries, SQL NULL + `unavailable_costs`,
   daily reports) shows `n/a` rather than a number. New
   `monitoring/formatting.py`, migration 003 (`SCHEMA_VERSION` 3), 35 tests
   in `tests/test_unavailable_costs.py`; **348 passed in 26.29 s**. The
   fail-closed fee engine was not touched.
2. **2026-10-09 — D16 fixtures made repeatable and re-verified.** The
   settlement test clock is fixed, default and candidate fixtures settle in
   its future, and regression assertions require both positive lockup and
   positive cost. The reported target and affected tests pass; the full suite
   is **313 passed in 25.01 s** and passes at three future frozen wall-clock
   dates. Mutation checks make both stale fixtures fail. Tests/docs only — no
   production settlement logic changed.
3. **2026-10-09 — original D16 repair and this handoff created.** Added
   `docs/AGENT_HANDOFF.md`, registered **D16**, and re-verified CI recency and
   sandbox venue reachability (both smoke scripts returned TLS EOF; K9/P7
   remain open).
4. **2026-09-14 — Phase 5 (PR #5).** D6 candidate blocking (§96), D9 LLM
   client wiring (§97) with a per-cycle budget, D15 provenance gate on
   certificates (§33). 47 new tests.
5. **2026-09-13 — Phase 2/3 gaps + Phase 4 (PR #4).** Exact Polymarket US fee
   model (198-value table reproduced), per-fill fees (D13),
   `scripts/kalshi_smoke.py` + D14, full-contract recording and read-only
   replay (D3/D4), backtest metric battery, paper realism (D11/D12).
6. **2026-09-13 — Phases 2 + 3 (PR #3).** Kalshi host migration, failover,
   streaming, tier-aware limits; Polymarket US adapter + WebSocket + provenance.

**Recommended next task (one):** **Ask the owner to run the two read-only
smoke scripts and paste the evidence back.** With D16 and D17 closed, the
repository has no agent-doable item left on its critical path: every
remaining phase exit (2, 3, 4, 5, 6) is waiting on evidence this sandbox
physically cannot produce.
The single highest-leverage request is venue reachability, because it gates
everything downstream — promoting Polymarket US out of
`API_DOCUMENTED_UNTESTED` is what makes collect mode record a *second* venue,
which is the precondition for real candidate pairs, real certificates, and the
first replayable data set:

```bash
python scripts/polymarket_us_smoke.py    # read-only, no key, no account
python scripts/kalshi_smoke.py           # read-only, no key needed
```

Both write sanitized evidence to `reports/evidence/` (git-ignored). Use the
mandatory beginner-friendly request format in `docs/AGENT_KICKOFF.md` ("HOW TO
ASK THE OWNER FOR HELP"), one task per message, and state the success
condition. Bundle the key requests (H2 Kalshi, H7 LLM) only *after* this
returns, so the owner is never handed five setup steps at once.

*While waiting*, the most useful offline work is re-verifying the §7b
inherited fixes by mutation — each one's claim can be re-derived here with no
owner, which converts INHERITED into VERIFIED and would catch any fix that has
silently regressed. Do **not** start anything in §10.
