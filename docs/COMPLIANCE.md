# COMPLIANCE

User jurisdiction: **US-UT (Utah)**, configured in
`config/settings.yaml: compliance.jurisdiction`. Nothing is hard-coded
as "legal"; every eligibility entry in `prediction_arb/compliance/jurisdiction.py`
carries status, effective date, source, and verification date.

## Current eligibility ledger (verified 2026-08-30)

| Venue | Category | Status | Consequence |
|---|---|---|---|
| kalshi | default | ELIGIBLE | paper + (future) live allowed |
| kalshi | sports | **UNKNOWN** | live BLOCKED; research allowed |
| forecast_ex | default | ELIGIBLE | live gated on adapter activation anyway |
| forecast_ex | sports | **UNKNOWN** | live BLOCKED |
| webull | all | UNKNOWN | stub venue; blocked |
| moomoo | all | UNKNOWN | stub venue; blocked |

Sports detection is category-prefix based (KXMLB, KXNFL, …) and errs
toward classifying as sports (conservative).

## Hard rules encoded

- UNKNOWN eligibility → `BLOCK LIVE TRADING`; paper/research continues
  (§35). Implemented in `prediction_arb/compliance/eligibility.py`.
- `prediction_arb/compliance/restrictions.py` enumerates prohibited actions (no
  scraping, no geo-bypass, no rate-limit circumvention, no ToS
  violations) and provides `assert_endpoint_is_official` used to keep
  adapters on documented hosts.
- The system never initiates deposits/withdrawals; capital rebalancing
  output is explicitly labeled ADVISORY ONLY (§30).

## Maintenance obligations

Re-verify before enabling live trading, and periodically after:
1. Kalshi fee schedule (config/fees.yaml `verified_at`; live mode uses
   strict freshness and refuses stale schedules).
2. Utah treatment of sports event contracts (litigation ongoing in
   multiple states as of research date).
3. Venue terms re: automated trading.
4. Provenance records (broker partnerships change; a new Webull/Moomoo
   venue relationship would need new evidence before reclassification).

**This document is not legal advice.**
