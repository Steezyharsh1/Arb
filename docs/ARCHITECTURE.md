# ARCHITECTURE

## Layer map

```
                    ┌──────────────────────────────┐
                    │       SHARED STRATEGY        │
                    │ normalization • provenance   │
                    │ matching • orderbooks • fees │
                    │ arbitrage • risk • compliance│
                    └──────────────┬───────────────┘
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
        HISTORICAL              PAPER                 LIVE
   HistoricalExecution    PaperExecution       LiveExecutionEngine
        Engine                Engine                   │
     (simulation)          (simulation)         LiveOrderFirewall
                                                       │
                                                  REAL ORDERS
```

## Security boundary (not a UI feature)

- `prediction_arb/execution/simulated.py` (Historical/Paper engines) has **no adapter
  attribute**. There is no object reachable from a simulation engine
  that can transmit an order.
- `prediction_arb/adapters/kalshi.py` refuses `place_order` unless constructed with
  `allow_order_placement=True`; `main.py` passes that only when
  `config.mode == Mode.LIVE`.
- `prediction_arb/execution/live.py` is the only importer of `LiveOrderFirewall`.
  `LiveExecutionEngine.submit_order` (the generic interface method)
  raises unconditionally; the only working path is
  `submit_order_with_context(request, FirewallContext)`.
- `prediction_arb/core/config.py` fails startup on any mode/engine/database/env
  inconsistency (spec test case 10 verified in tests + live run).
- Databases are per-mode files, tagged internally with their mode;
  opening a file under the wrong mode raises.

## Key data flow (paper cycle)

1. `PaperEngine._discover()` — verified adapters only; eligibility
   filter (Utah, category-aware; sports=UNKNOWN blocked from live).
2. `modes/shared.run_matching` — event grouping → deterministic filter
   → LLM (conservative no-match if unconfigured) → rules validator →
   match certificate (persisted).
3. `_fetch_books` — REST order books; Kalshi `orderbook_fp` normalized
   to YES-perspective bids/asks (asks synthesized from NO bids).
4. `modes/shared.run_arbitrage_scan` → `arbitrage/optimizer.search_event`
   — all independent (YES-venue, NO-venue) orderings per event, only
   pairs with APPROVED certificates.
5. `arbitrage/two_leg.evaluate_two_leg` — marginal-pair sizing, book
   walks, fee engine (FEE_DATA_INCOMPLETE blocks), settlement-delay
   cost (UNKNOWN blocks), status TRADE/WATCH/BLOCK with reasons.
6. Risk limits + health + kill switch → `ExecutionCoordinator` →
   PaperExecutionEngine → partial-fill handler if leg B fails.
7. Everything persisted to the paper DB; JSONL audit logs per mode.

## Repository layout & path resolution

The importable package is `prediction_arb/` and contains **code only**.
`config/`, `data/<mode>/`, `logs/<mode>/` and `reports/<mode>/` live at the
project root, because an installed package may be read-only and per-mode
runtime data must never ship inside it (§49, §51, §77).

`prediction_arb/core/paths.py` is the single place that resolves those
locations. Project root = `$PREDICTION_ARB_HOME` if set, else the first
ancestor of the package (then of the cwd) containing `config/settings.yaml`;
otherwise it raises `ProjectRootError` rather than guessing — a wrong root
would write one mode's data into another mode's directory.

## Extending

- New venue: implement `PredictionMarketAdapter`, add a provenance
  record with evidence, add a fee schedule, add eligibility records.
  The arbitrage engine needs no changes (§65).
- Multi-leg/logical/backtest/web-dashboard: stub interfaces are stable;
  implement behind them (§3).

## Concurrency

asyncio throughout; shared order-book state behind `asyncio.Lock`
(`prediction_arb/orderbooks/aggregator.py`); token-bucket rate limiting + exponential
backoff per adapter (§39). IBKR TWS (if ever used instead of Client
Portal) must be wrapped in `asyncio.to_thread` (§9).
