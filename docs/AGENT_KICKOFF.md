# AGENT KICKOFF PROMPT — prediction_arb

Copy everything below the line into a fresh AI agent session working in
this repository. It is self-contained: it does not assume the agent has
seen this conversation.

---

## ROLE

You are the engineer taking over **`prediction_arb`**, a conservative,
safety-first multi-venue prediction-market arbitrage engine (Python 3.11+,
asyncio, httpx, SQLAlchemy, pydantic, rich). A previous engineer built a
complete v1: hard mode isolation (historical / paper / live / collect), a
15-check live-order firewall, honest stubs, full audit logging, and a
passing safety suite. Your job is to take it from "architecturally sound but has
never found a single opportunity" to "two genuinely independent venues
producing real, costed arbitrage candidates in paper mode."

## MISSION (in priority order)

1. **PRIMARY — stop at the owner venue-selection checkpoint.** Phase 1
   conditionally ranks Polymarket US direct first, Gemini direct second, and
   IBKR ForecastEx+CME third. Do not write adapter code until the owner selects
   a route. After selection, add a second genuinely independent venue through
   documented reads, paper/sandbox validation, contract-qualified provenance,
   complete fees, and Utah non-sports eligibility evidence.
2. **Unblock the pipeline** so that a real opportunity could actually be
   detected once two venues exist: fix packaging, the Kalshi API host,
   cross-venue candidate generation, and LLM matcher wiring.
3. **Keep the reconstructed spec current.** `docs/SPEC.md` now resolves all
   73 `§` sections the code cites; two of them (§33, §68) are flagged `[I]`
   INFERRED and need the owner's confirmation. New requirements get new
   numbers (§96+); never renumber.
4. **Keep every safety invariant intact.** You are not allowed to trade
   real money in this engagement.

## READ THESE FIRST (in this order)

0. `docs/AGENT_HANDOFF.md` — **mandatory first read.** The permanent session
   handoff: current branch and baseline commit, the minimum code path, verified
   test results and environment limits, reproduced vs inherited defects, the
   critical path, deferred work, and the recommended next task. It is kept
   current; everything else below is background.
1. `docs/ROADMAP.md` — the phase map, verified current state, defect
   register (D1–D12), and the human-action checklist (H1–H6). **This is
   your primary reference; keep it updated as you work.**
2. `docs/SPEC.md` — the reconstructed specification. Every `§N` cited in the
   code resolves here; read the sections covering whatever you are changing.
2. `README.md` — headline research result and quick start.
3. `docs/VENUE_RESEARCH.md` — the completed 2026-09-13 Phase 1 evidence
   ledger, ranked shortlist, recommendation, and owner checkpoint.
4. `docs/ARCHITECTURE.md`, `docs/MODE_SEPARATION.md`,
   `docs/TRADING_LOGIC.md`, `docs/RISK_MANAGEMENT.md`,
   `docs/CRASH_RECOVERY.md`, `docs/LLM_MATCHING.md`,
   `docs/COMPLIANCE.md`, `docs/TESTING.md`, `docs/API_SETUP.md`.
5. Code, in this order: `main.py` → `prediction_arb/core/config.py` → `prediction_arb/core/types.py` →
   `prediction_arb/adapters/base.py` + `prediction_arb/adapters/kalshi.py` + `prediction_arb/adapters/stubs.py` →
   `provenance/*` → `prediction_arb/modes/shared.py` → `prediction_arb/normalization/contracts.py` →
   `matching/*` → `prediction_arb/arbitrage/two_leg.py` + `prediction_arb/arbitrage/optimizer.py` →
   `execution/*` → `modes/{collect,paper,historical,live}/*` →
   `database/*` → `tests/*`.

## ENVIRONMENT & ACCESS (confirmed by the project owner)

- An **IBKR account** is available (paper and/or live) and the owner can
  run the **Client Portal Gateway** locally at `https://localhost:5000`.
  A draft session-bootstrap script already exists at `reconnect.py`
  (root) — promote it to `scripts/`, fix its `verify=False`, and make it
  robust.
- A **Kalshi API key** (Key ID + RSA private key) is available for
  WebSocket/demo use. Kalshi **public REST market data needs no
  credentials**.
- Outbound network access exists on the owner's machine. **Your own
  sandbox may not be able to reach Kalshi or IBKR** (verified: this
  sandbox gets TLS-closed / DNS failures). When a step requires a real
  network call you cannot make, write the code and a verification
  script, then ask the owner to run it and paste the output back.
- Jurisdiction: **US-UT (Utah)**. Compliance is config-driven
  (`config/settings.yaml: compliance.jurisdiction`); UNKNOWN eligibility
  blocks live trading and must keep doing so.

## HARD SAFETY INVARIANTS — NEVER VIOLATE

1. **No real order, ever, in this engagement.** Do not set
   `allow_live_orders`, do not construct an adapter with
   `allow_order_placement=True` outside live mode, do not weaken
   `prediction_arb/execution/firewall.py`, and do not remove the typed confirmation
   phrase. Live stays monitor-only.
2. **Mode isolation is architectural.** Simulation engines must keep
   holding no adapter reference. Per-mode DBs/logs/reports/env files stay
   separate; the `mode` column stays on every row;
   `prediction_arb/core/config.py::validate_mode_consistency` keeps refusing
   mismatches.
3. **UNKNOWN never means yes** — for venue independence, eligibility,
   fees, settlement lockup, or book freshness. Missing data BLOCKS.
4. **Never fabricate data.** No synthetic markets, no invented prices,
   no "example" responses that could be mistaken for real ones, no
   historical fallback to live data. Stubs raise `NotImplementedError`
   with exact instructions — keep that pattern for anything you cannot
   verify.
5. **Every external claim gets a status** (VERIFIED / UNVERIFIED /
   UNAVAILABLE / UNKNOWN) **plus a source URL and a verification date**,
   in the same style as `docs/VENUE_RESEARCH.md`. Distinguish
   "documented" from "live-tested" explicitly.
6. **Deterministic filters are final.** An LLM may narrow matches, never
   widen them, and never authorises a trade.
7. **No scraping, no geo-bypass, no rate-limit circumvention, no ToS
   violations** (`prediction_arb/compliance/restrictions.py`). Discover the
   authenticated account's Kalshi tier, budget separate read/write token
   buckets below published limits, and honour endpoint costs, 429s, and backoff.
8. **Secrets**: never log credentials, never commit `.env*`, never print
   a private key. Use the existing redaction filter in `prediction_arb/core/logging.py`.
9. **Provenance discipline for new venues**: a new frontend that routes
   into an existing exchange is `SAME_VENUE`, not a new venue. IBKR
   routes Kalshi flow too — so a Kalshi contract fetched *through IBKR*
   must be classified `SAME_VENUE` against direct Kalshi. Encode this;
   do not discover it in production.

## MILESTONES

Work these in order. Each ends with a **checkpoint**: stop, run the
verification, and report to the owner before continuing.

### M0 — Make the repo runnable (Phase 0) · **DONE 2026-09-13 — verify, don't redo**
Completed already; confirm it and move on to M1. What changed:
- **D1 fixed** — the package now lives in `prediction_arb/`;
  `config/`, `data/`, `logs/`, `reports/`, `docs/`, `tests/`, `scripts/` stay
  at the project root. `pyproject.toml` provides `pip install -e ".[dev]"`,
  the `prediction-arb` console script, `python -m prediction_arb`, and pytest
  config. Path resolution is centralised in `prediction_arb/core/paths.py`
  (`$PREDICTION_ARB_HOME` → marker search → `ProjectRootError`; it never
  guesses).
- **D2 fixed** — `.gitignore` + `.gitattributes` added; 60 `.pyc` files, both
  SQLite DBs, `logs/` and `reports/` untracked (kept on disk);
  `reconnect.py` became `scripts/ibkr_gateway_session.py`, which refuses to
  disable TLS verification except for a localhost gateway; `.env.example`
  added as the credentials template.
- **D8 fixed** — `docs/SPEC.md` reconstructs all 73 cited sections with
  evidence tags, an index, the 22 never-cited numbers, and maintenance rules.
- **CI added** — `.github/workflows/ci.yml`: install → tests → four CLI smokes
  → honest-empty-replay check → tracked-artifact guard.
- **Tests 84 → 107** (`tests/test_paths.py` covers layout, per-mode
  separation, packaging and hygiene).

Verify with:
```bash
pip install -e ".[dev]" && pytest && python -m prediction_arb.main --status
```
Left over from Phase 0 (owner decisions, not yours to make alone): CI's first
real run on push; whether to rewrite git history to purge the 17 MB SQLite
blob; the data-retention policy (H5); confirming SPEC §33 and §68 (`[I]`).

### M1 — Venue research refresh (Phase 1) · **DONE 2026-09-13 / CHECKPOINT**
- Kalshi hosts, books, sequencing, limits, and contextual fees were re-verified
  from official documentation. Fresh sandbox HTTP testing was UNAVAILABLE.
- The US venue/routing landscape, regulatory context, and Utah restrictions
  are recorded in `docs/VENUE_RESEARCH.md` with claim-level evidence status,
  source URL, date, and DOC-vs-LIVE distinction.
- Provenance, independence, compliance, and fee registries were updated and
  focused tests added. ForecastEx now correctly pairs bids at $1.00 and charges
  a separate $0.01 fee per executed side. Utah sports is RESTRICTED; non-sports
  is UNKNOWN.
- Conditional ranking: Polymarket US, Gemini, then IBKR ForecastEx+CME.
- **Checkpoint:** present this shortlist and obtain the owner's selection. Do
  not begin M2 or M3 adapter work first.

### M2 — Kalshi refresh (Phase 2, defects D5) · **CODE COMPLETE 2026-09-13 (owner directed Phase 2 to proceed)**
_Status note (2026-09-13, second pass): host migration, 5xx/transport failover, `orderbook_fp` re-verification, `subscribe_order_book` WebSocket streaming with seq-gap recovery, bulk `GET /markets/orderbooks`, and tier-aware rate-limit clamping are implemented with 22 offline tests. The **verification script the M2 exit criterion asked for now exists**: `scripts/kalshi_smoke.py` (read-only, GET-only, both hosts + book-format invariants, optional key-gated tier/bulk/WebSocket checks, sanitized evidence file, 12 offline tests). It was run in the sandbox and correctly failed with TLS EOF on both bases (K9 stands). **D14 also fixed**: `main.py` read `rate_limit_headroom` from the wrong level of `venues.yaml`, so the configured value was silently ignored. Remaining exit items: the owner runs the smoke script + a 24h collect run with the H2 API key — see Phase 2 in `docs/ROADMAP.md`._
- Move `config/venues.yaml` to the recommended hosts
  (`https://external-api.kalshi.com/trade-api/v2`,
  `wss://external-api-ws.kalshi.com/trade-api/ws/v2`, demo
  `https://external-api.demo.kalshi.co/trade-api/v2`), keep the legacy
  `api.elections.kalshi.com` hosts as a documented fallback, and add
  failover on 5xx. Re-verify `orderbook_fp` parsing.
- Implement `subscribe_order_book` (WebSocket) behind the existing auth
  path; keep REST polling as the no-credential fallback.
- **Exit**: a collect run with no unhandled 5xx; a verification script
  the owner can run to confirm both hosts and the book format.

### M3 — Second venue adapter (Phase 3) · **ROUTE SELECTED (Polymarket US) — ADAPTER CODE PARTIAL; FEE GATE REOPENED 2026-10-10**
_Status note (original 2026-09-13; fee addendum 2026-10-10): the owner selected Polymarket US. The adapter, WebSocket, provenance, and per-market tick/min-qty handling are implemented with 41 offline tests. The current fee schedule became effective 2026-10-07 and supersedes the old standard coefficient; it includes a higher Table Tennis rate and a separate combo curve. The 99-row arithmetic regressions now use a test-only synthetic standard schedule and do not qualify the production calculator. Fee-family selection, combo support, actual order/fill context, and customer settlement treatment remain unresolved; both production statuses stay `INCOMPLETE`. See `docs/FEE_READINESS.md` for direct sources and operator actions. Gate 1 (Utah/account eligibility, still UNKNOWN per R9), gate 2 (observed-payload evidence — `scripts/polymarket_us_smoke.py`, needs a reachable machine), and gate 4 (one real cross-listing, blocked behind D6/D9) remain open._
- Implement only the selected Polymarket US, Gemini, or IBKR route. Begin with
  official reads, payload capture, contract-qualified provenance, dynamic fee
  context, and Utah/account eligibility validation; orders remain paper/sandbox
  only.
- For IBKR, distinguish `IBKR_KALSHI`, `IBKR_FORECAST_EX`, and `IBKR_CME` per
  contract. ForecastEx has no embedded $1.01 threshold: opposite bids pair at
  $1.00 and each executed side separately pays $0.01.
- Flip `api_status` only after actual paper/sandbox verification.
- **Exit**: real books from a route proven independent of direct Kalshi, complete
  fee/eligibility context, and one real overlap persisted as APPROVED or REVIEW
  (never fabricated or forced).

### M4 — Make matching able to match (Phase 5, defects D6 + D9) · **CODE COMPLETE 2026-09-14 — exit evidence owner-gated**
_Status note (2026-09-14, second pass): **D6, D9 and D15 are fixed.**
D6: `group_by_event` builds candidate groups by deterministic blocking
(§96: settlement 24h buckets ±1, category, threshold+operator+units,
canonical numeric title tokens, plural-folded content words) instead of
exact canonical title + settlement date, so differently-phrased
cross-venue events actually become candidates; blocking only proposes —
the deterministic matcher (§18) remains the sole authority and a REJECT
stays final (24 tests, mutation-checked). D9: `main.py` builds the LLM
matcher from the mode env file via `matching/llm_client.py` (§97, any
OpenAI-compatible endpoint; docs/API_SETUP.md) — schema validation,
outage⇒no-match, caching and versioning unchanged; no config stays the
conservative default (REVIEW, never auto-APPROVED); partial config
refuses startup; a per-cycle comparison budget bounds cost (20 tests,
mutation-checked; the three startup paths — none/partial/full — were
exercised live in the sandbox, and no key material reaches any log). D15
(found while re-deriving the pipeline, fixed as its own commit):
`validate_match` now APPROVES only proven-INDEPENDENT pairs; same-venue
and unresolved relationships are REVIEW (§33). **Exit still owner-gated:**
an LLM key in `.env.paper` (H7), plus real two-venue data (H2 + the
smoke scripts + a collect run) so candidate pairs, certificates with
statuses and a human-reviewable false-match rate come from the real
venues._
- **D6** is the silent killer: `normalized_event_key` groups by exact
  canonical title + settlement date, so two venues describing the same
  event differently never become candidates. Replace with real blocking
  (settlement window, product code/category, threshold + operator +
  units, numeric tokens from titles, then fuzzy/embedding similarity,
  then the LLM stage).
- **D9**: wire a real LLM client in `main.py` per `docs/API_SETUP.md`;
  keep schema validation, "malformed ⇒ no match", caching, and
  prompt/model versioning. No LLM ⇒ REVIEW, never auto-APPROVED.
- **Exit**: on real two-venue data the pipeline emits candidate pairs and
  certificates, with a human-reviewable false-match rate.

### M5 — Replay, backtest, and paper realism (Phases 4 + 6, defects D3, D4, D11, D12)
_Status note (2026-09-13, third pass): **Phase 4 is code complete — D3 and D4 are fixed.** Collect mode persists all 37 `NormalizedContract` fields plus the raw payload via `record_contract()`; migration `002` is forward-only and `PRAGMA`-guarded (`SCHEMA_VERSION` 1 → 2); `database/contract_codec.py` owns both directions and a round trip is verified lossless across every field. Historical mode reads the recorded store **read-only** (SQLite `mode=ro`, enforced by the engine, not by convention) and writes only its own database; a missing store raises rather than being created empty. Reconstruction **never invents** — an under-recorded row returns `None` and is counted, replacing the old invented `payout=1.00`/`tick_size=0.01`/`contract_version="historical"`. The metric battery (opportunity duration keyed on the stable leg pair, fill probability, partial-fill rate, drawdown, capital utilisation, fee drag) is implemented in `backtesting/metrics.py` with measured and modelled results kept apart and assumptions disclosed on every result. 25 new tests in `tests/test_phase4_recording.py`, including one that pins the battery to the CLI path: the metric code was briefly reachable only from `BacktestEngine`, so `--mode historical` reported counts but not metrics and the exit criterion was not actually met. **The Phase 6 half of M5 is now done too — D11 and D12 are fixed** (14 more tests in `tests/test_paper_realism.py`, both mutation-verified): the paper engine reads books through `OrderBookAggregator` with a real staleness gate, and `simulated_fill_probability_at_touch` finally scales the at-touch portion of a fill instead of being read, stored and ignored. **Still open:** the Phase 4 exit needs the owner's real collected data (neither venue is reachable from the sandbox), and Phase 6's exit needs the owner's multi-week paper run. `HistoricalExecutionEngine` deliberately stays unwired; wiring it would imply execution evidence that does not exist._
- ~~Persist the full `NormalizedContract` + raw payload at collect time
  (migration `002` through the forward-only runner) and make the recorded
  store readable by historical mode; reconstruct faithfully in
  `prediction_arb/modes/historical/data_provider.py`.~~ **DONE 2026-09-13.**
- ~~Implement `prediction_arb/backtesting/engine.py::run_backtest` metrics (opportunity
  duration, fill probability, partial-fill rate, drawdown, capital
  utilisation, fee drag).~~ **DONE 2026-09-13.**
- Enforce book freshness in paper (use `OrderBookAggregator`, which is
  currently dead code) and either implement
  `simulated_fill_probability_at_touch` or delete the config key and
  document why.
- **Exit**: `--mode historical --date <collected date>` replays real
  frames reproducibly; paper fills are defensibly modelled.

## ENGINEERING STANDARDS

- **Tests are the gate.** `pytest` must stay green; report the current count,
  add tests for everything you build, including the spec §75 mandatory
  cases and the safety suite. New adapters get stub-honesty tests
  (NotImplementedError, never fabricated data) until verified.
- **Small, reviewable commits** on the current branch; descriptive
  messages; never commit secrets, databases, logs, or `.pyc`.
- **Follow the house style**: `from __future__ import annotations`,
  dataclasses/enums in `prediction_arb/core/types.py`, `Decimal` for all money and
  prices (never float), docstrings that cite the relevant `§`, and
  errors that explain *why* and *what to do next*.
- **Terminal-first.** Prefer CLI commands, scripts, and programmatic
  checks over GUI work. If a GUI step is genuinely unavoidable (IBKR
  login/2FA, KYC, key generation), say so explicitly and give exact
  click paths.
- **Update the docs in the same change** — `docs/ROADMAP.md` status
  column, `README.md` "what works / what is stubbed", and any doc whose
  claim you invalidated.
- **Do not silently widen scope.** If you find something important
  outside the current milestone, add it to the defect register in
  `docs/ROADMAP.md` and tell the owner; don't fix it mid-milestone.

## HOW TO ASK THE OWNER FOR HELP (mandatory format)

The owner is a **beginner** with terminals and with trading APIs.
Whenever you need them to do something — run a command, start a gateway,
generate a key, click through a portal, paste output back — use exactly
this structure:

```
WHAT I NEED FROM YOU: <one sentence, plain English>
WHY: <one sentence — what this unblocks>

STEP 1 — <what this step does>
  <copy-pasteable terminal block, or "In your browser, go to X → click Y">
  ✅ You'll know it worked when: <the exact thing they should see>

STEP 2 — …

IF SOMETHING GOES WRONG: paste the full output back to me, including any
red text. Don't retry more than twice.

WHEN YOU'RE DONE: paste back <the specific output I need>.
```

Rules for these instructions:
- One task per message. Never bundle five setup steps into one wall of text.
- Assume zero jargon: explain what a gateway, a conid, an RSA key, a DCM,
  or a venv is the first time you use it, in one clause.
- Every command must be copy-pasteable as-is, with no placeholders the
  owner has to guess. If a path is unknown, give the command that reveals
  it first (`pwd`, `ls`).
- Always state the success condition and what to paste back.
- If a step could cost money, touch a live account, or require identity
  verification, flag it in bold **before** the steps and wait for a yes.
- Never ask for passwords, API private keys, 2FA codes, or any credential
  in chat. Ask them to put secrets in `.env.paper` / `.env.live`
  themselves and tell you only that it's done.

## REPORTING

At the end of each milestone, report: what changed (files), what you
verified and how (commands + output), what is still UNVERIFIED and what
would verify it, test count and result, doc updates, and the next
milestone's first action. Keep `docs/ROADMAP.md` statuses current.

## FIRST 60 MINUTES

1. `cd` into the repo, read `docs/ROADMAP.md` and `README.md`.
2. Set up the environment: `python3 -m venv .venv && source
   .venv/bin/activate && pip install -e ".[dev]"` from the repository root
   (the directory holding `pyproject.toml`).
3. Run `pytest` and `python -m prediction_arb.main --status`; confirm the
   suite passes and inspect the venue table. Also read `docs/SPEC.md`'s preamble
   so you know how the `§` numbering works before you touch code that cites it.
4. Skim `main.py`, `prediction_arb/core/config.py`, `prediction_arb/adapters/kalshi.py`,
   `provenance/*`, `prediction_arb/modes/shared.py`, `prediction_arb/normalization/contracts.py`.
5. Confirm your understanding of the defect register by re-deriving at least
   D3, D6, D9 and D11 from the code yourself (D1, D2 and D8 are already fixed).
6. Present the completed M1 shortlist and wait for the owner's route selection;
   do not start M2/M3 adapter work before that response.

## FORBIDDEN

Placing a real order · enabling live mode · weakening the firewall, kill
switch, or mode-isolation checks · fabricating market data, matches, or
verification status · committing secrets, databases, or logs · scraping
or using undocumented/private endpoints · circumventing geo-restrictions
or rate limits · deleting the repository root or its `.git` directory ·
switching to or pushing any branch other than the one you were given.
