# prediction_arb — Multi-Venue Prediction-Market Arbitrage Engine (v1)

A conservative, safety-first system for detecting **guaranteed (TYPE A)**
two-leg arbitrage across genuinely independent prediction-market venues,
with hard mode isolation (historical / paper / live), a live-order
firewall, full audit logging, and honest handling of everything that is
UNKNOWN.

## THE HEADLINE RESEARCH RESULT (read this first)

Of the four requested platforms, **three are the same exchange**:

| Frontend | Underlying exchange | Verdict |
|---|---|---|
| Kalshi | Kalshi DCM (Kalshi Klear DCO) | the venue itself |
| Webull event contracts | **Kalshi** | SAME_VENUE (frontend) |
| Moomoo event contracts | **Kalshi** | SAME_VENUE (frontend) |
| IBKR ForecastTrader | ForecastEx DCM+DCO | **INDEPENDENT** |

The only genuine cross-venue pair is **Kalshi ↔ ForecastEx**, and
ForecastEx access requires an IBKR account (adapter is a documented,
structured stub until one exists). Full evidence:
`docs/VENUE_RESEARCH.md`.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# safest first step — record real Kalshi market data (no credentials needed)
python main.py --mode collect

# paper trading (default mode): real data, simulated execution
python main.py --mode paper

# replay recorded data
python main.py --mode historical --date 2026-08-30
python main.py --mode historical --start 2026-08-01 --end 2026-08-30

# venue/provenance status + independence matrix
python main.py --status

# readiness for live (advisory)
python main.py --readiness

# emergency stop (persists across restarts until --unkill)
python main.py --kill
python main.py --unkill
```

Live mode (`--mode live`) requires typing `I UNDERSTAND LIVE TRADING`
interactively; no flag bypasses it. With only one live-capable venue,
live mode is monitor-only by construction.

## What works today (VERIFIED)
- Kalshi REST market data + order books (live-tested, no credentials
  needed for market data; `orderbook_fp` dollar format handled).
- Full pipeline in paper/collect: discovery → normalization →
  deterministic matching → (LLM stage, conservative no-match without a
  configured client) → certificates → order-book walking → fee/slippage/
  settlement-costed two-leg arbitrage → risk checks → simulated execution.
- Mode isolation, kill switch, crash-recovery intents, all 10 spec §75
  test cases (84 tests total: `python -m pytest tests/`).

## What is stubbed (honest NotImplementedError, clean interfaces)
- ForecastEx adapter (needs IBKR account), Webull (SAME_VENUE — no arb
  value), Moomoo (no event-contract API + SAME_VENUE).
- Multi-leg / logical arbitrage, graph cycle search, web dashboard,
  email/webhook alerts, full backtest metrics (§3 v1 scope).

## Documentation
`docs/`: VENUE_RESEARCH, ARCHITECTURE, API_SETUP, COMPLIANCE,
TRADING_LOGIC, LLM_MATCHING, RISK_MANAGEMENT, TESTING, MODE_SEPARATION,
CRASH_RECOVERY.

## Safety posture
- No real order can leave any non-live mode: simulated engines hold no
  adapter references; the Kalshi adapter refuses order placement unless
  constructed for live mode; the firewall re-verifies 15 conditions per
  order; UNKNOWN never means yes.
- Nothing here is financial or legal advice. Verify venue terms, fees,
  and Utah eligibility yourself before funding live trading.
