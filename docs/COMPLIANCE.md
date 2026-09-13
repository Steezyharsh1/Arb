# Compliance posture

Configured jurisdiction: **US-UT (Utah)** in
`config/settings.yaml: compliance.jurisdiction`.

This repository does not infer legality from CFTC registration, nationwide
marketing, or another state's court ruling. Records in
`prediction_arb/compliance/jurisdiction.py` carry status, source, effective date,
and verification date. See `docs/VENUE_RESEARCH.md` §6 for the evidence ledger.

## Current Utah ledger (verified 2026-09-13)

| Venue | Category | Status | Runtime consequence |
|---|---|---|---|
| every exchange and frontend route | sports / proposition bets | **RESTRICTED** | blocked in every mode's eligibility filter; never live |
| every exchange and frontend route | non-sports | **UNKNOWN** | live blocked; paper/historical research may continue under mode rules |

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
category does not become eligible: it falls back to `UNKNOWN`, which still blocks
live use.

## Hard controls

- `RESTRICTED` is never tradable.
- `UNKNOWN` never means yes; live eligibility fails closed.
- Missing provenance, fees, matching evidence, or fresh books independently
  blocks execution.
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
