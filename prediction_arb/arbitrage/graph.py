"""Arbitrage-graph prototype (§21); not part of the production call graph.

Nodes: (venue, contract_id, outcome). Edges: typed actions. The graph is a
future substrate for N-leg portfolio search. ``two_leg_candidates`` is tested
as a standalone helper but no production scanner consumes it; ``find_cycles``
remains a v1 stub. See Phase 9 in ``docs/ROADMAP.md``."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

from ..core.types import NormalizedContract, Side


class EdgeType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    CONVERT = "CONVERT"
    HEDGE = "HEDGE"
    SETTLEMENT = "SETTLEMENT"


@dataclass(frozen=True)
class Node:
    venue: str
    contract_id: str
    outcome: Side


@dataclass(frozen=True)
class Edge:
    src: Node
    dst: Node
    edge_type: EdgeType


@dataclass
class ArbGraph:
    nodes: set[Node] = field(default_factory=set)
    edges: list[Edge] = field(default_factory=list)
    contracts: dict[tuple[str, str], NormalizedContract] = field(default_factory=dict)

    def add_contract(self, contract: NormalizedContract) -> None:
        key = (contract.venue, contract.contract_id)
        self.contracts[key] = contract
        for side in (Side.YES, Side.NO):
            self.nodes.add(Node(contract.venue, contract.contract_id, side))

    def add_equivalence(self, a: NormalizedContract, b: NormalizedContract) -> None:
        """Record an approved match: YES(a) hedges NO(b) and vice versa.
        Both YES-sourced directions are added so the two-leg candidate
        iterator yields (a YES, b NO) AND (b YES, a NO)."""
        self.edges.append(Edge(
            Node(a.venue, a.contract_id, Side.YES),
            Node(b.venue, b.contract_id, Side.NO),
            EdgeType.HEDGE))
        self.edges.append(Edge(
            Node(b.venue, b.contract_id, Side.YES),
            Node(a.venue, a.contract_id, Side.NO),
            EdgeType.HEDGE))

    def two_leg_candidates(self) -> Iterator[tuple[NormalizedContract,
                                                   NormalizedContract]]:
        """Yield (yes_contract, no_contract) pairs from HEDGE edges."""
        seen: set[tuple] = set()
        for e in self.edges:
            if e.edge_type != EdgeType.HEDGE or e.src.outcome != Side.YES:
                continue
            key = (e.src.venue, e.src.contract_id, e.dst.venue, e.dst.contract_id)
            if key in seen:
                continue
            seen.add(key)
            a = self.contracts.get((e.src.venue, e.src.contract_id))
            b = self.contracts.get((e.dst.venue, e.dst.contract_id))
            if a and b:
                yield a, b

    def find_cycles(self, max_legs: int = 4):
        """Search for guaranteed-positive cycles/portfolios beyond two legs.

        NOT IMPLEMENTED IN V1 (spec §3, §21). The node/edge structures
        above are the stable interface for the future implementation.
        """
        raise NotImplementedError(
            "Multi-leg cycle search is a v1 stub (spec §21). Two-leg "
            "search is available via two_leg_candidates().")
