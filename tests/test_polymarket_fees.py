"""Polymarket US published fee arithmetic plus fail-closed production status.

Every expectation in this file comes from the venue's own fee documentation,
re-read 2026-10-10 (see ``docs/FEE_READINESS.md`` and
``docs/VENUE_RESEARCH.md``):

* https://docs.polymarket.us/fees
* https://docs.polymarket.us/fees#fee-rules
* https://docs.polymarket.us/fees#fee-schedule-by-price
* https://docs.polymarket.us/fees#faq

None of it is live-tested. The production schedule is deliberately
``INCOMPLETE`` for both trade and settlement: this file uses a **test-only
synthetic schedule** to check the standard-formula arithmetic, and separately
asserts that the production calculator fails closed. Passing these unit tests
does not make the production schedule execution-ready.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from prediction_arb.core.types import FeeDataError
from prediction_arb.fees.calculator import (
    FillSlice,
    ROUNDING_HALF_EVEN,
    round_cents,
)
from prediction_arb.orderbooks.orderbook import walk_buy

VENUE = "polymarket_us"

# Transcribed from the current standard "Fee Schedule by Price" table
# (https://docs.polymarket.us/fees#standard-fee-schedule-by-price, read
# 2026-10-10). Row i (0-based) is price $(i+1)/100 on a 100-contract
# order; 99 rows cover the documented $0.01-$0.99 range. These are reference
# values tested with a synthetic calculator, never a production fee quote.
PUBLISHED_TAKER_100_LOT = (
    "0.07 0.14 0.20 0.27 0.33 0.39 0.45 0.51 0.57 0.63 "
    "0.68 0.73 0.79 0.84 0.89 0.93 0.98 1.03 1.07 1.11 "
    "1.15 1.19 1.23 1.27 1.30 1.34 1.37 1.40 1.43 1.46 "
    "1.49 1.51 1.54 1.56 1.58 1.60 1.62 1.64 1.65 1.67 "
    "1.68 1.69 1.70 1.71 1.72 1.73 1.73 1.73 1.74 1.74 "
    "1.74 1.73 1.73 1.73 1.72 1.71 1.70 1.69 1.68 1.67 "
    "1.65 1.64 1.62 1.60 1.58 1.56 1.54 1.51 1.49 1.46 "
    "1.43 1.40 1.37 1.34 1.30 1.27 1.23 1.19 1.15 1.11 "
    "1.07 1.03 0.98 0.93 0.89 0.84 0.79 0.73 0.68 0.63 "
    "0.57 0.51 0.45 0.39 0.33 0.27 0.20 0.14 0.07"
)
PUBLISHED_MAKER_100_LOT = (
    "0.01 0.02 0.04 0.05 0.06 0.07 0.08 0.09 0.10 0.11 "
    "0.12 0.13 0.14 0.15 0.16 0.17 0.18 0.18 0.19 0.20 "
    "0.21 0.21 0.22 0.23 0.23 0.24 0.25 0.25 0.26 0.26 "
    "0.27 0.27 0.28 0.28 0.28 0.29 0.29 0.29 0.30 0.30 "
    "0.30 0.30 0.31 0.31 0.31 0.31 0.31 0.31 0.31 0.31 "
    "0.31 0.31 0.31 0.31 0.31 0.31 0.31 0.30 0.30 0.30 "
    "0.30 0.29 0.29 0.29 0.28 0.28 0.28 0.27 0.27 0.26 "
    "0.26 0.25 0.25 0.24 0.23 0.23 0.22 0.21 0.21 0.20 "
    "0.19 0.18 0.18 0.17 0.16 0.15 0.14 0.13 0.12 0.11 "
    "0.10 0.09 0.08 0.07 0.06 0.05 0.04 0.02 0.01"
)

def _published(row: str) -> list[Decimal]:
    values = [Decimal(v) for v in row.split()]
    # Guards the transcription itself: a slip here would silently compare
    # the wrong price against the wrong published number.
    assert len(values) == 99, f"expected 99 published rows, got {len(values)}"
    return values


@pytest.fixture
def polymarket_calc():
    """Test-only complete calculator for published standard arithmetic.

    Production config deliberately remains INCOMPLETE; tests of the published
    formula must not accidentally promote the runtime schedule.
    """
    from prediction_arb.fees.calculator import FeeCalculator

    return FeeCalculator.from_config({"schedules": [{
        "venue": VENUE,
        "version": "test-only-polymarket-standard-reference-2026-10-07",
        "effective_date": "2026-10-07",
        "verified_at": "2026-10-10",
        "verification_status": "VERIFIED",
        "documentation_tested": True,
        "live_test_status": "UNAVAILABLE",
        "source": "https://docs.polymarket.us/fees; test-only reference schedule",
        "rounding": ROUNDING_HALF_EVEN,
        "order_fee_cap": "cumulative_exact_rounded",
        "trade_calculation_status": "COMPLETE",
        "settlement_calculation_status": "INCOMPLETE",
        "rebate_policy": (
            "Prior-calendar-month taker amount-at-risk, weekly payout; "
            "not applied per order."),
        "volume_rebate_tiers": [
            {"threshold": "250000", "rebate": "0.10"},
            {"threshold": "1000000", "rebate": "0.25"},
            {"threshold": "10000000", "rebate": "0.50"},
        ],
        "fees": [
            {"fee_type": "standard_taker_reference",
             "multiplier": "0.0695", "applies_to": "taker"},
            {"fee_type": "maker_rebate_reference",
             "multiplier": "-0.0125", "applies_to": "maker"},
        ],
    }]})


@pytest.fixture
def polymarket_style_calc():
    """Two synthetic quadratic venues and a two-level walk on each side.

    Synthetic on purpose: this fixture isolates *which prices the engine
    charges fees on*. Real venue schedules stay in ``config/fees.yaml`` and
    are exercised by the fixtures above.
    """
    from prediction_arb.fees.calculator import FeeCalculator

    from .conftest import make_book, make_contract

    def schedule(venue: str) -> dict:
        return {
            "venue": venue,
            "version": "test-quadratic-v1",
            "effective_date": "2020-01-01",
            "verified_at": "2026-09-13",
            "verification_status": "VERIFIED",
            "documentation_tested": True,
            "source": "synthetic deterministic test fixture",
            "rounding": ROUNDING_HALF_EVEN,
            "order_fee_cap": "cumulative_exact_rounded",
            "trade_calculation_status": "COMPLETE",
            "settlement_calculation_status": "COMPLETE",
            "fees": [
                {"fee_type": "quadratic", "multiplier": "0.0695",
                 "applies_to": "taker"},
                {"fee_type": "settlement", "fixed_amount": "0",
                 "applies_to": "settlement"},
            ],
        }

    calc = FeeCalculator.from_config(
        {"schedules": [schedule("venue_a"), schedule("venue_b")]})
    yes_c = make_contract(venue="venue_a", contract_id="A-1",
                          underlying="a_dcm")
    no_c = make_contract(venue="venue_b", contract_id="B-1",
                         underlying="b_dcm")
    yes_book = make_book("venue_a", "A-1",
                         asks=[("0.30", "50"), ("0.40", "50")])
    no_book = make_book("venue_b", "B-1",
                        asks=[("0.35", "50"), ("0.45", "50")])
    return calc, yes_c, no_c, yes_book, no_book


class TestPublishedFeeTable:
    """The engine must reproduce the venue's own published numbers."""

    def test_transcription_is_complete_and_symmetric(self):
        taker = _published(PUBLISHED_TAKER_100_LOT)
        maker = _published(PUBLISHED_MAKER_100_LOT)
        # Documented: "Fees are symmetric around p = 0.50".
        assert taker == taker[::-1]
        assert maker == maker[::-1]
        # Documented maximum at p = $0.50 on a 100-lot: $1.74 / $0.31.
        assert taker[49] == Decimal("1.74")
        assert maker[49] == Decimal("0.31")

    def test_taker_column_matches_all_99_published_rows(self, polymarket_calc):
        for i, published in enumerate(_published(PUBLISHED_TAKER_100_LOT)):
            price = Decimal(i + 1) / Decimal(100)
            got = polymarket_calc.trade_fee(
                VENUE, price, Decimal("100"), is_taker=True)
            assert got == published, (
                f"taker fee at p={price} on 100 contracts: engine {got}, "
                f"venue publishes {published}")

    def test_maker_column_matches_all_99_published_rows(self, polymarket_calc):
        for i, published in enumerate(_published(PUBLISHED_MAKER_100_LOT)):
            price = Decimal(i + 1) / Decimal(100)
            got = polymarket_calc.trade_fee(
                VENUE, price, Decimal("100"), is_taker=False)
            # The maker rebate is a credit, so the engine returns it negative
            # while the venue's table shows the amount "Maker Receives".
            assert got == -published, (
                f"maker rebate at p={price} on 100 contracts: engine {got}, "
                f"venue publishes -{published}")

    def test_documented_examples(self, polymarket_calc):
        """The five worked examples in the fee FAQ, to the cent."""
        cases = [
            # (price, quantity, taker charge, maker credit)
            (Decimal("0.10"), Decimal("1000"), Decimal("6.26"), Decimal("1.12")),
            (Decimal("0.65"), Decimal("1000"), Decimal("15.81"), Decimal("2.84")),
            (Decimal("0.30"), Decimal("1000"), Decimal("14.60"), Decimal("2.62")),
            (Decimal("0.90"), Decimal("1000"), Decimal("6.26"), Decimal("1.12")),
            (Decimal("0.50"), Decimal("1000"), Decimal("17.38"), Decimal("3.12")),
        ]
        for price, qty, taker, maker in cases:
            assert polymarket_calc.trade_fee(
                VENUE, price, qty, is_taker=True) == taker
            assert polymarket_calc.trade_fee(
                VENUE, price, qty, is_taker=False) == -maker


class TestBankersRounding:
    """The venue's FAQ gives these two examples verbatim."""

    def test_faq_examples(self):
        assert round_cents(Decimal("0.025"), ROUNDING_HALF_EVEN) == \
            Decimal("0.02")   # "$0.025 rounds to $0.02 (down to even)"
        assert round_cents(Decimal("0.035"), ROUNDING_HALF_EVEN) == \
            Decimal("0.04")   # "$0.035 rounds to $0.04 (up to even)"

    def test_rounding_is_symmetric_for_credits(self):
        # A maker rebate is negative; banker's rounding must not flip sign
        # or round toward zero.
        assert round_cents(Decimal("-0.025"), ROUNDING_HALF_EVEN) == \
            Decimal("-0.02")
        assert round_cents(Decimal("-0.3125"), ROUNDING_HALF_EVEN) == \
            Decimal("-0.31")

    def test_published_examples_that_require_half_even(self, polymarket_calc):
        """Current worked examples include exact half-cent standard fees."""
        cases = [
            # (price, quantity, exact fee, banker's-rounded fee)
            ("0.10", "1000", "6.255", "6.26"),
            ("0.30", "1000", "14.595", "14.60"),
            ("0.50", "1000", "17.375", "17.38"),
        ]
        for price, quantity, exact, rounded in cases:
            p, c = Decimal(price), Decimal(quantity)
            raw = Decimal("0.0695") * c * p * (Decimal("1") - p)
            assert raw == Decimal(exact)
            assert polymarket_calc.trade_fee(VENUE, p, c) == Decimal(rounded)

        # The maker's midpoint 1000-lot is the opposite tie direction:
        # 3.125 rounds to 3.12, so the returned credit is -$3.12.
        assert polymarket_calc.trade_fee(
            VENUE, Decimal("0.50"), Decimal("1000"),
            is_taker=False) == Decimal("-3.12")

    def test_documented_table_tennis_and_combo_reference_arithmetic(self):
        """Check direct-page variants without exposing them as production fees."""
        # Table Tennis changes only the taker coefficient; its midpoint
        # 1000-lot fee is $25.00 and maker rebate remains $3.125 -> $3.12.
        tt_exact = Decimal("0.10") * Decimal("1000") * Decimal("0.50") * Decimal("0.50")
        maker_exact = Decimal("-0.0125") * Decimal("1000") * Decimal("0.50") * Decimal("0.50")
        assert round_cents(tt_exact, ROUNDING_HALF_EVEN) == Decimal("25.00")
        assert round_cents(maker_exact, ROUNDING_HALF_EVEN) == Decimal("-3.12")

        # Combo formula applies to the combo as a whole (not each component
        # leg) and has no maker rebate. These tests are reference arithmetic,
        # not a calculator path; production combos remain unsupported/blocked.
        def combo_fee(price: Decimal, quantity: Decimal) -> Decimal:
            one_minus = Decimal("1") - price
            return quantity * price * (
                Decimal("0.0695") * one_minus
                + Decimal("0.06") * one_minus ** 4)

        low = combo_fee(Decimal("0.10"), Decimal("1000"))
        mid = combo_fee(Decimal("0.50"), Decimal("1000"))
        assert low == Decimal("10.19160000000000000000000000")
        assert round_cents(low, ROUNDING_HALF_EVEN) == Decimal("10.19")
        assert mid == Decimal("19.25000000000000000000000000")
        assert round_cents(mid, ROUNDING_HALF_EVEN) == Decimal("19.25")

    def test_production_schedule_declares_reference_but_stays_blocked(self,
                                                                      fee_calc):
        from pathlib import Path
        import yaml

        schedule = fee_calc.schedules[VENUE]
        raw = yaml.safe_load((Path(__file__).resolve().parents[1]
                              / "config" / "fees.yaml").read_text())
        entry = next(item for item in raw["schedules"]
                     if item["venue"] == VENUE)
        reference = entry["published_reference"]
        assert reference["standard_taker_theta"] == "0.0695"
        assert reference["table_tennis_taker_theta"] == "0.10"
        assert reference["maker_rebate_theta"] == "-0.0125"
        assert reference["combo_taker_formula"] == (
            "C × p × [0.0695 × (1-p) + 0.06 × (1-p)^4]")
        assert reference["combo_maker_rebate"] == "none"
        assert schedule.verified_at.isoformat() == "2026-10-10"
        assert schedule.effective_date.isoformat() == "2026-10-07"
        assert schedule.rounding == ROUNDING_HALF_EVEN
        assert schedule.order_fee_cap == "cumulative_exact_rounded"
        assert schedule.trade_calculation_status == "INCOMPLETE"
        assert schedule.settlement_calculation_status == "INCOMPLETE"
        assert schedule.live_test_status == "UNAVAILABLE"
        assert [rule.multiplier for rule in schedule.rules] == [
            Decimal("0.0695"), Decimal("-0.0125")]
        with pytest.raises(FeeDataError, match="FEE_DATA_INCOMPLETE"):
            fee_calc.trade_fee(VENUE, Decimal("0.50"), Decimal("100"))

    def test_settlement_fee_still_fails_closed(self, fee_calc):
        with pytest.raises(FeeDataError, match="FEE_DATA_INCOMPLETE"):
            fee_calc.settlement_fee(VENUE, Decimal("100"))


class TestOrderLevelCap:
    """The aggressive-order cap from the fee rules (P8)."""

    def test_cap_reduces_total_to_rounded_cumulative_exact(
            self, polymarket_calc):
        # Two 1-contract fills at $0.50: exact fee is 0.0695*1*0.25 = 0.017375
        # each, which banker's-rounds UP to $0.02 apiece ($0.04), while the
        # cumulative exact fee is $0.03. The cap therefore trims the second
        # fill — and only ever reduces a charge.
        breakdown = polymarket_calc.trade_fee_fills(VENUE, [
            FillSlice(price=Decimal("0.50"), quantity=Decimal("1")),
            FillSlice(price=Decimal("0.50"), quantity=Decimal("1")),
        ], is_taker=True)
        assert breakdown.charged_per_fill == (Decimal("0.02"), Decimal("0.01"))
        assert breakdown.rounded_sum == Decimal("0.04")
        assert breakdown.cap == Decimal("0.03")
        assert breakdown.total == Decimal("0.03")
        assert breakdown.adjustment == Decimal("0.01")
        assert breakdown.was_capped is True

    def test_cap_never_increases_a_fill_charge(self, polymarket_calc):
        uncapped = polymarket_calc.trade_fee_fills(VENUE, [
            FillSlice(price=Decimal("0.50"), quantity=Decimal("1")),
            FillSlice(price=Decimal("0.50"), quantity=Decimal("1")),
            FillSlice(price=Decimal("0.50"), quantity=Decimal("1")),
        ])
        assert all(
            charged <= round_cents(
                Decimal("0.0695") * sl.quantity * sl.price
                * (Decimal("1") - sl.price), ROUNDING_HALF_EVEN)
            for charged, sl in zip(uncapped.charged_per_fill, [
                FillSlice(Decimal("0.50"), Decimal("1"))] * 3))
        assert uncapped.total <= uncapped.rounded_sum
        assert uncapped.total <= uncapped.cap

    def test_cap_binds_only_when_rounding_overshoots(self, polymarket_calc):
        # A single large fill has nothing to cap: rounded == exact-rounded.
        single = polymarket_calc.trade_fee_fills(VENUE, [
            FillSlice(price=Decimal("0.50"), quantity=Decimal("1000"))])
        assert single.adjustment == Decimal("0")
        assert single.was_capped is False
        assert single.total == Decimal("17.38")

    def test_maker_rebates_are_never_capped(self, polymarket_calc):
        """Documented: "Maker rebates are computed per fill, independently."
        """
        maker = polymarket_calc.trade_fee_fills(VENUE, [
            FillSlice(price=Decimal("0.50"), quantity=Decimal("100")),
            FillSlice(price=Decimal("0.50"), quantity=Decimal("100")),
        ], is_taker=False)
        assert maker.cap is None
        assert maker.adjustment == Decimal("0")
        # 0.0125 x 100 x 0.50 x 0.50 = 0.3125 -> banker's rounds to $0.31
        # per fill, credited twice with no order-level cap.
        assert maker.charged_per_fill == (Decimal("-0.31"), Decimal("-0.31"))
        assert maker.total == Decimal("-0.62")

    def test_running_total_never_exceeds_the_cap_at_any_prefix(
            self, polymarket_calc):
        slices = [FillSlice(price=Decimal("0.50"), quantity=Decimal("1"))
                  for _ in range(7)]
        b = polymarket_calc.trade_fee_fills(VENUE, slices)
        running_exact = Decimal("0")
        running_charged = Decimal("0")
        for charged, sl in zip(b.charged_per_fill, slices):
            running_exact += Decimal("0.0695") * sl.quantity * sl.price * (
                Decimal("1") - sl.price)
            running_charged += charged
            assert running_charged <= round_cents(
                running_exact, ROUNDING_HALF_EVEN)
        assert b.total == round_cents(b.exact_total, ROUNDING_HALF_EVEN)


class TestRebatesNeverApplied:
    """Volume rebates are recorded as evidence but never reduce a fee."""

    def test_tiers_are_recorded(self, polymarket_calc):
        tiers = polymarket_calc.schedules[VENUE].volume_rebate_tiers
        assert [(t.threshold, t.rebate_fraction) for t in tiers] == [
            (Decimal("250000"), Decimal("0.10")),
            (Decimal("1000000"), Decimal("0.25")),
            (Decimal("10000000"), Decimal("0.50")),
        ]
        assert "weekly" in polymarket_calc.schedules[VENUE].rebate_policy

    def test_high_volume_does_not_reduce_the_charged_fee(
            self, polymarket_calc):
        from prediction_arb.fees.calculator import FeeContext
        plain = polymarket_calc.trade_fee(VENUE, Decimal("0.50"),
                                          Decimal("1000"))
        with_context = polymarket_calc.trade_fee(
            VENUE, Decimal("0.50"), Decimal("1000"),
            context=FeeContext(
                contract_type="$1 event contract",
                access_route="direct Polymarket US API",
                prior_month_taker_volume=Decimal("25000000")))
        assert with_context == plain == Decimal("17.38")


class TestFailClosed:
    """Fee inputs the engine refuses to guess about."""

    def test_empty_fill_list_is_refused(self, polymarket_calc):
        with pytest.raises(FeeDataError, match="no fill slices"):
            polymarket_calc.trade_fee_fills(VENUE, [])

    def test_non_positive_quantity_is_refused(self, polymarket_calc):
        with pytest.raises(FeeDataError, match="non-positive quantity"):
            polymarket_calc.trade_fee_fills(
                VENUE, [FillSlice(Decimal("0.50"), Decimal("0"))])

    def test_price_outside_the_contract_range_is_refused(
            self, polymarket_calc):
        for bad in (Decimal("0.00"), Decimal("1.00"), Decimal("-0.10"),
                    Decimal("1.50")):
            with pytest.raises(FeeDataError, match=r"outside \(0.00, 1.00\)"):
                polymarket_calc.trade_fee_fills(
                    VENUE, [FillSlice(bad, Decimal("10"))])

    def test_unknown_rounding_mode_refuses_to_load(self):
        from prediction_arb.fees.calculator import FeeCalculator
        with pytest.raises(FeeDataError, match="unknown fee rounding"):
            FeeCalculator.from_config({"schedules": [{
                "venue": "invented",
                "version": "v1",
                "effective_date": "2020-01-01",
                "verified_at": "2026-09-13",
                "rounding": "round_half_up",
                "fees": [{"fee_type": "q", "multiplier": "0.06"}],
            }]})

    def test_unknown_order_cap_refuses_to_load(self):
        from prediction_arb.fees.calculator import FeeCalculator
        with pytest.raises(FeeDataError, match="unknown order fee cap"):
            FeeCalculator.from_config({"schedules": [{
                "venue": "invented",
                "version": "v1",
                "effective_date": "2020-01-01",
                "verified_at": "2026-09-13",
                "order_fee_cap": "per_fill_cap",
                "fees": [{"fee_type": "q", "multiplier": "0.06"}],
            }]})

    def test_kalshi_still_blocks(self, fee_calc):
        """Promoting Polymarket US must not loosen any other venue."""
        assert fee_calc.schedules["kalshi"].trade_calculation_status == \
            "INCOMPLETE"
        with pytest.raises(FeeDataError, match="FEE_DATA_INCOMPLETE"):
            fee_calc.trade_fee("kalshi", Decimal("0.50"), Decimal("100"))


class TestPerFillAccuracy:
    """Defect D13: the engine must charge the walked levels, not an average."""

    def test_walked_levels_differ_from_a_weighted_average_price(
            self, polymarket_calc):
        # 0.10 x 100 then 0.90 x 100: each standard reference fee rounds to
        # $0.63, then the cumulative exact-fee cap trims the order total to
        # $1.25. One fee priced at the $0.50 average would be $3.48; the
        # average is not what the venue bills.
        per_fill = polymarket_calc.trade_fee_fills(VENUE, [
            FillSlice(price=Decimal("0.10"), quantity=Decimal("100")),
            FillSlice(price=Decimal("0.90"), quantity=Decimal("100")),
        ])
        average = polymarket_calc.trade_fee(
            VENUE, Decimal("0.50"), Decimal("200"))
        assert per_fill.rounded_sum == Decimal("1.26")
        assert per_fill.cap == Decimal("1.25")
        assert per_fill.charged_per_fill == (Decimal("0.63"), Decimal("0.62"))
        assert per_fill.total == Decimal("1.25")
        assert average == Decimal("3.48")
        assert per_fill.total != average

    def test_two_leg_evaluation_charges_the_walked_levels(
            self, polymarket_style_calc):
        """End-to-end: Opportunity.total_fees is the sum of per-fill fees."""
        from prediction_arb.arbitrage.two_leg import evaluate_two_leg
        from prediction_arb.core.types import Independence, Mode

        fee_calc, yes_c, no_c, yes_book, no_book = polymarket_style_calc
        op = evaluate_two_leg(yes_c, no_c, yes_book, no_book, fee_calc,
                              Mode.PAPER,
                              independence=Independence.INDEPENDENT,
                              match_confidence=1.0)
        assert op is not None
        assert len(walk_buy(yes_book, op.quantity).levels_consumed) == 2

        expected = Decimal("0")
        for contract, book in ((yes_c, yes_book), (no_c, no_book)):
            walk = walk_buy(book, op.quantity)
            expected += fee_calc.trade_fee_fills(
                contract.venue,
                [FillSlice(price=l.price, quantity=l.quantity)
                 for l in walk.levels_consumed]).total
        assert op.total_fees == expected

        # And it is not the old weighted-average-price number.
        stale = Decimal("0")
        for contract, book in ((yes_c, yes_book), (no_c, no_book)):
            walk = walk_buy(book, op.quantity)
            stale += fee_calc.trade_fee(
                contract.venue, walk.weighted_average_price,
                walk.filled_quantity)
        assert op.total_fees != stale
