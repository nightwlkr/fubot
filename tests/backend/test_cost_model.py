"""Tests for backend/core/cost_model.py — the cost the bot actually pays.

The repo carried three inconsistent constants (0.0007 / 0.0014 / 0.00045) and
the one the harness charged, 0.0007, was both the lowest and based on a wrong
assumption about the order type.

Live entries are `timeInForce: GTC`, not `GTX` (post-only), at the zone
midpoint, with gate 4 admitting any market price within 1% of the zone — so the
order is often marketable at submission. Measured signature over 30 consecutive
entries: 27 filled strictly BETTER than the planned midpoint, 3 worse, 1 on.
A resting maker fills AT its price and never better, so that is a taker fill.
"""
import pytest

from backend.core.cost_model import (
    DEFAULT_SLIPPAGE,
    FEE_ROUND_TRIP,
    LIVE,
    MAKER_FEE,
    TAKER_FEE,
    CostModel,
)


class TestFillModes:
    def test_taker_is_the_default_and_is_what_live_does(self):
        assert LIVE.entry_fee == TAKER_FEE
        assert LIVE.exit_fee == TAKER_FEE

    def test_maker_is_cheaper_but_is_not_what_live_gets(self):
        maker = CostModel.for_fill_mode("maker")
        assert maker.round_trip < LIVE.round_trip

    def test_hybrid_is_entry_maker_exit_taker(self):
        h = CostModel.for_fill_mode("hybrid")
        assert h.entry_fee == MAKER_FEE and h.exit_fee == TAKER_FEE

    def test_unknown_mode_is_rejected_rather_than_defaulted(self):
        """A typo must not silently fall back to the cheapest model.

        Mutation: default the else branch to maker -> a typo would understate
        cost and make losing configs look viable.
        """
        with pytest.raises(ValueError):
            CostModel.for_fill_mode("tkaer")


class TestMagnitude:
    def test_modeled_taker_round_trip_is_above_the_old_assumed_constant(self):
        """The harness used to charge 0.0007; modeled taker cost is 0.00110."""
        assert FEE_ROUND_TRIP > 0.0007
        assert FEE_ROUND_TRIP == pytest.approx(0.00110, abs=1e-12)

    def test_round_trip_includes_both_fees_and_both_slippages(self):
        m = CostModel()
        assert m.round_trip == pytest.approx(
            TAKER_FEE * 2 + DEFAULT_SLIPPAGE * 2)

    def test_slippage_is_charged_not_ignored(self):
        """The old model had zero slippage; a STOP_MARKET does not fill on touch."""
        m = CostModel(entry_fee=0.0, exit_fee=0.0)
        assert m.round_trip_in_r(100.0, 1.0) == pytest.approx(
            100.0 * 2 * DEFAULT_SLIPPAGE)


class TestInverseWithStopDistance:
    """Fees are a fraction of notional; R divides by risk distance. That inverse
    relationship is the dominant cost drag and the reason tight stops lose."""

    @pytest.mark.parametrize("stop,expected_multiple", [
        (0.0025, 4.0),   # 0.25% stop costs 4x what a 1% stop does
        (0.0050, 2.0),
        (0.0100, 1.0),
    ])
    def test_cost_in_r_scales_inversely_with_stop(self, stop, expected_multiple):
        base = LIVE.round_trip_in_r(100.0, 0.01)
        assert LIVE.round_trip_in_r(100.0, stop) == pytest.approx(
            base * expected_multiple, rel=1e-6)

    def test_zero_risk_distance_is_infinite_not_zero(self):
        """Fail loud: a 0 risk distance cannot make cost free.

        Mutation: return 0.0 -> a degenerate trade books as cost-free and the
        R column is meaningless.
        """
        assert LIVE.round_trip_in_r(100.0, 0.0) == float("inf")

    def test_negative_risk_distance_is_infinite(self):
        assert LIVE.round_trip_in_r(100.0, -1.0) == float("inf")


class TestModeledCostOnMeasuredLiveTrade:
    """Trade 423 (UNIUSDT LONG, real fill 7.201, frozen risk distance 0.008838).

    This is the number that explains the losses. The stop was 0.123% of entry
    while the model charges 0.11% of notional round trip, so modeled costs are
    ~0.90R. This consumes most of the initial risk budget and makes this tight
    stop materially more expensive than the wider-stop trades.
    """

    def test_a_tight_stop_makes_a_round_trip_cost_almost_a_whole_r(self):
        cost = LIVE.round_trip_in_r(7.201, 0.008838)
        # Two 0.045% taker fees plus two 0.01% slippage allowances make the
        # round-trip cost ≈ 0.90R at a 0.123% stop.
        assert cost == pytest.approx(0.90, abs=0.05)
        assert cost > 0.60, (
            "at a 0.123% stop the round trip consumes most of the initial risk"
        )

    def test_the_wider_the_stop_the_more_viable_the_math(self):
        """Same strategy, same ~0.11% cost; only the stop width changes.

        NOTE THE UNITS: `round_trip_in_r` takes the risk DISTANCE IN PRICE
        TERMS (entry - stop), not a stop percentage. Passing 0.00123 for a
        0.123% stop on a $100 entry returns ~89R instead of ~0.81R — a 100x
        error that is silently plausible. See the unit test below.
        """
        entry = 100.0
        tight = LIVE.round_trip_in_r(entry, entry * 0.00123)   # observed stop
        wide = LIVE.round_trip_in_r(entry, entry * 0.0100)     # 1% stop
        assert wide < tight / 7
        assert wide < 0.12, "a 1% stop brings costs down to ~0.11R"


class TestUnitsAreUnambiguous:
    """`round_trip_in_r(entry, risk_dist)` — risk_dist is a PRICE distance.

    Passing a stop FRACTION by mistake yields a number ~100x too large, and it
    still looks like a plausible R value, so nothing would catch it."""

    def test_price_distance_gives_the_sane_answer(self):
        entry = 100.0
        # 1% stop = a price distance of 1.0 on a $100 entry.
        # 0.045% taker fees plus 0.01% slippage per side = 0.11% round trip.
        assert LIVE.round_trip_in_r(entry, 1.0) == pytest.approx(0.11, abs=1e-12)

    def test_a_fraction_mistaken_for_a_distance_is_obviously_wrong(self):
        """If a caller passes 0.01 (the 1% stop as a FRACTION) instead of 1.0
        (the same stop as a price distance), the result explodes. Pinning the
        magnitude so the mistake is unmissable."""
        sane = LIVE.round_trip_in_r(100.0, 1.0)
        mistaken = LIVE.round_trip_in_r(100.0, 0.01)
        assert mistaken == pytest.approx(sane * 100, rel=1e-6)
        assert mistaken > 9, "a >9R cost is self-evidently a units bug"
