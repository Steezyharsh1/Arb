# SPECIFICATION (RECONSTRUCTED) — prediction_arb v1

**Status:** RECONSTRUCTED; causal historical-evaluation §102 added 2026-10-10
(UTC), after evidence-state §101.
**Confidence:** derived, not authoritative. See "How this was reconstructed".

The original numbered specification that this codebase was built against was
never committed to the repository. Its section numbers survive across code,
config, docs and tests. The original audit counted 73 distinct references at
224 sites on 2026-09-13. After the historical addition (§102), **80 sections**
are reconstructed. The current tracked/nonignored Python + YAML inventory has
**313 literal citation sites across 76 distinct numbers** (2026-10-10);
every code/config citation resolves here. Documentation-local numbered headings
are not global spec citations and are excluded from that count. The older
451-site code/doc audit is historical, not a current measurement.
This document reconstructs the cited requirements from surviving code,
documentation, tests and configuration; numbers with insufficient surviving
evidence remain listed as UNKNOWN in Appendix B. Section §101 is the adapter-
evidence addition; §102 records the user-requested causal historical requirements. Both have code/test evidence.

**Coverage is explicit:** reconstructed sections and unresolved historical
gaps are listed separately. Do not infer content for an UNKNOWN number.

## How this was reconstructed

Each section below was derived **only** from surviving evidence:

| Tag | Meaning |
|---|---|
| `[C]` | RECONSTRUCTED-FROM-CODE — module docstrings, class/function behaviour, error messages |
| `[D]` | RECONSTRUCTED-FROM-DOCS — the ten documents in `docs/` |
| `[T]` | RECONSTRUCTED-FROM-TESTS — assertions in `tests/` |
| `[G]` | RECONSTRUCTED-FROM-CONFIG — `config/*.yaml` keys and comments |
| `[I]` | INFERRED — evidence is thin; **needs owner confirmation** before being treated as a requirement |

Rules this document follows (and that future edits must follow):

1. **Nothing is invented.** Where the evidence does not support a statement,
   the section says so and is tagged `[I]`.
2. **Numbering is frozen.** Section numbers are the ones the code cites. New
   requirements get NEW numbers appended at the end (§96+); never renumber,
   because 208 call sites depend on the existing numbers.
3. **Gaps stay gaps.** Numbers with no surviving citation are listed in
   Appendix B as UNKNOWN. Do not fill them in by guessing what "§34 probably
   said".
4. **Part groupings are inferred** from the numeric bands, not from the
   original document's structure.
5. Normative words (MUST / MUST NOT / SHOULD / MAY) are used as in the code's
   own behaviour: a MUST is something the implementation enforces or a test
   asserts.

---

## Part I — Scope, honesty and platform rules (§3–§13)

### §3 — v1 scope boundary  `[C,D]`
**Requirement.** v1 delivers guaranteed (TYPE A) two-leg cross-venue
arbitrage only. Multi-leg/N-outcome arbitrage, logical (TYPE B) arbitrage,
graph cycle search, the web dashboard and email/webhook alerts are OUT OF SCOPE
for v1 and MUST ship as stubs with stable interfaces. Historical evaluation now
includes the explicitly conditional snapshot-based simulator/metrics in §102;
tick-accurate feed reconstruction, realized settlement P&L and a genuine
performance evaluation remain unsupported. No simulation guarantees execution.
**Evidence.** `README.md` ("What is stubbed … §3 v1 scope"),
`prediction_arb/arbitrage/graph.py`, `arbitrage/logical.py`,
`arbitrage/multi_leg.py`, `backtesting/engine.py`, `monitoring/dashboard.py`,
`docs/ARCHITECTURE.md` ("Extending").

### §5 — Evidence-scoped integrations, no fabricated claims  `[C]`
**Requirement.** A venue integration MUST report the strongest evidence
actually held: documentation, endpoint connectivity, usable market data, and
full adapter verification are distinct claims. A partial implementation MAY
be used only for the evidenced, explicitly admitted read-only surface. It MUST
NOT claim `VERIFIED` from documentation, connectivity, mocks, or an unavailable
network attempt; it MUST NOT fabricate venue data. Missing evidence stays
unknown, and an unavailable attempt is neither a broken-adapter finding nor
verification.
**Evidence.** `prediction_arb/adapters/evidence.py`, `core/types.py`
(`APIStatus`), `prediction_arb/main.py` (`cmd_status`),
`tests/test_kalshi_smoke_script.py`, `tests/test_polymarket_us_smoke_script.py`.

### §7 — Structured stub contract  `[C,T]`
**Requirement.** A stub adapter MUST be importable, fully typed against the
adapter interface, report an honest `APIStatus`, and raise
`NotImplementedError` with a descriptive message (venue, capability, reason,
where to look) from every capability method. Stubs MUST NOT fabricate data,
and `healthcheck()` MUST report unhealthy rather than optimistic.
**Evidence.** `prediction_arb/adapters/stubs.py` (`StubAdapter._blocked`),
`tests/test_safety.py` (stub-honesty tests).

### §9 — Concurrency model  `[C,D]`
**Requirement.** The system is asyncio throughout. Shared mutable state (order
books, exposure, simulated cash) MUST be guarded (e.g. `asyncio.Lock`) and
MUST tolerate concurrent tasks. Any synchronous SDK (IBKR TWS API, Futu OpenD)
MUST be wrapped in `asyncio.to_thread` rather than blocking the event loop.
Out-of-order book updates (non-increasing sequence numbers) MUST be ignored.
**Evidence.** `prediction_arb/orderbooks/aggregator.py`,
`risk/exposure.py`, `execution/simulated.py`, `docs/ARCHITECTURE.md`
("Concurrency"), `docs/VENUE_RESEARCH.md` (IBKR/TWS note).

### §10 — Venue adapter interface  `[C]`
**Requirement.** Every venue MUST implement one normalized interface
(`PredictionMarketAdapter`): lifecycle (`connect`/`disconnect`), account
(`get_account`/`get_balances`/`get_positions`), market data
(`get_markets`/`get_market`/`get_order_book`/`subscribe_order_book`), orders
(`get_open_orders`/`place_order`/`cancel_order`/`cancel_all_orders`/
`get_order`/`get_fills`) and metadata (`get_supported_order_types`/
`get_market_microstructure`/`get_api_status`/`healthcheck`). Venue-specific
quirks MUST stay inside the adapter; nothing upstream may branch on venue
identity.
**Evidence.** `prediction_arb/adapters/base.py`, `adapters/kalshi.py`
(`orderbook_fp` handling, ask synthesis).

### §13 — Operational restrictions  `[C]`
**Requirement.** The system MUST NOT scrape, MUST NOT use undocumented or
private endpoints, MUST NOT circumvent geo-restrictions or rate limits, and
MUST NOT violate venue terms of service. Adapters MUST stay on official,
documented hosts (`assert_endpoint_is_official`).
**Evidence.** `prediction_arb/compliance/restrictions.py`,
`docs/COMPLIANCE.md`.

---

## Part II — Venues, provenance and normalization (§14–§17)

### §14 — Provenance registry with evidence  `[C,D]`
**Requirement.** Every venue MUST have a provenance record capturing frontend,
underlying exchange, clearing entity, contract issuer, matching engine,
liquidity source, settlement authority, order routing, confidence
(HIGH/MEDIUM/LOW), version, source URLs and verification date. Records MUST be
populated only from official announcements, official API documentation or CFTC
registration data — never from assumption. A venue with no record is UNKNOWN.
Clearing entities are tracked in their own registry on the same terms.
**Evidence.** `prediction_arb/provenance/exchange_registry.py`,
`provenance/clearing_registry.py`, `docs/VENUE_RESEARCH.md`.

### §15 — Independence engine  `[C,T]`
**Requirement.** Venue independence MUST be computed deterministically from
contract-qualified provenance. Sharing an underlying DCM, matching system,
contract issuer, or settlement authority collapses the pair to SAME_VENUE.
Sharing only a third-party clearing organization is non-dispositive: separate
DCMs may clear through one DCO while retaining separate contracts and books.
Missing/conflicting critical fields and generic multi-route frontend IDs are
UNKNOWN. UNKNOWN MUST NEVER be treated as independent, and only fully verified
INDEPENDENT pairs are eligible for guaranteed arbitrage.
**Evidence.** `prediction_arb/provenance/independence.py`,
`tests/test_units.py` (independence matrix), `tests/test_spec_cases.py`
(cases 3 and 4).

### §16 — Contract normalization  `[C]`
**Requirement.** Adapters emit `NormalizedContract`; the normalization layer
adds cross-venue grouping keys (candidate blocking per §96; canonical
outcome). Normalization MUST be conservative: it creates CANDIDATE groups
only and never decides equivalence — that is the matching pipeline's job
(§18–§20).
**Evidence.** `prediction_arb/normalization/contracts.py`,
`normalization/outcomes.py`, `normalization/venues.py`,
`normalization/settlement.py`.

### §17 — Outcome types  `[C]`
**Requirement.** v1 supports BINARY YES/NO contracts. Multi-outcome, ranged
and bracket markets MUST have a clean interface but MUST NOT be assumed to
cover the outcome space: `covers_full_outcome_space` returns False for
anything it cannot prove, and sum-based arbitrage requires a single common
payout across the outcome set.
**Evidence.** `prediction_arb/normalization/outcomes.py`,
`arbitrage/multi_leg.py`.

---

## Part III — Matching pipeline (§18–§22)

### §18 — Stage 1: deterministic filter  `[C,T]`
**Requirement.** Candidate pairs MUST pass a deterministic filter before any
LLM is consulted: outcome type, payout and currency, settlement time within a
small tolerance (6h), threshold/operator/units when either leg declares one,
presence of resolution-source text on both legs, and early-resolution
symmetry. A REJECT here is FINAL — no later stage may overrule it.
**Evidence.** `prediction_arb/matching/deterministic_matcher.py`,
`matching/rules_validator.py`, `tests/test_units.py`.

### §19 — Stage 2: LLM semantic matcher  `[C,D,T]`
**Requirement.** The LLM stage compares structured JSON payloads (not raw
titles) for semantic contract equivalence only. Its default MUST be
conservative: no LLM configured ⇒ no match; LLM outage ⇒ no match; low
confidence ⇒ no match. False negatives are preferred over false positives.
**Evidence.** `prediction_arb/matching/llm_matcher.py`,
`prediction_arb/matching/llm_client.py` (§97), `docs/LLM_MATCHING.md`,
`tests/test_units.py` (`test_no_llm_configured_is_no_match`),
`tests/test_llm_client.py`.

### §20 — Match certificates  `[C,T]`
**Requirement.** Every pair decision MUST produce a persistable certificate
recording both contract identities and versions, deterministic check results,
LLM verdict and confidence, final status (APPROVED / REJECTED / REVIEW),
independence classification, and the matcher/LLM-model/prompt versions. Only
APPROVED certificates may reach the arbitrage search. A certificate MUST be
APPROVED only when the venue relationship is proven INDEPENDENT (§33):
same-venue frontends and unresolved relationships are REVIEW, never
APPROVED (2026-09-14, defect D15).
**Evidence.** `prediction_arb/matching/match_certificates.py`,
`matching/rules_validator.py`, `database/repository.py`
(`save_match_certificate`), `arbitrage/optimizer.py`.

### §21 — Arbitrage graph  `[C]`
**Requirement.** Opportunities are modelled as a graph (contracts as nodes,
executable relationships as edges). Two-leg search is implemented; multi-leg
cycle search is a v1 stub (§3) and MUST raise rather than return partial
results.
**Evidence.** `prediction_arb/arbitrage/graph.py`.

### §22 — Logical (TYPE B) arbitrage  `[C,D]`
**Requirement.** Logical arbitrage (implication/entailment relationships
between different events) is research-only in v1. It MUST raise
`NotImplementedError` and MUST NOT feed execution until a defined test battery
passes. TYPE C/D opportunities are not eligible for execution at all.
**Evidence.** `prediction_arb/arbitrage/logical.py`,
`docs/TRADING_LOGIC.md`.

---

## Part IV — Order books, fees and two-leg economics (§23–§28)

### §23 — Order-book model and staleness  `[C,T]`
**Requirement.** Books are normalized to a single perspective (YES bids and
YES asks) regardless of how a venue quotes them; a venue that publishes only
bids on both sides MUST have its asks synthesized (Kalshi and ForecastEx:
`ask = 1.00 − opposite bid`). ForecastEx's $0.01 fee per executed side is
separate from its $1.00 matching identity. Every book carries a timestamp and
an optional sequence number. A book older than the configured maximum age is
STALE and MUST be hidden from decision-making, never used.
**Evidence.** `prediction_arb/orderbooks/orderbook.py` (`is_stale`),
`orderbooks/aggregator.py`, `adapters/kalshi.py` (`parse_order_book`),
`tests/test_failure_modes.py`, `tests/test_units.py`.

### §24 — Fee engine  `[C,G,T]`
**Requirement.** Fees MUST come from versioned, timestamped schedules recording
calculation status, documentation and live-test status, source, verification
date, fee context, and required runtime context. A value may be returned only
for a COMPLETE component whose freshness and required context pass validation.
Missing, incomplete, unverified, stale, or context-deficient data raises
`FEE_DATA_INCOMPLETE`; it MUST block execution and MUST NEVER default to zero or
an estimate. Kalshi's quadratic base model remains blocked until its dynamic
series/event multiplier and rounding state are represented. ForecastEx pairs
bids at $1.00 and separately charges $0.01 per executed side.
**Evidence.** `prediction_arb/fees/calculator.py`, `config/fees.yaml`,
`tests/test_units.py`, `tests/test_failure_modes.py`,
`docs/TRADING_LOGIC.md`.

### §25 — Guaranteed (TYPE A) arbitrage definition  `[C,D,T]`
**Requirement.** TYPE A is: buy YES on venue A and buy NO on venue B for the
same economic contract on INDEPENDENT venues, such that profit is guaranteed
under every settlement outcome after all costs. The test is never headline
prices: `yes_walk_cost(q) + no_walk_cost(q) + taker fees (both legs) +
settlement fees + settlement-delay cost < payout × q`.
**Evidence.** `prediction_arb/arbitrage/two_leg.py`,
`docs/TRADING_LOGIC.md`, `tests/test_arbitrage_engine.py`.

### §26 — Order-book walking  `[C,T]`
**Requirement.** Sizing and costing MUST consume actual displayed liquidity
level by level. Midpoint and last-traded price MUST NOT be used. Sizing stops
at the first marginal unit whose combined pair cost reaches the payout, is
bounded by each leg's displayed liquidity, by any caller maximum and by risk
limits, and MUST yield whole contracts only. Slippage is reported separately
(WAP − best) but MUST NOT be double-counted.
**Evidence.** `prediction_arb/orderbooks/orderbook.py` (`walk_book`),
`arbitrage/two_leg.py` (`find_max_profit_quantity`),
`tests/test_spec_cases.py` (case 2), `tests/test_units.py`.

### §27 — Multi-venue search  `[C,T]`
**Requirement.** For each normalized event the search MUST evaluate ALL
independent (YES-venue, NO-venue) orderings rather than a hard-coded venue
pair, MUST skip any pair without an APPROVED certificate, and MUST skip any
pair whose independence is not exactly INDEPENDENT.
**Evidence.** `prediction_arb/arbitrage/optimizer.py` (`search_event`),
`tests/test_arbitrage_engine.py`.

### §28 — Settlement timing  `[C,T]`
**Requirement.** Capital is locked until the LATEST leg settles. The system
MUST compute expected lockup days, the resulting cost of capital at the
configured annual rate, and the settlement-delay spread between legs. An
UNKNOWN lockup MUST block the opportunity (never cost as zero), and a spread
beyond the configured maximum MUST block it.
**Evidence.** `prediction_arb/normalization/settlement.py`,
`arbitrage/two_leg.py`, `tests/test_spec_cases.py` (case 9),
`tests/test_settlement_timing.py`, `docs/TRADING_LOGIC.md` ("Settlement
timing and capital lockup": per-venue capital-release evidence, D18).

---

## Part V — Capital, risk and execution (§29–§33)

### §29 — Capital is per-venue and non-fungible  `[C,T]`
**Requirement.** Deployable capital MUST be tracked per venue and MUST NOT be
treated as fungible across venues. Position sizing uses each leg's
venue-specific deployable cash; an unknown balance sizes to ZERO.
**Evidence.** `prediction_arb/risk/capital.py`,
`tests/test_failure_modes.py` (capital non-fungibility).

### §30 — No movement of money  `[C,D]`
**Requirement.** The system MUST NEVER initiate deposits, withdrawals or
transfers. Rebalancing output MUST be labelled ADVISORY ONLY.
**Evidence.** `prediction_arb/risk/capital.py` (`rebalancing_advice`),
`docs/COMPLIANCE.md`.

### §31 — Two-leg execution risk model  `[C,D,T]`
**Requirement.** Execution MUST follow PRECHECK → RESERVE → LEG A → CONFIRM →
LEG B → CONFIRM, with leg B sized to leg A's ACTUAL fill and the
time-between-legs recorded. The coordinator MUST be engine-agnostic (identical
behaviour for simulated and live engines). Unhedged exposure MUST be registered
the instant leg A fills and cleared only when hedged.
**Evidence.** `prediction_arb/execution/coordinator.py`,
`risk/exposure.py`, `docs/RISK_MANAGEMENT.md`,
`tests/test_spec_cases.py` (case 7).

### §32 — Partial-fill recovery  `[C,T]`
**Requirement.** A predefined, bounded recovery procedure MUST run when one leg
fills and the other does not: chase the hedge leg with bounded price
concession (total concession capped by `max_emergency_loss`), else unwind the
filled leg into its bids; alert and record regardless. Unresolved exposure MUST
raise a MANUAL ACTION REQUIRED alert recommending the kill switch. Bounds:
`max_unhedged_seconds` / `_contracts` / `_dollars`.
**Evidence.** `prediction_arb/execution/partial_fills.py`,
`execution/hedging.py`, `risk/exposure.py`, `docs/RISK_MANAGEMENT.md`,
`tests/test_spec_cases.py` (case 7), `tests/test_risk_enforcement.py` (partial
fill, retry, failed hedge/unwind, timeout breach, kill and safe recovery). D22
records that `max_emergency_loss` is a concession bound, not yet a generic
preflight bound on unknown unwind fills/fees.

### §33 — Pre-trade checklist  `[C,D,I]`
**Requirement.** Before any trade the system MUST verify: APPROVED match
certificate, INDEPENDENT venue relationship, current fee schedule, fresh order
books, sufficient real liquidity (walked), sufficient per-venue capital,
jurisdiction/category eligibility, API health for every involved venue, kill
switch disengaged, appropriate order type (limit-only; no market orders on
illiquid books) and acceptable settlement-delay spread.
**Note.** `backtesting/engine.py` also cites §33 alongside §92, which suggests
the original section paired the pre-trade checklist with a "backtest before
live" obligation. That second half is `[I]` INFERRED and needs owner
confirmation.
**Evidence.** `docs/TRADING_LOGIC.md` ("Pre-trade checklist (§33) mapping"),
`prediction_arb/execution/firewall.py`, `backtesting/engine.py`.

---

## Part VI — Compliance, monitoring, persistence and ranking (§35–§42)

### §35 — Eligibility gate  `[C,D,T]`
**Requirement.** Eligibility is tracked per (jurisdiction, venue, contract
category) with status, effective date, source and verification date. Nothing is
hard-coded as legal. RESTRICTED is never tradable; UNKNOWN MUST block LIVE while
allowing paper/research only under their mode rules. Missing records are
UNKNOWN. Category detection MUST inspect both venue category/ticker and event
text and err toward classifying clear league, game, player-prop, spread, and
total contracts as sports. The current Utah wildcard sports record is
RESTRICTED across all venues/routes.
**Evidence.** `prediction_arb/compliance/jurisdiction.py`,
`compliance/eligibility.py`, `docs/COMPLIANCE.md`.

### §36 — Operator dashboard  `[C]`
**Requirement.** Every run MUST display the active mode prominently, and
opportunities MUST be shown with gross, fees, slippage, net, quantity, capital,
independence, match confidence and status. v1 delivers a terminal (rich)
dashboard; the web dashboard is a stub that MUST raise rather than render
nothing (§3).
**Evidence.** `prediction_arb/monitoring/dashboard.py`.

### §37 — Alerts  `[C]`
**Requirement.** Alerts MUST be delivered to the operator for execution
outcomes, partial fills, unrecoverable exposure and kill-switch events. v1
implements a console sink plus structured log; email and webhook sinks are
stubs with a stable interface that MUST raise `NotImplementedError`, and a
multi-sink MUST tolerate a stubbed sink without dropping the others.
**Evidence.** `prediction_arb/monitoring/alerts.py`.

### §38 — API health gating  `[C,D]`
**Requirement.** Per-venue health MUST track consecutive errors, silence
windows, latency and rate-limit hits. Trading requires ALL involved venues to
be healthy; when data becomes unreliable, trading is disabled until recovery.
**Evidence.** `prediction_arb/monitoring/health.py`,
`docs/RISK_MANAGEMENT.md`, `modes/paper/engine.py` (`_maybe_execute`).

### §39 — Rate limiting  `[C,D]`
**Requirement.** Each adapter MUST enforce a token-bucket rate limit below the
venue's published limits and MUST retry retryable failures with exponential
backoff, honouring 429s. Limits are configured per venue
(`rate_limit_reads_per_sec`, `rate_limit_writes_per_sec`).
**Evidence.** `prediction_arb/adapters/ratelimit.py`, `config/venues.yaml`,
`docs/ARCHITECTURE.md`, `docs/VENUE_RESEARCH.md`.

### §40 — Persistence  `[C]`
**Requirement.** All markets, matches, provenance, fees, opportunities, trade
intents, positions and order-book snapshots MUST be persisted in a relational
schema (SQLAlchemy/SQLite in v1) with versioned, forward-only, recorded
migrations. Every row MUST carry the mode that produced it.
**Evidence.** `prediction_arb/database/models.py`, `database/repository.py`,
`database/migrations/runner.py`.

### §41 — Opportunity ranking  `[C]`
**Requirement.** Ranking MUST use a composite score — net profit, capital
efficiency, liquidity, match confidence, data uncertainty (block reasons) and
settlement-delay cost — and MUST NOT be percentage-return-only.
**Evidence.** `prediction_arb/arbitrage/optimizer.py` (`score`, `rank`).

### §42 — Profitability thresholds  `[C,G]`
**Requirement.** An opportunity is TRADE only if net profit ≥
`minimum_net_profit` (dollars) AND return on capital ≥ `minimum_net_return`;
profitable-but-below-threshold is WATCH; any hard failure or non-positive net
is BLOCK with recorded reasons.
**Evidence.** `config/settings.yaml`, `prediction_arb/arbitrage/two_leg.py`,
`docs/TRADING_LOGIC.md` ("Status ladder").

---

## Part VII — Modes and mode isolation (§43–§60)

### §43 — Mode isolation is architectural  `[C,D,T]`
**Requirement.** Switching modes is one flag, but isolation MUST be enforced by
structure, not by configuration discipline: separate engines, separate
databases, separate logs, separate reports, separate env files, and separate
directories. No object reachable from a simulation may be able to transmit an
order. (§43–§60 form one continuous block in the original numbering.)
**Evidence.** `docs/MODE_SEPARATION.md`, `tests/test_safety.py`,
`prediction_arb/execution/executor.py`, `core/config.py`.

### §44 — The three operating modes  `[C,D]`
**Requirement.** MODE 1 HISTORICAL replays recorded data (no credentials, no
network, fully reproducible). MODE 2 PAPER uses real market data with simulated
execution. MODE 3 LIVE uses real data and real orders behind the firewall. Each
mode has its own engine class, its own report writer and its own database; a
fourth, non-trading COLLECT mode (§90) exists for data acquisition only.
**Evidence.** `prediction_arb/modes/historical/engine.py`,
`modes/paper/engine.py`, `modes/live/engine.py`, `modes/collect/engine.py`,
`modes/*/reports.py`, `execution/simulated.py`, `execution/live.py`.

### §45 — One shared strategy pipeline  `[C,D]`
**Requirement.** Discovery → normalization → provenance → matching → order
books → fee/slippage/settlement costing → arbitrage detection → ranking MUST be
identical across modes. Only the DATA PROVIDER and the EXECUTION ENGINE may
differ per mode. Strategy code MUST NOT branch on mode to change its logic.
**Evidence.** `prediction_arb/modes/shared.py`, `docs/ARCHITECTURE.md`.

### §46 — Execution engine interface  `[C]`
**Requirement.** `ExecutionEngine` is abstract (submit/cancel/status/positions/
balances). The arbitrage and coordination layers MUST NOT know which
implementation they hold. Simulated and live engines are distinct classes, not
one class with a flag.
**Evidence.** `prediction_arb/execution/executor.py`,
`execution/simulated.py`, `execution/live.py`.

### §47 — Live confirmation phrase  `[C,G,T]`
**Requirement.** Entering live mode MUST require the operator to type
`I UNDERSTAND LIVE TRADING` interactively. No command-line flag, environment
variable or config value may bypass it, and the live engine MUST NOT be
constructible without the resulting token.
**Evidence.** `prediction_arb/execution/live.py`
(`LIVE_CONFIRMATION_PHRASE`, `LiveConfirmationToken`),
`modes/live/engine.py`, `config/live.yaml`, `tests/test_safety.py`.

### §48 — Credential separation  `[C,T]`
**Requirement.** Only the env file belonging to the active mode may be loaded.
A non-live env file containing any live-credential marker (a key whose name
contains `LIVE`) MUST cause startup refusal. Live-marked keys MUST be refused
outside live mode.
**Evidence.** `prediction_arb/core/config.py` (`load_mode_env`),
`tests/test_safety.py` (`test_live_keys_in_paper_env_refused`),
`docs/API_SETUP.md`.

### §49 — Physical database isolation  `[C,T]`
**Requirement.** Each mode MUST use its own database file under
`data/<mode>/`, and the file MUST internally record the mode that owns it.
Opening a database under the wrong mode MUST raise. (Collect shares the paper
store for market data only, and that pairing is explicitly whitelisted.)
**Evidence.** `prediction_arb/database/repository.py` (`_assert_mode_tag`),
`database/models.py`, `core/config.py` (`database_path`),
`tests/test_safety.py`.

### §50 — Simulation engines hold no adapters  `[C,T]`
**Requirement.** Historical and paper execution engines MUST have no adapter
attribute at all — there is nothing to toggle. Their cash balances come from
configuration, never from a real account, and their fills come from recorded or
freshly-fetched book snapshots via the walker.
**Evidence.** `prediction_arb/execution/simulated.py`,
`tests/test_safety.py` ("paper engine holds no adapters").

### §51 — Logs never cross modes  `[C]`
**Requirement.** Log files MUST be written under `logs/<mode>/` and MUST NOT be
shared between modes; cross-mode overwrite is impossible by construction.
**Evidence.** `prediction_arb/core/logging.py`,
`prediction_arb/core/paths.py`, `docs/MODE_SEPARATION.md`.

### §52 — Mode attribution on every record  `[C]`
**Requirement.** Every log record MUST carry the active mode so any line can be
attributed unambiguously, and the operator-facing banner MUST state mode,
execution engine, database and whether real orders are enabled.
**Evidence.** `prediction_arb/core/logging.py` (`JsonFormatter`),
`monitoring/dashboard.py` (`print_mode_banner`, `print_startup_summary`).

### §53 — Startup consistency validation  `[C,G,T]`
**Requirement.** Startup MUST refuse when the mode config's declared `mode`
disagrees with the requested mode, when `execution_engine` is not the one that
mode requires, when the declared `database` is not that mode's database, when
`allow_live_orders` is true outside live mode, or when live mode does not
explicitly set it. (Spec test case 10.)
**Evidence.** `prediction_arb/core/config.py`
(`validate_mode_consistency`), `config/{historical,paper,live}.yaml`,
`tests/test_spec_cases.py`.

### §54 — Live order firewall  `[C,T]`
**Requirement.** Every real order MUST pass a firewall that independently
re-verifies: live application mode on both app and request, live config loaded,
credentials loaded, account verified, operator confirmation given, contract
eligibility, INDEPENDENT venue relationship, APPROVED match certificate,
current fee schedule, risk limits, quantity limit, kill switch disengaged,
order-book freshness, presence of a limit price, price within expected
deviation, and that the opportunity still exists at transmission time. Any
failure MUST raise `FirewallBlocked`.
**Evidence.** `prediction_arb/execution/firewall.py`,
`tests/test_safety.py` (each check parameterized).

### §55 — No fallback between modes  `[C,D,T]`
**Requirement.** No fallback logic may exist anywhere: a live failure MUST NOT
degrade into a paper trade, paper MUST NOT silently use historical data, and
historical replay MUST NOT fall back to live data. Failure is surfaced, not
papered over.
**Evidence.** `prediction_arb/execution/executor.py`,
`modes/historical/engine.py`, `docs/MODE_SEPARATION.md`.

### §56 — Layered configuration  `[C,G]`
**Requirement.** Configuration is layered: global `settings.yaml`, then the
mode file (`historical|paper|live.yaml`), then the CLI `--mode` override.
Missing mode config is a startup refusal. Mode-specific values (paper starting
cash, simulated latency, simulated fill probability, env file, data dir) live
in the mode file.
**Evidence.** `prediction_arb/core/config.py`, `config/*.yaml`.

### §57 — Per-mode data providers, one fill model  `[C]`
**Requirement.** Each mode has its own data provider behind an identical
interface (historical: recorded frames; paper: live REST polling; live: live
data via verified adapters). Backtesting and historical replay MUST share ONE
deterministic fill model — the backtest simulator is an alias layer over
`HistoricalExecutionEngine`, not a second implementation.
**Evidence.** `prediction_arb/modes/historical/data_provider.py`,
`modes/paper/data_provider.py`, `modes/live/data_provider.py`,
`backtesting/simulator.py`, `execution/coordinator.py`.

### §58 — Workflow and readiness  `[C,D]`
**Requirement.** The progression is HISTORICAL (validate logic) → PAPER
(validate real-time behaviour) → LIVE. A readiness report MUST be advisory
only: it reports recorded-data volume, opportunities analysed and the number of
live-capable independent venues, and the final decision remains the operator's.
Readiness MUST be reported per mode and derived from observed state (§99): each
mode's block lists the criteria that apply to it with the observed value and,
when a criterion is unmet, the reason. Fee-schedule freshness is one of those
criteria, and its strictness depends on the mode (§98).
**Evidence.** `prediction_arb/main.py` (`cmd_readiness`),
`prediction_arb/monitoring/readiness.py`, `docs/MODE_SEPARATION.md`,
`docs/FEE_READINESS.md`.

### §60 — The only live order path  `[C,T]`
**Requirement.** `LiveExecutionEngine.submit_order` (the generic interface
method) MUST raise unconditionally. The only working path is
`submit_order_with_context(request, FirewallContext)`, and
`execution/live.py` is the only module that may import the firewall.
**Evidence.** `prediction_arb/execution/live.py`, `execution/executor.py`,
`modes/live/execution.py`, `tests/test_safety.py`.

---

## Part VIII — Recovery, audit, search and extensibility (§61–§68)

### §61 — Write-ahead intents and crash recovery  `[C,D,T]`
**Requirement.** Before any network I/O a live order MUST be recorded with
status PENDING, then SUBMITTED with the venue order id, then FILLED / REJECTED /
CANCELED. On live startup the engine MUST reconcile every PENDING/SUBMITTED
intent against venue truth (by order id, then by `client_order_id` among open
orders, then among fills), mark never-transmitted intents REJECTED, and flag
orphaned single legs for hedge review. `client_order_id` is a UUID idempotency
key.
**Evidence.** `prediction_arb/database/repository.py`
(`write_trade_intent`, `unreconciled_trades`), `execution/live.py`
(`recover_on_startup`), `docs/CRASH_RECOVERY.md`,
`tests/test_spec_cases.py` (case 8).

### §62 — Credentials are never logged  `[C,T]`
**Requirement.** Log output MUST be filtered so that no record attribute whose
name looks secret-like (key, secret, token, password, credential, private) is
ever written.
**Evidence.** `prediction_arb/core/logging.py` (`_SECRET_RE`),
`tests/test_safety.py`.

### §64 — No hard-coded venue pair  `[C]`
**Requirement.** The search driver MUST enumerate venue orderings dynamically
from the contracts present, so that adding a venue requires no change to the
search (see also §27, §65).
**Evidence.** `prediction_arb/arbitrage/optimizer.py`.

### §65 — Adding a venue is additive  `[D]`
**Requirement.** A new venue MUST require only: an adapter implementing §10, a
provenance record with evidence (§14), a fee schedule (§24) and eligibility
records (§35). The arbitrage engine MUST need no changes.
**Evidence.** `docs/ARCHITECTURE.md` ("Extending").

### §66 — Contract-level provenance refinement  `[C]`
**Requirement.** Independence MUST be refinable per contract: two contracts
sharing an underlying exchange, identifier namespace or order book are
SAME_VENUE regardless of frontend. Per-market provenance resolution returns
None (UNKNOWN) when it cannot resolve — never a guess.
**Evidence.** `prediction_arb/provenance/independence.py`
(`contracts_independent`), `provenance/market_provenance.py`.

### §68 — Opportunity lifetime bookkeeping  `[C,I]`
**Requirement.** The system MUST record inputs needed to reason about an
opportunity's lifetime: per-contract book update counts, and `first_seen` /
`last_seen` on persisted opportunities.
**Note.** The precise metric the original section demanded (e.g. minimum
observation window before acting, or opportunity duration reporting) is `[I]`
INFERRED from the two surviving citation sites and needs owner confirmation.
**Evidence.** `prediction_arb/orderbooks/aggregator.py` (`update_count`),
`database/models.py` (`OpportunityRow.first_seen/last_seen`).

---

## Part IX — Risk limits, testing, reporting and defaults (§70–§78)

### §70 — Hard risk limits  `[C,G,T]`
**Requirement.** Configured hard limits MUST stop new trades when reached:
`max_total_capital`, `max_capital_per_venue`, `max_capital_per_event`,
`max_capital_per_trade`, `max_unhedged_exposure`, `max_daily_loss`,
`max_open_positions`, `max_concurrent_orders`. Existing positions remain
monitored; a violation is never a reason to liquidate silently.
**Evidence.** `prediction_arb/risk/limits.py`,
`prediction_arb/execution/coordinator.py`, `config/risk_limits.yaml`,
`docs/RISK_MANAGEMENT.md`, `tests/test_risk_enforcement.py`. The paper path
now enforces these against coordinator state; D21 records that daily realized
P&L is incomplete without settlement/day-rollover accounting, and live remains
monitor-only (D24). Configuration/readiness alone is not enforcement evidence.

### §71 — Kill switch  `[C,D,T]`
**Requirement.** A global kill switch MUST be file-based (so it works across
processes and survives restarts), engagable from the CLI and programmatically,
and MUST block: the firewall, live startup, and new paper trades. It remains
engaged until explicitly disengaged. Emergency procedures (flatten-and-stop)
are driven by it or by unrecoverable exposure.
**Evidence.** `prediction_arb/risk/kill_switch.py`,
`execution/coordinator.py`, `execution/firewall.py`, `main.py`,
`docs/RISK_MANAGEMENT.md`, `tests/test_safety.py`,
`tests/test_risk_enforcement.py`. `execution/emergency.py` remains disconnected
(D24); it is not evidence that automatic cancel/flatten is active.

### §72 — Timeout discipline  `[C,D,T]`
**Requirement.** A transmission timeout MUST NEVER lead to a blind retry. The
engine MUST first query the venue (open orders, then fills) for the
`client_order_id`; only when the venue shows no trace may a retry be
considered. Ambiguous state is surfaced as UNKNOWN for manual reconciliation
rather than guessed away.
**Evidence.** `prediction_arb/execution/live.py`,
`execution/emergency.py`, `docs/CRASH_RECOVERY.md`.

### §74 — Test taxonomy  `[T]`
**Requirement.** The suite MUST cover unit behaviour (normalization, matching,
fees, book walking, arbitrage, risk, independence) and failure modes (stale or
out-of-order books, missing fee data, unknown venue relationships, insufficient
simulated cash, limit-price enforcement, absence of historical data, stubbed
backtesting, capital non-fungibility), alongside the safety suite (§43–§60) and
the mandatory cases (§75).
**Evidence.** `tests/test_units.py`, `tests/test_failure_modes.py`,
`tests/test_safety.py`, `docs/TESTING.md`.

### §75 — Ten mandatory test cases  `[T,D]`
**Requirement.** These ten cases MUST pass: (1) fees exceed $0.01 gross → no
trade; (2) 5 available vs 100 requested → sized to 5; (3) same underlying
exchange → no arbitrage; (4) UNKNOWN relationship → BLOCK; (5) LLM says match,
deterministic disagrees → REJECT; (6) 2% displayed vs 2.2% real costs → no
trade; (7) one leg fills → recovery procedure runs; (8) crash between legs →
intents found and reconciled; (9) T+0 vs T+3 settlement delay costed, unknown →
blocking None; (10) paper config + LiveExecutionEngine → startup refused.
**Evidence.** `tests/test_spec_cases.py`, `docs/TESTING.md`, `README.md`.

### §76 — Audit trail  `[C,D]`
**Requirement.** Decisions and actions MUST be attributable after the fact:
structured JSONL audit logs per mode (§51, §52), persisted match certificates
with versions (§20), persisted opportunities with block reasons (§40) and
persisted trade intents with status transitions (§61).
**Evidence.** `prediction_arb/core/logging.py`, `database/repository.py`,
`database/models.py`.

### §77 — Reporting  `[C]`
**Requirement.** Each mode MUST write its own reports to `reports/<mode>/`:
paper and live daily reports (opportunities detected, tradeable, trades
executed/submitted/filled) and a historical replay report (frames,
opportunities, reproducibility). Reports MUST NOT cross mode directories.
**Evidence.** `prediction_arb/modes/paper/reports.py`,
`modes/live/reports.py`, `modes/historical/reports.py`.

### §78 — Never auto-enter live  `[C,G]`
**Requirement.** The application MUST default to the mode in
`config/settings.yaml` and MUST NEVER auto-enter live mode; live requires an
explicit `--mode live` plus §47.
**Evidence.** `prediction_arb/main.py`, `config/settings.yaml`.

---

## Part X — Versioning, stubs, matching cache, collection and history (§82–§95)

### §82 — Versioning and caching  `[C,D]`
**Requirement.** Schema changes MUST be versioned through ordered, recorded,
forward-only migrations. Matcher results MUST be cached by contract-version
pair key and re-run only when a contract's rules change. Certificates MUST
record matcher, LLM-model and prompt versions so any decision can be
reproduced or audited.
**Evidence.** `prediction_arb/database/migrations/runner.py`,
`database/models.py` (`SCHEMA_VERSION`), `matching/llm_matcher.py`,
`docs/LLM_MATCHING.md`.

### §84 — Documented-but-untested adapters  `[C]`
**Requirement.** Where an API is documented but cannot be exercised (no
account, no session), the adapter MUST report `API_DOCUMENTED_UNTESTED`, MUST
implement only what can be verified, and MUST raise `NotImplementedError` with
exact activation instructions for the remainder. Its status MAY change only
after verification against a paper/sandbox account.
**Evidence.** `prediction_arb/adapters/forecast_ex.py`,
`docs/API_SETUP.md` ("ForecastEx (IBKR) — activation checklist").

### §87 — Matcher cache discipline  `[C,D]`
**Requirement.** Matching MUST be cached per (venue:contract_id:contract_version)
pair and re-run only when a contract's rules change; the cache MUST NOT be
shared across matcher or prompt versions.
**Evidence.** `prediction_arb/matching/llm_matcher.py`,
`docs/LLM_MATCHING.md`.

### §88 — LLM output validation  `[C,T]`
**Requirement.** Untrusted LLM output MUST be schema-validated: malformed
JSON, missing fields, wrong types, confidence outside [0,1], and contradictions
(`match=true` with any identity field false) MUST all be treated as NO MATCH.
Validation failure is never retried into a match.
**Evidence.** `prediction_arb/matching/llm_matcher.py`
(`validate_llm_output`), `tests/test_units.py`, `docs/LLM_MATCHING.md`.

### §89 — LLM role boundary  `[C,D]`
**Requirement.** The LLM's role is semantic contract equivalence ONLY. It MUST
NOT authorise trades, MUST NOT have access to execution APIs, and MUST NOT
overrule deterministic filters (§18). It receives structured payloads, not raw
venue text.
**Evidence.** `docs/LLM_MATCHING.md`,
`prediction_arb/matching/llm_matcher.py`, `matching/rules_validator.py`.

### §90 — Data-collection mode  `[C,D,T]`
**Requirement.** A collect mode MUST record markets and usable order-book
snapshots and MUST have NO execution engine at all. It MUST admit only
`MARKET_DATA_OBSERVED` or `VERIFIED` adapters; documentation or connectivity
alone MUST NOT admit a venue. Snapshots MUST be two-sided, finite, positive-size,
in-range, sorted, and uncrossed. Public market-data collection MUST require no
credentials, and collection MUST never construct or invoke an execution path.
**Evidence.** `prediction_arb/modes/collect/engine.py`,
`prediction_arb/orderbooks/validation.py`, `tests/test_collect_mode.py`,
`main.py`, `docs/API_SETUP.md`.

### §92 — No fabricated history or data  `[C,D,T]`
**Requirement.** Historical replay MUST use only recorded data. When no data
exists for the requested range it MUST say so and MUST NOT fabricate history,
synthesize books, or fall back to live data (§55). Full backtest metrics are a
stub until collected data justifies them, and the stub MUST raise.
**Evidence.** `prediction_arb/modes/historical/engine.py`,
`backtesting/engine.py`, `tests/test_failure_modes.py`.

### §95 — Safe defaults  `[G,C]`
**Requirement.** The default mode MUST be paper; the application MUST never
default to live. Default thresholds and limits live in `config/settings.yaml`
and are conservative (guaranteed-only arbitrage, limit orders only, market
orders disallowed, UNKNOWN blocking enabled).
**Evidence.** `config/settings.yaml`, `prediction_arb/core/config.py`.

### §96 — Cross-venue candidate proposal  `[C,T]`
**Requirement.** Added 2026-09-14 (defect D6 fix; replaces exact-title
grouping). Revised 2026-10-10 (Arb.8): the earlier settlement-bucket keys with
union-find grouping produced one giant component. The normalization layer MUST
propose cross-venue candidate pairs with these rules: a pair is proposed only
if (1) the two contracts are on different venues, (2) both have a settlement time
and the times differ by at most the deterministic matcher's settlement tolerance
(§18), and (3) they share a discriminative key (exact normalized threshold +
operator + units, a canonical numeric title token, or a plural-folded content
word) that is not dense at that settlement moment. Proposal MUST NOT be
transitive. Components MAY be used only to group contracts for the scan, never
as a claim of equivalence. Proposal MUST be recall-oriented and MUST only propose
candidates: it never decides equivalence, never promotes a pair past the
deterministic filter (§18, REJECT final), and never approves anything. Group
ids MUST be stable across runs and input orderings.
**Evidence.** `prediction_arb/normalization/contracts.py`
(`blocking_keys`, `candidate_pairs`, `component_indices`, `groups_from_pairs`,
`group_by_event`, `numeric_tokens`, `content_tokens`, `canonical_number`),
`tests/test_candidate_generation.py`, `tests/test_candidate_retrieval.py`,
`benchmarks/candidate_retrieval/`, `docs/CANDIDATE_RETRIEVAL.md`,
`docs/LLM_MATCHING.md`, `docs/ROADMAP.md` (defect D6).

### §97 — LLM client wiring and comparison budget  `[C,T]`
**Requirement.** Added 2026-09-14 (defect D9 fix). The LLM matcher MUST be
constructible from the mode env file — `LLM_API_BASE` / `LLM_API_KEY` /
`LLM_MODEL`, optional `LLM_TIMEOUT_SECONDS` — against any
OpenAI-compatible `/chat/completions` endpoint. The client MUST be
transport only: verdict logic, schema validation (§88), caching (§87) and
versioning stay in the matcher. Setting none of the variables MUST keep
the conservative no-LLM default (ambiguous pairs → REVIEW, never
auto-APPROVED, §19); setting a partial or malformed configuration MUST
refuse startup. Transport failures (unreachable, non-200, non-JSON,
unexpected shape) are no-match. A per-cycle comparison budget MUST bound
new LLM calls per scan cycle: pairs beyond the budget fail closed to
REVIEW, are NOT cached (a later cycle may still consider them), and cache
hits never consume budget; the budget window resets once per
`run_matching` call. The API key MUST never be logged.
**Evidence.** `prediction_arb/matching/llm_client.py`
(`build_llm_config`, `chat_completion`, `build_llm_matcher`),
`prediction_arb/matching/llm_matcher.py` (`begin_cycle`,
`max_comparisons_per_cycle`), `prediction_arb/modes/shared.py`
(`run_matching` budget reset), `tests/test_llm_client.py`,
`docs/API_SETUP.md` (LLM matcher section), `config/settings.yaml`
(`llm.max_comparisons_per_cycle`), `docs/ROADMAP.md` (defect D9).

### §98 — Mandatory fee-schedule freshness, per mode  `[C,T]`
**Requirement.** Added 2026-10-10 (defect D19 fix). A fee schedule's
`verified_at` date MUST be compared against `fees.max_schedule_age_days`
whenever a fee-derived decision is taken, and the strictness MUST come from one
rule keyed on the operating mode (`policy_for_mode`): LIVE and PAPER are
MANDATORY, HISTORICAL is RESEARCH, COLLECT is NOT_APPLICABLE. Under MANDATORY a
schedule that is older than the limit, carries no `verified_at`, or is not
marked verified MUST be refused exactly like a missing one —
`FeeDataError("FEE_DATA_INCOMPLETE: … Re-verify before live execution")` —
never a silent fallback to the stale number and never zero. An unknown mode
MUST fail closed (MANDATORY); an explicit caller override MUST win in both
directions. Under RESEARCH the stale schedule still prices the replay, because
history is still evidence, and the run MUST be labelled
`RESEARCH USE ONLY: fee schedule(s) older than <N> days` in the console, the
run summary and the report, so silence is never ambiguous. COLLECT takes no
fee-derived decision (it records contracts, matches and order books, and
`trade_fee_summary` reports UNKNOWN instead of raising), so freshness is
reported as NOT APPLICABLE there rather than blocking evidence gathering;
this is the one place where the mode policy is narrower than a literal reading
of "freshness is mandatory everywhere" would suggest.
**Evidence.** `prediction_arb/fees/freshness.py` (`FreshnessPolicy`,
`policy_for_mode`, `freshness_enforced`, `check_schedule`, `research_fee_label`,
`policy_note`), `prediction_arb/fees/calculator.py`
(`FeeCalculator.schedule_for(..., strict_freshness=...)`),
`prediction_arb/arbitrage/two_leg.py`, `prediction_arb/modes/shared.py`,
`prediction_arb/execution/simulated.py`,
`prediction_arb/modes/historical/engine.py`, `config/settings.yaml`
(`fees.max_schedule_age_days`), `tests/test_readiness.py`,
`docs/FEE_READINESS.md`, `docs/ROADMAP.md` (defect D19).

### §99 — Readiness verdicts are computed, never hardcoded  `[C,T]`
**Requirement.** Added 2026-10-10 (defect D10 fix). Every readiness criterion
MUST be derived from observed system state: collect/paper data capability
(`APIStatus.MARKET_DATA_OBSERVED` or `VERIFIED`) and separate full/live
adapter verification (`APIStatus.VERIFIED` **and** verified order-type
support), provenance independence (§15), matcher configuration (§97), fee
completeness (§24) and
freshness (§98), settlement-timing evidence (§28), recorded-data volume (§40),
jurisdiction eligibility (§35), the persistent kill switch (§71), configured
hard risk limits (§70), the order-book staleness gates (§23) and — for live
only — the existence of the live credential file (§48; existence is checked,
the file is never read outside live mode). A mode MUST be reported ready if and
only if none of its applicable criteria is BLOCKED, and a criterion that cannot
be evaluated MUST block rather than pass. Each unmet criterion MUST carry an
explicit reason naming the observation that failed, so the operator sees the
actual blocker. Producing the report MUST have no side effects: it MUST NOT
create the recorded store or its directory, write logs or reports, call a
venue, or enable anything — live stays gated by §43–§60 and §78.
**Evidence.** `prediction_arb/monitoring/readiness.py` (`Criterion`,
`CriterionState`, `ModeReadiness`, `ReadinessInputs`, `evaluate_all`,
`load_recorded_evidence`), `prediction_arb/main.py` (`cmd_readiness`),
`config/settings.yaml` (`readiness.minimum_recorded_snapshots`,
`readiness.minimum_analyzed_opportunities`,
`readiness.minimum_live_capable_venues`, `readiness.minimum_data_venues`),
`tests/test_readiness.py`,
`docs/MODE_SEPARATION.md`, `docs/ROADMAP.md` (defect D10).

### §100 — Rejections state their reason to the operator  `[C,D,T]`
**Requirement.** Added 2026-10-10. When a scan or replay cycle rejects
candidates, the operator MUST be told *why*, not only how many: the distinct
`block_reasons` MUST be printed with their occurrence counts, most common
first, from a bounded list that ends with an explicit
"and N further distinct reason(s)" tail. Nothing is printed when nothing was
blocked, so silence unambiguously means no rejections. Console text MUST be
escaped, so a reason containing brackets or markup cannot be swallowed or
reinterpreted by the renderer.
**Evidence.** `prediction_arb/monitoring/dashboard.py` (`print_block_reasons`),
`prediction_arb/modes/paper/engine.py`,
`prediction_arb/modes/historical/engine.py`, `tests/test_readiness.py`
(`TestBlockReasonsAreVisible`).

### §101 — Claim-level public adapter evidence  `[C,D,T]`
**Requirement.** Adapter status MUST keep documentation review, connectivity,
usable market data, and full adapter verification as separate dated claims.
A transport/TLS failure or service unavailability MUST be recorded with the
exact attempt and classified `UNAVAILABLE`; it MUST NOT demote a historical
observation, label an adapter broken, or promote it as verified. Connectivity
alone MUST NOT enable collection. Public smoke tests MUST use documented,
read-only endpoints within bounded published limits, require no credentials,
and never place orders. A public market-data observation MAY admit read-only
collection after manual evidence review; it MUST NOT imply authenticated,
streaming, order, or full-adapter verification.
**Evidence.** `prediction_arb/adapters/evidence.py`,
`prediction_arb/main.py` (`cmd_status`), `scripts/kalshi_smoke.py`,
`scripts/polymarket_us_smoke.py`, `tests/test_collect_mode.py`,
`tests/test_kalshi_smoke_script.py`,
`tests/test_polymarket_us_smoke_script.py`.

### §102 — Causal historical evidence and conditional simulation  `[C,D,T,G]`
**Requirement.** Historical evaluation MUST separate observed book data,
calculated gross/costed opportunities, assumed execution parameters and simulated
orders/positions/P&L. A sizing walk MUST NOT imply measured fill probability;
repeated opportunity-profit sums MUST NOT be labelled equity, trading P&L or a
performance bound. CLI and library MUST share the evaluator.

Decision/arrival lookup MUST use only already-available observations at exact
times, never a later quote or rounded earlier bucket. Source regressions,
future/ambiguous/corrupt state, excessive book age/skew, missing receipt/outcome/
provenance/as-of terms/rules or incomplete costs MUST fail execution qualification
closed; computable unsupported theory MAY remain separately labelled. Recording
MUST bind only already-observed immutable contract context, preserve legacy
missing evidence, and never migrate/write read-only input.

Simulation MUST be explicitly configured and conditional on supported data/costs.
It MUST declare latency/depth/continuity/capital assumptions; use dated arrival
depth, contingent IOC legs and decision-price limits; consume residual liquidity;
include actual simulated fees/reserves/capital; avoid repeated-pair profit
harvesting; and distinguish known no fills from unavailable execution. Incomplete
hedges MUST retain exposure and stop new entries. Aggregate simulated terminal
net MUST be withheld for naked/unavailable/incomplete execution, retain losses,
and never credit unobserved payouts as cash or realized P&L.

Reports MUST include input/cost provenance, skipped/blocked and unavailable data,
open paired positions/exposure, assumptions and minimum additional collection.
Empty/missing datasets MUST use the same honest schema with unavailable P&L.
No genuine dataset, measured fill/settlement, continuous feed coverage or
real-execution guarantee MAY be inferred from synthetic success. Day-granular
fee bounds and static supplied versions are not an intraday cost-history stream.
**Evidence.** `prediction_arb/backtesting/{policy,engine,simulator,metrics}.py`,
`prediction_arb/modes/historical/data_provider.py`, `database/{repository,models}.py`,
`database/migrations/runner.py` (004), `config/historical.yaml`,
`tests/test_historical_evaluation.py`, `tests/test_phase4_recording.py`,
`docs/HISTORICAL_EVALUATION.md`.

---

## Appendix A — Index of reconstructed sections

| § | Title | Tag | Primary evidence |
|---|---|---|---|
| §3 | v1 scope boundary | [C,D] | `README.md`, `prediction_arb/arbitrage/graph.py` |
| §5 | Evidence-scoped integrations, no fabricated claims | [C] | `prediction_arb/adapters/evidence.py`, `prediction_arb/core/types.py` |
| §7 | Structured stub contract | [C,T] | `prediction_arb/adapters/stubs.py`, `StubAdapter._blocked` |
| §9 | Concurrency model | [C,D] | `prediction_arb/orderbooks/aggregator.py`, `risk/exposure.py` |
| §10 | Venue adapter interface | [C] | `prediction_arb/adapters/base.py`, `adapters/kalshi.py` |
| §13 | Operational restrictions | [C] | `prediction_arb/compliance/restrictions.py`, `docs/COMPLIANCE.md` |
| §14 | Provenance registry with evidence | [C,D] | `prediction_arb/provenance/exchange_registry.py`, `provenance/clearing_registry.py` |
| §15 | Independence engine | [C,T] | `prediction_arb/provenance/independence.py`, `tests/test_units.py` |
| §16 | Contract normalization | [C] | `prediction_arb/normalization/contracts.py`, `normalization/outcomes.py` |
| §17 | Outcome types | [C] | `prediction_arb/normalization/outcomes.py`, `arbitrage/multi_leg.py` |
| §18 | Stage 1: deterministic filter | [C,T] | `prediction_arb/matching/deterministic_matcher.py`, `matching/rules_validator.py` |
| §19 | Stage 2: LLM semantic matcher | [C,D,T] | `prediction_arb/matching/llm_matcher.py`, `docs/LLM_MATCHING.md` |
| §20 | Match certificates | [C,T] | `prediction_arb/matching/match_certificates.py`, `matching/rules_validator.py` |
| §21 | Arbitrage graph | [C] | `prediction_arb/arbitrage/graph.py` |
| §22 | Logical (TYPE B) arbitrage | [C,D] | `prediction_arb/arbitrage/logical.py`, `docs/TRADING_LOGIC.md` |
| §23 | Order-book model and staleness | [C,T] | `prediction_arb/orderbooks/orderbook.py`, `is_stale` |
| §24 | Fee engine | [C,G,T] | `prediction_arb/fees/calculator.py`, `config/fees.yaml` |
| §25 | Guaranteed (TYPE A) arbitrage definition | [C,D,T] | `prediction_arb/arbitrage/two_leg.py`, `docs/TRADING_LOGIC.md` |
| §26 | Order-book walking | [C,T] | `prediction_arb/orderbooks/orderbook.py`, `walk_book` |
| §27 | Multi-venue search | [C,T] | `prediction_arb/arbitrage/optimizer.py`, `search_event` |
| §28 | Settlement timing | [C,T] | `prediction_arb/normalization/settlement.py`, `arbitrage/two_leg.py` |
| §29 | Capital is per-venue and non-fungible | [C,T] | `prediction_arb/risk/capital.py`, `tests/test_failure_modes.py` |
| §30 | No movement of money | [C,D] | `prediction_arb/risk/capital.py`, `rebalancing_advice` |
| §31 | Two-leg execution risk model | [C,D,T] | `prediction_arb/execution/coordinator.py`, `risk/exposure.py` |
| §32 | Partial-fill recovery | [C,T] | `prediction_arb/execution/partial_fills.py`, `execution/hedging.py` |
| §33 | Pre-trade checklist | [C,D,I] | `docs/TRADING_LOGIC.md`, `prediction_arb/execution/firewall.py` |
| §35 | Eligibility gate | [C,D,T] | `prediction_arb/compliance/jurisdiction.py`, `compliance/eligibility.py` |
| §36 | Operator dashboard | [C] | `prediction_arb/monitoring/dashboard.py` |
| §37 | Alerts | [C] | `prediction_arb/monitoring/alerts.py` |
| §38 | API health gating | [C,D] | `prediction_arb/monitoring/health.py`, `docs/RISK_MANAGEMENT.md` |
| §39 | Rate limiting | [C,D] | `prediction_arb/adapters/ratelimit.py`, `config/venues.yaml` |
| §40 | Persistence | [C] | `prediction_arb/database/models.py`, `database/repository.py` |
| §41 | Opportunity ranking | [C] | `prediction_arb/arbitrage/optimizer.py`, `score` |
| §42 | Profitability thresholds | [C,G] | `config/settings.yaml`, `prediction_arb/arbitrage/two_leg.py` |
| §43 | Mode isolation is architectural | [C,D,T] | `docs/MODE_SEPARATION.md`, `tests/test_safety.py` |
| §44 | The three operating modes | [C,D] | `prediction_arb/modes/historical/engine.py`, `modes/paper/engine.py` |
| §45 | One shared strategy pipeline | [C,D] | `prediction_arb/modes/shared.py`, `docs/ARCHITECTURE.md` |
| §46 | Execution engine interface | [C] | `prediction_arb/execution/executor.py`, `execution/simulated.py` |
| §47 | Live confirmation phrase | [C,G,T] | `prediction_arb/execution/live.py`, `LIVE_CONFIRMATION_PHRASE` |
| §48 | Credential separation | [C,T] | `prediction_arb/core/config.py`, `load_mode_env` |
| §49 | Physical database isolation | [C,T] | `prediction_arb/database/repository.py`, `_assert_mode_tag` |
| §50 | Simulation engines hold no adapters | [C,T] | `prediction_arb/execution/simulated.py`, `tests/test_safety.py` |
| §51 | Logs never cross modes | [C] | `prediction_arb/core/logging.py`, `prediction_arb/core/paths.py` |
| §52 | Mode attribution on every record | [C] | `prediction_arb/core/logging.py`, `JsonFormatter` |
| §53 | Startup consistency validation | [C,G,T] | `prediction_arb/core/config.py`, `validate_mode_consistency` |
| §54 | Live order firewall | [C,T] | `prediction_arb/execution/firewall.py`, `tests/test_safety.py` |
| §55 | No fallback between modes | [C,D,T] | `prediction_arb/execution/executor.py`, `modes/historical/engine.py` |
| §56 | Layered configuration | [C,G] | `prediction_arb/core/config.py`, `config/*.yaml` |
| §57 | Per-mode data providers, one fill model | [C] | `prediction_arb/modes/historical/data_provider.py`, `modes/paper/data_provider.py` |
| §58 | Workflow and readiness | [C,D] | `prediction_arb/main.py`, `cmd_readiness`, `prediction_arb/monitoring/readiness.py` |
| §60 | The only live order path | [C,T] | `prediction_arb/execution/live.py`, `execution/executor.py` |
| §61 | Write-ahead intents and crash recovery | [C,D,T] | `prediction_arb/database/repository.py`, `write_trade_intent` |
| §62 | Credentials are never logged | [C,T] | `prediction_arb/core/logging.py`, `_SECRET_RE` |
| §64 | No hard-coded venue pair | [C] | `prediction_arb/arbitrage/optimizer.py` |
| §65 | Adding a venue is additive | [D] | `docs/ARCHITECTURE.md` |
| §66 | Contract-level provenance refinement | [C] | `prediction_arb/provenance/independence.py`, `contracts_independent` |
| §68 | Opportunity lifetime bookkeeping | [C,I] | `prediction_arb/orderbooks/aggregator.py`, `update_count` |
| §70 | Hard risk limits | [C,G,T] | `prediction_arb/risk/limits.py`, `config/risk_limits.yaml` |
| §71 | Kill switch | [C,D,T] | `prediction_arb/risk/kill_switch.py`, `execution/emergency.py` |
| §72 | Timeout discipline | [C,D,T] | `prediction_arb/execution/live.py`, `execution/emergency.py` |
| §74 | Test taxonomy | [T] | `tests/test_units.py`, `tests/test_failure_modes.py` |
| §75 | Ten mandatory test cases | [T,D] | `tests/test_spec_cases.py`, `docs/TESTING.md` |
| §76 | Audit trail | [C,D] | `prediction_arb/core/logging.py`, `database/repository.py` |
| §77 | Reporting | [C] | `prediction_arb/modes/paper/reports.py`, `modes/live/reports.py` |
| §78 | Never auto-enter live | [C,G] | `prediction_arb/main.py`, `config/settings.yaml` |
| §82 | Versioning and caching | [C,D] | `prediction_arb/database/migrations/runner.py`, `database/models.py` |
| §84 | Documented-but-untested adapters | [C] | `prediction_arb/adapters/forecast_ex.py`, `docs/API_SETUP.md` |
| §87 | Matcher cache discipline | [C,D] | `prediction_arb/matching/llm_matcher.py`, `docs/LLM_MATCHING.md` |
| §88 | LLM output validation | [C,T] | `prediction_arb/matching/llm_matcher.py`, `validate_llm_output` |
| §89 | LLM role boundary | [C,D] | `docs/LLM_MATCHING.md`, `prediction_arb/matching/llm_matcher.py` |
| §90 | Data-collection mode | [C,D,T] | `prediction_arb/modes/collect/engine.py`, `orderbooks/validation.py`, `tests/test_collect_mode.py` |
| §92 | No fabricated history or data | [C,D,T] | `prediction_arb/modes/historical/engine.py`, `backtesting/engine.py` |
| §95 | Safe defaults | [G,C] | `config/settings.yaml`, `prediction_arb/core/config.py` |
| §96 | Cross-venue candidate blocking | [C,T] | `prediction_arb/normalization/contracts.py`, `tests/test_candidate_generation.py`, `docs/LLM_MATCHING.md` |
| §97 | LLM client wiring and comparison budget | [C,T] | `prediction_arb/matching/llm_client.py`, `prediction_arb/matching/llm_matcher.py`, `tests/test_llm_client.py`, `docs/API_SETUP.md` |
| §98 | Mandatory fee-schedule freshness, per mode | [C,T] | `prediction_arb/fees/freshness.py`, `prediction_arb/fees/calculator.py`, `tests/test_readiness.py`, `docs/FEE_READINESS.md` |
| §99 | Readiness verdicts are computed, never hardcoded | [C,T] | `prediction_arb/monitoring/readiness.py`, `prediction_arb/main.py`, `tests/test_readiness.py`, `docs/MODE_SEPARATION.md` |
| §100 | Rejections state their reason to the operator | [C,D,T] | `prediction_arb/monitoring/dashboard.py`, `prediction_arb/modes/paper/engine.py`, `tests/test_readiness.py` |
| §101 | Claim-level public adapter evidence | [C,D,T] | `prediction_arb/adapters/evidence.py`, smoke scripts, `tests/test_collect_mode.py` |
| §102 | Causal historical evidence and conditional simulation | [C,D,T,G] | `prediction_arb/backtesting/*`, historical provider, schema 4, `tests/test_historical_evaluation.py` |

## Appendix B — Numbers with no surviving citation

These section numbers are never cited anywhere in the repository. Their
content is **UNKNOWN** and MUST NOT be guessed. If the original document
is ever recovered, they can be filled in; until then they stay empty.

§1, §2, §4, §6, §8, §11, §12, §34, §59, §63, §67, §69, §73, §79, §80, §81, §83, §85, §86, §91, §93, §94

**Special case — §59:** never cited individually, but `docs/MODE_SEPARATION.md`
and `tests/test_safety.py` both cite the range **§43–§60**, so §59 certainly
existed and belonged to the mode-isolation block. Its specific content is
UNKNOWN; it is NOT reconstructed here.

## Appendix C — Range citations

Ranges imply that every number inside them existed in the original spec.

| Citation | Where |
|---|---|
| §18–§20 | `docs/LLM_MATCHING.md` |
| §18–§20 | `prediction_arb/matching/rules_validator.py` |
| §18–§20 | `prediction_arb/normalization/contracts.py` |
| §25–§27 | `prediction_arb/arbitrage/two_leg.py` |
| §29–§30 | `docs/RISK_MANAGEMENT.md` |
| §43–§60 | `docs/MODE_SEPARATION.md` |
| §43–§60 | `tests/test_safety.py` |
| §44–§46 | `prediction_arb/execution/executor.py` |
| §87–§89 | `prediction_arb/matching/llm_matcher.py` |

## Appendix D — Reconstruction confidence

* Sections reconstructed: **80** (2026-10-10; includes §101 and §102).
* Python/config literal citation sites: **313**, **76 distinct numbers**
  (tracked/nonignored `.py` / `.yaml`; all resolve). Documentation's own local
  numbered headings are excluded rather than confused with spec references.
* Sections carrying an `[I]` INFERRED flag — confirm or correct these first:

  * §33 — Pre-trade checklist
  * §68 — Opportunity lifetime bookkeeping

Tag distribution:

| Tag | Sections |
|---|---|
| `[C,T]` | 21 |
| `[C]` | 18 |
| `[C,D]` | 14 |
| `[C,D,T]` | 13 |
| `[C,G,T]` | 4 |
| `[C,G]` | 3 |
| `[C,D,I]` | 1 |
| `[D]` | 1 |
| `[C,I]` | 1 |
| `[T]` | 1 |
| `[T,D]` | 1 |
| `[G,C]` | 1 |
| `[C,D,T,G]` | 1 |

## Appendix E — Maintaining this document

1. **Never renumber.** 200+ call sites cite these numbers.
2. New requirements get new numbers at the end (§96–§102, …) and a real
   source: a decision record, an issue, or owner confirmation — not a guess.
3. When you change behaviour that a section describes, update the section
   in the same commit and keep its `Evidence.` list accurate.
4. Re-measure reconstructed sections and code/config citations after large
   changes. Use tracked/nonignored `.py` / `.yaml`, not `.venv` or generated
   reports. Every **code/config** cited number must resolve in this index;
   documentation-local section headings are not necessarily global spec numbers.
   Retained reconstructed sections may have documentation evidence without a
   current literal source citation, so the counts need not be equal.
5. If the original specification is ever recovered, this file becomes a
   diff target: replace inferred text with the real text, keep the numbers,
   and drop the `[I]` tags that no longer apply.
