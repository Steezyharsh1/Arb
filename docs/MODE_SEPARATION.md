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
