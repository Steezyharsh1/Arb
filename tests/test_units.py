"""Unit tests (§74): normalization, matching, fees, walking, arbitrage,
independence, risk limits, order types, settlement timing."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from prediction_arb.core.types import (
    FeeDataError,
    Independence,
    Mode,
    OpportunityStatus,
    OrderBook,
    PriceLevel,
)
from .conftest import make_book, make_contract


class TestOrderBookWalking:
    def test_walk_consumes_levels_in_order(self):
        from prediction_arb.orderbooks.orderbook import walk_buy
        book = make_book("kalshi", "X",
                         asks=[("0.40", "10"), ("0.42", "20"), ("0.45", "30")])
        w = walk_buy(book, Decimal("25"))
        assert w.fully_filled
        assert w.filled_quantity == Decimal("25")
        # 10*0.40 + 15*0.42 = 4.00 + 6.30 = 10.30
        assert w.total_cost == Decimal("10.30")
        assert w.weighted_average_price == Decimal("10.30") / Decimal("25")

    def test_walk_partial_when_book_thin(self):
        from prediction_arb.orderbooks.orderbook import walk_buy
        book = make_book("kalshi", "X", asks=[("0.40", "5")])
        w = walk_buy(book, Decimal("100"))
        assert not w.fully_filled
        assert w.filled_quantity == Decimal("5")

    def test_staleness(self):
        from prediction_arb.orderbooks.orderbook import is_stale
        fresh = make_book("kalshi", "X", asks=[("0.5", "1")])
        old = make_book("kalshi", "X", asks=[("0.5", "1")], age_seconds=5)
        assert not is_stale(fresh, 1000)
        assert is_stale(old, 1000)
        no_ts = OrderBook(venue="kalshi", contract_id="X")
        assert is_stale(no_ts, 1000)


class TestKalshiBookParsing:
    def test_orderbook_fp_parsing_synthesizes_asks(self):
        from prediction_arb.adapters.kalshi import KalshiAdapter
        adapter = KalshiAdapter(rest_base="https://api.elections.kalshi.com/trade-api/v2")
        data = {"orderbook_fp": {
            "yes_dollars": [["0.16", "44.54"], ["0.25", "53.00"]],
            "no_dollars": [["0.62", "95.00"], ["0.65", "17.97"]]}}
        book = adapter.parse_order_book("T", data)
        # YES bids sorted desc
        assert book.bids[0].price == Decimal("0.25")
        # YES asks = 1 - NO bid, best (lowest) first: 1-0.65=0.35
        assert book.asks[0].price == Decimal("0.35")
        assert book.asks[0].quantity == Decimal("17.97")
        assert book.asks[1].price == Decimal("0.38")


class TestFees:
    def test_kalshi_quadratic(self, fee_calc):
        # 100 contracts @ 0.50: 0.07*100*0.25 = 1.75
        fee = fee_calc.trade_fee("kalshi", Decimal("0.50"), Decimal("100"))
        assert fee == Decimal("1.75")

    def test_kalshi_fee_lower_at_extremes(self, fee_calc):
        mid = fee_calc.trade_fee("kalshi", Decimal("0.50"), Decimal("100"))
        edge = fee_calc.trade_fee("kalshi", Decimal("0.10"), Decimal("100"))
        assert edge < mid

    def test_forecastex_per_contract(self, fee_calc):
        fee = fee_calc.trade_fee("forecast_ex", Decimal("0.50"),
                                 Decimal("100"))
        assert fee == Decimal("1.00")

    def test_missing_schedule_raises(self, fee_calc):
        with pytest.raises(FeeDataError):
            fee_calc.trade_fee("webull", Decimal("0.50"), Decimal("10"))
        with pytest.raises(FeeDataError):
            fee_calc.trade_fee("unknown_venue", Decimal("0.50"), Decimal("10"))


class TestIndependence:
    def test_matrix(self):
        from prediction_arb.provenance.independence import is_independent
        assert is_independent("kalshi", "forecast_ex") == Independence.INDEPENDENT
        assert is_independent("kalshi", "webull") == Independence.SAME_VENUE
        assert is_independent("kalshi", "moomoo") == Independence.SAME_VENUE
        assert is_independent("webull", "moomoo") == Independence.SAME_VENUE
        assert is_independent("kalshi", "kalshi") == Independence.SAME_VENUE
        assert is_independent("kalshi", "nope") == Independence.UNKNOWN

    def test_unknown_never_eligible(self):
        from prediction_arb.provenance.independence import (
            eligible_for_guaranteed_arb,
        )
        assert not eligible_for_guaranteed_arb(Independence.UNKNOWN)
        assert not eligible_for_guaranteed_arb(Independence.LIKELY_INDEPENDENT)
        assert eligible_for_guaranteed_arb(Independence.INDEPENDENT)


class TestDeterministicMatcher:
    def test_pass_on_identical(self):
        from prediction_arb.matching.deterministic_matcher import (
            deterministic_match,
        )
        a = make_contract(venue="kalshi")
        b = make_contract(venue="forecast_ex")
        assert deterministic_match(a, b).passed

    def test_reject_on_threshold_mismatch(self):
        from prediction_arb.matching.deterministic_matcher import (
            deterministic_match,
        )
        a = make_contract(venue="kalshi", threshold=Decimal("4.25"),
                          threshold_op=">=", units="%")
        b = make_contract(venue="forecast_ex", threshold=Decimal("4.50"),
                          threshold_op=">=", units="%")
        assert not deterministic_match(a, b).passed

    def test_reject_on_settlement_gap(self):
        from prediction_arb.matching.deterministic_matcher import (
            deterministic_match,
        )
        a = make_contract(venue="kalshi")
        b = make_contract(
            venue="forecast_ex",
            settlement=a.settlement_time + timedelta(days=2))
        assert not deterministic_match(a, b).passed

    def test_reject_on_payout_mismatch(self):
        from prediction_arb.matching.deterministic_matcher import (
            deterministic_match,
        )
        a = make_contract(venue="kalshi")
        b = make_contract(venue="forecast_ex", payout=Decimal("100"))
        assert not deterministic_match(a, b).passed


class TestLLMValidation:
    def test_malformed_json_rejected(self):
        from prediction_arb.matching.llm_matcher import validate_llm_output
        assert not validate_llm_output("not json").valid
        assert not validate_llm_output('{"match": true}').valid

    def test_confidence_bounds(self):
        from prediction_arb.matching.llm_matcher import validate_llm_output
        base = ('{"match": true, "confidence": %s, "reasoning_summary": "x",'
                '"event_identity": true, "outcome_identity": true,'
                '"settlement_identity": true, "resolution_identity": true,'
                '"threshold_identity": true, "time_identity": true,'
                '"payout_identity": true, "ambiguities": []}')
        assert validate_llm_output(base % "0.99").valid
        assert not validate_llm_output(base % "1.5").valid

    def test_contradiction_rejected(self):
        from prediction_arb.matching.llm_matcher import validate_llm_output
        raw = ('{"match": true, "confidence": 0.99, "reasoning_summary": "x",'
               '"event_identity": true, "outcome_identity": false,'
               '"settlement_identity": true, "resolution_identity": true,'
               '"threshold_identity": true, "time_identity": true,'
               '"payout_identity": true, "ambiguities": []}')
        assert not validate_llm_output(raw).valid

    def test_no_llm_configured_is_no_match(self):
        import asyncio
        from prediction_arb.matching.llm_matcher import LLMMatcher
        m = LLMMatcher(client=None)
        a = make_contract(venue="kalshi")
        b = make_contract(venue="forecast_ex")
        res = asyncio.run(m.compare(a, b))
        assert not res.match and not res.valid


class TestRiskLimits:
    def test_limits_block(self):
        from prediction_arb.risk.limits import (
            RiskLimits, RiskState, check_trade_against_limits,
        )
        limits = RiskLimits.from_config({})
        state = RiskState()
        # per-trade limit default 100
        v = check_trade_against_limits(limits, state, Decimal("150"),
                                       ["kalshi", "forecast_ex"], "evt")
        assert any("max_capital_per_trade" in x for x in v)
        # daily loss
        state.daily_pnl = Decimal("-100")
        v = check_trade_against_limits(limits, state, Decimal("10"),
                                       ["kalshi"], "evt")
        assert any("max_daily_loss" in x for x in v)


class TestSettlementTiming:
    def test_lockup_and_cost(self):
        from prediction_arb.normalization.settlement import (
            capital_lockup_days, settlement_delay_cost,
        )
        a = make_contract(venue="kalshi")
        b = make_contract(venue="forecast_ex")
        lockup = capital_lockup_days([a, b])
        assert lockup is not None and lockup > 0
        cost = settlement_delay_cost(Decimal("100"), lockup, Decimal("0.05"))
        assert cost is not None and cost >= 0

    def test_unknown_venue_lockup_none(self):
        from prediction_arb.normalization.settlement import (
            capital_lockup_days,
        )
        c = make_contract(venue="mystery")
        assert capital_lockup_days([c]) is None


class TestOrderTypes:
    def test_kalshi_metadata(self):
        from prediction_arb.adapters.kalshi import KalshiAdapter
        a = KalshiAdapter(rest_base="https://api.elections.kalshi.com/trade-api/v2")
        ots = a.get_supported_order_types()
        assert ots.limit and ots.verified
        ms = a.get_market_microstructure()
        assert ms.type.value == "CLOB"

    def test_forecastex_no_market_orders(self):
        from prediction_arb.adapters.forecast_ex import ForecastExAdapter
        a = ForecastExAdapter()
        ots = a.get_supported_order_types()
        assert not ots.market and ots.limit
        assert a.get_market_microstructure().type.value == "PAIRED_BID"


class TestNormalization:
    def test_event_grouping(self):
        from prediction_arb.normalization.contracts import (
            group_by_event, with_normalized_keys,
        )
        a = with_normalized_keys(make_contract(venue="kalshi",
                                               event="Fed Raises rates?"))
        b = with_normalized_keys(make_contract(venue="forecast_ex",
                                               event="fed raises RATES?"))
        groups = group_by_event([a, b])
        assert len(groups) == 1

    def test_venue_aliases(self):
        from prediction_arb.normalization.venues import canonical_venue
        assert canonical_venue("ForecastEx") == "forecast_ex"
        assert canonical_venue("IBKR") == "forecast_ex"
        assert canonical_venue("Futu") == "moomoo"
        assert canonical_venue("???") is None
