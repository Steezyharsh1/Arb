# prediction_arb — Multi-Venue Prediction-Market Arbitrage Engine (v1)

A conservative, safety-first system for detecting **guaranteed (TYPE A)**
two-leg arbitrage across genuinely independent prediction-market venues,
with hard mode isolation (historical / paper / live), a live-order
firewall, full audit logging, and honest handling of everything that is
UNKNOWN.

## PHASE 1 RESEARCH RESULT (read this first)

Evidence was refreshed through **2026-09-13**. The old “only Kalshi ↔
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
**IBKR ForecastEx/CME**. The owner selected **Polymarket US** on 2026-09-13
and its adapter is now implemented (Phase 3): public discovery and
two-sided order books work with **no API key**, and the venue registers as
INDEPENDENT of Kalshi. It stays `API_DOCUMENTED_UNTESTED` — and collect
mode skips it — until observed-payload evidence is captured with
`python scripts/polymarket_us_smoke.py` from a machine that can reach the
venue. Only three of the researched DCMs expose a usable public API at
all; Railbird/DKeX, Ludlow/Novig, Rothera, and CDNA have none, and
reaching them would require scraping, which is prohibited (§13).

Utah sports contracts are **RESTRICTED** across venues; Utah non-sports
eligibility is **UNKNOWN**. Context-incomplete fees fail closed. Full claim-level
statuses, sources, dates, matrix, and recommendation:
`docs/VENUE_RESEARCH.md`.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -e ".[dev]"                              # package + test tooling
pytest                                               # run the full safety suite

# safest first step — inspect provenance/readiness; sends no orders
python -m prediction_arb.main --status

# collect public Kalshi data (recommended hosts + legacy fallback;
# with a Kalshi API key in .env.paper it uses bulk books + WS streaming)
python -m prediction_arb.main --mode collect

# Polymarket US read-only smoke test + evidence capture (no API key, no
# account, no orders) — promotes the venue out of API_DOCUMENTED_UNTESTED
python scripts/polymarket_us_smoke.py

# Kalshi read-only smoke test: both hosts + the orderbook_fp book format
# (no API key needed; with a key it also checks tier, bulk books and the
# WebSocket). This is Phase 2's owner-runnable verification step.
python scripts/kalshi_smoke.py --with-stream 10

# paper trading (default mode): real data, simulated execution
python -m prediction_arb.main --mode paper

# replay recorded data
python -m prediction_arb.main --mode historical --date 2026-08-30
python -m prediction_arb.main --mode historical --start 2026-08-01 --end 2026-08-30

# venue/provenance status + independence matrix
python -m prediction_arb.main --status

# readiness for live (advisory)
python -m prediction_arb.main --readiness

# emergency stop (persists across restarts until --unkill)
python -m prediction_arb.main --kill
python -m prediction_arb.main --unkill
```

Live mode (`--mode live`) requires typing `I UNDERSTAND LIVE TRADING`
interactively; no flag bypasses it. With only one live-capable venue,
live mode is monitor-only by construction.

## What works today
- The Kalshi adapter runs on the recommended `external-api` hosts with the
  legacy hosts as automatic fallbacks (5xx/transport failover, connect-time
  probing). Public REST works without credentials; with a key it adds
  authenticated **bulk order books** (`GET /markets/orderbooks`, ≤100
  tickers/request) in collect mode, **WebSocket streaming**
  (`orderbook_delta` with sequenced snapshots/deltas, gap-triggered
  recovery, reconnect backoff) in paper mode, and startup discovery of the
  account's rate-limit tier with budget clamping below the published limits.
  All of it degrades to public polling when no key is configured.
- The Kalshi parser handles documented fixed-point bid books and synthesizes
  complementary asks. Official hosts, formats, sequencing, limits, and fees
  were documentation-verified on 2026-09-13; a fresh sandbox network smoke
  test was **UNAVAILABLE**, not reported as live-tested.
- Cross-venue **candidate generation now works**: contracts are grouped by
  deterministic blocking (settlement-window buckets, category, threshold +
  operator + units, numeric tokens and content words of the titles) instead
  of exact title text, so the same real-world event phrased differently on
  two venues actually becomes a candidate pair. Blocking only proposes
  candidates — the deterministic matcher still rejects on any hard
  mismatch, and its REJECT is final.
- **The LLM matcher is now wired** (optional, owner-supplied key): put
  `LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` in `.env.paper` and any
  OpenAI-compatible endpoint can approve cross-venue matches — with
  schema validation (malformed/contradictory output ⇒ no match), outage
  ⇒ no-match, per-cycle cost budget, and model/prompt versions recorded
  on every certificate. With **no** key configured everything still runs,
  but every ambiguous pair ends in REVIEW and nothing is ever evaluated
  for arbitrage — never auto-APPROVED.
- Full paper/collect pipeline: discovery → normalization → blocking-based
  candidate generation → deterministic + LLM-gated matching → certificates
  → order-book walking → fee/slippage/settlement costing → risk checks →
  simulated execution. Incomplete fees
  now block rather than becoming estimates, so direct Kalshi opportunities
  remain blocked until contextual metadata is integrated.
- Collect mode now records the **full** normalized contract — settlement
  and cutoff times, resolution source/method, threshold + operator +
  units, early-resolution and cancellation rules, payout, tick size,
  order-size bounds, contract version, provenance, microstructure,
  supported order types, and the venue's own payload — so historical
  replay reconstructs what was actually observed instead of inventing
  plausible defaults. Reconstruction that cannot be done faithfully is
  counted and reported, never substituted.
- Historical mode reads the recorded store **read-only** (SQLite `mode=ro`,
  so writes are refused by the database engine rather than by convention)
  and writes replay output to its own database. A missing recorded store
  is reported honestly; it is never created empty, and history is never
  fabricated or back-filled from live data.
- Backtest metrics over recorded frames: opportunity duration, fill
  probability, partial-fill rate, drawdown, capital utilisation and fee
  drag. Measured quantities and modelled ones stay separated, and every
  result carries its assumptions in plain text — the equity curve assumes
  every executable opportunity filled, so it is an upper bound, never a
  forecast.
- Paper mode no longer flatters the strategy. Order books pass a real
  staleness gate before they are scanned **or** filled against, and the
  configured `simulated_fill_probability_at_touch` actually scales the
  at-touch portion of a fill. Both defects previously pushed results in the
  optimistic direction — the dangerous one, since a strategy that validates
  in paper and then fails live is worse than one that never validated.
  Books that fail the freshness gate are counted and reported, never
  silently dropped.
- Mode isolation, kill switch, crash-recovery intents, and all ten mandatory
  spec cases are covered by the test suite (`pytest` — 348 passing, all
  offline).
- An opportunity whose mandatory costs cannot be computed has **no** net
  profit — `None`/`n/a`, never a fabricated zero fee and never a positive
  figure next to a BLOCK verdict (D17). Gross edge and the costs that could
  be computed stay reported as clearly separate research figures, and a
  positive gross edge can never be mistaken for an executable opportunity
  (`tests/test_unavailable_costs.py`).
- Fees are charged **per fill** from the levels a walk actually consumed,
  with each venue's documented rounding, and Polymarket US's exact
  `Θ × C × p × (1-p)` model — banker's rounding per fill plus the
  cumulative aggressive-order cap that can only reduce a charge. The
  implementation reproduces all 198 values of the venue's own published
  100-lot fee table (`tests/test_polymarket_fees.py`). Volume rebates are
  recorded as evidence and never applied, because they are a weekly payout
  on prior-month volume rather than a per-trade discount.

## What is stubbed (honest NotImplementedError, clean interfaces)
- ForecastEx/IBKR, Webull, and Moomoo adapters. Phase 1 researched additional
  candidates but deliberately stopped before adapter implementation.
- Multi-leg / logical arbitrage, graph cycle search, web dashboard,
  email/webhook alerts (§3 v1 scope).
- Backtest *metrics* are implemented, but backtest *execution* is not:
  replay measures opportunities, it does not simulate fills against them
  (`HistoricalExecutionEngine` stays unwired on purpose). The phase exit
  criterion also needs real collected data, which requires an owner-run
  collect session on a network-reachable machine.

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
`docs/ROADMAP.md` (phase map, verified current state, defect register D1–D16,
human-action checklist) and `docs/SPEC.md` (the reconstructed specification —
every `§N` cited in the code resolves there).

`docs/`: AGENT_HANDOFF, ROADMAP, SPEC, AGENT_KICKOFF, VENUE_RESEARCH, ARCHITECTURE, API_SETUP,
COMPLIANCE, TRADING_LOGIC, LLM_MATCHING, RISK_MANAGEMENT, TESTING,
MODE_SEPARATION, CRASH_RECOVERY.

## Safety posture
- No real order can leave any non-live mode: simulated engines hold no
  adapter references; the Kalshi adapter refuses order placement unless
  constructed for live mode; the firewall re-verifies 15 conditions per
  order; UNKNOWN never means yes.
- Match certificates are APPROVED only for pairs whose venue relationship
  is **proven INDEPENDENT** — same-venue frontends (e.g. Kalshi contracts
  reached through Webull) and any unresolved relationship end in REVIEW
  for human handling, never in an APPROVED certificate.
- Nothing here is financial or legal advice. Verify venue terms, fees,
  and Utah eligibility yourself before funding live trading.
