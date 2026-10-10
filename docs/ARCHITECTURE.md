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
   HistoricalFill         PaperExecution       LiveExecutionEngine
       Simulator              Engine                   │
    (as-of depth)          (simulation)         LiveOrderFirewall
                                                       │
                                                  REAL ORDERS
```

## Security boundary (not a UI feature)

- `prediction_arb/backtesting/simulator.py::HistoricalFillSimulator` and the
  paper/unit engines in `execution/simulated.py` have **no adapter attribute**
  or real-order submit path. Historical CLI startup skips venue adapters and
  account calibration entirely; its only market-data source is recorded state.
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

1. `PaperEngine._discover()` — verified adapters only; applies
   `check_contract_eligibility` (Utah, category/event-text aware; sports is
   RESTRICTED, non-sports is UNKNOWN). The helper returns UNKNOWN as blocked
   for `Mode.LIVE`, but `LiveEngine` is monitor-only: no production
   eligibility scan or caller to the context-gated submit method exists. This
   is not an end-to-end live gate (D24).
2. `modes/shared.run_matching` — bounded candidate proposal
   (§96: cross-venue, settlement ±6h, threshold / numeric / content-word
   keys, local density cap; `docs/CANDIDATE_RETRIEVAL.md`) → deterministic filter → LLM (conservative no-match if
   unconfigured; env-wired client per §97) → rules validator (only a
   proven-INDEPENDENT pair can be APPROVED) → match certificate
   (persisted).
3. Books — streamed first if possible, polled otherwise. When
   credentials exist, `_ensure_streaming` subscribes each eligible
   market to the venue's WebSocket feed once (Kalshi: `orderbook_delta`,
   sequenced snapshot/deltas, gap-triggered recovery — see
   `prediction_arb/adapters/kalshi_ws.py`); books then update the live
   cache in real time (sub-second staleness). `_fetch_books` polls REST
   only for markets that are NOT streamed; both paths normalize Kalshi
   `orderbook_fp` to YES-perspective bids/asks (asks synthesized from NO
   bids). Without credentials (or on any streaming failure) everything
   is polled, exactly as in v1.
4. `modes/shared.run_arbitrage_scan` → `arbitrage/optimizer.search_event`
   — all independent (YES-venue, NO-venue) orderings per event, only
   pairs with APPROVED certificates.
5. `arbitrage/two_leg.evaluate_two_leg` — marginal-pair sizing, book
   walks, fee engine (FEE_DATA_INCOMPLETE blocks), settlement-delay
   cost (UNKNOWN blocks), status TRADE/WATCH/BLOCK with reasons.
6. Risk limits + health + kill switch → `ExecutionCoordinator` →
   PaperExecutionEngine → partial-fill handler if leg B fails.
7. Everything persisted to the paper DB; JSONL audit logs per mode.

## Historical flow (§102)

Schema-4 capture binds normalized book receipt/provenance/outcome and immutable
observed terms → read-only `HistoricalDataProvider` (exact as-of state) → common
`BacktestEngine` matching and decision-date costing → data/cost qualification
→ explicitly enabled `HistoricalFillSimulator` (arrival lookup, contingent IOC
legs, residual depth, cash/fee reserves, paired positions and naked exposure)
→ report schema 2. CLI persists calculated opportunities in its own historical
store, separately from simulated fills. No equity-by-opportunity summation,
forward quote selection, implied queue probability or settlement cash credit.
Legacy/incomplete records remain theory; see `docs/HISTORICAL_EVALUATION.md`.

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
- Multi-leg/logical/web-dashboard: stub interfaces are stable; implement
  behind them (§3). Historical evaluation has a conditional depth simulator,
  not a full tick-accurate/realized-performance backtest (§102).

## Concurrency

asyncio throughout; shared order-book state behind `asyncio.Lock`
(`prediction_arb/orderbooks/aggregator.py`); token-bucket rate limiting + exponential
backoff per adapter (§39). IBKR TWS (if ever used instead of Client
Portal) must be wrapped in `asyncio.to_thread` (§9).
