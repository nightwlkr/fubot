"""Extended indicator tests — ATR, FVG, BOS, CHoCH, pivots, structural, volume, 3-candle, trend.

Expected values come from the indicator definitions, never from implementation output.
"""
import pytest
from datetime import datetime

from backend.indicators.atr import calculate_atr
from backend.indicators.pivots import find_pivot_highs_lows
from backend.indicators.bos import detect_bos
from backend.indicators.choch import detect_choch
from backend.indicators.structural import analyze_structure
from backend.indicators.fvg import detect_fvg, check_fvg_filled, evaluate_fvg_entry
from backend.indicators.three_candle import detect_three_candles, evaluate_three_candle_entry
from backend.indicators.volume_profile import estimate_buy_sell_volume, analyze_zone_volume, get_volume_confirmed_filter
from backend.indicators.trend import analyze_trend_bias, should_trade_with_bias


def candle(o, h, l, c, minutes=0):
    return {
        "open": float(o), "high": float(h), "low": float(l), "close": float(c),
        "volume": 100.0,
        "close_time": datetime(2026, 1, 1, 0, minutes),
    }


class TestATR:
    def test_constant_range_atr_equals_range(self):
        candles = [candle(100, 105, 95, 100, i) for i in range(15)]
        assert calculate_atr(candles, period=14) == pytest.approx(10.0)

    def test_needs_period_plus_one(self):
        candles = [candle(100, 105, 95, 100, i) for i in range(14)]
        assert calculate_atr(candles, period=14) is None

    def test_true_range_uses_prev_close(self):
        # gap down: candle1 opens far below candle0 close → TR from gap is larger than range
        candles = [candle(100, 105, 95, 100, 0), candle(80, 85, 75, 80, 1)]
        candles += [candle(80, 85, 75, 80, i) for i in range(2, 15)]
        atr = calculate_atr(candles, period=14)
        # first TR = max(10, |85-100|, |75-100|) = 25, rest are 10
        assert atr is not None and atr > 10.0


class TestPivots:
    def test_finds_single_high_and_low(self):
        candles = [candle(100, 100, 100, 100), candle(101, 101, 101, 101),
                   candle(102, 110, 102, 108),  # peak
                   candle(101, 101, 101, 101), candle(100, 100, 100, 100),
                   candle(99, 99, 99, 99), candle(98, 98, 98, 98),
                   candle(97, 97, 97, 97),
                   candle(96, 96, 96, 96), candle(95, 95, 90, 96),  # valley (low 90)
                   candle(96, 96, 96, 96), candle(97, 97, 97, 97),
                   candle(98, 98, 98, 98)]
        highs, lows = find_pivot_highs_lows(candles, left_bars=2, right_bars=2)
        assert any(p.price == 110 for p in highs)
        assert any(p.price == 90 for p in lows)

    def test_too_short_returns_empty(self):
        highs, lows = find_pivot_highs_lows([candle(1, 2, 0.5, 1)] * 4, 5, 5)
        assert highs == [] and lows == []


def uptrend_series():
    """32 candles: peaks 110@7, 120@19; valleys 95@13, 100@25 → HH + HL."""
    out = []
    for i in range(32):
        if i == 7:
            c = candle(105, 110, 104, 109)
        elif i == 13:
            c = candle(101, 102, 95, 101)
        elif i == 19:
            c = candle(115, 120, 114, 119)
        elif i == 25:
            c = candle(106, 107, 100, 106)
        else:
            base = 100 + i * 0.5
            c = candle(base, base + 3, base - 3, base)
        c["close_time"] = datetime(2026, 1, 1, 0, i)
        out.append(c)
    return out


class TestBOS:
    def test_bullish_bos_when_high_broken(self):
        patterns = detect_bos(uptrend_series(), "BTCUSDT", "5m", left_bars=5, right_bars=5)
        bullish = [p for p in patterns if p.direction == "BULLISH"]
        assert bullish, "second peak (120) breaking the first pivot high (110) is a BULLISH BOS"
        assert any(p.broken_level == 110.0 for p in bullish)


class TestStructural:
    def test_uptrend_from_higher_highs_higher_lows(self):
        s = analyze_structure(uptrend_series(), "BTCUSDT", "5m", atr=1.0)
        assert s.trend == "UPTREND"
        assert s.pivot_highs_count >= 2 and s.pivot_lows_count >= 2

    def test_too_few_pivots_is_ranging(self):
        s = analyze_structure([candle(1, 2, 0.5, 1)] * 8, "BTCUSDT", "5m", atr=1.0)
        assert s.trend == "RANGING"


class TestFVG:
    def bullish_fvg_candles(self):
        # classic 3-candle imbalance: gap left between c1.high and c3.low,
        # with a bullish impulse candle (c2) between them
        return [
            candle(105, 106, 100, 101),   # c1 (high 106)
            candle(107, 112, 107, 111),   # c2 bullish impulse
            candle(111, 115, 110, 114),   # c3 low 110 > c1.high 106 → gap
        ]

    def test_detects_bullish_fvg(self):
        fvgs = detect_fvg(self.bullish_fvg_candles(), "BTCUSDT")
        assert len(fvgs) == 1
        f = fvgs[0]
        assert f.direction == "BULLISH"
        # classic zone: unfilled gap [c1.high, c3.low]
        assert f.zone_min == 106.0
        assert f.zone_max == 110.0
        assert f.zone_min < f.zone_max
        assert f.midpoint == pytest.approx(108.0)
        assert f.impulse_low == 107.0

    def test_no_fvg_without_gap(self):
        flat = [candle(100, 101, 99, 100, i) for i in range(5)]
        assert detect_fvg(flat, "X") == []

    def test_bullish_fill_semantics(self):
        """A bullish FVG is FILLED when price trades back DOWN into the zone."""
        f = detect_fvg(self.bullish_fvg_candles(), "BTCUSDT")[0]
        # zone is now [c1.high=106, c3.low=110]
        # price dips back into the gap (low <= zone_max=110) → filled
        assert check_fvg_filled(f, current_price=107.0, high=107.0, low=105.0) is True
        # price stays above the gap (low 112 > 110) → not filled
        assert check_fvg_filled(f, current_price=114.0, high=115.0, low=112.0) is False

    def test_fvg_entry_rr_by_construction(self):
        """TP is built as entry ± risk*rr_min, so reward/risk must equal rr_min and validate."""
        f = detect_fvg(self.bullish_fvg_candles(), "BTCUSDT")[0]
        res = evaluate_fvg_entry(f, atr=1.0, rr_min=3.0)
        assert res["is_valid"] is True
        assert res["rr_ratio"] == pytest.approx(3.0)
        # stop sits 1 ATR below the gap bottom
        assert res["stop_loss"] == pytest.approx(105.0)

    def test_fvg_stop_is_beyond_zone(self):
        f = detect_fvg(self.bullish_fvg_candles(), "BTCUSDT")[0]
        res = evaluate_fvg_entry(f, atr=1.0, rr_min=3.0)
        assert res["stop_loss"] < f.zone_min  # beyond the gap bottom


class TestCHoCH:
    def choch_series(self):
        """Bearish structure (lower highs 90 → 85) then a recent low (95) ABOVE
        the older high (90) → bullish CHoCH. Pivots with left/right=5:
        highs at 9 (90) and 15 (85); lows at 5 (76), 12 (78), 27 (95)."""
        out = []
        for i in range(33):
            if i == 5:
                c = candle(79, 80, 76, 79)          # L1 valley
            elif i == 9:
                c = candle(85, 90, 84, 89)          # H1 = 90
            elif i == 10:
                c = candle(85, 84.5, 82, 83)
            elif i == 11:
                c = candle(83, 84, 80, 81)
            elif i == 12:
                c = candle(81, 82, 78, 79)          # L2 valley = 78
            elif i == 13:
                c = candle(81, 84, 79.5, 82)
            elif i == 14:
                c = candle(82, 84.5, 81, 84)
            elif i == 15:
                c = candle(84, 85, 83, 84.5)        # H2 = 85 (lower high)
            elif 16 <= i <= 20:
                base = 84 - (i - 15) * 0.5
                c = candle(base, base + 0.5, base - 1, base)
            elif 21 <= i <= 26:
                c = candle(98, 100, 96, 99)         # plateau above the old high
            elif i == 27:
                c = candle(97, 98, 95, 97)          # L3 = 95 → breaks above H1
            elif 28 <= i <= 32:
                c = candle(97, 99, 96, 98)
            else:
                base = 80 + i * 0.5
                c = candle(base, base + 2, base - 1, base)
            c["close_time"] = datetime(2026, 1, 1, 0, i)
            out.append(c)
        return out

    def test_bullish_choch_detected_and_valid(self):
        patterns = detect_choch(self.choch_series(), "BTCUSDT", "5m", atr=1.0, rr_min=3.0)
        assert patterns, "recent low (95) above older high (90) is a bullish CHoCH"
        p = patterns[0]
        assert p.direction == "BULLISH"
        assert p.trigger_price == 95.0
        assert p.pivot_price == 90.0
        # TP built from risk*rr_min → reward/risk == rr_min → valid
        assert p.is_valid is True
        assert p.rr_ratio == pytest.approx(3.0)
        assert p.stop_loss == 90.0
        assert p.take_profit == 110.0


class TestThreeCandle:
    def sweep_candles(self):
        return [
            candle(100, 105, 100, 104),   # c1 range 100-105
            candle(104, 106, 99, 102),    # c2 sweeps below c1.low (99 < 100), closes inside
            candle(102, 108, 101, 107),   # c3 closes above c1.high
        ]

    def test_detects_bullish_sweep(self):
        pats = detect_three_candles(self.sweep_candles(), "BTCUSDT", "5m")
        assert len(pats) == 1
        p = pats[0]
        assert p.direction == "BULLISH"
        assert p.entry_price == 105.0      # c1.high
        assert p.stop_loss == 99.0         # c2 sweep low

    def test_entry_rr_by_construction(self):
        p = detect_three_candles(self.sweep_candles(), "BTCUSDT", "5m")[0]
        res = evaluate_three_candle_entry(p, atr=0.5, rr_min=3.0)
        assert res["is_valid"] is True
        assert res["rr_ratio"] == pytest.approx(3.0)
        assert res["take_profit"] == pytest.approx(105 + 6 * 3)

    def test_tight_stop_widened_to_atr_floor(self):
        """Regression: a structural stop closer than 1.5×ATR is pushed out to
        entry - 1.5×atr so 5m noise can't hit it instantly (was causing -1R).
        Research shows 1.5×ATR provides adequate buffer against stop hunts."""
        p = detect_three_candles(self.sweep_candles(), "BTCUSDT", "5m")[0]
        # structural risk is entry(105) - sweep low(99) = 6; give ATR=10 > 6
        res = evaluate_three_candle_entry(p, atr=10.0, rr_min=3.0)
        # stop must be widened to entry - 1.5*10 = 90.0
        assert res["stop_loss"] == pytest.approx(90.0)
        assert res["entry_price"] - res["stop_loss"] == pytest.approx(15.0)


class TestVolumeProfile:
    def test_close_near_high_is_buy_volume(self):
        buy, sell = estimate_buy_sell_volume(candle(100, 110, 100, 109))
        assert buy > sell

    def test_dominant_buy_confirmed(self):
        candles = [candle(100, 110, 100, 109, i) for i in range(5)]
        bias = analyze_zone_volume(candles)
        assert bias.dominant_side == "BUY" and bias.is_confirmed
        assert bias.buy_ratio > 0.55

    def test_filter_passes_only_matching_direction(self):
        candles = [candle(100, 110, 100, 109, i) for i in range(5)]
        assert get_volume_confirmed_filter("LONG", candles).passes_filter is True
        assert get_volume_confirmed_filter("SHORT", candles).passes_filter is False


class TestTrendBias:
    def test_strong_uptrend_is_bullish(self):
        closes = [100 + i for i in range(60)]  # monotonic up
        tb = analyze_trend_bias(closes)
        assert tb.direction == "BULLISH"
        assert tb.rsi_zone == "OVERBOUGHT"

    def test_overbought_blocks_longs(self):
        closes = [100 + i for i in range(60)]
        tb = analyze_trend_bias(closes)
        allowed, reason = should_trade_with_bias(tb, "LONG")
        assert allowed is False and "overbought" in reason.lower()

    def test_flat_market_neutral_scalp(self):
        closes = [100.0] * 60
        tb = analyze_trend_bias(closes)
        assert tb.direction == "NEUTRAL"
        allowed, reason = should_trade_with_bias(tb, "LONG")
        assert allowed is True and "scalp" in reason.lower()


class TestBacktester:
    """Backtest harness: deterministic over synthetic candles, no lookahead."""

    _MS_5M = 5 * 60 * 1000
    _MS_15M = 15 * 60 * 1000
    _MS_1H = 60 * 60 * 1000
    _T0 = 1_700_000_000_000

    # A seeded random walk, NOT a repeating block.
    #
    # Two earlier fixtures were structurally blind and neither test suite said so:
    #   - a MONOTONIC RAMP: no 3-candle pattern, no FVG, no CHoCH on any
    #     interval. Its "trades" were manufactured out of the seams where the
    #     misaligned proportional-index slices concatenated unrelated bars.
    #   - a repeating 4-bar BLOCK: plenty of 3-candle and FVG detections, but
    #     ZERO CHoCH, because `candles[i]["high"] == candles[i-4]["high"]` for
    #     every i, so no bar is strictly higher than its neighbours on both
    #     sides and `find_pivot_highs_lows` returns empty. `detect_choch` then
    #     bails at its `len(highs) < 2` guard. That left the 15m fill path — the
    #     exact path defect 4 was about — completely untested.
    #
    # The walk is deterministic (fixed seed) and non-periodic, which is the
    # property pivots need. `_series()` folds one 5m path into 15m and 1h so
    # all three intervals share a single coherent price history, as real klines
    # do — deriving each interval independently also made k1h opens identical
    # to k5m opens, which silently defeated one of the fill assertions.
    _SEED = 26
    _DRIFT = -0.05
    _VOL = 0.8

    @classmethod
    def _path(cls, n):
        """A deterministic pseudo-random OHLC path of 5m bars."""
        import random
        rnd = random.Random(cls._SEED)
        px, out = 100.0, []
        for _ in range(n):
            o = px
            c = o + rnd.gauss(cls._DRIFT, cls._VOL)
            h = max(o, c) + abs(rnd.gauss(0, cls._VOL * 0.5))
            l = min(o, c) - abs(rnd.gauss(0, cls._VOL * 0.5))
            out.append((o, h, l, c))
            px = c
        return out

    @staticmethod
    def _fold(path, stride):
        """Aggregate `stride` consecutive 5m bars into one coarser candle."""
        return [(path[i][0],
                 max(g[1] for g in path[i:i + stride]),
                 min(g[2] for g in path[i:i + stride]),
                 path[i + stride - 1][3])
                for i in range(0, len(path) - stride + 1, stride)]

    @classmethod
    def _candles(cls, bars, step_ms, start_idx=0):
        """Attach a real WALL-CLOCK grid to an OHLC sequence.

        The harness truncates each interval by time, not by proportional index,
        so a fixture without wall-clock times is not a weaker test — it is not
        runnable at all. The original `close_time` was a naive datetime
        repeating every 24 bars by `i % 24` and could never order two series.
        """
        return [{"open_time": cls._T0 + (start_idx + i) * step_ms,
                 "open": o, "high": h, "low": l, "close": c, "volume": 1000.0,
                 "close_time": cls._T0 + (start_idx + i) * step_ms + step_ms - 1}
                for i, (o, h, l, c) in enumerate(bars)]

    @classmethod
    def _series(cls):
        """The three intervals a `run` needs, spanning the SAME wall clock.

        1440 5m bars = 5 days, folding to 480 15m and 120 1h. All three start
        at the same wall clock, so `_source_index` maps 1h bar i to exactly
        12*i 5m bars and 4*i 15m bars."""
        p5 = cls._path(1440)
        return (cls._candles(cls._fold(p5, 12), cls._MS_1H),
                cls._candles(p5, cls._MS_5M),
                cls._candles(cls._fold(p5, 3), cls._MS_15M))

    @classmethod
    def _bt(cls, **overrides):
        from backend.backtest import Backtester
        return Backtester(settings={"rr_min": 3.0, "trade_min_trend_strength": "WEAK",
                                    "trade_neutral_markets": True,
                                    "volume_filter_enabled": False, **overrides})

    @classmethod
    def _trades_all_strategies(cls):
        """(strategy, timeframe) pairs for one run per strategy, all three on.

        A single run cannot do this: `run` opens at most one position per
        detection bar and appends candidates FVG, 3CANDLE, CHOC — the same order
        live uses — so on this data FVG takes the slot and the other two are
        never reached. Isolating each via the live strategy toggles is the only
        way to exercise all three fill paths."""
        k1h, k5m, k15m = cls._series()
        out = {}
        for name, ov in (
            ("FVG", {"strategy_3candle_enabled": False, "strategy_choch_enabled": False}),
            ("3CANDLE", {"strategy_fvg_enabled": False, "strategy_choch_enabled": False}),
            ("CHOC", {"strategy_fvg_enabled": False, "strategy_3candle_enabled": False}),
        ):
            r = cls._bt(**ov).run("X", k1h, k5m, k15m)
            assert r.trades, f"{name} produced no trades — fixture cannot test its fill"
            out[name] = r.trades
        return k1h, k5m, k15m, out

    def test_every_strategy_detects_on_the_fixture(self):
        """The fixture must exercise all THREE detectors on all THREE intervals.

        Two earlier fixtures were structurally blind and neither suite said so: a
        monotonic ramp detects nothing at all, and a repeating 4-bar block
        detects plenty of 3-candle and FVG signals but ZERO CHoCH — because
        `candles[i]["high"] == candles[i-4]["high"]` disqualifies every pivot,
        so `find_pivot_highs_lows` returns empty and `detect_choch` bails at its
        `len(highs) < 2` guard. That left the 15m fill path untested, which is
        precisely the path defect 4 was about."""
        from backend.indicators.atr import calculate_atr
        from backend.indicators.choch import detect_choch
        from backend.indicators.fvg import detect_fvg
        from backend.indicators.pivots import find_pivot_highs_lows
        from backend.indicators.three_candle import detect_three_candles
        k1h, k5m, k15m = self._series()
        for tf, series in (("1h", k1h), ("5m", k5m), ("15m", k15m)):
            atr = calculate_atr(series, 14) or 0.0
            highs, lows = find_pivot_highs_lows(series)
            assert len(highs) >= 2 and len(lows) >= 2, (
                f"{tf}: no pivots, so detect_choch returns [] at its guard "
                f"(got {len(highs)} highs / {len(lows)} lows)")
            assert detect_fvg(series), f"{tf}: no FVG"
            assert detect_three_candles(series), f"{tf}: no 3-candle pattern"
            assert any(c.is_valid for c in
                       detect_choch(series, "X", tf, atr, 3.0)), f"{tf}: no valid CHoCH"

    def test_backtest_deterministic(self):
        """Same input → identical result (pure function over candles)."""
        k1h, k5m, k15m = self._series()
        bt = self._bt()
        r1 = bt.run("X", k1h, k5m, k15m)
        r2 = bt.run("X", k1h, k5m, k15m)
        assert r1.summary() == r2.summary(), "backtest must be deterministic"

    def test_backtest_no_lookahead(self):
        """A fill lands on a LATER bar of the signal's OWN interval, at a price
        that bar's range actually reached.

        The previous version of this test asserted `t.entry == k1h[t.entry_idx]
        ["open"]` — which pinned defect 4 directly, and was green only because it
        asserted the bug. Its replacement (which any of the three series could
        satisfy, and whose "not a 1h open" half was defeated by the fixture
        giving every interval the same opens) is written as a property that can
        actually fail: index the RIGHT series by `t.timeframe`, and require the
        fill bar to be strictly later than the signal bar.

        Covering every strategy is the point — a 1h-only assertion is exactly
        what let the 15m path go untested."""
        series_by_tf = {"1h": None, "5m": None, "15m": None}
        k1h, k5m, k15m, per_strategy = self._trades_all_strategies()
        series_by_tf.update({"1h": k1h, "5m": k5m, "15m": k15m})
        seen = set()
        for trades in per_strategy.values():
            for t in trades:
                seen.add(t.timeframe)
                series = series_by_tf[t.timeframe]
                bar = series[t.entry_idx]
                assert bar["low"] <= t.entry <= bar["high"], (
                    f"{t.strategy}: entry {t.entry} is outside the FILL bar's "
                    f"range {bar['low']}..{bar['high']}")
                assert bar["open_time"] > t.signal_open_time, (
                    f"{t.strategy}: filled on/before the signal bar")
        assert seen == {"1h", "5m", "15m"}, \
            f"the fill property must be checked on all three intervals, saw {seen}"

    def test_the_exit_is_resolved_on_the_trades_own_interval(self):
        """`entry_idx` and `exit_idx` index the SAME series, and the exit bar is
        strictly after the fill bar.

        They used to be on incomparable scales: `entry_idx` indexed k5m/k15m
        while `_close(t, i, ...)` wrote `exit_idx = i` indexing k1h, so any
        consumer doing `k1h[t.entry_idx]` — which the old no-lookahead assertion
        did — read a completely unrelated bar. Worse, the exit scan could start
        on a bar that OPENED before the fill, which is lookahead on the exit."""
        k1h, k5m, k15m, per_strategy = self._trades_all_strategies()
        by_tf = {"1h": k1h, "5m": k5m, "15m": k15m}
        for trades in per_strategy.values():
            for t in trades:
                if t.exit_idx is None:
                    continue
                series = by_tf[t.timeframe]
                assert t.exit_idx < len(series), "exit_idx must index its own series"
                assert series[t.exit_idx]["open_time"] > series[t.entry_idx]["open_time"], \
                    "the exit bar must open strictly after the fill bar"
                # R is computed from the frozen entry-to-stop distance.
                # R is computed from the frozen entry-to-stop distance. A
                # zero-R exit is a breakeven close, which under the live
                # trailing rules happens at the MOVED stop — the BE stop sits
                # exactly at entry, and profit-lock can sit above it. So the
                # invariant is no longer "exit at the original stop or TP" but
                # the weaker, correct one: an exit can never be WORSE than the
                # worst stop the strategy was ever allowed to be resting.
                if t.pnl_r == 0.0:
                    if t.direction == "LONG":
                        assert t.exit_price >= t.stop, (
                            "a breakeven exit below the original stop means the "
                            "exit used a price the strategy was never resting at")
                    else:
                        assert t.exit_price <= t.stop, (
                            "a breakeven exit above the original stop means the "
                            "exit used a price the strategy was never resting at")

    def test_backtest_reports_expectancy(self):
        k1h, k5m, k15m = self._series()
        r = self._bt().run("X", k1h, k5m, k15m)
        s = r.summary()
        # summary has the keys the CLI/report depends on
        for key in ("signals", "closed", "win_rate", "total_r", "expectancy_r",
                    "trades_per_day"):
            assert key in s

    def test_a_zero_age_window_trades_nothing_and_is_counted_as_no_fill(self):
        """`signal_max_age_minutes=0` means the LIMIT expires the instant the
        signal is raised, so nothing can ever fill. This is the deterministic
        lever on the fill path: the old harness filled unconditionally at the
        next 1h open, so its trade count was a function of DETECTION alone and
        no setting could make it zero."""
        k1h, k5m, k15m = self._series()
        r = self._bt(signal_max_age_minutes=0).run("X", k1h, k5m, k15m)
        assert not r.trades, "no bar is reachable within a zero-length window"
        assert r.skipped.get("no_fill", 0) > 0, \
            "an unreachable window must be counted as no_fill"
        # Control: the same data with the live default DOES trade, so the
        # assertion above is about the age window and not an empty fixture.
        assert self._bt().run("X", k1h, k5m, k15m).trades
