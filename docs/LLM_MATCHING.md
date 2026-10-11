# LLM MATCHING

Role: **semantic contract equivalence only** (§89). The LLM never
authorizes trades, never sees execution APIs, and cannot overrule
deterministic filters.

## Pipeline (§18–§20, §87)

```
ALL MARKETS
  → candidate proposal (§96: cross-venue, settlement within ±6h, shared
    threshold / numeric-token / content-word key, non-dense at that
    settlement moment; pairwise, not transitive)          [cheap, recall-first]
  → deterministic filter (outcome type, payout, settlement ±6h,
    threshold/operator/units, resolution presence, early-resolution
    symmetry)                                            [REJECT is final]
  → LLM comparison (structured JSON payloads, not raw titles)
  → schema validation of output (§88)
  → rules validator (deterministic wins; UNKNOWN independence → REVIEW)
  → match certificate (APPROVED / REJECTED / REVIEW), persisted
```

A deterministic check can also be **unverified** (D25, 2026-10-11): one leg
declares a comparison operator or a unit and the other declares none, or a
venue token has no documented translation. That is not a mismatch and not a
pass. Such a pair is recorded as REVIEW with the field named, is **never**
APPROVED whatever the LLM says, and is deliberately not sent to the LLM at
all — no verdict could approve it, so it must not consume the bounded
per-cycle comparison budget. The certificate's semantic-stage entry records
`NOT_CONSULTED` explicitly rather than implying a verdict existed, and the
counts are reported separately from rejections
(`pairs.deterministic_unverified`).

Candidate proposal only *proposes* cross-venue pairs; it never decides
equivalence and can never promote a pair past the deterministic filter.
Measured recall and lost matches: `docs/CANDIDATE_RETRIEVAL.md`.
Exact-text grouping (the pre-2026-09-14 behaviour) was defect D6: two
venues never phrase the same event identically, so no cross-venue pair
was ever even considered.

## Failure protection (§88)

`validate_llm_output` rejects: malformed JSON, missing fields, wrong
types, confidence outside [0,1], and contradictions (match=true with
any identity field false). Any rejection == no match. LLM outages ==
no match. No LLM configured == no match (v1 default) — pairs end in
REVIEW for human handling, never auto-APPROVED.

## Caching & versioning (§82, §87, Arb.9)

Results cached by (venue:contract_id:contract_version, matcher_version,
prompt_version, model_version) — sensitive to contract versions and matching
implementation versions for correctness. A version change invalidates the
cache and forces re-evaluation. Certificates record matcher_version,
llm_model_version, llm_prompt_version and, via match_id, contract versions
and model version; a version change creates a new match_id → new row,
preserving the previous version's row for auditability.

## Deterministic prioritization & deduplication (Arb.9)

`run_matching` no longer processes pairs in discovery order. After the
deterministic filter, surviving pairs are scored by defensible pre-LLM
evidence:

- venue independence (INDEPENDENT first, SAME_VENUE last — SAME_VENUE can
  never be APPROVED, §33, so it should not consume budget first);
- shared blocking keys (thr=20, num=5, tok=1);
- settlement-time proximity (smaller delta first).

Ordering is fully deterministic: `(-priority, delta_seconds, sorted_contract_a,
sorted_contract_b, version_a, version_b)`. No gross edge or unverified LLM
output is used for ordering, and deterministic REJECT remains final. Pairs
with an unverifiable deterministic field (D25) take no LLM budget at all;
they are recorded as REVIEW in the same priority order.

Persistence deduplication: `Repository.save_match_certificate` skips writes
when all meaningful fields (status, score, independence, versions) are
identical to the existing row, preventing unbounded duplicate REVIEW records
on every scan cycle. Meaningful changes (verdict, model/prompt/matcher version)
update the row; version changes create a new row via new match_id. Full
verdict-change history requires the audit-log migration documented in
`docs/MATCHING_DEDUP_MIGRATION.md` — the smallest viable migration if full
history is later required.

## Client wiring & budget (§97, added 2026-09-14)

`matching/llm_client.py` builds the matcher from the mode env file —
`LLM_API_BASE` / `LLM_API_KEY` / `LLM_MODEL` (any OpenAI-compatible
`/chat/completions` endpoint; `LLM_TIMEOUT_SECONDS` optional). It is
transport only: verdict logic, schema validation and caching stay here.
No configuration ⇒ conservative no-LLM default. Partial configuration
refuses startup. A per-cycle comparison budget
(`llm.max_comparisons_per_cycle`, default 200, reset by `begin_cycle()`
once per `run_matching` call) bounds new LLM calls per scan cycle; pairs
beyond the budget fail closed to REVIEW and are deliberately not cached,
so a later cycle can still consider them. Cache hits never consume
budget.

## Prompt contract

The prompt (in `prediction_arb/matching/llm_matcher.py`, PROMPT_VERSION
`match-prompt-1.0.0`) instructs: when uncertain return false; false
negatives preferred; no inference beyond provided fields; JSON only.
