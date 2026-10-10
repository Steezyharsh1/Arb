# Historical evaluation — evidence, assumptions and simulation (Arb.13)

Verified offline on **2026-10-10**. This is research software, not evidence of
real fills and not a promise of execution or profit. Production fee schedules
remain `INCOMPLETE`; Polymarket US / ForecastEx capital-release timing remains
`UNKNOWN`. **No genuine two-venue historical dataset exists in this checkout.**

Requirements are recorded in `docs/SPEC.md` §102.

## 1. What the audit reproduced

At baseline `3d01ce9`, both `HistoricalEngine` and `BacktestEngine` ran matching
and opportunity costing, **not execution**. `BacktestFillSimulator` was only
an unused alias for the undated `HistoricalExecutionEngine`. The report's
`MODELLED` equity added the calculated profit of every TRADE observation,
including repeated/overlapping observations, as if each filled completely.
Sizing walks were called fill statistics / fill probability. Neither was
execution evidence; even the claimed performance “upper bound” was unjustified.

The provider kept the **last independently sampled book in each minute** and
labelled the frame with the minute's start. An offline reproduction put books
from **12:00:10 and 12:00:55 into a 12:00:00 decision** — 10 and 55 seconds of
look-ahead, respectively. There was no historical staleness gate. Opportunity
rows were stamped with the replay wall clock, not the decision time. Contract
reconstruction used the latest mutable market row, potentially later terms.
Fees checked their effective dates against today's clock. CLI startup also
constructed/connected venue adapters unnecessarily, despite the offline claim.

All these paths are now tested. Historical CLI startup constructs **no venue
adapters**, does no venue/account calibration, and never falls back to live data.
A configured external LLM can still perform retrospective matching; its verdicts
are not claimed to be historical observations or inherently reproducible.

## 2. What the stored data does and does not establish

| Recording | Available evidence | Permitted evaluation |
|---|---|---|
| Schema ≤3 | Displayed bids/asks, one timestamp, raw payload, latest-only market terms. Kalshi timestamp was local normalization time; Polymarket US could be venue `transactTime` or a local fallback. Receipt/provenance, outcome identity, sequence and per-snapshot terms were not separately stored | **THEORY ONLY**. Quote time cannot establish when the collector knew the quote; latest terms cannot establish earlier terms. No simulated fills, even if calculated net edge is positive |
| Schema 4, complete fields | Separate local `received_at`, `timestamp_source`, priced `outcome_side`, sequence when supplied, microstructure, and immutable `contract_json` + `contract_observed_at` bound to each book | **Conditional, snapshot-based simulation** when age/skew/contract/cost gates pass. Availability is observed; unchanged state between samples and the fill haircut are assumed |
| Either schema | No persisted feed/session coverage and gap ledger, queue position, account fills, resolutions, cash payouts or complete historical fee-version stream | **Not tick-accurate reconstruction, not measured fills, not realized trading P&L** |

Migration **004** (`SCHEMA_VERSION` 4) adds nullable fields. It never backfills
old receipt times from quote/persist times or old terms from today's market row.
Read-only replay reads old schemas without migrating or mutating them. New
Kalshi REST/WS and Polymarket US parsers stamp local availability at normalization
(a conservative bound on when the normalized book became usable), distinguish
venue time from local fallback, and identify their YES-perspective ladders.
`save_orderbook_snapshot` freezes only terms already recorded **at or before
receipt**. A contract discovered later is not attached retroactively.

Collection still rejects invalid/empty books rather than recording gap events.
Paper mode is not a continuous snapshot recorder. These are collection coverage
limitations, **not** evidence that a valid old book remained available.

## 3. Decision-time and execution-time rules

- Replay is event-driven in **local availability order**, with exact timestamps,
  not minute buckets. Unstamped legacy rows use quote-time order **for labelled
  theory only**. State can be seeded from before a requested range, never after
  its end; there is no synthetic scan when no in-range observation occurs.
- A decision or arrival uses the **latest observation at or before that time**.
  Delayed old venue quotes cannot be sorted into earlier knowledge or overwrite
  a newer quote. Future source timestamps, conflicting same-time books, corrupt
  or empty updates invalidate state instead of selecting a favourable price.
- Both quote and receipt ages must be within `maximum_book_age_ms` (default
  1,000 ms); the two receipt times must be within `max_pair_skew_ms` (250 ms).
  These are declared research tolerances, **not proof of exchange simultaneity**.
- Snapshot-bound contract identity/version, $1 binary microstructure, outcome
  identity and known tick/minimum rules are required for simulation. Historical
  scan/fills explicitly view a YES book as NO using complementary bids/asks when
  needed; they do not buy the YES ask twice and call it a hedge. Unidentified
  legacy ladders retain their original arithmetic only as theory.
- Costs use the recorded decision/arrival time, not the replay wall clock.
  Supplied fee schedules must cover that date; future-dated or expired-at-that-
  time schedules block. Date bounds are **day-granular**, not an intraday fee
  regime/history selector. Intraday fee changes require more precise effective
  boundaries/version selection before a faithful historical cost reconstruction
  is possible. Freshness remains the separately labelled RESEARCH policy.
- Library-injected decision books/terms must match the provider's dated as-of
  inputs, not borrow valid receipt/contract evidence for different or future
  calculation prices. Both identity and full displayed state are compared.
- An unsupported calculated candidate is persisted as **BLOCK** with data
  reasons, but retains its explicitly theoretical gross/costed edge if computable.
  `evaluation_json` records decision time, exact input snapshot IDs/times/outcome,
  contract versions, fee versions and limitations. Historical opportunity IDs
  and `timestamp`/`first_seen`/`last_seen` use the decision time, not wall-clock
  UUID/time. The output store never contaminates the recorded input store.

## 4. One shared, explicitly configured simulation

`BacktestEngine` and the historical CLI share `HistoricalFillSimulator` in
`backtesting/simulator.py` (`BacktestFillSimulator` is its alias). Config and the
startup banner now name the actual model. The older undated
`execution.simulated.HistoricalExecutionEngine` remains for direct unit tests;
it is **not** an evaluation backdoor.

`config/historical.yaml` declares defaults: 100 ms decision-to-leg-A latency,
100 ms additional leg-B latency, a **0.5 displayed-depth fraction**, 250 ms
maximum observation skew, and separate venue-local simulated starting cash.
A library caller without `simulation_config` does **not** silently simulate.
The defaults/haircut are assumptions, not calibrated execution probabilities.

The model:

1. Selects cost/decision/data-qualified candidates, subject to configured total
   and per-trade capital caps and venue-local cash. This is not the full paper
   coordinator or live risk/eligibility/firewall checklist.
2. Makes one attempt per **unordered contract/version pair** per run. Sequential
   in-flight orders block intervening new entries. It does not harvest the same
   opportunity profit on every subsequent frame.
3. At each logical arrival, rechecks as-of depth, freshness, captured terms and
   date-effective costs. It never waits for or uses the next favourable quote.
   Arrivals beyond the recording/range horizon are **UNAVAILABLE**, not fills.
4. Uses immediate-or-cancel buys at the **worst consumed decision-time price**,
   not a multi-level WAP limit. There is no price concession, retry, unwind,
   queue/hidden-liquidity inference or real adapter. Leg B requests only leg A's
   known filled quantity.
5. Floors the configured fraction of each displayed level to whole contracts,
   consumes residual snapshot/outcome depth across competing orders, costs actual
   fill slices, and reserves eventual settlement fees exactly once. A new external
   snapshot resets displayed depth — another explicit counterfactual assumption.
6. Keeps cash committed, paired positions open, and any naked residual exposure
   visible with quantity, side, entry cost/fees and age. It stops new entries on
   unresolved exposure; it does not write the real/global kill switch or invent
   a successful recovery or payout cash credit.

## 5. Reading report schema 2

| Section / field | Classification and meaning |
|---|---|
| `observed_data` | **OBSERVED** inventory, missing timestamps/context, corrupt/future/out-of-order/ambiguous data, stale observations, unreconstructable contracts and minimum collection requirements |
| `contracts_reconstructed` | **CALCULATED inventory** of as-of contract references summed across decision frames, not unique contracts; repeated appearances do not authorize repeated fills |
| `calculated_opportunities.gross_theoretical_edge_sum` | **CALCULATED, pre-cost** payout minus walked quoted costs; repeated observations may count the same prices repeatedly |
| `costed_opportunities` / `costed_positive_opportunities` | **CALCULATED** complete cost-ledger / positive net-edge counts; never filled-trade counts. Missing costs and block reasons are counted separately |
| `executable_under_replay_policy` | **CALCULATED qualification** under stated cost/decision/temporal assumptions, before simulation's capital/order selection; not real execution-ready or a guarantee |
| `theoretical_depth_coverage` | **CALCULATED sizing walks**, not `fills` or `fill_probability_at_touch` |
| `sampled_observation_spans` / `capital` / `fee_drag` | **CALCULATED** sample first/last spans, per-frame capital demand and opportunity fee drag; not continuous lifetimes, deployed capital or actual charges |
| `theoretical_net_edge_observations` | **THEORETICAL known-subset edge sum**, including calculated negative edges. Not equity, portfolio return, drawdown, trading P&L or a performance bound |
| `calculation_inputs` / simulation `policy`, `assumptions` | Supplied fee/timing versions/evidence and every **ASSUMED** latency/depth/continuity/settlement/capital rule |
| Simulation orders / `filled_orders`, `partial_orders` | **SIMULATED** fills at dated as-of depth; unavailable order data stays null, not a zero/perfect fill |
| `completed_two_leg_positions` / `fully_completed_target_positions` | **SIMULATED** nonzero paired quantity / full originally targeted quantity. Balanced partial entries and naked residuals are different outcomes |
| `completed_pair_net_pnl_known_subset` | **SIMULATED conditional terminal payoff** for costed paired quantities only, after actual simulated acquisition/trade costs and allocated settlement/capital costs. Residuals are not assigned a free recovery or payout |
| `net_simulated_pnl` | Same **conditional terminal** basis, aggregated only over attempted entries with complete execution/cost data and no unresolved exposure. Withheld (`null` / `n/a`) otherwise; includes negative results, never winners only |
| `realized_net_simulated_pnl` | **UNAVAILABLE**: no position-close or resolution/payout-to-cash records were replayed. No calculated payout is silently turned into realized cash |

Paired/residual acquisition and fee allocation is pro rata and explicitly
assumed. Net terminal payoff assumes the certified complementary contracts
settle according to their terms; cancellation/dispute/default is not simulated.
Only the model-selected entries are covered; unsupported, unpriced, cash-blocked
and repeated candidates earn **no** simulated profit. Skips, unavailable orders,
unresolved exposure and paired-but-still-open positions remain in the report.
An empty run writes the **same report structure**, with null P&L/rates, not a
profitable empty backtest.

**Schema change:** the old JSON `fills` and `modelled_equity` sections are removed.
Consumers must read the classifications above; do not relabel theoretical edge
as simulated P&L. Library observation-counter attributes are retained with these
new definitions. Determinism requires identical captured inputs, cost/policy
versions and matching verdicts, not an arbitrary repeat call to an external LLM.

## 6. Minimum further collection — do not fabricate a missing backtest

1. On an authorized, reachable environment, capture **both independent venues**
   with a synchronized local UTC clock, quote time/provenance, local receipt
   time and preferably request-start/response timing. Preserve full displayed
   ladders, priced outcome and contemporaneously observed terms/version/rules.
2. Collect the relevant pair frequently/concurrently enough for the declared
   age/skew/latency limits, with coverage through **both order arrivals**. The
   current 10-second sequential broad-market collector does not establish this.
3. For faithful state continuity, add persisted feed/session IDs, sequenced
   snapshot/delta coverage, missing/halted/empty books, disconnect/gap/recovery
   markers and clock-skew/latency bounds. Schema 4 alone is not this feed ledger.
4. Obtain complete effective-dated (intraday where applicable) fee/account/market
   evidence and capital-release timing (`docs/FEE_READINESS.md`); don't promote
   published reference rates or UNKNOWN timing merely to permit a simulation.
5. For realized outcomes, retain resolution, cash-credit and position-close
   records. For fill calibration, use authorized observed account fills, never
   live orders created for this task. No record of displayed liquidity can prove
   a real order would have filled identically.

Keep raw stores/reports local or in external storage, **out of Git**. A lack of
these records is a reported limitation, not permission to synthesize history.

## 7. Verification (offline, not venue evidence)

- Baseline: `.venv/bin/pytest -o addopts=''` — **643 passed in 35.05 s**.
- After change: same command — **704 passed in 68.46 s**.
- New deterministic battery: `.venv/bin/pytest -o addopts='' tests/test_historical_evaluation.py`
  — **61 passed in 28.24 s**. Empty/missing/invalid datasets, out-of-order/late/future
  quotes, skew/staleness, as-of terms, old-schema read-only/migration preservation,
  partial/unavailable fills, capital/depth reuse, fee effective/expiry timing,
  conditional losses, capture metadata, persistence, CLI/library parity and
  no-venue CLI behavior are covered.
- Successful TEST-ONLY case: 3 opportunity observations → **one** simulated
  10-contract paired position, 2 fills, $7.00 purchase cost + $0.27 trade fees +
  $0.0030 capital cost → **$2.7270 conditional terminal net**, **no realized P&L**
  and no credited payout cash. The test explicitly chooses depth fraction 1;
  the production example default is 0.5. A partial second leg retains 6 naked
  YES contracts and withholds aggregate P&L despite positive paired-subset edge.
- Eight mutation checks each produced **1 failing regression**, then source was
  restored: forward quote lookup, ignored arrival staleness, always-supported
  legacy grade, optimistic partial aggregate P&L, depth reuse, wall-clock fees,
  historical venue initialization, and library decision/source input binding.
  The grade mutation initially exposed a missing assertion; it was strengthened
  before the seven-check batch. Final review additionally reproduced the library
  future-book injection path; normalization-aware term/full-book binding closes
  it, and the eighth mutation proves that guard is required.
- `compileall` for package/tests and `git diff --check` both pass. Existing spec
  numbers remain unchanged; §102 is appended with actual code/test evidence.
- `--status`, `--readiness`, and `--mode historical --date 2020-01-01` all exit 0.
  Readiness leaves the runtime tree unchanged and never creates the recorded
  store. The empty replay's JSON has null simulated/realized P&L and collection
  requirements; no venue connection is attempted. CI was **not run remotely**.
