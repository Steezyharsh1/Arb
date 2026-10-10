# MODE SEPARATION

Three operating modes plus a data-collection mode. Switching is one
flag (`--mode X`), but isolation is architectural (§43–§60).

## Enforcement inventory

| Mechanism | Where | What it stops |
|---|---|---|
| Mode-config consistency validation | `prediction_arb/core/config.py` | paper.yaml + LiveExecutionEngine → startup refused (verified live) |
| `allow_live_orders` legality | `prediction_arb/core/config.py` | true outside live → refused |
| Env-file separation | `prediction_arb/core/config.py::load_mode_env` | `*LIVE*` keys in non-live env → refused |
| Per-mode DB file + internal mode tag | `prediction_arb/database/repository.py` | opening live DB from paper → RuntimeError |
| Simulation engines hold no adapters | `prediction_arb/execution/simulated.py` | no code path from paper/historical to a venue |
| Adapter-level order gate | `prediction_arb/adapters/kalshi.py` | `place_order` raises unless constructed for live |
| Firewall-only live path | `prediction_arb/execution/live.py` | unguarded `submit_order` raises FirewallBlocked |
| Confirmation phrase object | `LiveConfirmationToken` | live engine cannot be built without the typed phrase |
| No fallback logic exists | everywhere | live failure never becomes paper, and vice versa (§55) |
| Per-mode logs/reports/data dirs | `prediction_arb/core/logging.py`, `modes/*/reports.py` | cross-mode overwrites impossible |
| `mode` column on every DB row | `prediction_arb/database/models.py` | attribution always unambiguous |
| Mode-derived fee-freshness policy (§98) | `prediction_arb/fees/freshness.py`, `modes/shared.py`, `arbitrage/two_leg.py`, `execution/simulated.py` | live/paper refuse a fee schedule that is stale, undated or unverified (`FEE_DATA_INCOMPLETE`); historical still costs it and is labelled `RESEARCH USE ONLY`; collect calculates no fee; an unknown mode fails closed |
| Derived readiness verdicts (§99) | `prediction_arb/monitoring/readiness.py`, `main.py::cmd_readiness` | a mode can only be reported READY when every applicable criterion passes on observed state; the report creates nothing and enables nothing |

## Directory separation

```
data/{historical,paper,live}/     — per-mode SQLite databases
logs/{historical,paper,live}/     — per-mode JSONL logs
reports/{historical,paper,live}/  — per-mode reports
```

## Startup banners

Every start prints the mode banner and:
```
ACTIVE MODE: PAPER
EXECUTION ENGINE: PaperExecutionEngine
DATABASE: prediction_arb_paper
REAL ORDERS: DISABLED
```

## Workflow (§58)

HISTORICAL (validate logic) → PAPER (validate real-time behavior) →
LIVE. `python -m prediction_arb.main --readiness` reports advisory readiness; the
final decision is the user's, and live always requires the typed
confirmation phrase.

The report is **derived, per mode** (§99): one block each for COLLECT,
HISTORICAL, PAPER and LIVE, every criterion rendered
`PASS / WARN / BLOCKED  label = observed value — reason`, and a mode is ready
only when none of its applicable criteria is BLOCKED. Nothing is hardcoded —
the same code path reports LIVE ready when the observed state actually
qualifies (proved by `tests/test_readiness.py`) — and producing the report has
no side effects: it opens the recorded store read-only if it exists, never
creates `data/`, `logs/` or `reports/`, and calls no venue.

What each mode is held to, and why the bar differs:

| Mode | Criteria it must pass | Fee-freshness policy (§98) |
|---|---|---|
| COLLECT (3 criteria) | verified data adapters (it records only venues whose `APIStatus` is VERIFIED), kill switch disengaged | NOT_APPLICABLE — it records contracts, matches and order books and never calculates a fee |
| HISTORICAL (6) | recorded-data volume (a missing store blocks; it is reported, never created), kill switch disengaged | RESEARCH — a stale schedule still prices the replay, and the run, its summary and its report are labelled `RESEARCH USE ONLY: fee schedule(s) older than …`; fee completeness, settlement timing and the matcher are WARN rather than BLOCKED, because replay measures logic instead of executing |
| PAPER (9) | verified adapters, a provenance-INDEPENDENT pair, a configured matcher, complete fee schedules, evidenced settlement timing, book-staleness gates, kill switch disengaged; eligibility is WARN here (it only blocks live) | MANDATORY — paper results are the evidence live readiness is built on, so they may not be costed on a schedule nobody has re-verified |
| LIVE (12) | everything PAPER is held to (with eligibility BLOCKING), plus ≥2 live-capable independent venues (adapter `APIStatus.VERIFIED` **and** verified order-type support), recorded paper/replay volume, resolved jurisdiction eligibility, every configured hard risk limit, and the existence of `.env.live` (existence only — §48 forbids reading live credentials outside live mode) | MANDATORY |

Rejected candidates are explained where the operator is looking (§100):
`monitoring/dashboard.py::print_block_reasons` prints the distinct
`block_reasons` with their counts, most common first, after the opportunity
table in both paper and historical runs, and prints nothing when nothing was
blocked.
