"""Candidate-retrieval benchmark harness (§96, docs/CANDIDATE_RETRIEVAL.md).

Runs an exhaustive ground-truth pass over every cross-venue pair and then
scores a candidate generator against it:

* candidate pairs proposed (cross-venue only, the pairs the matcher would see);
* connected-component sizes of the candidate graph;
* recall against (a) labelled genuinely-equivalent pairs, (b) pairs the
  deterministic matcher passes (what the LLM could ever see), and (c) the
  intersection, which is the set the current pipeline could actually approve;
* runtime (median of repeated runs) and peak Python heap during generation;
* an end-to-end pass through ``modes.shared.run_matching`` with a labelled
  ORACLE matcher and the production 200-comparison budget, so the LLM budget
  is measured the same way the paper engine spends it.

The oracle is an analysis device, not a matcher: it answers from the hidden
labels. It exists to measure what retrieval loses and how the budget is spent.
It never ships into the production path.

Run:  ``python -m benchmarks.candidate_retrieval.run --contracts 1000``
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from prediction_arb.core.types import NormalizedContract  # noqa: E402
from prediction_arb.matching.deterministic_matcher import deterministic_match  # noqa: E402
from prediction_arb.matching.llm_matcher import LLMMatcher, LLMMatchResult  # noqa: E402
from prediction_arb.modes.shared import run_matching  # noqa: E402
from prediction_arb.normalization.contracts import (  # noqa: E402
    candidate_pairs,
    component_indices,
    with_normalized_keys,
)
from prediction_arb.matching.rules_validator import validate_match  # noqa: E402
from prediction_arb.matching.match_certificates import build_certificate  # noqa: E402

from . import legacy_contracts as legacy  # noqa: E402
from .synthetic import Listing, build_dataset  # noqa: E402

REFERENCE_PATH = Path(__file__).with_name("reference_contracts.json")
PRODUCTION_LLM_BUDGET = 200  # config/settings.yaml → llm.max_comparisons_per_cycle

Pair = tuple[int, int]


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

def listings_to_contracts(listings: list[Listing]) -> list[NormalizedContract]:
    return [x.to_contract() for x in listings]


def load_reference() -> tuple[list[Listing], dict[str, str]]:
    """Load the small hand-labelled exhaustive reference set (§96 recall)."""
    raw = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    listings = []
    for r in raw["contracts"]:
        listings.append(Listing(
            venue=r["venue"], contract_id=r["contract_id"], eq_id=r["eq_class"],
            family=r.get("family", "reference"), title=r["title"],
            settlement=datetime.fromisoformat(r["settlement"]).astimezone(timezone.utc),
            threshold=Decimal(r["threshold"]) if r.get("threshold") is not None else None,
            operator=r.get("operator"), units=r.get("units"),
            category=r.get("category", ""), resolution=r["resolution"],
            payout=Decimal(r.get("payout", "1.00"))))
    return listings, raw.get("notes", {})


# ---------------------------------------------------------------------------
# Ground truth (exhaustive)
# ---------------------------------------------------------------------------

@dataclass
class Truth:
    n: int
    cross_pairs: set[Pair]
    eq: set[Pair]
    det: set[Pair]

    @property
    def eq_det(self) -> set[Pair]:
        return self.eq & self.det


def exhaustive_truth(contracts: list[NormalizedContract],
                     eq_ids: list[str]) -> Truth:
    n = len(contracts)
    cross: set[Pair] = set()
    eq: set[Pair] = set()
    det: set[Pair] = set()
    for i in range(n):
        a = contracts[i]
        for j in range(i + 1, n):
            b = contracts[j]
            if a.venue == b.venue:
                continue
            p = (i, j)
            cross.add(p)
            if eq_ids[i] == eq_ids[j]:
                eq.add(p)
            if deterministic_match(a, b).passed:
                det.add(p)
    return Truth(n=n, cross_pairs=cross, eq=eq, det=det)


# ---------------------------------------------------------------------------
# Candidate generators
# ---------------------------------------------------------------------------

@dataclass
class Candidates:
    pairs: set[Pair]                     # cross-venue index pairs (i<j) proposed
    components: list[list[int]]          # connected components of the candidate graph


def legacy_generator(contracts: list[NormalizedContract]) -> Candidates:
    """BEFORE (frozen): union-find over 24h settlement buckets ±1, category,
    threshold, numeric and content keys; every cross-venue pair in a group is
    proposed. Reproduces `git show 91d46df:prediction_arb/normalization/contracts.py`."""
    index = {(c.venue, c.contract_id): i for i, c in enumerate(contracts)}
    groups = legacy.group_by_event([legacy.with_normalized_keys(c) for c in contracts])
    pairs: set[Pair] = set()
    comps: list[list[int]] = []
    for members in groups.values():
        idx = sorted(index[(m.venue, m.contract_id)] for m in members)
        comps.append(idx)
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                i, j = idx[x], idx[y]
                if contracts[i].venue != contracts[j].venue:
                    pairs.add((i, j))
    return Candidates(pairs=pairs, components=comps)


def retrieval_generator(contracts: list[NormalizedContract], **kwargs) -> Candidates:
    """AFTER (production): pairwise, bounded, non-transitive retrieval."""
    norm = [with_normalized_keys(c) for c in contracts]
    pairs = candidate_pairs(norm, **kwargs)
    comps = sorted(component_indices(len(norm), pairs), key=len, reverse=True)
    return Candidates(pairs=set(pairs), components=comps)


GENERATORS: dict[str, Callable[[list[NormalizedContract]], Candidates]] = {
    "legacy": legacy_generator,
    "retrieval": retrieval_generator,
}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _component_stats(n: int, comps: list[list[int]]) -> dict:
    sizes = sorted((len(c) for c in comps), reverse=True)
    covered = set(i for c in comps for i in c)
    singletons = n - len(covered)
    return {
        "components": len(comps) + singletons,
        "largest_component": sizes[0] if sizes else 0,
        "top5_component_sizes": sizes[:5],
        "singleton_contracts": singletons,
    }


def score(name: str, cands: Candidates, truth: Truth,
          current: Optional[Candidates] = None) -> dict:
    p = cands.pairs
    cross_cand = p & truth.cross_pairs
    eq_c = cross_cand & truth.eq
    det_c = cross_cand & truth.det
    eqdet_c = cross_cand & truth.eq_det

    def ratio(a: int, b: int) -> Optional[float]:
        return round(a / b, 4) if b else None

    out = {
        "impl": name,
        "contracts": truth.n,
        "all_pairs": truth.n * (truth.n - 1) // 2,
        "cross_venue_pairs": len(truth.cross_pairs),
        "candidate_pairs": len(cross_cand),
        "candidate_fraction_of_cross": ratio(len(cross_cand), len(truth.cross_pairs)),
        "det_pass_pairs_total": len(truth.det),
        "det_pass_pairs_proposed": len(det_c),
        "equivalent_pairs_total": len(truth.eq),
        "equivalent_pairs_proposed": len(eq_c),
        "recall_equivalent": ratio(len(eq_c), len(truth.eq)),
        "equivalent_and_det_pass_total": len(truth.eq_det),
        "equivalent_and_det_pass_proposed": len(eqdet_c),
        "recall_equivalent_and_det_pass": ratio(len(eqdet_c), len(truth.eq_det)),
        "recall_det_pass": ratio(len(det_c), len(truth.det)),
        "equivalent_but_det_reject": len(truth.eq - truth.det),
    }
    out.update(_component_stats(truth.n, cands.components))
    if current is not None:
        base = current.pairs & truth.cross_pairs & truth.eq_det
        lost = base - cands.pairs
        out["lost_vs_current"] = sorted(lost)
        out["lost_vs_current_count"] = len(lost)
        out["recall_vs_current"] = ratio(len(base & cands.pairs), len(base))
    return out


def time_and_memory(gen: Callable[[list[NormalizedContract]], Candidates],
                    contracts: list[NormalizedContract],
                    repeats: int = 3) -> dict:
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        gen(contracts)
        times.append(time.perf_counter() - t0)
    tracemalloc.start()
    gen(contracts)
    _cur, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"runtime_s_median": round(statistics.median(times), 4),
            "runtime_s_runs": [round(t, 4) for t in times],
            "peak_python_heap_mb": round(peak / 1e6, 2)}


# ---------------------------------------------------------------------------
# End-to-end with an oracle matcher and the production budget
# ---------------------------------------------------------------------------

class OracleMatcher(LLMMatcher):
    """Labelled stand-in for the LLM (analysis only). Honours the same per-cycle
    budget as production; answers ``match=True`` iff the hidden labels agree."""

    def __init__(self, eq_by_key: dict[str, str], budget: Optional[int]):
        super().__init__(client=None, model_version="oracle",
                         max_comparisons_per_cycle=budget)
        self._eq = eq_by_key
        self.calls = 0

    async def compare(self, a, b):  # type: ignore[override]
        if self._cycle_comparisons >= (self.max_comparisons_per_cycle or 10**12):
            return LLMMatchResult(False, 0.0, "", valid=False,
                                  error="budget exhausted (oracle)")
        self._cycle_comparisons += 1
        self.calls += 1
        ka = f"{a.venue}:{a.contract_id}"
        kb = f"{b.venue}:{b.contract_id}"
        same = self._eq[ka] == self._eq[kb]
        return LLMMatchResult(
            match=same, confidence=0.99 if same else 0.01,
            reasoning_summary="oracle", field_identities={
                k: same for k in ("event_identity", "outcome_identity",
                                  "settlement_identity", "resolution_identity",
                                  "threshold_identity", "time_identity",
                                  "payout_identity")},
            model_version="oracle", valid=True)


async def legacy_run_matching(contracts: list[NormalizedContract],
                              llm: LLMMatcher) -> tuple[dict, dict]:
    """BEFORE: the pre-2026-10-10 loop — every cross-venue pair inside a
    legacy union-find group goes to the deterministic matcher, in group order.
    Identical to `modes/shared.run_matching` except for the candidate source."""
    contracts = [legacy.with_normalized_keys(c) for c in contracts]
    groups = legacy.group_by_event(contracts)
    llm.begin_cycle()
    approved: dict[tuple[str, str], tuple[str, float]] = {}
    stats = {"pairs_considered": 0, "pairs_deterministic_pass": 0}
    for group in groups.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.venue == b.venue:
                    continue
                stats["pairs_considered"] += 1
                det = deterministic_match(a, b)
                if not det.passed:
                    continue
                stats["pairs_deterministic_pass"] += 1
                llm_res = await llm.compare(a, b)
                outcome = validate_match(a, b, det, llm_res)
                cert = build_certificate(a, b, det, llm_res, outcome)
                if cert.status == "APPROVED":
                    ka = f"{a.venue}:{a.contract_id}"
                    kb = f"{b.venue}:{b.contract_id}"
                    approved[(min(ka, kb), max(ka, kb))] = (
                        cert.match_id, cert.llm_confidence)
    return approved, stats


def end_to_end(impl: str, contracts: list[NormalizedContract],
               eq_by_key: dict[str, str], truth: Truth,
               budget: Optional[int]) -> dict:
    matcher = OracleMatcher(eq_by_key, budget)
    if impl == "legacy":
        approved, stats = asyncio.run(legacy_run_matching(contracts, matcher))
    else:
        approved, result = asyncio.run(run_matching(contracts, matcher))
        stats = {"pairs_considered": result.pairs_considered,
                 "pairs_deterministic_pass": result.pairs_deterministic_pass}
    tp = fp = 0
    for (ka, kb) in approved:
        if eq_by_key[ka] == eq_by_key[kb]:
            tp += 1
        else:
            fp += 1
    total_eq = len(truth.eq)
    return {
        "llm_budget": budget,
        "llm_calls": matcher.calls,
        "pairs_considered": stats["pairs_considered"],
        "pairs_deterministic_pass": stats["pairs_deterministic_pass"],
        "approved": len(approved),
        "approved_equivalent": tp,
        "approved_non_equivalent": fp,
        "equivalent_pairs_total": total_eq,
        "approved_recall_vs_all_equivalent": round(tp / total_eq, 4) if total_eq else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_source(source: str, n: int, seed: int,
                skew: float = 1.3) -> tuple[list[Listing], dict]:
    if source == "reference":
        return load_reference()
    return build_dataset(n, seed, calendar_skew=skew), {"calendar_skew": skew}


def run(source: str, n: int, seed: int, impls: list[str], *,
        with_e2e: bool, timing: bool, skew: float = 1.3) -> dict:
    listings, notes = load_source(source, n, seed, skew)
    contracts = listings_to_contracts(listings)
    eq_ids = [x.eq_id for x in listings]
    eq_by_key = {f"{x.venue}:{x.contract_id}": x.eq_id for x in listings}
    truth = exhaustive_truth(contracts, eq_ids)
    before = legacy_generator(contracts)
    report = {
        "source": source,
        "contracts": len(contracts),
        "seed": seed if source != "reference" else None,
        "notes": notes,
        "truth": {
            "cross_venue_pairs": len(truth.cross_pairs),
            "equivalent_pairs": len(truth.eq),
            "det_pass_pairs": len(truth.det),
            "equivalent_and_det_pass_pairs": len(truth.eq_det),
            "equivalent_but_det_reject": len(truth.eq - truth.det)},
        "impls": {}}
    for name in impls:
        gen = GENERATORS[name]
        cands = gen(contracts)
        entry = score(name, cands, truth, current=before)
        if timing:
            entry.update(time_and_memory(gen, contracts))
        if with_e2e:
            entry["end_to_end"] = {
                "budget_200": end_to_end(name, contracts, eq_by_key, truth,
                                         PRODUCTION_LLM_BUDGET),
                "unbudgeted": end_to_end(name, contracts, eq_by_key, truth, None)}
        report["impls"][name] = entry
    return report


def sweep(source: str, n: int, seed: int, skew: float = 1.3) -> list[dict]:
    """Sensitivity of retrieval to its two knobs (families, block-size cap).

    Reports candidate pairs, recall on labelled equivalents, and recall on the
    equivalents the matcher can pass — so the cap is chosen with its recall
    cost visible rather than by intuition."""
    listings, _ = load_source(source, n, seed, skew)
    contracts = listings_to_contracts(listings)
    truth = exhaustive_truth(contracts, [x.eq_id for x in listings])
    rows = []
    family_sets = {
        "thr": ("thr",),
        "thr+num": ("thr", "num"),
        "thr+num+tok": ("thr", "num", "tok"),
        "tok": ("tok",),
    }
    for fam_name, fams in family_sets.items():
        for cap in (5, 10, 20, 40, 80, 160, 100000):
            cands = retrieval_generator(contracts, families=fams, max_local_density=cap)
            s_ = score(f"{fam_name}|cap={cap}", cands, truth)
            rows.append({
                "families": fam_name, "max_local_density": cap,
                "calendar_skew": skew,
                "candidate_pairs": s_["candidate_pairs"],
                "components": s_["components"],
                "largest_component": s_["largest_component"],
                "recall_equivalent": s_["recall_equivalent"],
                "recall_equivalent_and_det_pass": s_["recall_equivalent_and_det_pass"],
                "recall_det_pass": s_["recall_det_pass"],
                "det_pass_proposed": s_["det_pass_pairs_proposed"],
            })
    return rows


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["synthetic", "reference"], default="synthetic")
    ap.add_argument("--contracts", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20261010)
    ap.add_argument("--skew", type=float, default=1.3,
                    help="settlement-calendar concentration (1.3 default; 2.5 stress)")
    ap.add_argument("--impl", action="append", choices=sorted(GENERATORS),
                    help="repeatable; default: all registered")
    ap.add_argument("--no-timing", action="store_true")
    ap.add_argument("--no-e2e", action="store_true")
    ap.add_argument("--sweep", action="store_true",
                    help="print the family/block-size sensitivity table instead")
    ap.add_argument("--json", type=Path, help="write the full report here")
    args = ap.parse_args(argv)
    if args.sweep:
        rows = sweep(args.source, args.contracts, args.seed, args.skew)
        if args.json:
            args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        for r in rows:
            print(json.dumps(r))
        return 0
    impls = args.impl or list(GENERATORS)
    if "legacy" not in impls:
        impls = ["legacy"] + impls
    rep = run(args.source, args.contracts, args.seed, impls,
              with_e2e=not args.no_e2e, timing=not args.no_timing, skew=args.skew)
    if args.json:
        args.json.write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in rep.items() if k != "impls"}, indent=2,
                     default=str))
    for entry in rep["impls"].values():
        print(json.dumps(entry, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
