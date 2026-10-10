# Matching deduplication — smallest viable migration (Arb.9)

## Current design

`ContractMatchRow.match_id` is `UNIQUE`. `Repository.save_match_certificate`
upserts by `match_id`: if a row exists it updates `validation_status`,
`match_score`, `certificate_json` (and after Arb.9, also `independence`,
`llm_version`, `llm_prompt_version`, `matcher_version`).

`match_id` after Arb.9 includes:
- sorted `venue:contract_id` for both legs
- both `contract_version`s
- `MATCHER_VERSION`
- `LLM_PROMPT_VERSION`
- `model_version`

Thus a change in contract version or model/prompt/matcher version creates a
new `match_id` → new row, preserving the old row. This already gives
auditability for version changes without a schema migration.

## What is not preserved

If the same `match_id` (same contract versions, same matcher/prompt/model
versions) changes verdict (e.g. LLM returns `REVIEW` then `APPROVED`, or
`REVIEW` then `REJECTED` due to a prompt tweak that does not bump
`LLM_PROMPT_VERSION`), the current upsert **overwrites** the previous row.
The previous verdict is lost.

Full history would require multiple rows per `match_id`.

## Why we do not rewrite the schema in this change

- Removing the unique constraint on `match_id` would allow unbounded duplicate
  REVIEW rows for unchanged candidates — exactly the bloat Arb.9 is meant to
  prevent — unless additional deduplication logic is added.
- Adding version columns to the primary key without an audit log still loses
  the *reason* for a verdict change unless the full certificate JSON is kept
  per version.

The smallest safe migration that preserves auditability **and** prevents
per-cycle bloat is:

### Option A — separate audit log table (recommended, minimal)

Create `contract_match_history`:

```sql
CREATE TABLE contract_match_history (
    id INTEGER PRIMARY KEY,
    match_id VARCHAR(64) NOT NULL,
    previous_status VARCHAR(16),
    new_status VARCHAR(16) NOT NULL,
    previous_score FLOAT,
    new_score FLOAT,
    independence VARCHAR(24) NOT NULL,
    llm_version VARCHAR(64) NOT NULL,
    llm_prompt_version VARCHAR(64) NOT NULL,
    matcher_version VARCHAR(64) NOT NULL,
    certificate_json TEXT NOT NULL,
    changed_at DATETIME NOT NULL,
    change_reason TEXT NOT NULL -- e.g. "verdict_change", "model_version_change"
);
CREATE INDEX ix_history_match_id ON contract_match_history (match_id);
CREATE INDEX ix_history_changed_at ON contract_match_history (changed_at);
```

Then `save_match_certificate`:
1. If no existing row → INSERT into `contract_matches` + INSERT into history with `previous_status=NULL`.
2. If existing and meaningful fields differ → INSERT into history with old→new, then UPDATE `contract_matches`.
3. If unchanged → skip both writes.

This keeps `contract_matches` as latest state (current unique constraint intact)
and `contract_match_history` as append-only audit log. No existing rows are
migrated; history starts from deployment.

### Option B — composite primary key (alternative)

Change `contract_matches` to allow multiple rows per logical pair:

- Add `contract_version_a`, `contract_version_b`, `created_at` to uniqueness,
  or replace `UNIQUE(match_id)` with `UNIQUE(match_id, created_at)`.
- Keep deduplication: skip INSERT if latest row for same `match_id` has same
  status/score/versions.

This is larger because it changes the existing table's uniqueness and requires
backfill of version columns for old rows (which lack them).

## Decision for Arb.9

We implement deduplication in code (skip unchanged writes) and document Option A
as the smallest viable migration if full verdict-change history is later
required. We do NOT implement the migration here to avoid an unreviewed schema
rewrite.

## Cache invalidation

`LLMMatcher._cache_key` now includes `MATCHER_VERSION`, `LLM_PROMPT_VERSION`,
and `model_version` in addition to `venue:contract_id:contract_version`. A
change in any of those invalidates the cache, forcing a re-evaluation. Budget-
exhausted results are deliberately NOT cached, so they remain eligible for
later cycles.

## Deterministic prioritization

`run_matching` now scores surviving deterministic-pass pairs by:
- independence (INDEPENDENT first, SAME_VENUE last)
- shared blocking keys (thr=20, num=5, tok=1)
- settlement-time proximity (smaller delta first)

Ordering is fully deterministic: `(-priority, delta_seconds, sorted_contract_a,
sorted_contract_b, version_a, version_b)`. No gross edge or unverified LLM
output is used for ordering, and deterministic REJECT remains final.
