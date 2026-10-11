# prediction_arb — Multi-Venue Prediction-Market Arbitrage Engine (v1)

A conservative, safety-first system for detecting **guaranteed (TYPE A)**
two-leg arbitrage across genuinely independent prediction-market venues,
with hard mode isolation (historical / paper / live), a guarded live-order
firewall component (live mode is monitor-only, not an enabled order path),
audit logging, and honest handling of everything that is UNKNOWN.

## PHASE 1 RESEARCH RESULT (read this first)

The venue/provenance research baseline is **2026-09-13**; active-route fee
sources were directly re-reviewed **2026-10-10**. The old “only Kalshi ↔
ForecastEx” conclusion is obsolete: Polymarket US, Gemini Titan, CDNA, CME,
Railbird/DKeX, Ludlow/Novig, and Rothera have distinct DCM/matching identities.
Consumer apps still require contract-qualified routing:

| Route example | Underlying exchange | Verdict vs direct Kalshi |
|---|---|---|
| Coinbase, Webull, Moomoo Kalshi contracts | Kalshi | SAME_VENUE |
| IBKR Kalshi contract | Kalshi | SAME_VENUE |
| IBKR ForecastEx / CME contract | ForecastEx / CME | INDEPENDENT |
| Robinhood contract | Kalshi, ForecastEx, Rothera, or CDNA | UNKNOWN until route identified |
| FanDuel contract | CME or OG/CDNA | UNKNOWN until route identified |
| DraftKings contract | CME, CDNA, or proprietary DKeX/Railbird | UNKNOWN until route identified |

The conditional adapter shortlist was **Polymarket US**, **Gemini**, then
**IBKR ForecastEx/CME**. The owner selected **Polymarket US** on 2026-09-13.
Its public REST implementation and offline tests exist, but this sandbox has
not received a live HTTP response; it remains `API_DOCUMENTED_UNTESTED` and
collect skips it. Kalshi is `MARKET_DATA_OBSERVED`, based on historical public
collection (2026-08-30), not full verification of the current adapter. The
2026-10-10 public smoke attempts for both venues were `UNAVAILABLE` (TLS EOF),
not evidence that either adapter is broken or verified. `--status` keeps
reviewed documentation, connectivity, usable market data, and full adapter
verification separate. Only three of the researched DCMs expose a usable
public API at all; reaching venues without one would require prohibited
scraping (§13).

Utah sports contracts are **RESTRICTED** across venues; Utah non-sports
eligibility is **UNKNOWN**. Context-incomplete fees fail closed. Of the three
enabled venue pairs, all are provenance-independent but **none is currently
end-to-end cost-evaluable**: Kalshi, Polymarket US, and ForecastEx each have
at least one mandatory fee/settlement component marked `INCOMPLETE`. Only
Kalshi has historical usable-market-data evidence for read-only collection;
no venue has current full adapter verification. Capital-lockup timing is
evidenced only for Kalshi (documented per-market bound); Polymarket US and
ForecastEx settlement timing is `UNKNOWN`, so their legs also block on
lockup (`docs/TRADING_LOGIC.md`, D18). See
`docs/FEE_READINESS.md` for the auditable pair matrix, direct evidence,
calculation states, and exact operator actions; broader claim-level venue
research is in `docs/VENUE_RESEARCH.md`.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -e ".[dev]"                              # package + test tooling
pytest                                               # run the full safety suite

# safest first step — inspect provenance/readiness; sends no orders
python -m prediction_arb.main --status

# one bounded, public read-only collection attempt; it does not invoke execution
# A successful exit alone does not mean a book was received or saved.
python -m prediction_arb.main --mode collect --cycles 1

# bounded, public-only read-only smoke probes (no credentials or orders);
# network failure is recorded as UNAVAILABLE, not adapter failure. A
# successful smoke is only evidence for manual review, never full verification.
python scripts/polymarket_us_smoke.py
python scripts/kalshi_smoke.py

# paper research: admitted venue data when reachable; simulated execution only
python -m prediction_arb.main --mode paper

# replay recorded data
python -m prediction_arb.main --mode historical --date 2026-08-30
python -m prediction_arb.main --mode historical --start 2026-08-01 --end 2026-08-30

# venue/provenance status + independence matrix
python -m prediction_arb.main --status

# readiness, derived per mode from observed state (advisory: enables
# nothing, creates nothing, calls no venue). Every criterion prints
# PASS / WARN / BLOCKED with the value it observed and the reason.
python -m prediction_arb.main --readiness

# emergency stop (persists across restarts until --unkill)
python -m prediction_arb.main --kill
python -m prediction_arb.main --unkill
```

Live mode (`--mode live`) requires typing `I UNDERSTAND LIVE TRADING`
interactively; no flag bypasses it. No adapter currently has full live
verification, so there are zero live-capable venues; live mode remains
monitor-only by construction.

## What works today
- Kalshi's **implementation** includes recommended `external-api` hosts with
  legacy fallback on transport/5xx, public REST, authenticated bulk books
  (≤100 tickers/request), sequenced WebSocket snapshots/deltas with gap
  recovery, and account-tier budget clamping. Offline tests cover parsing,
  sequence-gap recovery and REST fallback. Its current API evidence is only
  `MARKET_DATA_OBSERVED`, based on a historical public collection; full
  adapter verification, authenticated bulk behavior and WebSocket behavior
  have not been established by a current live probe. The bounded public smoke
  on 2026-10-10 was `UNAVAILABLE` (connection/TLS failure), which neither
  verifies nor marks the adapter broken.
- The Kalshi parser handles documented fixed-point bid books and synthesizes
  complementary asks. Documentation for hosts, formats, sequencing and limits
  was reviewed on 2026-09-13; fee sources were re-reviewed on 2026-10-10. The
  Kalshi trade and settlement schedules remain `INCOMPLETE — BLOCK` until
  per-series/event, account/fill, and settlement context is represented.
- Cross-venue **candidate generation now works**: a pair is proposed when
  the two contracts are on different venues, settle within 6 hours of each
  other, and share a discriminative key (exact normalized threshold, numeric
  token, or content word of the title). This replaces the earlier
  settlement-bucket grouping, which chained a 1,000-contract benchmark into one
  component. Proposals are bounded by a local density rule and are never
  transitive. On the synthetic 1,000-contract benchmark this is 3,262 pairs
  instead of 333,296, with every deterministically passable equivalent pair
  kept. Retrieval only proposes candidates. The deterministic matcher still
  rejects on any hard mismatch (a mismatch between two declared operators or
  units is still final), but a field the two legs cannot be compared on — one
  leg declares a comparison operator or unit and the other declares none, as
  with Kalshi's `strike_type` versus Polymarket US's absent operator (D25) —
  is no longer reported as a mismatch: the pair is recorded as REVIEW with the
  field named, can never be APPROVED, and is counted separately from
  rejections. Benchmark, design and known misses:
  `docs/CANDIDATE_RETRIEVAL.md`.
- **The LLM matcher is now wired** (optional, owner-supplied key): put
  `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` in `.env.paper` and any
  OpenAI-compatible endpoint can approve cross-venue matches — with
  schema validation (malformed/contradictory output ⇒ no match), outage
  ⇒ no-match, per-cycle cost budget, and model/prompt versions recorded
  on every certificate. With **no** key configured everything still runs,
  but every ambiguous pair ends in REVIEW and nothing is ever evaluated
  for arbitrage — never auto-APPROVED.
- The paper research pipeline is implemented in code: discovery → normalization
  → bounded candidate proposal → deterministic + LLM-gated matching → certificates
  → order-book walking → fee/slippage/settlement costing → paper risk checks →
  simulated execution. It has **not** been validated end-to-end on current real
  two-venue inputs: no current Polymarket US payload is observed, there is no
  LLM configured, and active production fees/timing block costed decisions. A
  test-only synthetic end-to-end path is covered separately; it is not venue or
  strategy evidence.
- Collect mode records only usable two-sided snapshots for adapters with
  `MARKET_DATA_OBSERVED` or `VERIFIED` evidence. It never invokes execution.
  Normalized contracts retain observed settlement/cutoff, provenance,
  microstructure, and per-market trading rules when supplied; unknown tick or
  minimum-quantity rules remain unknown instead of receiving invented defaults.
  Invalid, crossed, stale or otherwise unusable books are rejected and counted.
  Historical replay reconstructs what was observed; unreconstructable values
  are counted, never substituted.
- Historical mode reads the recorded store **read-only** (SQLite `mode=ro`,
  so writes are refused by the database engine rather than by convention)
  and writes replay output to its own database. A missing recorded store
  is reported honestly; it is never created empty, and history is never
  fabricated or back-filled from live data.
- Historical evaluation uses exact receipt-time, as-of books, never minute
  rounding or future quotes. Old records without temporal/outcome/contract
  evidence are **THEORY ONLY**. Supported records can run the shared conditional
  depth/latency/cash model, which separates calculated opportunities, simulated
  fills, completed pairs and unresolved exposure. Reports never add repeated
  opportunity profits into “equity”, infer fill probability from sizing, or
  credit unobserved settlement cash. Empty/incomplete runs retain null P&L.
  **No genuine historical dataset or real-fill evidence exists in this checkout.**
  See [`docs/HISTORICAL_EVALUATION.md`](docs/HISTORICAL_EVALUATION.md) for the
  audit, report schema 2, explicit assumptions and minimum further collection.
- Paper mode no longer flatters the strategy. Order books pass a real
  staleness gate before they are scanned **or** filled against, and the
  configured `simulated_fill_probability_at_touch` actually scales the
  at-touch portion of a fill. Both defects previously pushed results in the
  optimistic direction — the dangerous one, since a strategy that validates
  in paper and then fails live is worse than one that never validated.
  Books that fail the freshness gate are counted and reported, never
  silently dropped.
- Mode isolation, kill switch, crash-recovery intents, and all ten mandatory
  spec cases are covered by the offline suite. Current checkout re-run
  (`.venv/bin/pytest -o addopts=''`, Python 3.11.2 / pytest 9.1.1, 2026-10-10): **764 passed in 36.12 s**.
  The earlier 534-test suite also passed with the fee clock frozen at
  2026-10-14 / 2027-10-09 / 2031-01-02; that future-clock sweep was not
  repeated for the current 764-test suite. Current CLI outcomes, including a
  failed one-cycle collection attempt and an honestly empty replay, are recorded
  in `docs/TESTING.md`.
- Readiness is **derived, not asserted** (D10, SPEC §99). `--readiness`
  reports one block per mode (COLLECT, HISTORICAL, PAPER, LIVE) and every
  criterion is computed from observed state — adapter verification *and*
  verified order-type support, provenance independence, matcher
  configuration, fee completeness and freshness, settlement-timing evidence,
  recorded-data volume, jurisdiction eligibility, the kill switch, the
  configured hard risk limits, the book-staleness gates and the existence of
  `.env.live` (existence only; live credentials are never read outside live
  mode). A mode is READY only when nothing applicable is BLOCKED, and each
  unmet criterion names the observation that failed, so the operator sees the
  actual blocker. On 2026-10-10, readiness reported zero fully verified
  live-capable venues, 0/3 complete fee schedules, UNKNOWN settlement timing
  on two venues, no recorded volume, eligibility UNKNOWN, and no `.env.live`;
  the report enables nothing. `COLLECT: READY` reflects applicable evidence/admission
  gates; it is not current network reachability or a successful collection run.
  This checkout's 2026-10-10 one-cycle collector attempt received TLS EOF and
  recorded no books (details in `docs/TESTING.md`).
- Mandatory fee freshness is a real gate (D19, SPEC §98). One rule derives the
  strictness from the mode: **live and paper MANDATORY**, **historical
  RESEARCH**, **collect NOT_APPLICABLE**, unknown modes fail closed. A
  schedule verified more than `fees.max_schedule_age_days` (30) ago, with no
  verification date, or not VERIFIED/sourced is refused in live and paper
  exactly like a missing one (`FEE_DATA_INCOMPLETE … Re-verify before live
  execution`) — never priced on stale data and never assumed free. A research
  replay is not blocked by an old schedule, because recorded history is still
  evidence, but the run is labelled `RESEARCH USE ONLY: fee schedule(s) older
  than …` in the console, the run summary and the report. Freshness is not
  completeness: all three production schedules are fresh *and* `INCOMPLETE`,
  so they still block.
- Rejections explain themselves (SPEC §100). After the opportunity table,
  paper and historical runs print the distinct block reasons with their
  counts, most common first, instead of leaving the operator with a number of
  blocked rows and no cause.
- Capital lockup runs to each leg's evidenced capital-release time (venue
  resolution bound + documented settlement delay), not to the event time.
  Every venue's timing input carries a meaning, evidence status, sources and
  review date; unknown timing, a passed release time, or a payload missing
  the documented fields blocks instead of costing zero, long-dated
  contracts are charged for their full lockup, and replay measures lockup
  from the recorded frame time (D18, `tests/test_settlement_timing.py`).
- An opportunity whose mandatory costs cannot be computed has **no** net
  profit — `None`/`n/a`, never a fabricated zero fee and never a positive
  figure next to a BLOCK verdict (D17). Gross edge and the costs that could
  be computed stay reported as clearly separate research figures, and a
  positive gross edge can never be mistaken for an executable opportunity
  (`tests/test_unavailable_costs.py`).
- The fee calculator can process an explicitly supplied per-fill price and
  quantity list, including Polymarket US banker's rounding and the published
  aggressive-order cap. Offline tests reproduce the current standard 99-row
  100-lot fee table and check Table Tennis/combo reference arithmetic using
  **test-only synthetic data**. They do not make the production schedule
  complete: current Polymarket US fee-family selection and settlement, Kalshi
  series/event and account state, and ForecastEx route/settlement evidence
  remain unresolved. Production fees stay `INCOMPLETE`; no fee estimate is
  exposed. Volume rebates are recorded but never applied as per-trade
  discounts. See `docs/FEE_READINESS.md`.

## What is stubbed (honest NotImplementedError, clean interfaces)
- ForecastEx/IBKR, Webull, and Moomoo adapters. Phase 1 researched additional
  candidates but deliberately stopped before adapter implementation.
- Multi-leg / logical arbitrage, graph cycle search, web dashboard,
  email/webhook alerts (§3 v1 scope).
- Historical **conditional** fill simulation is implemented (§102), but
  tick-accurate feed reconstruction, calibrated fill probabilities, closed/
  settled realized P&L and a genuine two-venue performance evaluation are not.
  The latter requires owner-run, evidence-complete collection on a reachable
  machine; missing history cannot be fabricated. The legacy undated
  `HistoricalExecutionEngine` is not the replay evaluator.

## Repository layout

```
prediction_arb/          the importable package — CODE ONLY
    core/paths.py        resolves config/, data/, logs/, reports/ (see §49)
    adapters/ arbitrage/ compliance/ database/ execution/ fees/ matching/
    modes/ monitoring/ normalization/ orderbooks/ provenance/ risk/
    main.py              CLI entry point (`python -m prediction_arb.main`)
config/                  YAML: settings, venues, fees, risk limits, per-mode
data/<mode>/             per-mode SQLite databases + KILL_SWITCH   (git-ignored)
logs/<mode>/             per-mode JSONL audit logs                 (git-ignored)
reports/<mode>/          generated reports                        (git-ignored)
docs/                    the documentation set listed below
scripts/                 operator utilities (IBKR gateway session check)
tests/                   units, safety, failure modes, and spec §75 cases
pyproject.toml           packaging, console script, pytest config
```

Runtime artifacts (`data/`, `logs/`, `reports/`) are created on demand and are
never committed; `.env.*` files hold credentials and are never committed either
(`.env.example` is the template).

## Documentation

**Start here:** `docs/AGENT_HANDOFF.md` — the permanent entry point for a fresh
engineer or agent session (current baseline commit, verified test results,
environment limits, defects, critical path, what not to start). Then
`docs/ROADMAP.md` (authoritative prioritized task list, current state, defect
register D1–D29, human-action checklist) and `docs/SPEC.md` (the reconstructed specification —
every `§N` cited in the code resolves there).

`docs/`: AGENT_HANDOFF, ROADMAP, SPEC, AGENT_KICKOFF, VENUE_RESEARCH, ARCHITECTURE, API_SETUP,
COMPLIANCE, TRADING_LOGIC, LLM_MATCHING, RISK_MANAGEMENT, TESTING,
MODE_SEPARATION, CRASH_RECOVERY, HISTORICAL_EVALUATION.

## Safety posture
- `--mode live` is monitor-only in the current application. The
  `LiveExecutionEngine` and 15-check firewall exist as guarded code/test
  components, but production code does not construct `FirewallContext` or call
  its order-submission method. Do not read the firewall's existence as live
  execution being enabled. Simulated engines hold no adapter references, and
  non-live modes cannot place real orders; UNKNOWN never means yes.
- Match certificates are APPROVED only for pairs whose venue relationship
  is **proven INDEPENDENT** — same-venue frontends (e.g. Kalshi contracts
  reached through Webull) and any unresolved relationship end in REVIEW
  for human handling, never in an APPROVED certificate.
- Nothing here is financial or legal advice. Verify venue terms, fees,
  and Utah eligibility yourself before funding live trading.
