"""Arbitrage engine tests: sizing, profitability math, reproducibility,
multi-venue search, graph candidates."""
from __future__ import annotations

import asyncio
from decimal import Decimal

from prediction_arb.arbitrage.two_leg import (
    evaluate_two_leg,
    find_max_profit_quantity,
)
from prediction_arb.core.types import (
    Independence,
    Mode,
    OpportunityStatus,
)
from .conftest import make_book, make_contract


class TestSizing:
    def test_marginal_pricing_stops_at_payout(self):
        yes = make_book("kalshi", "Y",
                        asks=[("0.40", "10"), ("0.55", "100")])
        no = make_book("forecast_ex", "N",
                       asks=[("0.50", "10"), ("0.50", "100")])
        # first 10: 0.90 < 1.00 OK; next: 0.55+0.50=1.05 >= 1.00 stop
        q = find_max_profit_quantity(yes, no, Decimal("1.00"))
        assert q == Decimal("10")

    def test_zero_when_no_edge(self):
        yes = make_book("kalshi", "Y", asks=[("0.60", "100")])
        no = make_book("forecast_ex", "N", asks=[("0.45", "100")])
        assert find_max_profit_quantity(yes, no, Decimal("1.00")) == 0


class TestProfitMath:
    def test_profitable_opportunity_trade_status(self, fee_calc):
        yes_c = make_contract(venue="kalshi", contract_id="Y")
        no_c = make_contract(venue="forecast_ex", contract_id="N")
        yes = make_book("kalshi", "Y", asks=[("0.40", "500")])
        no = make_book("forecast_ex", "N", asks=[("0.50", "500")])
        op = evaluate_two_leg(yes_c, no_c, yes, no, fee_calc, Mode.PAPER,
                              independence=Independence.INDEPENDENT)
        assert op is not None
        assert op.status == OpportunityStatus.TRADE
        assert op.quantity == Decimal("500")
        # gross cost 0.90*500=450; payout 500; fees: kalshi quad on 0.40
        # (0.07*500*0.40*0.60=8.40) + fx 0.01*500=5 → net ≈ 500-450-13.4-delay
        assert op.net_profit > Decimal("30")
        assert op.capital_required > Decimal("450")
        assert op.return_on_capital > 0

    def test_reproducible(self, fee_calc):
        yes_c = make_contract(venue="kalshi", contract_id="Y")
        no_c = make_contract(venue="forecast_ex", contract_id="N")
        yes = make_book("kalshi", "Y", asks=[("0.40", "500")])
        no = make_book("forecast_ex", "N", asks=[("0.50", "500")])
        a = evaluate_two_leg(yes_c, no_c, yes, no, fee_calc, Mode.HISTORICAL,
                             independence=Independence.INDEPENDENT)
        b = evaluate_two_leg(yes_c, no_c, yes, no, fee_calc, Mode.HISTORICAL,
                             independence=Independence.INDEPENDENT)
        assert a is not None and b is not None
        assert (a.net_profit, a.quantity, a.gross_cost, a.total_fees) == \
               (b.net_profit, b.quantity, b.gross_cost, b.total_fees)


class TestMultiVenueSearch:
    def test_search_requires_approved_certificate(self, fee_calc):
        from prediction_arb.arbitrage.optimizer import search_event
        yes_c = make_contract(venue="kalshi", contract_id="Y")
        no_c = make_contract(venue="forecast_ex", contract_id="N")
        books = {("kalshi", "Y"): make_book("kalshi", "Y",
                                            asks=[("0.40", "100")]),
                 ("forecast_ex", "N"): make_book("forecast_ex", "N",
                                                 asks=[("0.50", "100")])}
        # no approved pairs → no opportunities (Principle B)
        ops = search_event([yes_c, no_c], books, fee_calc, Mode.PAPER, {})
        assert ops == []
        # with approval → found
        ka, kb = "kalshi:Y", "forecast_ex:N"
        approved = {(min(ka, kb), max(ka, kb)): ("m1", 0.99)}
        ops = search_event([yes_c, no_c], books, fee_calc, Mode.PAPER,
                           approved)
        assert len(ops) >= 1
        assert ops[0].match_id == "m1"

    def test_same_venue_pairs_never_evaluated(self, fee_calc):
        from prediction_arb.arbitrage.optimizer import search_event
        a = make_contract(venue="kalshi", contract_id="A")
        b = make_contract(venue="webull", contract_id="B")
        books = {("kalshi", "A"): make_book("kalshi", "A",
                                            asks=[("0.30", "100")]),
                 ("webull", "B"): make_book("webull", "B",
                                            asks=[("0.30", "100")])}
        ka, kb = "kalshi:A", "webull:B"
        approved = {(min(ka, kb), max(ka, kb)): ("m1", 0.99)}
        ops = search_event([a, b], books, fee_calc, Mode.PAPER, approved)
        assert ops == []   # SAME_VENUE filtered before evaluation


class TestGraph:
    def test_two_leg_candidates(self):
        from prediction_arb.arbitrage.graph import ArbGraph
        g = ArbGraph()
        a = make_contract(venue="kalshi", contract_id="A")
        b = make_contract(venue="forecast_ex", contract_id="B")
        g.add_contract(a)
        g.add_contract(b)
        g.add_equivalence(a, b)
        pairs = list(g.two_leg_candidates())
        assert len(pairs) == 2   # (A yes, B no) and (B yes, A no)


class TestMatchingPipeline:
    def test_full_pipeline_no_llm_gives_review_not_approved(self):
        """Without an LLM, matches end in REVIEW — never auto-APPROVED."""
        from prediction_arb.matching.llm_matcher import LLMMatcher
        from prediction_arb.modes.shared import run_matching
        a = make_contract(venue="kalshi", contract_id="A")
        b = make_contract(venue="forecast_ex", contract_id="B")
        approved, result = asyncio.run(
            run_matching([a, b], LLMMatcher(client=None)))
        assert approved == {}
        assert result.pairs_deterministic_pass == 1
        assert all(c.status in ("REVIEW", "REJECTED")
                   for c in result.certificates)

    def test_pipeline_with_mock_llm_approves(self):
        from prediction_arb.matching.llm_matcher import LLMMatcher
        from prediction_arb.modes.shared import run_matching

        async def good_llm(prompt: str) -> str:
            return ('{"match": true, "confidence": 0.998, '
                    '"reasoning_summary": "identical", '
                    '"event_identity": true, "outcome_identity": true, '
                    '"settlement_identity": true, "resolution_identity": true, '
                    '"threshold_identity": true, "time_identity": true, '
                    '"payout_identity": true, "ambiguities": []}')

        a = make_contract(venue="kalshi", contract_id="A")
        b = make_contract(venue="forecast_ex", contract_id="B")
        approved, result = asyncio.run(
            run_matching([a, b], LLMMatcher(client=good_llm,
                                            model_version="mock-1")))
        assert len(approved) == 1
        assert result.certificates[0].status == "APPROVED"
