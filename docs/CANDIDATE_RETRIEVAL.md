# Candidate retrieval (§96) — Arb.8

**Status (2026-10-10):** implemented on branch `arena/732e107e-arb` (base
`91d46df`). Retrieval is a *proposal* stage only. The deterministic matcher
(§18), the LLM (§19, restricted authority) and the rules validator are
unchanged. Full suite: **582 passed in 27.75 s** (`pytest -o addopts=""`).

This document is the authoritative record for the candidate-generation change:
the baseline, the design, the benchmark, the before/after numbers, every lost
match, what is *not* claimed, and the decisions still owed to the owner.
Raw run output is committed under `benchmarks/candidate_retrieval/results/`.

---

## 1. Summary

| Question | Answer |
|---|---|
| Does the inherited giant component reproduce? | **Yes.** 1,000 contracts → 1 connected component of 1,000 under the inherited design. All 499,500 pairs are considered, 333,296 of them cross-venue (same-venue pairs are never matched). |
| Does the inherited "2,111 deterministic-pass pairs" reproduce? | **No.** On the current benchmark generator the same design gives **986** deterministic-pass pairs (1,675 at calendar skew 2.5). The generator that produced 2,111 is not in the repository, so the figure cannot be re-derived. It is reported as a *generator difference*, not as a regression. |
| What changed? | Candidate proposal is now **pairwise and non-transitive** (no union-find over shared buckets). Settlement is a ±6 h pairwise window tied to the matcher's own tolerance. The `settle` and `cat` keys no longer propose pairs. A key that is common among contracts settling at the **same moment** (local density > 20) proposes nothing at that moment. Components are used only to group contracts for the scan. |
| Candidates (1,000 synthetic) | 333,296 → **3,262** (0.98 % of cross-venue pairs). |
| Equivalent ∩ deterministic-pass recall (synthetic, 256 pairs) | 1.0 before → **1.0** after. |
| Equivalent ∩ deterministic-pass recall (reference, 16 pairs) | 1.0 before → **0.9375** after (15/16). The one loss is a genuine paraphrase with no shared key. See §7. |
| Largest component (synthetic 1,000) | 1,000 → **51** (80 at skew 2.5). |
| Runtime, candidate generation only (median of 3) | 0.186 s → **0.040 s** (synthetic 1,000). |
| Peak Python heap, candidate generation | 44.62 MB → **2.28 MB**. |
| No perfect recall claimed | Recall is measured on one synthetic generator (4 seeds × 2 calendar skews) and on 50 hand-labelled reference listings. It is not a guarantee on venue data. See §8. |

## 2. Baseline (before)

The frozen baseline is `benchmarks/candidate_retrieval/legacy_contracts.py`,
a verbatim copy of `prediction_arb/normalization/contracts.py` at `91d46df`
(only the imports are rewritten). It must not be edited.

The inherited design put each contract in a 24-hour UTC settlement bucket
(its own bucket and both neighbours), added category, threshold + operator +
units, numeric title tokens and plural-folded content words as keys, and joined
every contract that shares *any* key with union-find. There was no cap.

Measured on the synthetic 1,000 benchmark (seed 20261010, skew 1.3):

| Metric | Before (`legacy`) |
|---|---|
| All pairs / cross-venue pairs | 499,500 / 333,296 |
| Candidate pairs proposed | **333,296 (100 %)** |
| Components / largest | **1 / 1,000** |
| Deterministic-pass pairs (what the LLM could see) | 986 |
| Labelled equivalent pairs | 301 |
| Equivalent ∩ deterministic-pass (what the pipeline can approve) | 256 |
| Equivalent but deterministic-reject (unreachable by any retrieval) | 45 |
| Runtime, median of 3 (candidate generation) | 0.186 s |
| Peak Python heap | 44.62 MB |
| Budget 200 (production `llm.max_comparisons_per_cycle`): calls / approved / equivalent recall | 200 / 69 / 0.229 |
| No budget: calls / approved / equivalent recall | 986 / 256 / 0.851 |

Recall in the last two rows is over all 301 labelled equivalents, so it is
capped at 256/301 = 0.851 by the deterministic matcher.

## 3. Design (after)

Implemented in `prediction_arb/normalization/contracts.py`:

1. **Keys** (`blocking_keys`, families `thr`, `num`, `tok`):
   - `thr:<op>:<number>:<units>` — exact normalized threshold, operator and units
     (`'10000.00'` and `'1E+4'` agree);
   - `num:<n>` — every canonical numeric token in the title (`$100k` = `$100,000`);
   - `tok:<word>` — every plural-folded, stopword-filtered content word.
   - `cat:` is available only for diagnostics. It is excluded because venue
     taxonomies differ, so cross-venue category equality is rare and generic.
   - **No settlement key.** Settlement is a pairwise window (rule 2 below).
2. **Pairwise proposal** (`candidate_pairs`). A pair `(i, j)` is proposed iff:
   1. the two contracts are on different venues (D15: same-venue pairs are never matched);
   2. both have a settlement time and the times differ by at most
      `RETRIEVAL_SETTLEMENT_WINDOW` = `SETTLEMENT_TOLERANCE` (6 h, imported from
      the deterministic matcher, so the two cannot drift apart);
   3. they share a key, and for that key neither contract is in a *dense*
      moment: at most `MAX_LOCAL_DENSITY` = **20** holders of the key settle within
      the window of it.
3. **Local, not global, density.** A word common across the calendar (`shutdown`,
   `senate`) still identifies its event when its listings settle at different
   times. A word that many contracts share at the same moment (`2026`, `total`,
   `will` on a busy expiry) cannot discriminate there, so it proposes nothing
   at that moment. A global per-key cap was tried and rejected (§10).
4. **Non-transitive.** Two contracts that merely share a neighbour are not
   proposed together.
5. **Components for grouping only.** `groups_from_pairs` / `group_by_event` take
   the connected components of the proposed pairs. The scan uses them as coarse
   groups. A component is never a claim that its members match.
6. **Proposal is not approval.** Every proposed pair still goes through the
   deterministic matcher (REJECT final), the LLM (restricted: may narrow, never
   widen), and the rules validator. Neither blocking nor the retrieval score can
   establish a match.

Rule 2 is a *necessary* condition of the deterministic settlement check
(§18, 6 h). Pairs outside the window are rejected anyway, so removing them
cannot remove a pair the matcher could pass.

Code changes: `contracts.py` (retrieval, `component_indices`,
`groups_from_pairs`, `SETTLEMENT_TOLERANCE` exposed), `deterministic_matcher.py`
(public `SETTLEMENT_TOLERANCE`, rules unchanged), `modes/shared.py`
(`run_matching` iterates `candidate_pairs`).

## 4. Benchmark

Package `benchmarks/candidate_retrieval/`:

| File | Purpose |
|---|---|
| `synthetic.py` | Seeded multi-venue generator (`build_dataset(n, seed, calendar_skew)`). Includes threshold-less families (`shutdown`, `senate`) so the benchmark is not dominated by threshold markets. Labels equivalence classes. |
| `reference_contracts.json` | 50 hand-labelled listings (Kalshi, Polymarket US, ForecastEx). Equivalence was labelled by the author and **not independently reviewed**. Notes record the boundary cases and the known miss. |
| `legacy_contracts.py` | Frozen baseline (§2). Do not edit. |
| `run.py` | Harness. Exhaustive ground truth over every cross-venue pair; scores each generator; times it; runs an end-to-end pass through `modes.shared.run_matching` with an **oracle** matcher (answers from the hidden labels, analysis-only, never shipped) and the production 200-call budget. |
| `fallback_window.py` | Owner-decision evidence for the 60-minute fallback (§9). |
| `results/` | Committed JSON outputs of every number in this document. |

Truth definitions: *equivalent* = same event, same outcome direction, threshold,
operator, units and payout, settlement within venue-timing tolerance. *det-pass*
= the deterministic matcher passes the pair. *eq∩det* = both. The reported
recall figures are, in order of importance:

- **eq∩det recall** — the share of pairs the current pipeline could approve
  that retrieval proposes. This is the figure the brief asks to preserve.
- equivalent recall — over all labelled equivalents (capped by the matcher).
- det-pass recall — over all deterministic passes. Losses here are non-equivalent
  decoys, not lost matches. They reduce the LLM's work.

Reproduce:

```bash
.venv/bin/python -m benchmarks.candidate_retrieval.run --source reference --json benchmarks/candidate_retrieval/results/reference.json
.venv/bin/python -m benchmarks.candidate_retrieval.run --source synthetic --contracts 1000 --seed 20261010 --skew 1.3 --json benchmarks/candidate_retrieval/results/synthetic_1000_seed20261010.json
.venv/bin/python -m benchmarks.candidate_retrieval.run --source synthetic --contracts 1000 --seed 20261010 --skew 2.5 --json benchmarks/candidate_retrieval/results/stress_1000_seed20261010_skew2.5.json
.venv/bin/python -m benchmarks.candidate_retrieval.run --source reference --sweep --json benchmarks/candidate_retrieval/results/sweep_reference.json
.venv/bin/python -m benchmarks.candidate_retrieval.run --source synthetic --contracts 1000 --seed 20261010 --sweep --json benchmarks/candidate_retrieval/results/sweep_synthetic_1000_seed20261010.json
.venv/bin/python -m benchmarks.candidate_retrieval.run --source synthetic --contracts 1000 --seed 20261010 --skew 2.5 --sweep --json benchmarks/candidate_retrieval/results/sweep_stress_1000_seed20261010_skew2.5.json
.venv/bin/python -m benchmarks.candidate_retrieval.fallback_window
```

## 5. Results (after, at the shipped `MAX_LOCAL_DENSITY = 20`)

### 5.1 Synthetic 1,000, seed 20261010, skew 1.3 (`results/synthetic_1000_seed20261010.json`)

| Metric | Before (`legacy`) | After (retrieval) |
|---|---|---|
| Candidate pairs | 333,296 | **3,262** (0.98 %) |
| Components / largest | 1 / 1,000 | 119 / 51 |
| Deterministic-pass proposed | 986 / 986 | 720 / 986 (0.730) |
| Equivalent proposed | 301 / 301 | 256 / 301 (0.851, the 45 lost are all det-reject) |
| **Equivalent ∩ det-pass proposed** | 256 / 256 | **256 / 256 (1.0)** |
| Runtime median (3 runs) | 0.186 s | 0.040 s |
| Peak Python heap | 44.62 MB | 2.28 MB |
| Budget 200: LLM calls / approved / equivalent recall | 200 / 69 / 0.229 | 200 / **122** / **0.405** |
| No budget: LLM calls / approved / equivalent recall | 986 / 256 / 0.851 | 720 / 256 / 0.851 |

The budget row is the production ordering through `run_matching`, so it
reflects what the paper engine would spend on this data. The 200-call cap
approves more equivalent pairs after than before (122 vs 69). The 200 calls
cover 28 % of the deterministic-pass set after (200/720) against 20 % before
(200/986). The benchmark does not separate that effect from the ordering. The
gain is measured on synthetic data only.

### 5.2 Stress, skew 2.5 (`results/stress_1000_seed20261010_skew2.5.json`)

The busiest settlement moment holds 43 contracts (25 at skew 1.3). This is the
case that most stresses the density rule.

| Metric | Before | After |
|---|---|---|
| Candidate pairs | 333,296 | **3,289** |
| Largest component | 1,000 | 80 |
| Deterministic-pass proposed | 1,675 / 1,675 | 873 / 1,675 (0.521) |
| Equivalent ∩ det-pass proposed | 256 / 256 | **256 / 256 (1.0)** |
| Runtime median / peak heap | 0.172 s / 44.62 MB | 0.038 s / 2.28 MB |
| Budget 200: approved / equivalent recall | 51 / 0.169 | 128 / 0.425 |

### 5.3 Reference, 50 hand-labelled listings (`results/reference.json`)

| Metric | Before | After |
|---|---|---|
| Cross-venue pairs | 796 | **36** (4.5 %) |
| Components / largest | 1 / 50 | 22 / 6 |
| Deterministic-pass proposed | 29 / 29 | 26 / 29 (0.897) |
| Equivalent proposed | 17 / 17 | 15 / 17 |
| **Equivalent ∩ det-pass proposed** | 16 / 16 | **15 / 16 (0.9375)** |
| Budget 200: approved / equivalent recall | 16 / 0.941 | 15 / 0.882 |

The reference set has 17 equivalent pairs in total. Only 16 of them are
deterministically passable, and retrieval loses one of those 16. Section 7 lists
it, with the boundary cases.

### 5.4 Robustness across seeds (`results/seed*_skew*.json`, `--seed` / `--skew`)

| Seed | Skew | Legacy candidates | Retrieval candidates | Eq∩det recall | Det-pass recall | Largest component |
|---|---|---|---|---|---|---|
| 20261010 | 1.3 | 333,296 | 3,262 | 1.0 | 0.730 | 51 |
| 20261010 | 2.5 | 333,296 | 3,289 | 1.0 | 0.521 | 80 |
| 1 | 1.3 | 333,247 | 2,941 | 1.0 | 0.717 | 42 |
| 1 | 2.5 | 333,247 | 3,279 | 1.0 | 0.493 | 86 |
| 2 | 1.3 | 333,236 | 3,032 | 1.0 | 0.671 | 39 |
| 2 | 2.5 | 333,236 | 2,820 | 1.0 | 0.448 | 79 |
| 3 | 1.3 | 333,147 | 3,293 | 1.0 | 0.675 | 73 |
| 3 | 2.5 | 333,147 | 2,774 | 1.0 | 0.464 | 109 |

Equivalent ∩ det-pass recall is 1.0 in all eight runs, and the largest component
is 39–109 rather than 1,000. Lost pairs are, in every run, non-equivalent
decoys: the `lost_vs_current_count` is 0 on every synthetic run.

## 6. Sensitivity: the two knobs

From `results/sweep_*.json`. Equivalent ∩ det-pass recall and candidate count.

**Density cap** (families `thr+num+tok`):

| Cap | Synthetic (skew 1.3) | Largest comp. | Stress (skew 2.5) | Largest comp. | Reference |
|---|---|---|---|---|---|
| 5 | 922 / **0.984** | 26 | 1,032 / **0.961** | 26 | 36 / 0.938 |
| 10 | 1,737 / 1.0 | 33 | 1,856 / 1.0 | 51 | 36 / 0.938 |
| **20 (shipped)** | 3,262 / 1.0 | 51 | 3,289 / 1.0 | 80 | 36 / 0.938 |
| 40 | 6,268 / 1.0 | 71 | 6,161 / 1.0 | 118 | 36 / 0.938 |
| 100,000 (no cap) | 7,590 / 1.0 | 71 | 14,923 / 1.0 | 118 | 36 / 0.938 |

Cap 5 loses recall on both synthetic sets, so it is rejected. Caps 10 and 20 both
reach 1.0 on every tested dataset. Cap 20 was kept over 10 as a margin for
unseen data. That costs about 1,400–1,500 more candidates, and the largest
stress component grows from 51 to 80. This is a judgement, not a measured
optimum: the benchmark cannot separate caps 10 and 20. Caps of 40 and above add
candidates without recall gain.

**Families** (cap 20, synthetic skew 1.3; reference in parentheses):

| Families | Candidates | Eq∩det recall | Det-pass recall |
|---|---|---|---|
| `thr` only | 219 (15) | 0.809 (0.750) | 0.222 (0.517) |
| `thr+num` | 2,472 (36) | 0.930 (0.938) | 0.635 (0.897) |
| `tok` only | 1,985 (27) | **1.0** (0.938) | 0.570 (0.759) |
| **`thr+num+tok` (shipped)** | 3,262 (36) | **1.0** (0.938) | **0.730** (0.897) |

`thr` alone is not sufficient. A threshold-only design looked perfect on an
earlier generator without threshold-less families, and that result was
misleading. `thr+num` never reaches full eq∩det recall on synthetic (max
0.9727), so the `tok` family is needed.

**Open alternative.** `tok` alone matches the shipped eq∩det recall on both
datasets with fewer candidates (1,985 vs 3,262 synthetic). It gives up
deterministic-pass coverage (0.57 vs 0.73 synthetic, 0.76 vs 0.90 reference).
That coverage loss is on non-equivalent decoys. We kept `thr` and `num` because
the brief names normalized thresholds and numbers as discriminative attributes,
and because a threshold market whose titles share no content word is exactly the
case those keys exist for. **The tested data does not show that `thr`/`num`
are necessary**, so the choice is a design judgement. Revisit with real
two-venue data before narrowing the keys.

## 7. Lost matches (explicit)

Lost = a pair the current pipeline proposes (or can approve) that retrieval
does not propose. Every loss below is listed, and each is attributed by label.

**Reference set (50 listings), current retrieval vs exhaustive truth:**

| # | Pair | Label | Deterministic | Lost because | Recoverable by |
|---|---|---|---|---|---|
| L1 | `KX-APPROP-OCT01-2026` (Kalshi) ↔ `federal-funds-law-sep30` (Polymarket US), `ref-paraphrase-no-shared-token-oct1` | **Equivalent** | **Passes** | A genuine paraphrase with no shared content word or number after stopword removal, so no key links them. Their settlement times are **identical** (0 h), so the settlement window already admits the pair; only the key is missing. | A settlement-only proposal path would recover it, but its earlier ablation proposed ~324k candidates on the pre-threshold-less generator (§10). The narrowest form, a 60-min window (§9), recovers it at +1,850 synthetic candidates. A similarity signal beyond exact keys would also work and must stay proposal-only. |
| L2 | `KX-MENTION-TARIFF-OCT28` ↔ `FX-WS-2026-YANKEES` | Non-equivalent decoy | Passes | No shared key, so not proposed. | Not needed. Non-equivalent. |
| L3 | `FX-WS-2026-YANKEES` ↔ `KX-MENTION-RECORD-OCT28` | Non-equivalent decoy | Passes | No shared key, so not proposed. | Not needed. Non-equivalent. |

Also not proposed, and **not a retrieval loss**: `KX-CPIM-OCT26-GE03` ↔
`cpi-mom-oct-2026-ge-0-3` is labelled equivalent but settles **6 h 01 m** apart,
outside the 6 h tolerance. The deterministic matcher rejects it, so no retrieval
design can approve it. It stays in the reference set as a boundary case.
Boundary cases inside tolerance: `ref-fed-hold-375-oct26` (5 h 50 m) and
`ref-spx-gt6500-dec18` (5 h 59 m), both proposed.

**Synthetic 1,000, skew 1.3:** 266 deterministic-pass pairs are lost, and **all
266 are non-equivalent** (0 equivalent). The 45 equivalents not proposed are all
deterministic-reject, so they are unreachable under either design. Equivalent
∩ det-pass lost: **0**. The same holds in every seed run in §5.4.

**Net outcome for the pipeline:** on the tested data retrieval loses **no**
approvable equivalent pair on synthetic data, and **one** on the reference set
(L1). The synthetic 1,000 benchmark's LLM-visible set shrinks by 27 %
(986 → 720 det-pass), and that shrinkage is decoys.

## 8. What is NOT claimed

- **No perfect recall.** The figures are measured on one synthetic generator,
  four seeds and two calendar skews, and on 50 hand-labelled listings. Real
  venue wording, settlement habits and taxonomies can produce misses that
  neither dataset contains. L1 shows the design already misses a real-looking
  paraphrase.
- **Reference labels are the author's.** They have not been independently reviewed.
- **Timings are sandbox measurements** of candidate generation only (median of 3,
  Python 3.11, one machine). They do not include matching, and they are not
  production latency.
- **The end-to-end budget row uses an oracle matcher.** It shows how the budget is
  spent, not how often the real LLM approves.
- **The 2,111 figure** is not reproduced (§1). It is recorded as a generator
  difference and is not used as a baseline anywhere.
- **Equivalence on the synthetic generator is defined by its own labels**, so
  synthetic recall measures retrieval against those labels, not against venue truth.

## 9. Owner decisions

### 9.1 Accept the reference recall loss (L1), or adopt a 60-minute settlement fallback

Both are open. Neither is adopted. The current code does **not** silently cap
or fall back.

| Option | Reference eq∩det | Synthetic candidates | Synthetic det-pass | Synthetic eq∩det |
|---|---|---|---|---|
| **A. Current design (shipped)** | 15/16 (0.9375) | 3,262 | 720 / 986 | 256 / 256 |
| **B. A + all cross-venue pairs within 60 min** | 16/16 (1.0) | 5,112 (+57 %) | 796 / 986 | 256 / 256 |

Option B recovers L1 at the cost of +1,850 synthetic candidates (+76 det-pass),
with **zero** synthetic recall gain. It is measured by
`benchmarks/candidate_retrieval/fallback_window.py` (`results/fallback_60min.json`).

**Recommendation:** keep A. Option B recovers one pair on the reference set, and
that pair has identical settlement times. Only a settlement-based path recovers
it, and the settlement-based paths we measured cost far more on synthetic data.
B reintroduces a settlement-only proposal path that the brief asked us to avoid.
Ask for a real-data case before accepting that cost. If the owner wants L1
recovered, a similarity signal that stays proposal-only is the cleaner route.

## 10. Rejected alternatives (do not retry without new evidence)

- **Global per-key cap (40, `MAX_BLOCK_SIZE`).** It pruned threshold-less families
  whose shared keys have document frequency 46–92 (`tok:shutdown` 53,
  `tok:republican` 46). It lost 12 of 256 eq∩det pairs, all shutdown/Senate.
  Density is therefore counted locally, per settlement moment.
- **Local cap 5.** Eq∩det recall 0.984 (synthetic skew 1.3) and 0.961 (skew 2.5).
- **Settlement buckets with union-find.** Settlement alone chains 987 of 1,000
  contracts into one component. Settlement is now a pairwise window.
- **Category key across venues.** Venue taxonomies differ, so it contributes
  nothing reliable. It is excluded from proposal keys.
- **Threshold-only retrieval.** Looked perfect on a generator with no
  threshold-less families. It reaches 0.809 eq∩det recall with threshold-less families.

## 11. Related defect (not fixed) — D25

Recorded in `docs/AGENT_HANDOFF.md` §7a. Retrieval proposes the pair, and the
deterministic matcher rejects it because the venues encode the same threshold
differently: Kalshi sets `threshold_operator` to the raw `strike_type` string
(for example `greater`), and Polymarket US sets it to `None`. Reproduced
2026-10-10: identical event, threshold 5.0, settlement equal, Kalshi `greater`
vs Polymarket `None` ⇒ `threshold_operator` check fails. With both `>` it
passes. Units are `None` on both adapters, so the units check passes
vacuously, and a real units mismatch cannot be detected. This is outside
retrieval and is not fixed here.

## 12. Tests

`tests/test_candidate_generation.py` (pipeline-level, 29 tests, including the
revised settlement and threshold cases) and `tests/test_candidate_retrieval.py`
(31 tests): settlement window and day boundary, numeric normalization
(`100k` = `100,000`), different wording, generic words that must not link,
non-transitivity, proposal-is-not-approval, the labelled reference set (including
the pinned known miss L1), synthetic recall, and the 1,000-contract legacy
baseline (1 component).

Mutation checks (production code restored and verified with `diff`):

| Mutation | Result |
|---|---|
| Settlement window 400 days (`RETRIEVAL_SETTLEMENT_WINDOW`) | caught, 10 failed |
| No density bound (`MAX_LOCAL_DENSITY = 10**9`) | caught, 2 failed in 0.65 s. A fixture that scaled with the cap hung the run and was fixed first |
| Global cap 40 on each key | caught, 1 failed. A fixture whose unique round-name token linked the pair hid the word key and was fixed first |
| Transitive closure of proposed pairs (legacy-style grouping) | caught, 4 failed |
