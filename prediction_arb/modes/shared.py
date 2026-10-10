"""Shared strategy pipeline (§45, §57).

Identical across historical/paper/live modes:
  discovery → normalization → provenance → matching → order books →
  fee/slippage/settlement costing → guaranteed-arbitrage detection →
  ranking. Only the DATA PROVIDER and EXECUTION ENGINE differ per mode.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from ..arbitrage.optimizer import search_event
from ..core.clock import ensure_utc
from ..core.types import Independence, Mode, NormalizedContract, Opportunity, OrderBook
from ..fees.calculator import FeeCalculator
from ..fees.freshness import freshness_enforced
from ..matching.deterministic_matcher import deterministic_match
from ..matching.llm_matcher import LLMMatcher
from ..matching.match_certificates import MatchCertificate, build_certificate
from ..matching.rules_validator import validate_match
from ..normalization.contracts import (
    blocking_keys,
    candidate_pairs,
    group_by_event,
    groups_from_pairs,
    with_normalized_keys,
)
from ..provenance.independence import contracts_independent


def _independence_priority_score(ind: Independence) -> int:
    # Higher is more valuable to resolve: INDEPENDENT pairs can become
    # APPROVED and thus lead to executable opportunities; SAME_VENUE can
    # never be APPROVED (§33, D15) and should not consume budget first.
    return {
        Independence.INDEPENDENT: 100,
        Independence.LIKELY_INDEPENDENT: 50,
        Independence.UNKNOWN: 0,
        Independence.LIKELY_SAME_VENUE: -100,
        Independence.SAME_VENUE: -200,
    }.get(ind, 0)


def _shared_keys_score(a: NormalizedContract, b: NormalizedContract) -> tuple[int, int]:
    ka = blocking_keys(a)
    kb = blocking_keys(b)
    shared = ka & kb
    score = 0
    for k in shared:
        if k.startswith("thr:"):
            score += 20
        elif k.startswith("num:"):
            score += 5
        elif k.startswith("tok:"):
            score += 1
    return score, len(shared)


def _settlement_delta_seconds(a: NormalizedContract, b: NormalizedContract) -> float:
    if a.settlement_time is None or b.settlement_time is None:
        return 0.0
    try:
        delta = abs(ensure_utc(a.settlement_time) - ensure_utc(b.settlement_time))
        return float(delta.total_seconds())
    except Exception:
        return 0.0


@dataclass
class ScanResult:
    markets_scanned: int = 0
    events_grouped: int = 0
    pairs_considered: int = 0
    pairs_deterministic_pass: int = 0
    certificates: list[MatchCertificate] = field(default_factory=list)
    opportunities: list[Opportunity] = field(default_factory=list)


async def run_matching(contracts: list[NormalizedContract],
                       llm: LLMMatcher) -> tuple[dict, ScanResult]:
    """Stage 1+2+certificate for all cross-venue pairs within event groups.

    Returns (approved_pairs, partial ScanResult). approved_pairs maps
    sorted contract-key tuples to (match_id, confidence).

    Candidate processing is deterministic and budget-efficient (Arb.9):
    * deterministic REJECT is final and never reaches the LLM;
    * surviving pairs are scored by defensible, pre-LLM evidence:
      venue independence (INDEPENDENT first), shared blocking keys
      (thr > num > tok), and settlement-time proximity;
    * pairs are processed in priority order, so a limited per-cycle LLM
      budget is spent on the most promising, highest-value candidates
      first;
    * ordering is fully deterministic (priority, delta, lexical contract
      ids/versions) — no discovery-order dependence.
    """
    result = ScanResult(markets_scanned=len(contracts))
    contracts = [with_normalized_keys(c) for c in contracts]
    # Retrieval (§96): pairwise, bounded, non-transitive. Only these pairs
    # reach the deterministic matcher; proposal is not approval.
    pairs = candidate_pairs(contracts)
    result.events_grouped = len(groups_from_pairs(contracts, pairs))
    # One budget window per scan cycle (§97): a cycle can spend at most
    # `max_comparisons_per_cycle` NEW LLM calls; excess pairs stay REVIEW.
    llm.begin_cycle()

    # First pass: deterministic filter + priority scoring (cheap, defensible).
    scored: list[tuple[tuple, int, int, NormalizedContract, NormalizedContract, object]] = []
    for i, j in pairs:
        a, b = contracts[i], contracts[j]
        result.pairs_considered += 1
        det = deterministic_match(a, b)
        if not det.passed:
            continue
        result.pairs_deterministic_pass += 1

        # Defensible pre-LLM evidence for prioritization.
        ind = contracts_independent(a, b)
        ind_score = _independence_priority_score(ind)
        shared_score, _shared_cnt = _shared_keys_score(a, b)
        delta_s = _settlement_delta_seconds(a, b)
        # Smaller delta is better → subtract hours from priority.
        delta_hours = delta_s / 3600.0
        total_priority = ind_score + shared_score - delta_hours

        # Deterministic tie-breakers: delta, lexical contract ids, versions.
        ca = f"{a.venue}:{a.contract_id}"
        cb = f"{b.venue}:{b.contract_id}"
        # Use sorted ids for tie-breaking stability regardless of input order.
        sorted_ca, sorted_cb = (ca, cb) if ca <= cb else (cb, ca)
        # For version tie-breaker, keep versions aligned with sorted ids.
        if ca <= cb:
            va, vb = a.contract_version, b.contract_version
        else:
            va, vb = b.contract_version, a.contract_version

        # Sort key: higher priority first → -priority, then smaller delta,
        # then lexical ids/versions (all deterministic).
        sort_key = (-total_priority, delta_s, sorted_ca, sorted_cb, va, vb)
        scored.append((sort_key, i, j, a, b, det))

    # Deterministic prioritization: most promising first.
    scored.sort(key=lambda x: x[0])

    approved: dict[tuple[str, str], tuple[str, float]] = {}
    for sort_key, i, j, a, b, det in scored:
        llm_res = await llm.compare(a, b)
        outcome = validate_match(a, b, det, llm_res)
        cert = build_certificate(a, b, det, llm_res, outcome)
        result.certificates.append(cert)
        if cert.status == "APPROVED":
            ka = f"{a.venue}:{a.contract_id}"
            kb = f"{b.venue}:{b.contract_id}"
            approved[(min(ka, kb), max(ka, kb))] = (
                cert.match_id, cert.llm_confidence)
    return approved, result


async def run_arbitrage_scan(
    contracts: list[NormalizedContract],
    books: dict[tuple[str, str], OrderBook],
    approved_pairs: dict[tuple[str, str], tuple[str, float]],
    fee_calc: FeeCalculator,
    mode: Mode,
    *,
    minimum_net_profit: Decimal,
    minimum_net_return: Decimal,
    maximum_settlement_delay_days: Decimal,
    cost_of_capital_annual: Decimal,
    max_quantity: Optional[Decimal] = None,
    strict_fee_freshness: Optional[bool] = None,
    evaluation_time: Optional[datetime] = None,
) -> list[Opportunity]:
    """Scan every event group for approved, independent two-leg arbitrage.

    ``strict_fee_freshness`` defaults to ``None`` = "the mode decides"
    (§98, D19): LIVE and PAPER refuse a fee schedule verified more than
    ``fees.max_schedule_age_days`` ago, HISTORICAL replay still costs it and
    the caller labels the run RESEARCH USE ONLY
    (:func:`fees.freshness.policy_for_mode`). Passing an explicit boolean
    overrides the policy, which is what the freshness tests do to prove both
    directions.
    """
    strict = freshness_enforced(mode, strict_fee_freshness)
    contracts = [with_normalized_keys(c) for c in contracts]
    groups = group_by_event(contracts)
    out: list[Opportunity] = []
    for _event, group in groups.items():
        if len({c.venue for c in group}) < 2:
            continue
        out.extend(search_event(
            group, books, fee_calc, mode, approved_pairs,
            minimum_net_profit=minimum_net_profit,
            minimum_net_return=minimum_net_return,
            maximum_settlement_delay_days=maximum_settlement_delay_days,
            cost_of_capital_annual=cost_of_capital_annual,
            max_quantity=max_quantity,
            strict_fee_freshness=strict,
            evaluation_time=evaluation_time))
    return out
