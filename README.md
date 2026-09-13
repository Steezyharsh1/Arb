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

> **⚠ This research is dated 2026-08-30 and is being re-verified.** IBKR now
> fronts three exchanges (Kalshi, CME Group, ForecastEx) through one account and
> one API, and several new CFTC-regulated venues have launched since. Refreshing
> this table is **Phase 1** — see `docs/ROADMAP.md`. Do not treat the "only one
> independent pair" conclusion as current without checking that document.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate   # Python 3.11+
pip install -e ".[dev]"                              # package + test tooling
pytest                                               # 107 tests, all passing

# safest first step — record real Kalshi market data (no credentials needed)
python -m prediction_arb.main --mode collect

# paper trading (default mode): real data, simulated execution
python -m prediction_arb.main --mode paper

# replay recorded data
python -m prediction_arb.main --mode historical --date 2026-08-30
python -m prediction_arb.main --mode historical --start 2026-08-01 --end 2026-08-30

# venue/provenance status + independence matrix
python -m prediction_arb.main --status

# readiness for live (advisory)
python -m prediction_arb.main --readiness

# emergency stop (persists across restarts until --unkill)
python -m prediction_arb.main --kill
python -m prediction_arb.main --unkill
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
  test cases (107 tests total: `pytest`, or `python -m pytest tests/`).

## What is stubbed (honest NotImplementedError, clean interfaces)
- ForecastEx adapter (needs IBKR account), Webull (SAME_VENUE — no arb
  value), Moomoo (no event-contract API + SAME_VENUE).
- Multi-leg / logical arbitrage, graph cycle search, web dashboard,
  email/webhook alerts, full backtest metrics (§3 v1 scope).

## Repository layout

```
prediction_arb/          the importable package — CODE ONLY
    core/paths.py        resolves config/, data/, logs/, reports/ (see §49)
    adapters/ arbitrage/ compliance/ database/ execution/ fees/ matching/
    modes/ monitoring/ normalization/ orderbooks/ provenance/ risk/
    main.py              CLI entry point (`python -m prediction_arb.main`)
config/                  YAML: settings, venues, fees, risk limits, per-mode
data/<mode>/             per-mode SQLite databases + KILL_SWITCH   (git-ignored)
logs/<mode>/             per-mode JSONL audit logs                 (git-ignored)
reports/<mode>/          generated reports                        (git-ignored)
docs/                    the documentation set listed below
scripts/                 operator utilities (IBKR gateway session check)
tests/                   107 tests: units, safety, failure modes, spec §75
pyproject.toml           packaging, console script, pytest config
```

Runtime artifacts (`data/`, `logs/`, `reports/`) are created on demand and are
never committed; `.env.*` files hold credentials and are never committed either
(`.env.example` is the template).

## Documentation

**Start here:** `docs/ROADMAP.md` (phase map, verified current state, defect
register D1–D12, human-action checklist) and `docs/SPEC.md` (the reconstructed
specification — every `§N` cited in the code resolves there).

`docs/`: ROADMAP, SPEC, AGENT_KICKOFF, VENUE_RESEARCH, ARCHITECTURE, API_SETUP,
COMPLIANCE, TRADING_LOGIC, LLM_MATCHING, RISK_MANAGEMENT, TESTING,
MODE_SEPARATION, CRASH_RECOVERY.

## Safety posture
- No real order can leave any non-live mode: simulated engines hold no
  adapter references; the Kalshi adapter refuses order placement unless
  constructed for live mode; the firewall re-verifies 15 conditions per
  order; UNKNOWN never means yes.
- Nothing here is financial or legal advice. Verify venue terms, fees,
  and Utah eligibility yourself before funding live trading.
