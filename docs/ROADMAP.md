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
| `pytest` | **107 passed** (~0.8s): the original 84 plus 23 layout/packaging tests added in Phase 0. Runs on a fresh clone with no hacks |
| `python -m prediction_arb.main --status` | Works: kalshi=VERIFIED, forecast_ex=API_DOCUMENTED_UNTESTED; independence matrix correct |
| `python -m prediction_arb.main --readiness` | 13,801 snapshots recorded; **0 opportunities ever analyzed**; live NOT RECOMMENDED |
| Live-capable venues | **1** (Kalshi). Cross-venue arbitrage is therefore impossible today |
| `data/paper/…sqlite3` | 21,892 markets (all Kalshi, mostly sports), 13,801 book snapshots spanning 2026-08-30 → 2026-09-05, tagged `mode=collect` |
| `data/historical/…sqlite3` | **Empty** (0 rows in every table) |
| `reports/historical/…json` | `{"frames": 0, "opportunities": 0}` |
| `logs/collect/collect_20260831.jsonl` | 32 error rows: 18× DNS failure, 14× Kalshi HTTP 500 (2026-08-31 → 2026-09-04) |
| Outbound network from this sandbox | Kalshi unreachable (TLS closed) — venue calls must be verified on the user's machine |

**Bottom line:** the safety architecture is real and tested; the
*opportunity-finding* half of the system has never produced a single
candidate, and four independent reasons (D6, D7, D9, and having only one
venue) each guarantee that it cannot yet.

---

## 2. Phase map

| Phase | Name | Status | Primary exit criterion |
|---|---|---|---|
| 0 | Repo hygiene, packaging, runnability, spec reconstruction | **DONE 2026-09-13** | Fresh clone → `pip install -e ".[dev]"` → 107 tests pass → CLI runs, no manual hacks |
| 1 | Venue & API research refresh (2026) | STALE (dated 2026-08-30) | Ranked, evidence-backed shortlist of second-venue candidates |
| 2 | Kalshi adapter refresh + streaming | PARTIAL/BROKEN (D5) | 24h collect run with ≥99% cycle success; WebSocket streaming in paper |
| 3 | **Second independent venue adapter** | NOT STARTED — **current objective** | Real books from a venue proven INDEPENDENT of Kalshi, in paper mode |
| 4 | Data lineage, replay, backtest metrics | BROKEN (D3, D4) | `--mode historical --date …` replays recorded data and reports metrics |
| 5 | Matching that can actually match | BROKEN (D6, D9) | APPROVED certificates produced from real cross-venue data |
| 6 | Paper validation & realism | PARTIAL (D11, D12) | Multi-week paper run with realistic fill/staleness modelling |
| 7 | Compliance & legal re-verification | STALE | Utah eligibility per venue per category, re-verified with sources |
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
| CI: install → 107 tests → four CLI smokes → honest-empty-replay check → tracked-artifact guard | `.github/workflows/ci.yml` (new) |
| Tests: 23 new layout/packaging/hygiene tests (84 → 107) | `tests/test_paths.py` (new), `tests/conftest.py` |
| Docs updated for the new layout and commands | `README.md`, `docs/API_SETUP.md`, `docs/ARCHITECTURE.md`, `docs/TESTING.md`, and package-qualified module paths throughout `docs/` |

Still open from Phase 0 (deliberately not done unilaterally):
1. **CI has not run** — it executes on the first push/pull request.
2. **Git history still contains the 17 MB SQLite blob.** Untracking stops the
   bleed; removing it from history needs a rewrite (`git filter-repo`/BFG) and
   must be the owner's decision.
3. **Data-retention policy (H5)** is still the owner's call; `.gitignore`
   documents the recommendation (keep locally, out of git).
4. **`docs/SPEC.md` §33 and §68 carry `[I]` INFERRED flags** — confirm or
   correct them.

---

### Phase 1 — Venue & API research refresh  ·  STALE

`docs/VENUE_RESEARCH.md` is dated 2026-08-30 and its headline
conclusion — "the only genuine cross-venue pair is Kalshi ↔ ForecastEx,
and ForecastEx needs an IBKR account" — needs re-testing. Reported
developments since then (**all UNVERIFIED by this repo; verify before
relying on them**):

| Development | Reported source | Why it matters |
|---|---|---|
| IBKR's prediction-market platform now fronts **three** exchanges — Kalshi, CME Group and ForecastEx — from one account and one API | forecasttrader.interactivebrokers.com/en/home.php | One integration can unlock **two** venues that are independent of Kalshi (CME, ForecastEx) |
| Documented IBKR Web API event-contract workflow: `GET /iserver/secdef/search?symbol=FF`, `/iserver/secdef/strikes`, `/iserver/secdef/info?conid=…&exchange=FORECASTX&secType=OPT`, `GET /iserver/marketdata/snapshot?conids=…&fields=31,84,85,86,88,7059`, `/iserver/marketdata/history`, orders via the standard Web API order endpoint; routing destination `FORECASTX`; ForecastEx contracts modelled as index options (Call=YES, Put=NO); **cannot be sold, only bought** — exit = buy the opposite side, IB nets automatically | interactivebrokers.com/campus/ibkr-api-page/event-contracts | This is the concrete spec for implementing `prediction_arb/adapters/forecast_ex.py` |
| Kalshi now recommends `https://external-api.kalshi.com/trade-api/v2` (demo: `https://external-api.demo.kalshi.co/trade-api/v2`, WS: `wss://external-api-ws.kalshi.com/trade-api/ws/v2`); the `api.elections.kalshi.com` hosts the repo uses remain "supported for compatibility" | docs.kalshi.com/getting_started/api_environments | Likely related to the HTTP 500s in the collect log (D5) |
| **Polymarket US** is live as a CFTC DCM+DCO with its own API surface and US-facing docs (docs.polymarket.us); KYC + US account required for API keys; the *international* platform geo-blocks US trading while its read endpoints stay open | defirate.com/prediction-markets; quantvps.com/blog/polymarket-us-api-available | Potentially the deepest non-Kalshi US liquidity, and a real CLOB API with WebSocket |
| Other live CFTC-regulated venues: **Gemini Predictions** (Gemini Titan DCM + Olympus DCO), **Crypto.com CDNA / OG Predictions**, **Railbird Exchange**, CME-routed **FanDuel Predicts** and **DraftKings Predict**, **Novig** (Ludlow DCM approved 2026-06, not launched) | defirate.com/prediction-markets (2026-08-26) | More candidate independent venues; each needs its own evidence trail |
| Legal pressure: Wisconsin AG complaints (2026-04-23/24) against Kalshi, Coinbase, Polymarket, Robinhood, Crypto.com; Robinhood halting Michigan sports contracts by 2026-09-09; Sixth Circuit preemption fight; CFTC withdrew the 2024 event-contract proposal (Feb 2026) and issued a no-action letter to 19 DCMs (2026-05-13) | mexc/defirate/rotowire summaries | Compliance ledger (Phase 7) must be re-verified; Utah sports remains UNKNOWN |
| ForecastEx "also carries Robinhood order flow" | defirate.com/prediction-markets | If true, Robinhood ↔ ForecastEx is SAME_VENUE — must be encoded, not assumed |

Deliverables
- `docs/VENUE_RESEARCH.md` v2 with per-venue: entity structure, DCM/DCO
  registration, matching engine, liquidity source, clearing, API surface
  (documented vs live-tested), fee schedule, settlement, Utah
  eligibility, automated-trading terms. Every claim tagged
  VERIFIED / UNVERIFIED / UNAVAILABLE with source URL + date.
- Updated `prediction_arb/provenance/exchange_registry.py` (new records **only** with
  evidence), `prediction_arb/provenance/independence.py` matrix,
  `prediction_arb/compliance/jurisdiction.py` ledger, `config/fees.yaml` schedules.
- A ranked candidate table: independence confidence × API quality ×
  Utah eligibility × contract overlap with Kalshi × liquidity.

**Trap to encode, not discover later:** IBKR routes *Kalshi* flow too.
A Kalshi contract fetched through IBKR is `SAME_VENUE` with a Kalshi
contract fetched directly, even though the API and the account differ.
The provenance registry must model "frontend vs underlying exchange"
for every new record, exactly as it already does for Webull/Moomoo.

---

### Phase 2 — Kalshi adapter refresh + streaming  ·  PARTIAL/BROKEN

- Switch `config/venues.yaml` to the recommended hosts, keep the legacy
  host as a documented fallback, and add host-failover on 5xx.
- Re-verify `orderbook_fp` parsing against the new host (dollar-string
  levels, fractional quantities, YES-ask synthesis from NO bids).
- Implement `subscribe_order_book` (currently raises
  `NotImplementedError`) — WebSocket auth is required even for market
  data, so this needs the user's Kalshi API key (H2).
- Consider `GET /markets/orderbooks` (bulk, up to 100 tickers,
  **requires auth**) to replace per-ticker polling; polling 500 markets
  at 8 req/s takes ~60s per cycle, which is why book staleness (D11)
  matters so much.
- Respect published rate limits (~10 reads/s, ~5 order writes/s); never
  circumvent them (`prediction_arb/compliance/restrictions.py`).

Exit: a 24-hour `--mode collect` run with ≥99% successful cycles and no
unhandled 5xx; streaming books in paper mode with sub-second staleness.

---

### Phase 3 — Second independent venue adapter  ·  **CURRENT OBJECTIVE**

Two credible routes; the roadmap recommends doing **A first** because
the user already has IBKR access and one integration yields two
independent venues.

**Route A — IBKR Client Portal Web API (ForecastEx + CME event contracts)**
1. Make gateway session handling reliable and scriptable (H3): start
   the gateway, authenticate, detect session expiry, re-auth. The root
   `reconnect.py` scratch script is the seed of this; promote it to
   `scripts/ibkr_gateway.py` with proper TLS handling.
2. Discovery: `secdef/search` → `secdef/strikes` → `secdef/info` to map
   ForecastEx product codes to IBKR `conid`s; cache the mapping.
3. Market data: `marketdata/snapshot` fields → `NormalizedContract` +
   `OrderBook`. Encode ForecastEx `PAIRED_BID` microstructure: no asks;
   synthetic YES ask = 1.01 − best NO bid; the $0.01 is the exchange fee.
4. Orders: standard Web API order endpoint, **paper account only**,
   behind the existing `allow_order_placement` gate. ForecastEx
   positions cannot be sold — model exit as buying the opposite side.
5. Wrap any synchronous SDK (TWS API / ib_insync) in
   `asyncio.to_thread` per the architecture doc.
6. Flip `api_status` only after paper verification, and add the
   provenance/fee/eligibility records from Phase 1.

**Route B — Polymarket US (CLOB)**
- Requires a US Polymarket account + KYC + API keys (H4). Read
  endpoints are open; trading is authenticated. Verify Utah
  availability *before* building, and never geo-bypass
  (`prediction_arb/compliance/restrictions.py` forbids it).

Exit criteria (either route): paper mode fetches real books from the new
venue; `--status` shows it VERIFIED; the independence matrix shows
INDEPENDENT vs Kalshi with evidence; ≥1 event where both venues list an
economically equivalent contract is identified and persisted as a match
certificate (APPROVED or REVIEW — never fabricated).

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

### Phase 7 — Compliance & legal re-verification  ·  STALE

Re-verify and record, with sources and dates: Utah treatment of sports
event contracts per venue; each venue's terms on automated/API trading;
current fee schedules (`config/fees.yaml` `verified_at`, live mode
refuses stale schedules); the 2026 litigation/regulatory developments
listed in Phase 1. UNKNOWN keeps blocking live trading — that is the
design, not a bug. Nothing here is legal advice.

---

### Phase 8 — Live readiness & first live path  ·  NOT STARTED (HUMAN)

Sequence: IBKR **paper** account → Kalshi **demo**
(`external-api.demo.kalshi.co`) → live with hard caps. Rehearse: kill
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
| D1 | ~~Package unimportable as committed~~ **FIXED 2026-09-13**: package moved to `prediction_arb/`, `pyproject.toml` added, three working entry points | Was: `import prediction_arb` → ModuleNotFoundError on a fresh clone. Now: `pip install -e ".[dev]"` → 107 tests pass | 0 |
| D2 | ~~No `.gitignore`; artifacts tracked~~ **FIXED 2026-09-13**: `.gitignore` + `.gitattributes` added, 60 `.pyc` + both DBs + `logs/` + `reports/` untracked (files kept on disk), `reconnect.py` → `scripts/ibkr_gateway_session.py` with safe TLS handling. *Caveat: the 17 MB blob remains in git **history**; purging it needs a history rewrite (owner's call)* | `git ls-files \| grep -c pycache` → 0; CI step guards against regressions | 0 |
| D3 | Collect data is invisible to historical replay | collect → `data/paper/…` (tagged `mode=collect`); historical → `data/historical/…` (0 rows); report shows `frames: 0` | 4 |
| D4 | Recorded markets lose the metadata matching needs | `MarketRow` has no settlement/rules/threshold/payout/version columns; `_reconstruct_contract` sets `settlement_time=None`, `resolution_source=""` → `deterministic_match` rejects on both | 4 |
| D5 | Kalshi on legacy host, intermittent failures, no failover | `config/venues.yaml` → `api.elections.kalshi.com`; collect log: 18 DNS failures + 14 HTTP 500s | 2 |
| D6 | Cross-venue candidate generation cannot produce candidates | `normalized_event_key` = exact canonical title + settlement date | 5 |
| D7 | Venue research stale; the "only one independent pair" premise is likely obsolete | `docs/VENUE_RESEARCH.md` dated 2026-08-30 vs Phase 1 findings | 1 |
| D8 | ~~Spec document missing~~ **FIXED 2026-09-13**: `docs/SPEC.md` reconstructs all 73 cited sections from code/docs/tests/config evidence, with `[I]` flags on the two thin ones (§33, §68) and an appendix of the 22 numbers that have no surviving citation | Was: 73 distinct `§` sections cited at 224 sites with no spec file. Now: `grep -c "^### §" docs/SPEC.md` → 73 | 0 |
| D9 | No LLM configured ⇒ no APPROVED certificates ⇒ nothing is ever evaluated | `main.py`: `LLMMatcher(client=None)`; `optimizer.search_event` skips pairs without approval | 5 |
| D10 | Live mode is monitor-only; readiness check hardcodes the venue count | `prediction_arb/modes/live/engine.py::run`; `main.py::cmd_readiness` (`False` literal) | 8 |
| D11 | No book-freshness enforcement in paper; `OrderBookAggregator` is dead code | aggregator referenced only in tests; `maximum_book_age_ms` enforced solely by the live firewall | 6 |
| D12 | Configured fill probability is ignored | `fill_probability_at_touch` stored in `prediction_arb/execution/simulated.py` and never read | 6 |

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
pytest                                     # should end with "107 passed"
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
5. Sessions expire (roughly daily). Re-run steps 2–4 when they do; the
   `reconnect.py` script at the repo root is a draft of this flow.

**H4 — Polymarket US (only if Phase 1 picks Route B)**
1. Verify Polymarket US is available to you in Utah **before** building
   anything; do not attempt to bypass any geographic restriction.
2. Complete KYC in the official app, then generate API keys in the
   developer portal. Save the private key immediately — it is shown once.

**H5 — Decide the data policy**
Recorded market data is already 17 MB for one day of collection. Decide
how long to retain it and whether any of it belongs in git (recommended:
none; keep it local or in external storage).

**H6 — Say the word before anything live**
Nothing in Phases 0–7 places a real order. Live work starts only in
Phase 8, only after paper/demo drills, and only with your typed
confirmation phrase.
