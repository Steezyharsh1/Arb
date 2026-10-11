# AGENT HANDOFF — prediction_arb

**Read this file first.** It is the concise session handoff; `docs/ROADMAP.md`
is the authoritative prioritized task list and defect register. Subject-specific
evidence and definitions remain in the linked documents below.

- **Snapshot date:** 2026-10-10 (UTC), consolidation re-run on Python 3.11.2.
- **Repository:** `Steezyharsh1/Arb`, package `prediction_arb`.
- **Fixed session branch:** `arena/32d39149-arb`.
- **Base:** `43c683b73c8cb04f72ee0fc84af62975acea0bc4` (merge of PR #19).
- The working tree was clean at session start. This work is documentation-only;
  it adds no venue, strategy family, or live-order capability.

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

`--readiness` reporting **COLLECT: READY** is a derived admission/configuration
verdict for one evidence-qualified adapter. It is **not** a claim that an
endpoint is currently reachable or that a collection cycle succeeded. This
session's one-cycle collection command exited 0 but recorded no books after
public requests failed before HTTP response.

---

## 2. Verified state (re-run 2026-10-10)

| Check | Observed result |
|---|---|
| Full offline suite | `.venv/bin/pytest -o addopts=''`: **764 passed in 36.12 s** (the 60 Arb.14 telemetry cases are included); Python 3.11.2 / pytest 9.1.1. The suite uses mocks/local fixtures, not venue connectivity. |
| `python -m prediction_arb.main --status` | Exit 0. Kalshi `MARKET_DATA_OBSERVED` (historical observation only); Polymarket US `API_DOCUMENTED_UNTESTED`; ForecastEx `API_DOCUMENTED_UNTESTED` (stub). No adapter is fully verified. All three enabled unordered pairs are provenance-`INDEPENDENT`; that says nothing about books or costs. The latest dedicated smoke-script claims still show `UNAVAILABLE` (TLS EOF) from the 2026-10-10 morning probes. |
| `python -m prediction_arb.main --readiness` | Exit 0, advisory and side-effect-free. On a fresh runtime tree: `COLLECT: READY` (one of three adapters with market-data evidence), `HISTORICAL: NOT READY` (no recorded store), `PAPER: NOT READY — 5 blocker(s)`, `LIVE: NOT READY — 8 blocker(s)`. There are 0 fully verified live-capable venues. Fee freshness passed 3/3 on this date; fee completeness is still 0/3. |
| `python -m prediction_arb.main --mode collect --cycles 1` | Exit 0, **no current books recorded**. At about 18:04 UTC, Kalshi's two configured REST bases and Polymarket US's public gateway returned TLS EOF before an HTTP response. ForecastEx was skipped as a stub; Polymarket US was subsequently skipped by the collection admission gate. No credentials or orders were used. The CLI catches discovery failures, so exit 0 is not a successful-data assertion. |
| `python -m prediction_arb.main --mode historical --date 2020-01-01` | Exit 0 after that collection attempt. The recorded store opened read-only and contained 0 snapshots: 0 as-of frames, 0 calculated opportunities, no simulated positions; simulated and realized P&L were `n/a`. The report did not fall back to a live feed or fabricate history. |
| `python -m prediction_arb.main --pipeline-report` | Exit 0; `UNAVAILABLE: no scan cycle was recorded` for the day. Missing observations are not rendered as zero. |
| GitHub Actions | `gh pr checks 19`: merged PR #19's `tests + CLI smoke` check passed (run `38071479907`). This is not a CI result for this documentation change. |

The generated empty databases, logs, and reports from these probes were removed;
there is no checked-out recorded dataset. The separate official-source reviews
and dedicated smoke-script evidence in `docs/VENUE_RESEARCH.md` and
`docs/FEE_READINESS.md` are dated evidence inherited from earlier sessions;
they were not re-opened against external venue sites in this consolidation.
The sandbox's current public collection attempt adds no successful venue
payload and does not change any adapter's evidence status.

### What the code actually provides

- **Collection:** read-only public discovery and validated usable-book
  persistence for adapters whose market-data evidence admits collection. It
  has no execution engine. A successful command still needs an observed
  payload; an exit code alone is not evidence of collected data.
- **Paper:** an implemented discovery → matching → fresh-book scan → walked
  costs → paper-risk coordinator → simulated fills path. Offline synthetic
  end-to-end tests prove composition only. Production fee/timing gates and
  unavailable current feeds prevent a real costed two-venue paper result.
- **Fees/readiness:** LIVE/PAPER freshness is mandatory; HISTORICAL freshness
  is labelled research; COLLECT does not calculate fees. Freshness is not
  completeness. All active production schedules remain `INCOMPLETE`, and a
  missing mandatory cost leaves net profit unavailable rather than zero.
- **Historical:** CLI and library share exact-availability/as-of evaluation
  and the explicitly conditional `HistoricalFillSimulator`. It distinguishes
  observed data, calculated edge, assumptions, simulated fills/exposure, and
  realized cash. Legacy context stays missing; no genuine dataset or calibrated
  execution evidence is present.
- **Live:** `LiveEngine.run()` is monitor-only. `LiveExecutionEngine` and the
  15-check firewall exist as guarded code/test components, but no production
  `FirewallContext` is constructed and no application order loop calls the
  submit method. This is **not** an enabled live-execution path. No real order
  may be added or tested in this task.
- **Telemetry:** Arb.14 added bounded persisted pipeline counters, simulated
  attempt records, and the read-only `--pipeline-report`. D29 remains open:
  the same economic pair can receive new opportunity UUIDs and be simulated
  again across paper cycles without pair-level position deduplication.

Detailed definitions: `docs/TRADING_LOGIC.md`, `docs/HISTORICAL_EVALUATION.md`,
`docs/RISK_MANAGEMENT.md`, `docs/MODE_SEPARATION.md`, and `docs/SPEC.md`.

---

## 3. Three highest-impact blockers and dependencies

| Priority | Blocker | Why it matters | Dependencies / who can unblock it |
|---|---|---|---|
| 1 | **No current usable two-venue feed.** Only Kalshi has historical `MARKET_DATA_OBSERVED` evidence; Polymarket US has no observed payload. The one-cycle attempt here received TLS EOF for both public surfaces. | Without two admitted feeds there is no real pair to match or cost. The current broad, sequential collector is not a tick-accurate paired historical feed and does not persist a complete halt/gap/session ledger. | **Operator/network:** run the bounded public read from an authorized reachable environment; credentials are not required for the documented public reads. Review open two-sided books and per-market rules. For historical evaluation, select/build synchronized paired capture with receipt/source times, adequate arrival coverage, and explicit empty/halt/gap records. **Agent:** can test parsers, gates, and capture code once sanitized payloads/specs are available. |
| 2 | **Mandatory costs and capital-release timing are unresolved.** 0/3 enabled pairs are end-to-end cost-evaluable; all three venues' production trade and settlement components are `INCOMPLETE`. Polymarket US and ForecastEx timing are `UNKNOWN`. | Even with books and a match, the production evaluator must BLOCK and cannot report net profit. Documented reference rates and synthetic test schedules are not account-reconciled execution fees. | **Operator/authoritative evidence:** exact route/account context, existing redacted multi-fill fee/balance reports, settlement-fee treatment, and resolution-to-cash evidence for the selected pair; current authoritative terms where available. Do not create a live order for evidence. **Agent:** integrate schedules/timing and add regressions after evidence arrives. See `docs/FEE_READINESS.md` and `docs/TRADING_LOGIC.md`. |
| 3 | **No reliable real approved-pair paper record yet.** No LLM is configured; ambiguous pairs remain REVIEW. D25 records a Kalshi/Polymarket operator/units normalization false-negative; D29 permits repeat simulation of the same economic pair. | A real feed alone does not produce an approved, economically interpretable paper result. Broad candidate-recall changes would add workload before current feed and cost blockers are cleared; the known reference miss is documented and not an adopted policy change. | Depends on blocker 1 for actual contract payloads, blocker 2 for costed decisions, an **owner-supplied local LLM key** (H7) for semantic approval, and an owner choice of pair-level dedupe/cooldown semantics before D29 is closed. The agent can implement deterministic normalization and test dedupe once field evidence and policy are settled; no key belongs in chat or Git. |

Other open defects D21–D24 and D26 are recorded in `docs/ROADMAP.md` and
`docs/RISK_MANAGEMENT.md`. They include incomplete paper daily-loss and
emergency-loss coverage, absent liquidity/latency gates, intentionally unwired
live risk/emergency handling, and disconnected capital/hedging prototypes.
They are not evidence of live protection. D28's code repair is complete, but its
real-data/calibration exit remains blocked by item 1.

---

## 4. Autonomous work vs operator-gated work

**Safe for an agent without account access:** maintain offline regression tests;
inspect and repair deterministic adapter normalization against authoritative
schemas or sanitized payloads; implement a bounded paired-capture/gap ledger;
implement pair identity/deduplication after the owner chooses the semantics;
reconcile documentation, CLI wording, and collection telemetry. Do not promote
any venue, fee, settlement, legal, or live-readiness claim based on mocks.

**Requires an operator, reachable network, credentials, or authoritative
external/human evidence:** public venue reads from a reachable network; sustained
collection; an LLM key; account-specific fee/fill/settlement evidence; Utah and
account eligibility/legal review; the data-retention decision; and any owner
choice on candidate-recall or paper pair-dedup policy. Keep credentials and
unredacted statements local. Never ask the owner to paste a secret into chat.

---

## 5. Recent meaningful commits (verified from Git history)

Newest first; merge commits are shown only where useful for locating the
consolidated change:

- `43c683b` — merge PR #19, the current base.
- `514c088` — **Arb.14: persisted pipeline observability and read-only report**;
  schema 5 / migration 005, closed-vocabulary counters, simulated attempt
  records, and D29 finding. Full suite at that change: 764 tests; ten targeted
  mutations were caught.
- `a61d612` — **Arb.13: causal, evidence-scoped historical evaluation**;
  exact-time/as-of replay, schema 4 snapshot evidence, conditional execution
  model, report schema 2. Real-data/calibration exit remains unmet.
- `4f15d68` — **Arb.12: synthetic two-leg paper-path validation**; offline
  candidate-to-simulator composition and refusal/failure cases only.
- `cdb62ff` — **D27: separate adapter evidence claims and gate collection**;
  historical Kalshi observation is distinct from current verification.

The repository history was shallow in the initial checkout; it was fetched for
this review without changing branches. Historical feature-commit test numbers
are change-specific, not substitutes for the current re-run above.

---

## 6. Safety invariants and deferred scope

1. No real order or live-order enablement in this task. Live stays monitor-only;
   do not construct or wire a production `FirewallContext`.
2. UNKNOWN never means yes for independence, eligibility, fees, settlement
   lockup, book freshness, or verification age.
3. Never fabricate data, terms, fills, fees, settlement timing, or historical
   context. Historical input remains read-only and cannot fall back to live.
4. A deterministic REJECT is final; an LLM may narrow candidate matches only.
5. No scraping, geo-bypass, rate-limit circumvention, or terms-of-service
   violations. Never commit `.env*`, credentials, databases, reports, or logs.
6. Do not add strategy families, venues, live execution, or broad architecture
   changes during consolidation. TYPE A two-leg research is the scope.

---

## 7. One recommended next action

**Run a selected-pair evidence pilot for Kalshi ↔ Polymarket US from an
operator-authorized, network-reachable environment.** First capture and review
one bounded public market/book payload per venue, including UTC receipt time,
source time, outcome, contract terms, tick, and minimum quantity. In the same
pilot, request redacted *existing* account fee/fill and settlement-to-cash
records needed to close the pair's trade-fee, settlement-fee, and capital-release
gaps. Do not create a live order or provide credentials in chat.

This is higher value than broad candidate-recall expansion or a paper run today:
without both current books and complete mandatory costs, those efforts cannot
produce a real costed candidate. Do not start a generic 24-hour historical
capture and call it causal; choose paired cadence/gap evidence before using it
for historical performance claims. The detailed order, owners, and exit criteria
are in `docs/ROADMAP.md` §1.1.
