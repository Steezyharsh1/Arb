# AGENT HANDOFF — prediction_arb

**Read this file first.** It is the concise session handoff; `docs/ROADMAP.md`
is the authoritative prioritized task list and defect register. Subject-specific
evidence and definitions remain in the linked documents below.

- **Snapshot date:** 2026-10-11 (UTC). Arb.18 cross-venue threshold-operator and
  units normalization (D25, matcher side; D40 opened for the adapter-side
  fields gap) on Python 3.11.2 / pytest 9.1.1 (prior sessions: Arb.17 on
  2026-10-11, consolidation on 2026-10-10).
- **Repository:** `Steezyharsh1/Arb`, package `prediction_arb`.
- **Fixed session branch:** `arena/bdb9670b-arb` (Arb.18).
- **Base:** `70068e1c2e8b81c0808d7b5309781c365d57a9e2` (`main`, merge of PR #22
  / Arb.17; CI run `38110139461` on `main` passed).
- **Arb.18 change summary:** the deterministic matcher no longer compares raw
  venue operator tokens. `prediction_arb/normalization/thresholds.py` now owns
  the documented venue→canonical operator vocabulary and a three-state read;
  `adapters/kalshi.py` emits canonical operators (`greater`→`>`) while keeping
  the venue's raw token in `resolution_method`/`raw`; the matcher records a
  declared-vs-unknown operator or unit as **unverified** (not a mismatch, not a
  pass); `matching/rules_validator.py` can never APPROVE such a pair;
  `matching/match_certificates.py` records and renders the unverifiable field;
  `modes/shared.py` records those pairs as REVIEW and deliberately does not
  spend the bounded LLM budget on them (explicit `NOT_CONSULTED` result);
  `monitoring/pipeline.py` / `pipeline_report.py` report unverified counts
  separately from rejections; `MATCHER_VERSION` is `det-1.1.0`. Adds no venue,
  strategy family, or live-order capability; live stays monitor-only.
- **This session's commit hash is reported in the session report**, not in this
  file: the handoff is committed with the change, so it cannot claim a hash
  that does not exist yet (the next session's handoff update should record it).

---

## 1. Mission and current state in one minute

Build a useful research system for **guaranteed TYPE A two-leg** opportunities
across genuinely independent venues. Preserve fail-closed costs, evidence
provenance, mode isolation, and the rule that UNKNOWN is never permission.

The core code and offline tests are substantial; **a real, costed two-venue
result has not been demonstrated**. In this checkout, Kalshi is admitted for
read-only collection only because of historical public data from 2026-08-30.
Polymarket US has no observed live payload; ForecastEx is a structured stub.
All three enabled venues have incomplete trade and settlement fee schedules;
Polymarket US and ForecastEx capital-release timing is UNKNOWN. No LLM is
configured, so ambiguous pairs remain REVIEW. No genuine two-venue historical
dataset, account fill/settlement evidence, or measured strategy performance is
present.

Arb.18 removed one non-external blocker: a pair whose two legs could not be
compared on an operator or unit is no longer deleted as if it were a mismatch.
It is recorded as REVIEW (never APPROVED) with the field named, because
"cannot verify" is not evidence of disagreement — while "declared and
different" is still a final reject. That does not move the two external
blockers below: no current two-venue feed, and 0/3 end-to-end cost-evaluable
pairs.

`--readiness` reporting **COLLECT: READY** is a derived admission/configuration
verdict for one evidence-qualified adapter. It is **not** a claim that an
endpoint is currently reachable or that a collection cycle succeeded. The
inherited record (2026-10-10, not re-attempted by this session) is that a
one-cycle collection command exited 0 while recording no books, after public
requests failed before an HTTP response. This session ran only the offline,
side-effect-free CLI probes below.

---

## 2. Verified state (re-run 2026-10-11, Arb.18)

| Check | Observed result |
|---|---|
| Full offline suite | `.venv/bin/pytest -o addopts=''`: **860 passed in 46.79 s** on Python 3.11.2 / pytest 9.1.1 (2026-10-11 Arb.18 run; 790 from Arb.17 plus 70 in the new `tests/test_threshold_operator_normalization.py`; 0 xfailed). Mocks/local fixtures only — not venue connectivity. |
| D25 reproduction (offline, real adapter code paths) | Kalshi payload `strike_type: "greater"` now stores `>` (raw `greater` kept in `resolution_method`/`raw`); Polymarket US payload still stores `None`. The pre-fix raw-token comparison `"greater" != None` is `True` (→ REJECT). After the fix `deterministic_match` reports `passed=True`, `checks["threshold_operator"]` absent, `unverified=["threshold_operator"]`, `confidence=0.875`, `MATCHER_VERSION=det-1.1.0`. |
| Mutation checks | Seven reverts of the Arb.18 production change were each caught by the targeted files (failure counts 4, 4, 3, 6, 1, 11, 1) and every mutated file was restored (sha256-verified). Details in `docs/ROADMAP.md` D25. |
| `python -m prediction_arb.main --status` | Exit 0. Kalshi `MARKET_DATA_OBSERVED` (historical observation only); Polymarket US `API_DOCUMENTED_UNTESTED`; ForecastEx `API_DOCUMENTED_UNTESTED` (stub). No adapter is fully verified. All three enabled unordered pairs are `INDEPENDENT`; that says nothing about books or costs. |
| `python -m prediction_arb.main --readiness` | Exit 0, advisory and side-effect-free (no runtime tree created). On this checkout: `COLLECT: READY` (one of three adapters with market-data evidence), `HISTORICAL: NOT READY — 1 blocker`, `PAPER: NOT READY — 5 blockers`, `LIVE: NOT READY — 8 blockers`. 0 fully verified live-capable venues. |
| `python -m prediction_arb.main --pipeline-report` | Exit 0; `UNAVAILABLE: no paper store … nothing has been recorded yet.` Missing observations are not rendered as zero. Generated runtime artifacts were removed after the probes. |
| GitHub Actions | `gh run list`: the `main` push run for the base merge (Arb.17, run `38110139461`) succeeded; the Arb.17 pull-request run `38110108523` succeeded. No CI result exists for the Arb.18 branch yet. |

### What the code actually provides

- **Collection:** read-only public discovery and validated usable-book
  persistence for adapters whose market-data evidence admits collection. It has
  no execution engine. An exit code alone is not evidence of collected data.
  There is still no persisted feed/session coverage or empty/halt/gap ledger
  (`docs/HISTORICAL_EVALUATION.md` §6) — that is the recommended next agent
  task.
- **Paper:** an implemented discovery → matching → fresh-book scan → walked
  costs → paper-risk coordinator → simulated fills path. Offline synthetic
  end-to-end tests prove composition only. Production fee/timing gates and
  unavailable current feeds prevent a real costed two-venue paper result.
- **Matching (Arb.18 / D25):** three-state deterministic semantics. Checks:
  outcome type, payout/currency, settlement ±6 h, threshold value (still a hard
  mismatch when the two legs declare different values or only one declares
  one), operator and units (canonical-vs-canonical equal → pass, different →
  FINAL reject, anything involving an unknown/unmapped token → **unverified**),
  resolution-source presence, early-resolution symmetry. An unverified pair
  reaches the certificate as REVIEW with the field named, never consumes the
  per-cycle LLM comparison budget (`NOT_CONSULTED`), and can never be APPROVED.
  Unverified counts appear in the paper cycle output and in
  `--pipeline-report` (`pairs.deterministic_unverified`).
- **Fees/readiness:** LIVE/PAPER freshness is mandatory; HISTORICAL freshness
  is labelled research; COLLECT does not calculate fees. Freshness is not
  completeness. All active production schedules remain `INCOMPLETE`, and a
  missing mandatory cost leaves net profit unavailable rather than zero.
- **Historical:** CLI and library share exact-availability/as-of evaluation and
  the explicitly conditional `HistoricalFillSimulator`. No genuine dataset or
  calibrated execution evidence is present.
- **Live:** `LiveEngine.run()` is monitor-only. `LiveExecutionEngine` and the
  15-check firewall exist as guarded code/test components, but no production
  `FirewallContext` is constructed and no application order loop calls the
  submit method. No real order may be added or tested in this task.
- **Execution safety (Arb.16 / Arb.17):** D30–D37 are fixed in code and covered
  by `tests/test_execution_fault_matrix.py`. D38 (live fill accounting), D39
  (paper restart resets in-memory risk state), D24/D26 (live
  coordinator/`FirewallContext` and emergency wiring) remain open. Read
  `docs/EXECUTION_SAFETY_ASSESSMENT.md` before any live work.
- **Telemetry:** bounded persisted pipeline counters, simulated attempt records
  and read-only `--pipeline-report` (Arb.14), now including unverified
  deterministic fields. D29 remains open: the same economic pair can receive
  new opportunity UUIDs and be re-simulated across paper cycles without
  pair-level position deduplication (found by Arb.14 telemetry).

Detailed definitions: `docs/TRADING_LOGIC.md`, `docs/HISTORICAL_EVALUATION.md`,
`docs/RISK_MANAGEMENT.md`, `docs/MODE_SEPARATION.md`, and `docs/SPEC.md`.

---

## 3. Three highest-impact blockers and dependencies

| Priority | Blocker | Why it matters | Dependencies / who can unblock it |
|---|---|---|---|
| 1 | **No current usable two-venue feed.** Only Kalshi has historical `MARKET_DATA_OBSERVED` evidence; Polymarket US has no observed payload. Public probes from this sandbox have consistently returned TLS EOF before an HTTP response. | Without two admitted feeds there is no real pair to match or cost. The sequential 10-second collector is also not a tick-accurate paired feed and persists no halt/gap/session ledger. | **Operator/network:** run the bounded public reads (`scripts/kalshi_smoke.py`, `scripts/polymarket_us_smoke.py`) from an authorized reachable environment and review sanitized payloads. No credentials are required for the documented public reads. **Agent:** can implement/test the paired-capture and gap/session ledger offline so the pilot's capture is causal evidence. |
| 2 | **Mandatory costs and capital-release timing are unresolved.** 0/3 enabled pairs are end-to-end cost-evaluable; all three venues' production trade and settlement components are `INCOMPLETE`. Polymarket US and ForecastEx timing are `UNKNOWN`. | Even with books and a match, the production evaluator must BLOCK and cannot report net profit. Documented reference rates and synthetic test schedules are not account-reconciled execution fees. | **Operator/authoritative evidence:** exact route/account context, existing redacted multi-fill fee/balance reports, settlement-fee treatment, and resolution-to-cash evidence for the selected pair. Do not create a live order for evidence. **Agent:** integrate schedules/timing and add regressions after evidence arrives (`docs/FEE_READINESS.md`, `docs/TRADING_LOGIC.md`). |
| 3 | **No reliable real approved-pair paper record yet.** No LLM is configured; ambiguous pairs remain REVIEW. D25's matcher-side false negative is fixed (Arb.18); the remaining fields work is D40 (Polymarket US documents typed `assetPriceTerms` the adapter does not read; Kalshi `cap_strike` is unmodelled). D29 permits repeat simulation of the same economic pair. | A real feed alone does not produce an approved, economically interpretable paper result. Broad candidate-recall changes would add workload before current feed and cost blockers are cleared. | Depends on blocker 1 for actual contract payloads, blocker 2 for costed decisions, an **owner-supplied local LLM key** (H7) for semantic approval, an observed payload plus an inclusivity rule before any D40 adapter change, and an owner choice of pair-level dedupe/cooldown semantics before D29 is closed. No key belongs in chat or Git. |

Other open defects D21–D24, D26, D38–D40 are recorded in `docs/ROADMAP.md` and
`docs/RISK_MANAGEMENT.md`. D39 (paper restart clears in-memory risk state) is
the smallest independent code-only safety candidate if the capture ledger is
judged too broad for one session.

---

## 4. Autonomous work vs operator-gated work

**Safe for an agent without account access:** maintain offline regression tests;
implement the bounded paired-capture / halt-gap-session ledger for collect mode
against the existing adapter interfaces; reconcile documentation, CLI wording,
and collection telemetry; persist risk state across restarts (D39, with D21's
daily ledger); implement pair identity/deduplication only after the owner
chooses the semantics. Do not promote any venue, fee, settlement, legal, or
live-readiness claim based on mocks.

**Requires an operator, reachable network, credentials, or authoritative
external/human evidence:** public venue reads from a reachable network;
sustained collection; an LLM key; account-specific fee/fill/settlement
evidence; Utah and account eligibility/legal review; the data-retention
decision; Polymarket US `assetPriceTerms` inclusivity evidence (D40); and any
owner choice on candidate-recall or paper pair-dedup policy. Keep credentials
and unredacted statements local. Never ask the owner to paste a secret into
chat.

---

## 5. Recent meaningful commits (verified from Git history)

Newest first; merge commits are shown only where useful for locating the
consolidated change:

- `70068e1` — merge PR #22 (`arena/c09068ab-arb`), the Arb.17 merge and the base
  for Arb.18. CI run `38110139461` on `main` passed.
- `b67b1d4` — **Arb.16: execution safety assessment and single-shot order POST (D30)**;
  added `docs/EXECUTION_SAFETY_ASSESSMENT.md` and `tests/test_execution_fault_matrix.py`.
- `d7ec12d` — merge PR #20 (`10b4c42`, Arb.15 handoff/roadmap consolidation).
- `43c683b` — merge PR #19 (`514c088`).
- `514c088` — **Arb.14: persisted pipeline observability and read-only report**;
  schema 5 / migration 005, closed-vocabulary counters, simulated attempt
  records, and the D29 finding.
- `a61d612` — **Arb.13: causal, evidence-scoped historical evaluation**;
  exact-time/as-of replay, schema 4 snapshot evidence, conditional execution
  model, report schema 2.
- `4f15d68` — **Arb.12: synthetic two-leg paper-path validation**; offline
  candidate-to-simulator composition and refusal/failure cases only.
- `cdb62ff` — **D27: separate adapter evidence claims and gate collection**.

Arb.17's own merge commit is the current HEAD; its change record (D31–D37) is
in `docs/ROADMAP.md` and `docs/EXECUTION_SAFETY_ASSESSMENT.md`. This checkout is
shallow: only `70068e1` and the Arb.18 work is local history, so older hashes
above are the previously recorded values.

---

## 6. Safety invariants and deferred scope

1. No real order or live-order enablement in this task. Live stays monitor-only;
   do not construct or wire a production `FirewallContext`.
2. UNKNOWN never means yes for independence, eligibility, fees, settlement
   lockup, book freshness, or verification age — and, since Arb.18, an
   unverifiable deterministic field is never a pass either: it is REVIEW-only
   and can never be APPROVED. It is also never a mismatch: a declared-vs-
   declared difference is the only operator/units disagreement that rejects.
3. Never fabricate data, terms, fills, fees, settlement timing, or historical
   context. Historical input remains read-only and cannot fall back to live.
4. A deterministic REJECT is final; an LLM may narrow candidate matches only.
5. No scraping, geo-bypass, rate-limit circumvention, or terms-of-service
   violations. Never commit `.env*`, credentials, databases, reports, or logs.
6. Do not add strategy families, venues, live execution, or broad architecture
   changes during consolidation. TYPE A two-leg research is the scope.

---

## 7. One recommended next action

**Implement the bounded paired-capture and gap/session ledger for collect mode**
(agent-only, offline, no venue access): persist per-venue receipt time and
source time, feed/session identity, sequenced coverage, and explicit
empty/halt/gap/disconnect/recovery markers, with regression tests over mocked
adapters — the requirement is already written in
`docs/HISTORICAL_EVALUATION.md` §6 and `docs/ROADMAP.md` row 1. It is the code
half of blocker 1: without it, an operator's 24-hour capture cannot support a
causal historical claim, and both Kalshi and Polymarket US feeds are already
integration-tested offline against mocks, so no external evidence is needed to
build and verify it.

In parallel, the operator-gated half is unchanged and still the highest-value
action overall: **run the selected-pair evidence pilot for Kalshi ↔ Polymarket US
from an authorized, network-reachable environment** — one bounded public
market/book payload per venue with UTC receipt/source time, outcome, contract
terms, tick and minimum quantity, plus the redacted existing fee/fill and
settlement-to-cash records from blocker 2. If a future session must pick a
smaller independent code task instead, **D39 (persist risk state across paper
restarts, with D21's daily-loss ledger)** is the next safety candidate.

Do not start a generic 24-hour capture and call it causal, and do not close
D40 by guessing an operator mapping: its gating input is an observed payload
plus an explicit inclusivity rule.
