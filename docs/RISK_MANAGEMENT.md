# RISK MANAGEMENT

## Hard limits (config/risk_limits.yaml, §70)

max_total_capital, max_capital_per_venue / per_event / per_trade,
max_unhedged_exposure, max_daily_loss, max_open_positions,
max_concurrent_orders. Any violation → the trade is not attempted;
existing positions remain monitored.

## Partial-fill bounds (§32)

max_unhedged_seconds / contracts / dollars, max_emergency_loss.
Recovery procedure (prediction_arb/execution/partial_fills.py):
1. Chase the hedge leg with bounded price concession (total concession
   cost capped by max_emergency_loss).
2. If chase fails, unwind the filled leg into its bids.
3. Alert + record regardless; unresolved exposure triggers a
   MANUAL ACTION REQUIRED alert recommending the kill switch.

## Kill switch (§71)

File-based (`data/KILL_SWITCH`), engaged by `python -m prediction_arb.main --kill` or
programmatically (EmergencyManager). Blocks the firewall, blocks live
startup, blocks new paper trades, and persists across restarts until
`--unkill`.

## Execution risk model (§31)

PRECHECK → leg A → confirm → leg B (sized to leg A's actual fill) →
confirm; time-between-legs recorded. Unhedged exposure is registered in
ExposureTracker the instant leg A fills and cleared only when hedged.

## Capital (§29–§30)

Per-venue, non-fungible. Sizing uses each leg's venue-specific
deployable cash; unknown balances size to zero. Rebalancing suggestions
are advisory only.

## Health gating (§38)

HealthMonitor tracks per-venue consecutive errors, silence windows,
latency, and rate-limit hits; trading requires ALL involved venues
healthy.

## Crash recovery (§61)

Intent (PENDING) written before transmission; SUBMITTED after; on
startup, LiveExecutionEngine.recover_on_startup queries the venue for
every PENDING/SUBMITTED intent and reconciles (resting / filled /
never-transmitted), flagging orphaned single legs for hedge review.
Timeouts never trigger blind retries — venue state is queried first
(§72).
