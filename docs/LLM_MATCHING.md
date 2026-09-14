# LLM MATCHING

Role: **semantic contract equivalence only** (§89). The LLM never
authorizes trades, never sees execution APIs, and cannot overrule
deterministic filters.

## Pipeline (§18–§20, §87)

```
ALL MARKETS
  → candidate blocking (§96: settlement 24h buckets ±1, category,
    threshold + operator + units, numeric title tokens, plural-folded
    content words; connected components)                  [cheap, recall-first]
  → deterministic filter (outcome type, payout, settlement ±6h,
    threshold/operator/units, resolution presence, early-resolution
    symmetry)                                            [REJECT is final]
  → LLM comparison (structured JSON payloads, not raw titles)
  → schema validation of output (§88)
  → rules validator (deterministic wins; UNKNOWN independence → REVIEW)
  → match certificate (APPROVED / REJECTED / REVIEW), persisted
```

Blocking only *proposes* cross-venue candidate pairs; it never decides
equivalence and can never promote a pair past the deterministic filter.
Exact-text grouping (the pre-2026-09-14 behaviour) was defect D6: two
venues never phrase the same event identically, so no cross-venue pair
was ever even considered.

## Failure protection (§88)

`validate_llm_output` rejects: malformed JSON, missing fields, wrong
types, confidence outside [0,1], and contradictions (match=true with
any identity field false). Any rejection == no match. LLM outages ==
no match. No LLM configured == no match (v1 default) — pairs end in
REVIEW for human handling, never auto-APPROVED.

## Caching & versioning (§82, §87)

Results cached by (venue:contract_id:contract_version) pair key;
re-run only when a contract's rules change. Certificates record
matcher_version, llm_model_version, llm_prompt_version.

## Prompt contract

The prompt (in `prediction_arb/matching/llm_matcher.py`, PROMPT_VERSION
`match-prompt-1.0.0`) instructs: when uncertain return false; false
negatives preferred; no inference beyond provided fields; JSON only.
