"""Sizing tests — the notional cap is silently overriding `risk_percent`.

Live evidence this pins (2026-10-19, three consecutive entries):

    trade  notional   stop%    1R
    423    $180.03    0.123%   $0.2209
    425    $144.62    0.188%   $0.2715
    426    $112.35    0.434%   $0.4881

With `risk_percent=1.0` and `max_position_notional_pct=100`, the cap binds
whenever `stop% < risk%/cap%` (= 1%). Every observed stop is far below that, so
the cap bound on 100% of entries and effective risk was 0.12-0.43%, not the
configured 1%.

The notionals also fall as positions open ($180 -> $144 -> $112): sizing reads
`availableBalance` (FREE MARGIN), which shrinks as collateral is locked. So the
third position of a session is sized off the smallest base — risk per trade is
anti-correlated with exposure, the opposite of intent.

`cap_position_notional` logs the bind at `debug`, and `StrategyService`'s
warning at `strategy_service.py:262-268` is suppressed under uvicorn's config —
so on the VPS this was invisible. These tests make it assertable instead.
"""
from decimal import Decimal

import pytest

from backend.services.risk_service import RiskService


def svc() -> RiskService:
    return RiskService()


class TestCapBindsWhenStopIsTight:
    """The algebraic condition: the cap binds iff stop% < risk% / cap%."""

    @pytest.mark.parametrize("stop_pct,expect_capped", [
        (Decimal("0.00123"), True),    # trade 423, real
        (Decimal("0.00188"), True),    # trade 425, real
        (Decimal("0.00434"), True),    # trade 426, real
        (Decimal("0.0075"), True),     # typical measured stop, still binds
        (Decimal("0.0100"), False),    # exactly the threshold: does not bind
        (Decimal("0.0250"), False),    # wide stop: risk target is reachable
    ])
    def test_bind_condition(self, stop_pct, expect_capped):
        balance = Decimal("180")
        entry = Decimal("100")
        risk_pct = Decimal("1.0")
        cap_frac = Decimal("1.0")

        risk_usd = balance * risk_pct / Decimal("100")
        uncapped_qty = (risk_usd / (entry * stop_pct)).quantize(Decimal("0.000001"))
        capped_qty = svc().cap_position_notional(
            uncapped_qty, entry, balance, cap_frac)

        assert (capped_qty < uncapped_qty) is expect_capped

    def test_a_capped_position_risks_far_less_than_configured(self):
        """Quantifies the damage: 1% configured, 0.123% actual.

        Reproduces live trade 423 exactly — qty 14.634 capped to 1.800, risk
        $1.80 -> $0.2214."""
        balance, entry, stop = Decimal("180"), Decimal("100"), Decimal("99.877")
        qty = svc().calc_position_size(balance, Decimal("1.0"), entry, stop)
        capped = svc().cap_position_notional(
            qty, entry, balance, Decimal("1.0"))
        # ACTUAL risk is what the capped size can lose: qty * price_diff.
        actual_risk = capped * (entry - stop)
        intended_risk = balance * Decimal("1.0") / Decimal("100")
        effective_pct = actual_risk / balance * Decimal("100")
        assert actual_risk < intended_risk * Decimal("0.2"), (
            f"actual risk ${actual_risk} vs intended ${intended_risk} "
            f"({effective_pct}% of balance, not 1%) — the notional cap is the "
            "primary sizing rule, not a safety backstop"
        )


class TestSizingBaseShrinksAsPositionsOpen:
    """`calc_position_size` is fed `availableBalance` (free margin), which falls
    as collateral locks. Risk per trade therefore drops as exposure builds."""

    def test_free_margin_decline_shrinks_each_trade(self):
        r = svc()
        entry, stop = Decimal("100"), Decimal("99.877")   # 0.123% stop
        sizes = [
            r.calc_position_size(free, Decimal("1.0"), entry, stop)
            for free in (Decimal("180"), Decimal("144"), Decimal("112"))
        ]
        assert sizes[0] > sizes[1] > sizes[2], (
            "later positions in a session get SMALLER size from a smaller "
            "free-margin base — risk is anti-correlated with exposure"
        )

    def test_risk_against_total_balance_is_far_below_one_percent(self):
        """The number that matters: 1% was configured; this is what happened."""
        total_balance = Decimal("216")     # equity_log, stable
        free_margin = Decimal("180")
        entry, stop = Decimal("100"), Decimal("99.877")
        qty = svc().calc_position_size(free_margin, Decimal("1.0"), entry, stop)
        qty = svc().cap_position_notional(qty, entry, free_margin, Decimal("1.0"))
        risk_usd = qty * (entry - stop)
        assert (risk_usd / total_balance * Decimal("100")) < Decimal("0.2")


class TestMathItselfIsCorrect:
    """The bug is the INPUTS and the cap parameterisation, not the arithmetic.
    These guard against 'fixing' correct maths later."""

    def test_position_size_risks_exactly_the_configured_amount(self):
        qty = svc().calc_position_size(
            Decimal("1000"), Decimal("1.0"), Decimal("100"), Decimal("99"))
        assert qty * (Decimal("100") - Decimal("99")) == Decimal("10")

    def test_low_priced_coins_are_not_oversized(self):
        """The 2026-10-12 LINKUSDT regression: qty must not carry a stray
        * entry_price, which made the realised loss risk_usd * entry."""
        qty = svc().calc_position_size(
            Decimal("175"), Decimal("1.0"), Decimal("13"), Decimal("12.87"))
        assert abs(qty * (Decimal("13") - Decimal("12.87")) - Decimal("1.75")) \
            <= Decimal("0.02"), (
            "realised loss should be 1% of a $175 balance = $1.75, NOT "
            "$1.75 * entry_price"
        )