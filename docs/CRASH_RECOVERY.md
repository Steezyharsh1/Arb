# CRASH RECOVERY

## Write-ahead intents (§61)

Every live order follows:

```
1. INSERT trades row  status=PENDING   (before any network I/O)
2. firewall verify                     (failure → REJECTED)
3. transmit
4. UPDATE status=SUBMITTED + venue order_id
5. on confirmation → FILLED / REJECTED / CANCELED
```

## Startup reconciliation

`LiveExecutionEngine.recover_on_startup()` runs before anything else in
live mode:

For each PENDING/SUBMITTED/UNKNOWN row (`Repository.unreconciled_trades()`, D32):
1. If we have a venue order_id → `get_order` and adopt venue truth.
2. Else search venue open orders by client_order_id → SUBMITTED.
3. Else search venue fills by client_order_id → FILLED, flagged as a
   possible **orphaned leg** (one leg filled, hedge state unknown) for
   manual/automated hedge review.
4. Else (no trace in open orders or fills) → marked `UNKNOWN` (never
   `REJECTED`, D33): absence from an unpaginated listing does not prove an
   order was rejected or never transmitted, so the intent stays visible in
   `unreconciled_trades()` until resolved by a definitive venue lookup or
   manual reconciliation.

This prevents: ghost orders, duplicate orders, false `REJECTED` records from
truncated listings, and undetected orphaned positions.

## Timeout discipline (§72)

A transmission timeout or transport error (`asyncio.TimeoutError`,
`TimeoutError`, `httpx.TimeoutException`, `httpx.TransportError`, D31) NEVER
leads to a blind retry. The engine queries open orders and fills for the
`client_order_id` first; if the venue shows nothing, status `UNKNOWN` is
recorded and surfaced for manual reconciliation rather than guessed away.

## Simulated modes

Paper/historical use the same repository schema (per-mode DBs), so the
recovery logic is exercised in tests (`TestCase8CrashRecovery`) without
touching a venue.
