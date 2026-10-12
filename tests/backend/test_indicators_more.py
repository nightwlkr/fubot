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
        """Regression: a structural stop closer than 1×ATR is pushed out to
        entry - atr so 5m noise can't hit it instantly (was causing -1R)."""
        p = detect_three_candles(self.sweep_candles(), "BTCUSDT", "5m")[0]
        # structural risk is entry(105) - sweep low(99) = 6; give ATR=10 > 6
        res = evaluate_three_candle_entry(p, atr=10.0, rr_min=3.0)
        # stop must be widened to entry - 10 = 95
        assert res["stop_loss"] == pytest.approx(95.0)
        assert res["entry_price"] - res["stop_loss"] == pytest.approx(10.0)


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
