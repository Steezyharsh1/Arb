# Compliance posture

Configured jurisdiction: **US-UT (Utah)** in
`config/settings.yaml: compliance.jurisdiction`.

This repository does not infer legality from CFTC registration, nationwide
marketing, or another state's court ruling. Records in
`prediction_arb/compliance/jurisdiction.py` carry status, source, effective date,
and verification date. See `docs/VENUE_RESEARCH.md` §6 for the evidence ledger.

## Current Utah ledger (verified 2026-09-13)

| Venue | Category | Status | Helper policy / current runtime path |
|---|---|---|---|
| every exchange and frontend route | sports / proposition bets | **RESTRICTED** | `check_contract_eligibility` rejects it; `PaperEngine._discover` applies that check. Historical replay does not call this filter and never places orders |
| every exchange and frontend route | non-sports | **UNKNOWN** | The helper blocks `Mode.LIVE` and permits paper/research modes. `PaperEngine` calls it, but `LiveEngine` is monitor-only: no production eligibility scan or caller to the context-gated live submit method exists, so this is not an end-to-end live gate |

Why sports is restricted:

- Utah's 2026 statute expressly includes proposition bets in its gambling
  definitions. **VERIFIED**, 2026-09-13:
  https://le.utah.gov/xcode/Title76/Chapter9/C76-9-S1401_2026050620260506.pdf
- The 2026-08-04 final judgment in `KalshiEX LLC v. Cox` held that the CEA
  did not preempt Utah enforcement against Kalshi's sports contracts.
  **VERIFIED**, 2026-09-13:
  https://www.courthousenews.com/wp-content/uploads/2026/08/kalshi-utah-shelby-decision.pdf

The Third Circuit reached a pro-preemption result in a New Jersey preliminary-
injunction appeal, while the Ninth Circuit reached the opposite likely-merits
result in a Nevada appeal. Those conflicts do not override the Utah statute and
final Utah judgment for this system. A reported Tenth Circuit denial of an
injunction pending appeal remains **UNVERIFIED** because Phase 1 did not obtain
the primary appellate order. Sources and dates are in the research ledger.

The CFTC's June 2026 Rule 40.11 publication is an NPRM, not an effective final
rule. No final amendment was identified through the evidence cut-off; this is a
negative search result, not a permanent legal conclusion.

## Category detection

The classifier uses venue category/ticker plus event text. Clear league, game,
player-prop, spread, total, and sports terms map to `sports`. An unrecognized
category does not become eligible: it falls back to `UNKNOWN`. The eligibility
helper fails closed for `Mode.LIVE`, but `LiveEngine` is monitor-only: no
production eligibility scan or caller to the context-gated live submit method
exists.

## Hard controls

- `RESTRICTED` is rejected by `check_contract_eligibility`; the current caller
  is `PaperEngine._discover`. Historical replay does not execute.
- `UNKNOWN` never means yes. The helper rejects it for `Mode.LIVE`, but no live
  discovery/scanning/order path currently invokes the helper; this policy is not
  an end-to-end live control.
- In the active paper scan/execution path, missing provenance, fees, matching
  evidence, or fresh books independently blocks execution. Readiness also
  reports live blockers but enables no order route.
- `prediction_arb/compliance/restrictions.py` prohibits scraping, geo-bypass,
  rate-limit circumvention, and ToS violations and restricts adapters to official
  endpoints.
- The system never initiates deposits or withdrawals; rebalance output is
  advisory only.

## Before any live enablement

1. Obtain venue- and category-specific Utah eligibility review/confirmation.
2. Re-verify all current litigation, statutes, CFTC rules, and venue restrictions.
3. Reconcile account/contract fee context and mark the exact route schedule
   complete in `config/fees.yaml`.
4. Verify automation terms, account permissions, underlying DCM, matching book,
   clearing route, and settlement authority for each contract.
5. Preserve live confirmation, firewall, risk, freshness, and kill-switch gates.

**This is not legal advice.**
