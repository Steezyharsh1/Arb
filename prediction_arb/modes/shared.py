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
from ..core.types import Mode, NormalizedContract, Opportunity, OrderBook
from ..fees.calculator import FeeCalculator
from ..fees.freshness import freshness_enforced
from ..matching.deterministic_matcher import deterministic_match
from ..matching.llm_matcher import LLMMatcher
from ..matching.match_certificates import MatchCertificate, build_certificate
from ..matching.rules_validator import validate_match
from ..normalization.contracts import (
    candidate_pairs,
    group_by_event,
    groups_from_pairs,
    with_normalized_keys,
)


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
    sorted contract-key tuples to (match_id, confidence)."""
    result = ScanResult(markets_scanned=len(contracts))
    contracts = [with_normalized_keys(c) for c in contracts]
    # Retrieval (§96): pairwise, bounded, non-transitive. Only these pairs
    # reach the deterministic matcher; proposal is not approval.
    pairs = candidate_pairs(contracts)
    result.events_grouped = len(groups_from_pairs(contracts, pairs))
    # One budget window per scan cycle (§97): a cycle can spend at most
    # `max_comparisons_per_cycle` NEW LLM calls; excess pairs stay REVIEW.
    llm.begin_cycle()

    approved: dict[tuple[str, str], tuple[str, float]] = {}
    for i, j in pairs:
        a, b = contracts[i], contracts[j]
        result.pairs_considered += 1
        det = deterministic_match(a, b)
        if not det.passed:
            continue
        result.pairs_deterministic_pass += 1
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
