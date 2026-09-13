# TESTING

```bash
python -m pytest tests/          # 84 tests
```

## Coverage map

**Spec §75 mandatory cases** (`tests/test_spec_cases.py`):
1. Fees exceed $0.01 gross → NO TRADE ✅
2. 5 available vs 100 requested → sized to 5 ✅
3. Same underlying exchange → NO ARBITRAGE ✅
4. UNKNOWN relationship → BLOCK ✅
5. LLM says match, deterministic disagrees → REJECT ✅
6. 2% displayed, 2.2% real costs → NO TRADE ✅
7. One leg fills → recovery procedure runs ✅
8. Crash between legs → intents found & reconciled ✅
9. T+0 vs T+3 settlement delay costed (unknown → None, blocking) ✅
10. paper config + LiveExecutionEngine → startup refused ✅
    (also verified against the real CLI)

**Unit** (`test_units.py`): book walking, staleness, Kalshi
`orderbook_fp` parsing (ask synthesis from NO bids), quadratic/fixed
fees, missing-fee errors, independence matrix, deterministic matcher
rejections, LLM schema validation (malformed/contradiction/bounds),
risk limits, settlement lockup, order-type metadata, normalization,
venue aliases.

**Safety** (`test_safety.py`): paper engine holds no adapters; adapter
refuses orders outside live; mode-mismatch rejection; confirmation
phrase; firewall — every one of its checks individually blocks
(parameterized); kill switch; stub honesty (NotImplementedError, never
fabricated data); live-credential leakage into paper env refused; DB
mode-tag enforcement.

**Failure** (`test_failure_modes.py`): stale/out-of-order books hidden;
FEE_DATA_INCOMPLETE blocks (never zero); strict fee freshness;
insufficient sim cash; limit-price respected; historical no-data
honesty; backtest stub raises; capital non-fungibility.

**Arbitrage** (`test_arbitrage_engine.py`): marginal sizing stops at
payout; profitable path yields TRADE; reproducibility (identical inputs
→ identical outputs); search requires APPROVED certificates; SAME_VENUE
pairs never evaluated; graph candidates; full matching pipeline with a
mock LLM (approve) and without one (REVIEW only).

## Integration testing done live (no credentials)

- `--status`, `--mode collect --cycles 1` (500 real Kalshi markets, 40
  books recorded), `--mode paper --cycles 1`, `--mode historical`
  (fails safely with no data), kill-switch blocking live startup, and
  the paper+LiveExecutionEngine startup refusal.
