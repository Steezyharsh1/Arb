# RISK MANAGEMENT

## Authoritative configuration and scope

`config/risk_limits.yaml` is the **only** hard-risk configuration. The former
`settings.yaml → risk` copy was removed on 2026-10-10: it was not read by paper
execution, used the mismatched key `max_trade_capital`, and could drift while
appearing active. `--readiness` verifies that all 12 values are explicit,
numeric, and positive; that is a configuration check only. Runtime evidence is
the coordinator path and `tests/test_risk_enforcement.py`.

The enforced execution path in this release is **paper only**. Live remains
monitor-only and no production code builds a `FirewallContext`; no real order
was enabled by this work. Historical mode evaluates/reports opportunities but
does not execute them.

## Runtime enforcement inventory (2026-10-10)

| Configured limit | Runtime decision point | Paper behavior | Status / caveat |
|---|---|---|---|
| `max_total_capital: 1000` | Coordinator precheck, immediately before lifecycle reservation | Blocks when prior conservatively deployed capital plus known `Opportunity.capital_required` would exceed $1,000 | **ENFORCED.** Capital is not released because paper settlement/close accounting does not exist; this fails closed by eventually blocking too much, never too little. |
| `max_capital_per_venue: 500` | Same precheck | Uses each leg's walked purchase cost plus that venue's trade and settlement fees; it no longer divides the total evenly between venues | **ENFORCED.** |
| `max_capital_per_event: 200` | Same precheck | Blocks projected event allocation; successful and unresolved filled lifecycles update event state | **ENFORCED.** No release until a settlement lifecycle exists (conservative). |
| `max_capital_per_trade: 100` | Same precheck | Blocks known trade capital above $100 | **ENFORCED.** An opportunity with unknown capital is not executable before this point. |
| `max_unhedged_exposure: 25` | Prospective pre-leg-A check; after A; after B; before each recovery order; after recovery | Aggregate naked dollar exposure, including the proposed first leg, must stay at or below $25 | **ENFORCED.** A breach after a fill engages the kill switch but recovery orders remain allowed because they reduce risk. |
| `max_daily_loss: 50` | Coordinator precheck | Blocks at P&L ≤ -$50; observed hedge-concession/unwind losses are posted immediately | **PARTIAL — D21.** Paper has no settlement/position-close lifecycle, so it cannot yet post settlement-day realized P&L. It is not described as full daily-P&L protection. |
| `max_open_positions: 20` | Coordinator precheck | Tests projected existing + two new legs (not only the existing count); updates after fills, including unresolved single legs | **ENFORCED CONSERVATIVELY.** Flattened recovery adds zero; there is no settlement release yet. |
| `max_concurrent_orders: 4` | Coordinator precheck and reservation | Reserves both legs for the entire leg-A/leg-B/recovery lifecycle and releases in `finally`; coordinator transactions are serialized so two tasks cannot pass against stale state | **ENFORCED.** Recovery uses the existing reservation. |
| `max_unhedged_seconds: 30` | Exposure checkpoints listed above | Measures from the first leg-A fill; partial hedge updates preserve that timestamp | **ENFORCED.** A breach stops unrelated new trades via the kill switch but does not strand the recovery order. |
| `max_unhedged_contracts: 100` | Prospective and lifecycle exposure checkpoints | Blocks leg A prospectively and detects any post-fill breach | **ENFORCED.** |
| `max_unhedged_dollars: 25` | Prospective and lifecycle exposure checkpoints | Per-incident dollar bound, separate from the aggregate `max_unhedged_exposure` bound | **ENFORCED.** |
| `max_emergency_loss: 10` | Before every hedge retry in `PartialFillHandler` | Stops the hedge chase before the next order if cumulative price-concession cost would exceed $10 | **PARTIAL — D22.** It bounds hedge price concession, as §32 originally specified, but does not pre-bound unknown exit fees or the realized loss of an unwind. A generic preflight quote/fee interface is required before that broader meaning is safe. |

A risk check that fails before leg A returns an `ExecutionReport` containing the
named violation and submits **zero** simulated orders. Checks are repeated in
`ExecutionCoordinator`, not only in `PaperEngine._maybe_execute`, so a direct
coordinator caller does not bypass them.

## Paper capital (§29)

Paper cash is venue-local and non-fungible. `config/paper.yaml` now explicitly
allocates:

| Enabled venue | Simulated starting cash |
|---|---:|
| Kalshi | $1,000 |
| Polymarket US | $750 |
| ForecastEx | $750 |

The two secondary venues intentionally use the same rehearsal allocation. These
are simulation values, not observed account balances. Startup rejects an
enabled venue omitted from `paper_starting_cash`, and
`PaperExecutionEngine.submit_order()` independently rejects any unknown venue
with `paper starting cash is not configured for venue ...`; it does not use an
implicit zero and does not allow a sell to create cash. Simulated sells are
limited to held quantity and reduce the recorded position FIFO, so an unwind
cannot claim success while leaving the original paper position untouched.

## Partial-fill and exposure lifecycle (§31–§32)

The coordinator now performs:

1. Repeat executable/mode/kill-switch checks.
2. Check capital, loss, position, order, and **prospective leg-A exposure**.
3. Reserve two order slots.
4. Submit leg A; record actual unhedged quantity and dollars immediately.
5. Check exposure, then size leg B to leg A's actual fill.
6. On a short hedge, replace the exposure with the actual unmatched quantity.
7. Before each recovery order, check age/contracts/dollars/aggregate exposure.
8. Chase the hedge only within the concession budget, then unwind only the
   still-unmatched part of leg A.
9. Clear exposure only when the result is HEDGED or FLATTENED. If both hedge
   and unwind fail, retain the residual exposure, account for deployed capital,
   and persistently engage the global kill switch.
10. Release the order reservation in `finally` on every outcome.

A kill switch engaged during recovery blocks **new** trades; it deliberately
does not block the in-progress risk-reducing hedge/unwind. This behavior is
fault-injection tested for partial fills, a successful retry, failed hedge plus
failed unwind, timeout breach, kill activation, and safe clearing.

## Kill switch (§71)

The switch is file-based (`data/KILL_SWITCH`) and persists until `--unkill`.
It blocks live startup, the live firewall, `PaperEngine._maybe_execute`, and now
the coordinator precheck itself. Unresolved paper exposure and a runtime
unhedged-limit breach engage it automatically. Failure to write the switch is
reported as **CRITICAL: manual process stop required**; it is never reported as
safe.

`execution/emergency.py::EmergencyManager` remains disconnected. It does not
cancel orders or flatten positions and its engine/adapter assumptions have not
been validated. This change did **not** activate it or any automatic live
flatten/cancel behavior. The only newly connected emergency action is the
persistent stop-new-trades switch described and tested above.

## Other configured execution constraints found by the Arb.10 audit

These are not fields in `risk_limits.yaml`, but they read like execution
controls and were traced through the production call graph:

- `minimum_net_profit`, `minimum_net_return`, settlement-delay spread, fee
  completeness/freshness, and both order-book-age limits are enforced in the
  scan/paper path.
- The former `guaranteed_only`, `block_unknown_status`,
  `block_unknown_venue_relationship`, `preferred_order_type`, and
  `allow_market_orders` YAML switches were removed from `settings.yaml` because
  no production caller read them. The current behavior is fixed in code:
  scanning constructs TYPE A guaranteed opportunities only; only proven
  `INDEPENDENT` pairs may be approved/evaluated; and the eligibility helper
  blocks UNKNOWN for `Mode.LIVE` while allowing supported non-live research.
  `PaperEngine` calls that helper, but `LiveEngine` has no discovery/eligibility
  scan path, so this is not a live production gate. The paper coordinator always
  constructs priced LIMIT orders; the firewall verifier rejects a missing limit
  price if called, but no live coordinator is wired. Regression tests pin these
  current behaviors. This cleanup does not add new configurability.
- `minimum_liquidity_ratio` and both execution-latency settings were also
  removed (**D23**), not implemented. The walker stops at displayed liquidity,
  but it does not compare a liquidity ratio; the coordinator records
  `time_between_legs_ms`, but does not compare it to a threshold. Do not
  reintroduce these keys until the sizing denominator and post-leg-A recovery
  semantics are specified and fault-tested.
- Live risk values are not yet supplied to a production `FirewallContext`
  (**D24**). Live is monitor-only, so this does not create a real-order bypass;
  it is a blocker that must be resolved before any live coordinator is built.

## Health and crash recovery

`HealthMonitor` still gates paper execution before the coordinator. The live
engine still writes intent before transmission and reconciles timeout/startup
state, but no live coordinator is connected. See `docs/CRASH_RECOVERY.md`.
