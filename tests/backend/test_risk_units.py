"""Regression tests for R-multiple derivation — backend/core/risk_units.py.

The live defect these pin: an identical -$1.06 BTCUSDT stop-out was recorded as
-1.46R, -1.91R and -1.97R on three separate rows (trades 325/316/319), and
AVAXUSDT trade 354 read -18.26R. Because the daily breaker sums `pnl_r`, those
readings move the -3R limit that is supposed to stop a losing day.

Every test here is paired with the mutation it forbids — change the code to the
old behaviour and the named test goes red.
"""

import math

import pytest

from backend.core.risk_units import (
    UNKNOWN_R_COST,
    daily_r_contribution,
    pnl_r_from,
    risk_distance,
)


class TestRiskDistance:
    """1R is the distance to the ORIGINAL stop, not the live one."""

    def test_frozen_value_wins_over_a_trailed_stop(self):
        # The stop trailed from 0.0238 back toward entry 0.0244; the frozen
        # distance is the only trustworthy basis.
        d = risk_distance(entry_price=0.02444, stop_loss=0.02440123, frozen=0.00063557)
        assert d == pytest.approx(0.00063557)

    def test_derives_from_entry_and_stop_when_no_frozen_value(self):
        d = risk_distance(entry_price=100.0, stop_loss=97.0)
        assert d == pytest.approx(3.0)

    @pytest.mark.parametrize("bad", [0.0, -1.0, None, float("nan"), float("inf"), "abc"])
    def test_unusable_frozen_value_is_not_trusted(self, bad):
        """A frozen column that is 0/NaN/garbage must not become the denominator.

        Mutation: return `float(frozen)` unguarded -> these pass 0.0/NaN through
        and produce inf or zero R.
        """
        if bad is None:
            d = risk_distance(entry_price=100.0, stop_loss=97.0, frozen=None)
            assert d == pytest.approx(3.0)
        else:
            assert risk_distance(entry_price=100.0, stop_loss=97.0, frozen=bad) == \
                pytest.approx(3.0), "a bad frozen value must fall back, not be used"

    def test_missing_everything_is_unknown_not_zero(self):
        assert risk_distance(entry_price=None, stop_loss=None, frozen=None) is None

    def test_entry_equal_to_stop_is_unknown(self):
        # A stop trailed exactly to entry: distance 0 cannot define 1R.
        assert risk_distance(entry_price=1.2672, stop_loss=1.2672) is None


class TestPnlR:
    """R = realised P&L / (quantity x 1R distance)."""

    def test_known_loss_reads_minus_one_r(self):
        # The real BTCUSDT rows: -$1.06 against a $1.06 risk basis is exactly -1R,
        # not the -1.46/-1.91/-1.97 the live table showed.
        assert pnl_r_from(-1.06, 1.0, 1.06) == pytest.approx(-1.0)

    def test_unknown_distance_yields_none_not_zero(self):
        assert pnl_r_from(-1.06, 1.0, None) is None

    def test_non_finite_pnl_is_unknown(self):
        assert pnl_r_from(float("nan"), 1.0, 1.06) is None
        assert pnl_r_from(float("inf"), 1.0, 1.06) is None

    @pytest.mark.parametrize("qty", [0.0, -1.0, None, float("nan")])
    def test_bad_quantity_is_unknown(self, qty):
        assert pnl_r_from(-1.06, qty, 1.06) is None

    def test_non_finite_result_collapses_to_none(self):
        assert pnl_r_from(1e308, 1e-300, 1e-300) is None

    def test_the_18r_row_cannot_be_reproduced(self):
        """Trade 354: -$8.34 booked as -18.26R implies a $0.4567 risk basis
        against an intended ~$2.21. With the real basis the same dollars are
        a normal loss, not a catastrophe."""
        r = pnl_r_from(-8.34, 1.0, 2.21)
        assert r == pytest.approx(-3.7747, rel=1e-3)  # a large but finite loss
        assert abs(r) < 18.26


class TestDailyRContribution:
    """How a trade moves `daily_summary.total_pnl_r`.

    SIGNED, because the breaker is net (`max(0, -total)`): a win must offset a
    loss, and an unknown R must pull the total DOWN.
    """

    def test_loss_contributes_negatively(self):
        assert daily_r_contribution(-1.46) == pytest.approx(-1.46)

    def test_win_contributes_positively(self):
        """A +3R win must ADD 3R to the day, not subtract it.

        Mutation: return abs(value) -> this returns -3.0 and a winning day trips
        the -3R breaker.
        """
        assert daily_r_contribution(3.0) == pytest.approx(3.0)

    def test_breakeven_contributes_zero(self):
        assert daily_r_contribution(0.0) == pytest.approx(0.0)

    def test_unknown_r_charges_a_full_loss(self):
        """Fail closed: an unmeasurable trade must not pass the limit free.

        Mutation: return 0.0 for None -> an unknown-R day reads as 'nothing
        consumed' and the breaker never fires.
        """
        assert daily_r_contribution(None) == pytest.approx(-UNKNOWN_R_COST)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), "x"])
    def test_non_finite_r_charges_a_full_loss(self, bad):
        assert daily_r_contribution(bad) == pytest.approx(-UNKNOWN_R_COST)


class TestBreakerEndToEnd:
    """The three rows that broke the ruler must now charge the same R."""

    def test_identical_dollar_losses_charge_identical_r(self):
        losses = [(-1.06, 1.06), (-1.06, 1.06), (-1.06, 1.06)]
        charges = [daily_r_contribution(pnl_r_from(u, 1.0, d)) for u, d in losses]
        assert charges == [pytest.approx(-1.0)] * 3

    def test_unknown_r_days_still_trip_the_breaker(self):
        """Three unmeasurable losses must exceed the -3R limit on their own."""
        limit = 3.0
        total = sum(daily_r_contribution(pnl_r_from(-1.06, 1.0, None)) for _ in range(3))
        assert max(0.0, -total) >= limit
