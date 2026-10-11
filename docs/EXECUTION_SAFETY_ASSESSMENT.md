# Execution safety assessment — simulated boundary and live-path readiness (Arb.16)

- **Date:** 2026-10-11 (UTC). **Branch:** `arena/5e91ce2b-arb`, from `main` at `d7ec12d`.
- **Scope:** the order-intent lifecycle in `prediction_arb/execution/`, the live
  order path (`live.py`, `firewall.py`, `adapters/kalshi.py`), risk and kill-switch
  controls, and their offline tests. This document is an engineering assessment,
  not a live-readiness claim.
- **Method:** source reading with call-graph greps; the full offline suite
  (Python 3.11.2, pytest 9.1.1, httpx 0.28.1); throwaway probes in `/tmp` that
  use `httpx.MockTransport` and in-memory engines; and one read-only check of
  Kalshi's public documentation. **No venue was contacted, no credential was
  loaded, and no order was sent.**
- **Result:** 764 → **776 passed, 7 `xfail(strict=True)`** (the seven open defects
  D31–D37 below, each failing for its stated reason; see §5).

---

## 0. Verdict

| Question | Answer | Basis |
|---|---|---|
| Can `collect`, `historical`, or `paper` reach a real order-placement path? | **No, by construction and by test.** Simulated engines hold no adapter or transport. Only `LiveExecutionEngine` calls `place_order`, and no paper, historical, or collect module constructs it. | §3; `test_paper_engine_has_no_adapters`, `test_simulated_engines_hold_no_order_transport`, `test_kalshi_adapter_blocks_orders_outside_live` |
| Can the live path place an order today from the CLI? | **No.** `LiveEngine.run()` is monitor-only and never calls `submit_order_with_context`. No production code builds a `FirewallContext`. | §2; ROADMAP D24 |
| Is the live path safe to enable? | **No.** Seven open defects (D31–D37) affect ambiguous outcomes, restart recovery, and the coordinator's handling of live results. D38 affects accounting, and D39 affects paper restart. See §8. | §5, §8 |
| Was a safety defect fixed here? | **Yes: D30.** An order POST was retried and failed over across hosts, so one intent could be transmitted up to eight times. Now it is sent once. | §4; `TestOrderPostIsNotRetried` |
| Is the paper boundary adequate for research simulation? | **Adequate for the simulation boundary itself, with known limits**: restart resets paper risk state (D39), daily-loss and unwind bounds are incomplete (D21, D22), and simulated fills do not dedupe identifiers (D34). Simulated P&L is not performance evidence (D29). | §6, §8 |

---

## 1. Order-intent lifecycle

Paper and historical share one coordinator path. Live would add the intent row,
firewall, and reconciliation steps shown after the arrow.

```
scan (modes/shared.py, arbitrage/two_leg.py) → Opportunity
  → PAPER engine _maybe_execute            kill switch check          (modes/paper/engine.py:364)
  → ExecutionCoordinator.execute           lifecycle lock             (coordinator.py:77)
      precheck: is_executable, mode, 2 legs, kill switch, risk limits, exposure  (:81–138)
      reserve open_orders += 2             released in finally        (:143, :147)
      LEG A  engine.submit_order           (:158) ── paper: PaperExecutionEngine (no transport)
      exposure record + checkpoint         (:182–187)
      LEG B  engine.submit_order           (:189–199)
      both filled → clear exposure, record deployment                 (:201–207)
      else → PartialFillHandler.handle     (coordinator :218–236; partial_fills.py:60)
             hedge retries (≤2, concession ≤ max_emergency_loss; new uuid4 per retry)  (partial_fills.py:90–134)
             unwind leg A at limit 0.01    (partial_fills.py:136–180)
             unresolved → exposure kept, kill switch engaged           (coordinator :243–265, :309)
  live only, not wired:
      write_trade_intent PENDING (repo) → LiveOrderFirewall.verify (15 checks, firewall.py:42)
      → KalshiAdapter.place_order → one POST /portfolio/orders (kalshi.py, `_post_order_once`)
      → status update; timeout → venue reconciliation (asyncio.TimeoutError only, D31)
      → startup: recover_on_startup over PENDING/SUBMITTED only (D32, D33)
```

### Order-capable boundaries

A real order could only leave the process through one of these:

| # | Boundary | Guard | Production caller? |
|---|---|---|---|
| B1 | `KalshiAdapter.place_order` → `POST /portfolio/orders` | `allow_order_placement`, set only when `config.mode == LIVE` (`main.py:93`); `PermissionError` otherwise | `LiveExecutionEngine` only (`live.py:99`) |
| B2 | `LiveExecutionEngine.submit_order_with_context` | Firewall (15 checks), intent write first; `LiveEngine.startup` passes only `VERIFIED` adapters | Constructed only by `LiveEngine.startup` (`modes/live/engine.py:73`), reached only after the typed phrase and the kill-switch check (`main.py` live branch). `run()` does not submit. |
| B3 | `LiveExecutionEngine.cancel_order` → `DELETE /portfolio/orders/{id}` | **No firewall and no kill-switch check.** Cancellation is risk-reducing, but the path is unguarded. | None (no production caller) |
| B4 | `PolymarketUSAdapter.place_order` / `cancel_order` | Raises `PolymarketOrderPlacementDisabled` unconditionally (`polymarket_us.py:566–580`) | — |
| B5 | Webull, Moomoo, ForecastEx stubs | `NotImplementedError` | — |
| — | `PaperExecutionEngine`, `HistoricalExecutionEngine` | No adapter or transport attribute. `submit_order` fills against a book lookup. | Not a boundary |

`submit_order` on `LiveExecutionEngine` always raises `FirewallBlocked`, so there
is no unguarded entry (`live.py:153`; tested by `test_unguarded_submit_blocked`).

---

## 2. Live gates

| Gate | Status | Evidence / gap |
|---|---|---|
| Config: `allow_live_orders` | **Verified at construction.** The engine refuses when false. | `test_live_engine_refuses_without_allow_flag`. `live.yaml` is the only file with `allow_live_orders: true`. |
| Typed confirmation | **Verified.** The exact phrase is required; no flag bypasses it. | `test_confirmation_phrase_required`. **Gap:** a single human gate, with no re-prompt on a live loop because none exists. |
| Firewall: 15 checks | **Verified in isolation.** Each check blocks on its own. | `TestFirewall.test_each_check_blocks`, `test_kill_switch_blocks`. **Gap:** nothing constructs a production `FirewallContext` (D24). The caller supplies `book_timestamp`, `expected_price`, and `opportunity_still_valid`, and their correctness is not checked here. |
| Firewall placement | **Verified in the engine.** The verify runs after the intent write and before transmission. A failure marks the intent `REJECTED` and the adapter is not called. | `test_firewall_block_never_reaches_the_adapter` |
| Credential isolation | **Verified for env files.** Live-marked keys are refused in the paper env. The adapter's order gate is a constructor flag set from mode. | `test_live_keys_in_paper_env_refused`; `build_adapters` (`main.py:75–112`). Live keys are not reachable from paper construction. |
| Kill switch | **Verified as a fail-closed file.** Corrupt or partly written state still reads as engaged. Checked before paper execution, at the coordinator precheck, and at firewall verify. Engaged on unresolved exposure, exposure breach, or recovery exception. Startup refuses live while engaged. | `test_kill_switch_is_checked_inside_coordinator_not_only_paper_loop`, `test_failed_hedges_and_failed_unwind_engage_kill_switch`, `test_timeout_breach_engages_kill_but_recovery_order_remains_allowed`. **Gaps:** not checked between legs, which is deliberate so a hedge can complete. `engage` is not atomic. `--kill` overwrites the reason. `EmergencyManager` does not cancel or flatten (D24, D26). |
| Rate limits | **Partly verified.** Kalshi reads 8/s and writes 3/s, with tier clamping. The order POST now takes one write token per intent. | Budget-clamp tests in `test_kalshi_phase2.py`. **Gap:** `TokenBucket` has no direct unit test. |
| Capital and exposure | **Verified in paper** through the coordinator: per trade, venue, event, total, positions, orders, unhedged dollars/contracts/seconds, and emergency loss. Checked before leg A, after each leg, before each recovery order, and after recovery. | `tests/test_risk_enforcement.py` (see §4). **Gaps:** daily loss has no settlement ledger and resets on restart (D21, D39). Emergency loss bounds hedge concession, not unwind loss (D22). No liquidity or latency gate (D23). `max_unhedged_seconds` is checked only at checkpoints, not by a timer. |
| Audit logging | **Partly verified.** Live: a `TradeRow` intent is written before transmission and updated afterward (`repo`). Paper: attempt summaries are persisted. Alerts go to the console and to the logger. | **Gaps:** no request or response payload log. `open_position()` has no caller, so the position ledger is never written. Paper fills and orders live only in memory (`SimState`). |

---

## 3. Mode isolation (collect, historical, paper)

- `LiveExecutionEngine` is imported only by `modes/live/engine.py` and its
  re-export `modes/live/execution.py`. `main.py` reaches it only through the lazy
  import of `LiveEngine` in the live branch (`main.py:395`). The name also
  appears as a string in `core/config.py` and `monitoring/readiness.py`, and in
  docstrings. Those are not imports. No `modes/paper`, `modes/historical`, or
  `modes/collect` module imports or constructs it.
- `build_adapters` sets `allow_order_placement=(config.mode == Mode.LIVE)` for
  Kalshi and Polymarket US. Collect, historical, and paper therefore construct
  adapters that refuse orders. The two smoke scripts pass `False` explicitly.
- `PaperExecutionEngine` has no `adapters`, `firewall`, or transport attribute
  (`test_paper_engine_has_no_adapters`). Mode mismatch is rejected in the engine
  as well (`test_mode_mismatch_rejected_by_sim_engine`).
- The kill-switch file lives at `data/KILL_SWITCH` and is global across modes.
  Engaging it blocks paper trades on the next scan and refuses live startup
  (`main.py:448`). It does not stop an order already in flight.

---

## 4. Fault-injection coverage

Every scenario below is offline. "Passing" means the test passes today.

| Scenario | Result | Test(s) | Status |
|---|---|---|---|
| Stale book | Simulator reads through the freshness-gated aggregator. A stale book is rejected as "no order book". | `test_stale_book_is_never_a_simulated_fill_source`, `test_paper_realism.py::TestPaperFreshnessEndToEnd` | Verified |
| Venue disconnection on reads | GET fails over across bases, then raises when all fail. | `test_reads_still_fail_over_to_the_fallback_host`, `test_kalshi_phase2.py` | Verified |
| Venue disconnection on order POST | Single attempt. The error propagates and does not rotate host. A 429 now surfaces as `httpx.HTTPStatusError` (it was `TransportError`); the engine maps any non-timeout exception to `UNKNOWN`, so the outcome is still ambiguous. | `TestOrderPostIsNotRetried` (4 failure modes) | **Verified after D30 fix** |
| Duplicate order POST | Before D30: 8 POSTs for one intent on ReadTimeout or ConnectError, 2 on 500. After: 1. | `TestOrderPostIsNotRetried` | **Verified after D30 fix** |
| Timeout, asyncio type | Reconciled by `client_order_id` (open orders, then fills). The order is never re-sent. | `test_live_timeout_by_asyncio_error_reconciles_by_client_id` | Verified |
| Timeout, httpx type (the real adapter's exception) | Not reconciled. Re-raised with the intent left `UNKNOWN`. | `test_httpx_timeout_is_reconciled_by_client_id` | **Open, D31** |
| Firewall block | Adapter not called. Intent `REJECTED`. | `test_firewall_block_never_reaches_the_adapter` | Verified |
| Partial fill, retry hedges | Exposure cleared. | `test_partial_fill_retry_safely_hedges_and_clears_exposure` | Verified |
| Failed hedge, failed unwind | Kill switch engaged, exposure retained. | `test_failed_hedges_and_failed_unwind_engage_kill_switch` | Verified |
| Leg B raises | Treated as unfilled. Hedged by retry. Not reported as success. | `test_leg_b_exception_is_recovered_not_reported_as_success` | Verified |
| Hedge retry raises (ambiguous) | Another hedge is submitted under a new uuid before the first is resolved. | `test_ambiguous_hedge_retry_error_does_not_submit_another_hedge` | **Open, D37** |
| Leg A raises | Treated as "failed pre-transmission". No exposure recorded, no kill switch. | `test_leg_a_exception_is_not_treated_as_safe_no_order` | **Open, D35** |
| Leg A resting (`SUBMITTED`) | Aborts with no cancel, no exposure, no kill switch. | `test_resting_leg_a_is_cancelled_or_tracked` | **Open, D36** |
| Restart with an `UNKNOWN` intent | Not in startup recovery. | `test_unknown_intent_is_visible_to_startup_recovery` | **Open, D32** |
| Restart with an order missing from the listing | Marked `REJECTED` ("never transmitted"). | `test_missing_from_listing_is_not_recorded_as_never_sent` | **Open, D33** |
| Simulator: same `client_order_id` twice | Fills twice (10 held from a 5-contract request). | `test_resubmitting_the_same_client_order_id_does_not_double_fill` | **Open, D34** |
| Kill switch engaged before execute | Precheck refuses. No leg submitted. | `test_kill_switch_is_checked_inside_coordinator_not_only_paper_loop` | Verified |
| Kill switch engaged mid-lifecycle | Next trade blocked. Recovery continues by design. | `test_timeout_breach_engages_kill_but_recovery_order_remains_allowed` | Verified |
| Restart of the paper process | Positions, exposure, daily P&L, and deployment reset. The kill file persists. | Code reading: `PaperEngine.__init__` builds fresh `RiskState` and `ExposureTracker`. No restart test. | **Open, D39** (code-verified only) |
| Prospective and aged exposure, per-limit breaches | Blocked before leg A, with zero submissions. | `TestUnhedgedExposureLifecycle`, `TestHardLimitCoordinatorBoundary` | Verified |
| Max emergency loss | Hedge chase stopped before submission. | `test_max_emergency_loss_stops_hedge_chase_before_submission` | Verified (bounds concession only, D22) |
| Observed recovery loss feeds daily gate | Next trade blocked. | `test_observed_recovery_loss_feeds_daily_loss_gate` | Verified |
| Fee data missing or stale | Simulated fill rejected. Opportunity blocked. | `test_context_incomplete_fee_rejects_simulated_order`, `test_stale_fee_schedule_strict` | Verified |

### Probes not committed

Four throwaway probes (`/tmp/probe`, outside the repository) confirmed the
pre-fix behavior and the post-fix behavior:

1. Kalshi order POST under ReadTimeout, ConnectError, and 500: 8, 8, and 2
   attempts before the fix, across both hosts, all with the same
   `client_order_id`. After the fix, 1 attempt each.
2. Live engine with `httpx.ReadTimeout`: reconciliation not run, intent not in
   `unreconciled_trades()`.
3. Coordinator with leg A raising, or resting: no exposure, no kill switch.
4. Simulator with the same request twice: 10 contracts held.

---

## 5. Open defects — test marker and exact failure

Each `xfail(strict=True)` test fails for the reason its marker states. `strict`
means that when the defect is fixed, the suite reports XPASS and the marker must
be removed on purpose. Checked with `pytest --runxfail`:

- D31: `httpx.ReadTimeout` raised; no reconciliation.
- D32: `UNKNOWN` row absent from `unreconciled_trades()`.
- D33: intent status `REJECTED` after an empty listing.
- D34: `held == 10`, expected `5`.
- D35: `exposed == False` and `kill_switch.is_engaged() == False`.
- D36: same.
- D37: 4 submissions, expected 3.

---

## 6. Interval between legs and failure of the second leg

- **Paper.** `simulated_latency_ms` (250 ms in `config/paper.yaml`) sleeps before
  each submit, and the book is read after the sleep, so a streamed update can
  land between legs. This is a modelled delay, not measured venue latency.
  Exposure is checkpointed after leg A, so a breach engages the kill switch and
  leg B still runs, because completing the pair reduces risk.
- **Live.** No gate on the interval. `time_between_legs_ms` is recorded but not
  checked (D23).
- **Leg B fails, paper.** Up to two hedge retries, each with a new `client_order_id`
  and a price concession bounded by `max_emergency_loss` (default $10, checked
  before each submit). Then an unwind of leg A at limit 0.01. If the unwind fails,
  exposure is retained, the kill switch engages, and the outcome is an alert.
  Unwind loss is unbounded in price (D22). The alternatives in `hedging.py` are not
  wired (D26).
- **Leg B fails, live.** The same procedure runs, but a retry after an ambiguous
  error is a second live order (D37), and exposure is held only in memory. A
  restart clears it, and only the kill file survives (D39).

---

## 7. Guarantees: tested, assumed, unverified

### Tested (offline, passing)

- No simulated engine holds a transport; mode mismatch is rejected.
- Firewall blocks each of its checks; a block never reaches the adapter.
- Live engine refuses construction without `allow_live_orders`; the confirmation phrase is required (`test_confirmation_phrase_required`).
- Unguarded `submit_order` on the live engine raises.
- Kalshi order POST is single-attempt, with no host rotation (D30). Reads still fail over.
- Coordinator precheck: kill switch, mode, leg count, all risk limits, exposure aging and breaches.
- Partial-fill recovery: retry hedge, bounded concession, unwind, unresolved path with kill switch.
- Stale books and missing fee data are refused in simulation.

### Assumed (not verified against code paths or venue)

- That a leg-A exception means the order was **not** transmitted (D35). Nothing
  checks this.
- That a missing fill or order in a venue listing means it never reached the
  venue (D33). The listings are not shown to be complete.
- That the kill switch is checked often enough. It is checked only at paper
  execution, the coordinator precheck, and firewall verify. No live loop exists to check it continuously.
- That `book_timestamp` and `expected_price` supplied to the firewall are
  correct. They come from the caller.
- That `max_unhedged_seconds` is meaningful. It is checked only at checkpoints,
  and paper fills are effectively instantaneous.
- That a retried `place_order` with the same `client_order_id` is safe. The old
  behavior relied on this; the new behavior does not depend on it.

### Unverified against the real venue

- **Duplicate `client_order_id` behavior.** Kalshi's public quick start
  ([docs.kalshi.com, "Quick Start: Create your first order"](https://docs.kalshi.com/getting_started/quick_start_create_order))
  says the API rejects duplicate `client_order_id`s and that a retry may reuse
  the ID. A third-party SDK reference
  ([TexasCoding/kalshi-python-sdk, `docs/resources/orders.md`](https://github.com/TexasCoding/kalshi-python-sdk/blob/main/docs/resources/orders.md))
  says a reused ID returns the original order. The quick start documents
  `/portfolio/events/orders`; the adapter calls `/portfolio/orders`. This
  checkout has not verified either claim on that endpoint, and neither source
  is venue evidence for this adapter. D30 removes the dependency rather than
  relying on it.
- **Order status semantics.** `_map_status` maps `resting`, `pending`,
  `executed`, `canceled`, and `cancelled`. Anything else becomes `UNKNOWN`. Live
  placement sets `average_fill_price=None` and `fees=0`, and counts only
  `taker_fill_count` (D38).
- **Cancel and amend semantics**, and whether a cancel after a fill is accepted.
- **Timeout and 429 behavior** under real load. The 8/s and 3/s budgets are
  configured, not measured.
- **Fill reporting latency and completeness**: whether `/portfolio/fills` pages,
  and how far back it reaches.
- **Live fees**: effective fees on fills are unknown (FEE_READINESS).

---

## 8. Unresolved failure modes

| ID | Failure mode | Severity | Phase | Test |
|---|---|---|---|---|
| D31 | Real adapter timeout is not reconciled (httpx types are not `asyncio.TimeoutError`) | High (live) | 8 | `test_httpx_timeout_is_reconciled_by_client_id` (xfail) |
| D32 | `UNKNOWN` intents are never picked up by startup recovery | High (live) | 8 | `test_unknown_intent_is_visible_to_startup_recovery` (xfail) |
| D33 | Absence from a listing is recorded as `REJECTED` | High (live) | 8 | `test_missing_from_listing_is_not_recorded_as_never_sent` (xfail) |
| D34 | Simulator does not deduplicate `client_order_id` (no in-tree trigger) | Medium (paper) | 6 | `test_resubmitting_the_same_client_order_id_does_not_double_fill` (xfail) |
| D35 | Leg-A exception assumed pre-transmission; no exposure, no kill switch | High (live), Medium (paper) | 8 | `test_leg_a_exception_is_not_treated_as_safe_no_order` (xfail) |
| D36 | Resting (or `UNKNOWN`) leg A aborts with no cancel and no tracking. Kalshi maps a resting limit to `SUBMITTED`, so a normal resting order takes this path. | High (live) | 8 | `test_resting_leg_a_is_cancelled_or_tracked` (xfail) |
| D37 | Ambiguous hedge-retry error leads to another hedge under a new id | High (live) | 8 | `test_ambiguous_hedge_retry_error_does_not_submit_another_hedge` (xfail) |
| D38 | Live result drops average fill price, fees, and maker fills; exposure dollars use the limit price | High (live accounting) | 8 | Documented only; no test (field names unverified) |
| D39 | Paper restart resets daily loss, deployment, and unresolved exposure; a daily-loss breach does not engage the kill switch | Medium (paper) | 6 | Code-verified; no restart test |
| D21, D22, D23, D24, D26, D29 | Existing defects (see ROADMAP) | — | — | — |

---

## 9. Prioritized remediation

1. **Done (Arb.16): D30.** One POST per order intent, no host rotation.
2. **Make ambiguous outcomes first-class (D31, D35, D36, D37).** Introduce one
   outcome type: ACCEPTED, REJECTED (definitive), or UNKNOWN. Reconcile UNKNOWN
   by `client_order_id` before any retry, next leg, or unwind. Unresolved UNKNOWN
   engages the kill switch. Ambiguity is never coerced to "not sent".
3. **Fix startup recovery (D32, D33).** Include `UNKNOWN` in `unreconciled_trades`.
   Mark `REJECTED` only from a definitive venue response for that
   `client_order_id`, never from absence in a listing. Page fills and open orders
   to completion, or look up by id.
4. **Map live results honestly (D38).** Record average fill price, fees, maker and
   taker fills, and partial states. Compute exposure from actual fills, not from
   the limit price. Fixture tests need venue payload samples or the official
   schema.
5. **Idempotent simulator (D34).** Reject or return the original result for a
   repeated `client_order_id`.
6. **Persist risk state (D39, D21).** Persist exposure and the daily-loss ledger,
   with a UTC day rollover, and reconcile on start. A daily-loss breach should
   block, and the block should survive restart.
7. **Build the live coordinator (D24, D26)** only after items 2–4, with
   demo or sandbox fault drills (duplicate, timeout, cancel failure, orphaned
   fill, firewall context generation). Keep `EmergencyManager` disconnected until
   it can cancel and flatten.
8. **Unwind and emergency bounds (D22, D23).** A freshness-gated recovery quote
   and fee preflight, and a defined liquidity and latency gate.
9. **Owner-gated venue verification (demo only).** Confirm duplicate
   `client_order_id` behavior on the endpoint the adapter uses. Confirm status
   enums, cancel-after-fill, fill pagination, and 429 behavior. No live orders.
10. **Durable audit trail.** A per-mode order and fill ledger, persisted alerts,
    and payload logging with credentials redacted.

---

## 10. Reproduce

```bash
.venv/bin/pytest -o addopts='' -q                              # 776 passed, 7 xfailed
.venv/bin/pytest -o addopts='' -q tests/test_execution_fault_matrix.py -k TestOpenDefects --runxfail
.venv/bin/pytest -o addopts='' -q tests/test_safety.py -k TestOrderPostIsNotRetried
python -m prediction_arb.main --status                         # exit 0
python -m prediction_arb.main --readiness                      # exit 0, advisory
```

Reverting `prediction_arb/adapters/kalshi.py` to `d7ec12d` makes the four
failure-mode cases of `TestOrderPostIsNotRetried` fail (they were checked; the
success and read-failover controls still pass).

---

## 11. Non-claims

- Not live-ready. No live path is enabled, and none was tested against a venue.
- No venue behavior is verified by this work (see §7).
- Simulated fills, latencies, and P&L are model outputs, not performance evidence.
- The `xfail` tests are a record of known defects. They are not evidence that
  the defects are handled.
