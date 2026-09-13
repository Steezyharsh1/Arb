"""Logical (TYPE B) arbitrage — V1 STUB, research-only (§22).

Data structures for logical relations between contracts are defined so
the database and matcher can record them today; the detection engine
raises NotImplementedError and is marked research-only. Extensive tests
are required before this may ever feed live execution (§22).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..core.types import Mode, NormalizedContract, Opportunity, OrderBook


class LogicalRelation(str, Enum):
    SUBSET = "A_SUBSET_B"                 # A ⊂ B  →  P(A) <= P(B)
    MUTUALLY_EXCLUSIVE = "MUTUALLY_EXCLUSIVE"
    EXHAUSTIVE_PARTITION = "SUM_TO_ONE"   # A + B + C = 1
    IMPLIES = "A_IMPLIES_B"
    IMPLIES_NOT = "A_IMPLIES_NOT_B"


@dataclass(frozen=True)
class LogicalConstraint:
    relation: LogicalRelation
    contracts: tuple[str, ...]     # venue:contract_id keys
    source: str                    # how the relation was established
    verified: bool                 # deterministic verification status


def find_logical_opportunities(
    constraints: list[LogicalConstraint],
    contracts: dict[str, NormalizedContract],
    books: dict[str, OrderBook],
    mode: Mode,
) -> list[Opportunity]:
    """Detect price inconsistencies implied by logical constraints
    (e.g. P(A) > P(B) while A ⊂ B).

    NOT IMPLEMENTED IN V1 (spec §22): research-only stub. Before
    implementation: constraint verification must be deterministic (never
    LLM-only), and the engine must produce TYPE_B opportunities that are
    ineligible for live execution until the test battery in §22 passes.
    """
    raise NotImplementedError(
        "Logical arbitrage is a v1 research-only stub (spec §3, §22). "
        "LogicalConstraint/LogicalRelation are the stable data structures "
        "for the future implementation.")
