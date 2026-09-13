# TESTING

```bash
pytest                        # full suite (configured in pyproject.toml)
python -m pytest tests/       # equivalent
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

**Layout & packaging** (`test_paths.py`, Phase 0): project-root discovery
(marker search, `$PREDICTION_ARB_HOME` override, loud failure instead of
guessing), per-mode `data/`/`logs/`/`reports/` separation, global (not
per-mode) kill-switch file, no runtime artifacts inside the importable
package, `pyproject.toml` console script / dependency wiring, `.gitignore`
coverage, and a credentials-free `.env.example`.

**Unit** (`test_units.py`): book walking, staleness, Kalshi
`orderbook_fp` parsing (ask synthesis from NO bids), isolated synthetic
formula math, production fee-context failures, ForecastEx $1.00 pairing and
separate per-side fees, route-qualified provenance, shared-DCO independence,
Utah category/event-text sports restrictions, deterministic matcher rejections,
LLM schema validation, risk limits, settlement lockup, metadata, normalization,
and aliases.

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

## Integration-testing record

On 2026-08-30, a prior run exercised `--mode collect --cycles 1` against the
Kalshi compatibility host (500 markets and 40 non-empty books), paper mode,
historical no-data behavior, kill-switch refusal, and startup isolation.

On 2026-09-13, Phase 1 re-verified Kalshi/Polymarket US/Gemini documentation,
but fresh sandbox HTTP smoke tests ended in a TLS EOF before any HTTP response.
Those fresh network results are **UNAVAILABLE**, not successful live tests.
`--status` and the full suite remain local verification gates.
