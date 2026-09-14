"""Exact Polymarket US fee model (Phase 3 gate 3) + per-fill fee accuracy.

Every expectation in this file comes from the venue's own published fee
documentation, re-read 2026-09-13 (evidence rows P8 and P11 in
``docs/VENUE_RESEARCH.md``):

* https://docs.polymarket.us/fees
* https://docs.polymarket.us/fees#fee-rules
* https://docs.polymarket.us/fees#fee-schedule-by-price
* https://docs.polymarket.us/fees#faq

None of it is live-tested: the schedule keeps ``live_test_status:
UNAVAILABLE`` and settlement fees stay execution-blocking. What these tests
prove is that the engine's arithmetic is now identical to the venue's
published arithmetic — including the 100-lot table, which is only
reproducible with banker's rounding (several rows round *down* from an exact
half, which ordinary rounding would send up).
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

# Transcribed verbatim from the venue's "Fee Schedule by Price" table
# (https://docs.polymarket.us/fees#fee-schedule-by-price, read 2026-09-13).
# Row i (0-based) is the price $(i+1)/100 on a 100-contract order. 99 rows:
# the documented tradable price range is $0.01-$0.99.
PUBLISHED_TAKER_100_LOT = (
    "0.06 0.12 0.17 0.23 0.28 0.34 0.39 0.44 0.49 0.54 "
    "0.59 0.63 0.68 0.72 0.76 0.81 0.85 0.89 0.92 0.96 "
    "1.00 1.03 1.06 1.09 1.12 1.15 1.18 1.21 1.24 1.26 "
    "1.28 1.31 1.33 1.35 1.36 1.38 1.40 1.41 1.43 1.44 "
    "1.45 1.46 1.47 1.48 1.48 1.49 1.49 1.50 1.50 1.50 "
    "1.50 1.50 1.49 1.49 1.48 1.48 1.47 1.46 1.45 1.44 "
    "1.43 1.41 1.40 1.38 1.36 1.35 1.33 1.31 1.28 1.26 "
    "1.24 1.21 1.18 1.15 1.12 1.09 1.06 1.03 1.00 0.96 "
    "0.92 0.89 0.85 0.81 0.76 0.72 0.68 0.63 0.59 0.54 "
    "0.49 0.44 0.39 0.34 0.28 0.23 0.17 0.12 0.06"
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
def polymarket_calc(fee_calc):
    """The production ledger's Polymarket US schedule."""
    schedule = fee_calc.schedules.get(VENUE)
    assert schedule is not None, "polymarket_us missing from config/fees.yaml"
    return fee_calc


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
                {"fee_type": "quadratic", "multiplier": "0.06",
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
        # Documented maximum at p = $0.50 on a 100-lot: $1.50 / $0.31.
        assert taker[49] == Decimal("1.50")
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
            (Decimal("0.10"), Decimal("1000"), Decimal("5.40"), Decimal("1.12")),
            (Decimal("0.65"), Decimal("1000"), Decimal("13.65"), Decimal("2.84")),
            (Decimal("0.30"), Decimal("1000"), Decimal("12.60"), Decimal("2.62")),
            (Decimal("0.90"), Decimal("1000"), Decimal("5.40"), Decimal("1.12")),
            (Decimal("0.50"), Decimal("1000"), Decimal("15.00"), Decimal("3.12")),
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

    def test_published_rows_that_require_half_even(self, polymarket_calc):
        """Rows ordinary rounding would get wrong — the table proves the rule.

        These prices make Θ × C × p × (1-p) land exactly on a half cent, and
        the venue publishes the *even* neighbour: 1.365 -> $1.36 (not
        $1.37), 1.485 -> $1.48 (not $1.49), 0.765 -> $0.76 (not $0.77).
        """
        for cents, published in ((15, "0.76"), (35, "1.36"), (45, "1.48"),
                                 (55, "1.48"), (65, "1.36"), (85, "0.76")):
            price = Decimal(cents) / Decimal(100)
            assert polymarket_calc.trade_fee(
                VENUE, price, Decimal("100")) == Decimal(published)

    def test_schedule_declares_the_documented_rules(self, polymarket_calc):
        schedule = polymarket_calc.schedules[VENUE]
        assert schedule.rounding == ROUNDING_HALF_EVEN
        assert schedule.order_fee_cap == "cumulative_exact_rounded"
        assert schedule.trade_calculation_status == "COMPLETE"
        # Settlement stays blocking: no settlement fee is published anywhere
        # reviewed, and documented-by-absence is not verification.
        assert schedule.settlement_calculation_status == "INCOMPLETE"
        assert schedule.live_test_status == "UNAVAILABLE"

    def test_settlement_fee_still_fails_closed(self, polymarket_calc):
        with pytest.raises(FeeDataError, match="FEE_DATA_INCOMPLETE"):
            polymarket_calc.settlement_fee(VENUE, Decimal("100"))


class TestOrderLevelCap:
    """The aggressive-order cap from the fee rules (P8)."""

    def test_cap_reduces_total_to_rounded_cumulative_exact(
            self, polymarket_calc):
        # Two 1-contract fills at $0.50: exact fee is 0.06*1*0.25 = 0.015
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
                Decimal("0.06") * sl.quantity * sl.price
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
        assert single.total == Decimal("15.00")

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
            running_exact += Decimal("0.06") * sl.quantity * sl.price * (
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
        assert with_context == plain == Decimal("15.00")


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
        # 0.10 x 100 then 0.90 x 100: exact per-fill fees are $0.54 each,
        # while one fee priced at the $0.50 average is $3.00. Same money
        # spent, very different fee — the average is not what the venue
        # bills.
        per_fill = polymarket_calc.trade_fee_fills(VENUE, [
            FillSlice(price=Decimal("0.10"), quantity=Decimal("100")),
            FillSlice(price=Decimal("0.90"), quantity=Decimal("100")),
        ])
        average = polymarket_calc.trade_fee(
            VENUE, Decimal("0.50"), Decimal("200"))
        assert per_fill.total == Decimal("1.08")
        assert average == Decimal("3.00")
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
