"""Owner-decision evidence: what a 60-minute cross-venue settlement fallback costs.

NOT adopted. The production retrieval (``candidate_pairs``) proposes a pair only
through a shared key inside the deterministic settlement window. This script
measures the alternative an owner might choose instead: additionally propose
EVERY cross-venue pair whose settlement times differ by at most 60 minutes,
with no key at all. It reports the extra pairs and how many of them the
deterministic matcher would pass, and whether any labelled equivalent that the
current retrieval misses is recovered.

Run:  ``python -m benchmarks.candidate_retrieval.fallback_window``
Writes ``results/fallback_60min.json`` (pass ``--json -`` to print only).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from prediction_arb.normalization.contracts import candidate_pairs, with_normalized_keys

from .run import (build_dataset, exhaustive_truth, listings_to_contracts,
                  load_reference)

RESULTS = Path(__file__).parent / "results" / "fallback_60min.json"
FALLBACK_SECONDS = 3600


def _measure(name: str, listings) -> dict:
    contracts = listings_to_contracts(listings)
    truth = exhaustive_truth(contracts, [x.eq_id for x in listings])
    current = set(candidate_pairs([with_normalized_keys(c) for c in contracts]))
    ts = [c.settlement_time.timestamp() if c.settlement_time else None
          for c in contracts]
    window_pairs: set[tuple[int, int]] = set()
    for i in range(len(contracts)):
        for j in range(i + 1, len(contracts)):
            if (contracts[i].venue != contracts[j].venue
                    and ts[i] is not None and ts[j] is not None
                    and abs(ts[i] - ts[j]) <= FALLBACK_SECONDS):
                window_pairs.add((i, j))
    combined = current | window_pairs

    def row(label: str, pairs: set) -> dict:
        return {
            "variant": label,
            "candidate_pairs": len(pairs),
            "det_pass_proposed": len(pairs & truth.det),
            "det_pass_total": len(truth.det),
            "equivalent_and_det_proposed": len(pairs & truth.eq_det),
            "equivalent_and_det_total": len(truth.eq_det),
        }

    return {
        "dataset": name,
        "contracts": len(contracts),
        "variants": [row("current retrieval", current),
                     row("current + 60-min cross-venue fallback", combined)],
        "extra_pairs_from_fallback": len(combined) - len(current),
        "newly_recovered_equivalent_and_det": sorted(
            (contracts[i].contract_id, contracts[j].contract_id)
            for i, j in (combined - current) & truth.eq_det),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=str(RESULTS),
                    help="output path, or '-' for stdout only")
    args = ap.parse_args(argv)
    report = {
        "fallback_window_seconds": FALLBACK_SECONDS,
        "status": "NOT ADOPTED (owner decision pending; see docs/CANDIDATE_RETRIEVAL.md)",
        "reference": _measure("reference", load_reference()[0]),
        "synthetic_1000_seed20261010": _measure("synthetic", build_dataset(1000)),
    }
    text = json.dumps(report, indent=1)
    if args.json == "-":
        print(text)
        return
    Path(args.json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json).write_text(text + "\n")
    for key in ("reference", "synthetic_1000_seed20261010"):
        block = report[key]
        for v in block["variants"]:
            print(f"{block['dataset']:>28s} {v['variant']:<38s} pairs={v['candidate_pairs']:6d} "
                  f"det={v['det_pass_proposed']:4d}/{v['det_pass_total']} "
                  f"eq∩det={v['equivalent_and_det_proposed']}/{v['equivalent_and_det_total']}")
        print(f"{'':>28s} newly recovered eq∩det: {block['newly_recovered_equivalent_and_det']}")


if __name__ == "__main__":
    main()
