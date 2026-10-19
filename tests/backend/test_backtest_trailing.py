"""The harness must model the exits production actually runs.

Before 2026-09-19 `_resolve_exit` walked forward to whichever of the ORIGINAL
stop / target the range reached first. Production meanwhile runs two trailing
rules — `PositionManager._check_be_trailing` (stop to entry once +0.5R) and
`_check_profit_trailing` (trail behind by 0.5R once +1.0R).

That is not a cosmetic gap. The 2026-10-15 live audit measured **81% of exits
at approximately 0R**, which static SL/TP cannot produce: a static stop either
fills at -1R or never. So every expectancy number the harness reported described
an exit distribution the bot does not have.
"""
import pytest

from backend.backtest import BacktestTrade, Backtester

MS_5M = 5 * 60 * 1000


def _bar(i, o, h, l, c):
    return {"open_time": i * MS_5M, "close_time": (i + 1) * MS_5M - 1,
            "open": o, "high": h, "low": l, "close": c}


def _trade(**kw):
    base = dict(entry_idx=0, entry=100.0, stop=99.0, take_profit=103.0,
                direction="LONG", strategy="3CANDLE", symbol="TESTUSDT")
    base.update(kw)
    return BacktestTrade(**base)


def _bt(**settings):
    s = {"be_trail_trigger_r": 0.5, "profit_lock_trigger_r": 1.0,
         "profit_lock_trail_r": 0.5}
    s.update(settings)
    return Backtester(settings=s)


class TestBreakevenStop:
    """+0.5R moves the stop to entry, turning a would-be -1R into a scratch."""

    def test_a_winner_that_retraces_exits_at_breakeven_not_minus_one_r(self):
        # +0.8R (101.8), then back through entry. Without trailing the stop at
        # 99.0 would NOT be hit here at all — the trade would keep running, so
        # this bar sequence must reach a bar that hits 99 to close without it.
        bt = _bt(profit_lock_trigger_r=None)
        series = [_bar(0, 100.0, 100.0, 100.0, 100.0),
            _bar(1, 100.0, 101.8, 100.5, 101.5),   # +0.8R -> stop to entry 100
            _bar(2, 101.5, 101.6, 99.5, 100.0),   # sweeps back through entry
        ]
        t = _trade()
        bt._resolve_exit(t, series)
        assert t.exit_reason == "BE_STOP"
        assert t.exit_price == pytest.approx(100.0)
        assert t.pnl_r == pytest.approx(0.0), (
            "a BE scratch books 0R, not -1R — this is the 81%-of-exits effect "
            "the static model could not produce")

    def test_trailing_does_not_rescue_a_trade_that_reached_the_stop_first(self):
        bt = _bt()
        series = [_bar(0, 100.0, 100.0, 100.0, 100.0),
                 _bar(1, 100.0, 100.2, 98.5, 99.0)]   # stop hit before any profit
        t = _trade()
        bt._resolve_exit(t, series)
        assert t.exit_reason == "SL_HIT"
        assert t.pnl_r == pytest.approx(-1.0)

    def test_short_side_mirrors(self):
        bt = _bt(profit_lock_trigger_r=None)
        series = [_bar(0, 100.0, 100.0, 100.0, 100.0),
            _bar(1, 100.0, 99.5, 98.2, 98.5),   # -0.8R -> stop to entry 100
            _bar(2, 98.5, 100.5, 98.4, 100.0),
        ]
        t = _trade(direction="SHORT", stop=101.0, take_profit=97.0)
        bt._resolve_exit(t, series)
        assert t.exit_reason == "BE_STOP"
        assert t.pnl_r == pytest.approx(0.0)


class TestOneWayGuard:
    """A stop only ever moves toward profit — the live guard that stops a
    retrace from re-widening a stop the market already proved it needed."""

    def test_the_stop_never_widens_back(self):
        # profit-lock disabled so BE is the only rule that can arm — otherwise
        # the +0.8R bar arms profit-lock first (needs +1.0R? no: profit 0.8R
        # < 1.0R so it does NOT arm, but assert the BE stop specifically).
        bt = _bt(profit_lock_trigger_r=None)
        series = [_bar(0, 100.0, 100.0, 100.0, 100.0),
            _bar(1, 100.0, 101.8, 100.9, 101.5),   # arms BE at 100
            _bar(2, 101.5, 101.6, 99.0, 99.5),    # closes at the BE stop
        ]
        t = _trade()
        bt._resolve_exit(t, series)
        # It must NOT have re-widened to 99.0 and booked -1R.
        assert t.exit_price == pytest.approx(100.0)
        assert t.pnl_r == pytest.approx(0.0)


class TestProfitLock:
    def test_profit_lock_trails_behind_the_extreme(self):
        bt = _bt()
        series = [_bar(0, 100.0, 100.0, 100.0, 100.0),
            _bar(1, 100.0, 102.0, 100.5, 101.8),   # +2.0R -> stop = 102.0 - 0.5R
            _bar(2, 101.8, 101.9, 101.4, 101.5),
        ]
        t = _trade()
        bt._resolve_exit(t, series)
        expected = 102.0 - 0.5 * 1.0        # trail 0.5R behind the 102.0 extreme
        assert t.exit_reason == "BE_STOP"
        assert t.exit_price == pytest.approx(expected)
        assert t.pnl_r == pytest.approx(1.5), (
            "exit at 101.5 is +1.5R from entry 100 — the trail is 0.5R behind "
            "the 102.0 extreme, but that is 1.5R of actual profit")


class TestDisabling:
    def test_trailing_can_be_switched_off_to_recover_the_static_model(self):
        series = [_bar(0, 100.0, 100.0, 100.0, 100.0),
            _bar(1, 100.0, 101.8, 100.5, 101.5),
            _bar(2, 101.5, 101.6, 98.5, 99.0),
        ]
        static = Backtester(settings={"be_trail_trigger_r": None,
                                      "profit_lock_trigger_r": None})
        t = _trade()
        static._resolve_exit(t, series)
        assert t.exit_reason == "SL_HIT"
        assert t.pnl_r == pytest.approx(-1.0)

    def test_defaults_match_production_settings(self):
        bt = Backtester()
        assert bt.trailing["be_trail_trigger_r"] == 0.5
        assert bt.trailing["profit_lock_trigger_r"] == 1.0
        assert bt.trailing["profit_lock_trail_r"] == 0.5


class TestWithinBarOrdering:
    def test_a_tighter_stop_cannot_be_credited_on_the_bar_that_creates_it(self):
        """The bar's range has no internal path. A stop tightened by bar N's
        extreme must not then be used to close ON bar N — the order could not
        have been resting yet. Pessimistic by construction."""

        class _Spy(Backtester):
            def __init__(self, **kw):
                super().__init__(settings=kw)
                self.seen = []

            def _close(self, trade, idx, price, reason):
                self.seen.append((idx, price, reason))
                super()._close(trade, idx, price, reason)

        # Bar 1 spans from below the eventual BE stop to well above entry.
        series = [_bar(0, 100.0, 100.0, 100.0, 100.0),
                 _bar(1, 100.0, 102.0, 98.0, 100.5)]
        spy = _Spy(be_trail_trigger_r=0.5, profit_lock_trigger_r=None)
        t = _trade()
        spy._resolve_exit(t, series)
        assert spy.seen[0][0] == 1
        assert spy.seen[0][1] == pytest.approx(99.0), \
            "must close at the ORIGINAL stop (99.0), not at the BE stop (100.0) " \
            "that this same bar's high would have created"
        assert spy.seen[0][2] == "SL_HIT"