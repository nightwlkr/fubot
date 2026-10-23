"""
Regression tests for prototype indicator modules: VWAP, Order Block, ORB.

Locks in the two real bugs found in review:
  1. VWAP stop must be placed beyond the DEVIATION EXTREME (prev candle's
     wick), not the reversal candle's wick.
  2. fvg_within_order_block must NOT match Order Blocks that form AFTER the
     FVG — confluence has to be visible at signal time, not in the future.

Also covers basic sanity for the ORB range logic.

Also covers the strategy_compare harness contract: the overlap cap must
actually bind (it tracked entry indices, which the ascending-signal prune
always emptied) and unresolved trades must be counted, not silently dropped.
"""
import importlib.util
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_strategy_compare():
    """Import scripts/strategy_compare.py as a module (it is a script, not a package)."""
    path = REPO_ROOT / "scripts" / "strategy_compare.py"
    spec = importlib.util.spec_from_file_location("strategy_compare", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["strategy_compare"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestFVGEntryLookahead:
    """An FVG on 1h bar i is only knowable at the CLOSE of hour i.

    The 1h bar spans 12 five-minute bars, so its close is 5m index +11 and the
    first tradable 5m bar is +12. Entering at +1 (the start of the still-forming
    hour) let the exit scan and stop/TP geometry use a gap that did not exist yet
    — it inflated OB+FVG expectancy from +0.315R to +0.402R.
    """

    def test_fvg_entry_index_is_after_the_forming_hour(self):
        sc = _load_strategy_compare()

        # 1h bar 3 opens at base + 3h -> 5m index 36. It occupies 5m indices
        # 36..47, so it closes at 47 and the first tradable bar is 48.
        fvg_idx = 36
        assert fvg_idx + 12 - 1 == 47, "1h bar closes at fvg_idx + 11"
        assert sc.fvg_entry_idx_5m(fvg_idx) == 48, \
            "entry must be the first 5m bar after the forming hour closes"

    def test_entry_never_precedes_the_forming_bar(self):
        sc = _load_strategy_compare()
        for fvg_idx in range(0, 500):
            entry = sc.fvg_entry_idx_5m(fvg_idx)
            assert entry >= fvg_idx + 12, "entry must skip the whole forming hour"


class TestOverlapCap:
    """The max_open cap must bind, and unresolved trades must be counted."""

    def _flat_candles(self, n, high, low):
        return [{"open": high, "high": high, "low": low, "close": high} for _ in range(n)]

    def test_cap_binds_when_trades_stay_open(self):
        """Signals every bar whose trades never exit: max_open=1 must take 1 trade."""
        sc = _load_strategy_compare()

        # LONG entry 100, stop 50, tp 100000 — flat candles at 100 hit neither.
        plan = [("LONG", 100.0, 50.0, 100000.0, i) for i in range(100, 200)]
        candles = self._flat_candles(300, 100, 100)

        closed, open_count = sc._run_capped(
            plan, candles, 1, "TEST",
            entry_of=lambda p: (p[0], p[1], p[2], p[3], p[4]),
        )

        assert closed == []
        assert open_count == 1, f"cap of 1 must admit exactly 1 trade, got {open_count}"

    def test_cap_counts_each_open_trade_separately(self):
        """Several unresolved trades must occupy SEPARATE slots.

        They all carry open_until == len(candles); a set would collapse them
        into one and let the cap admit the whole signal stream.
        """
        sc = _load_strategy_compare()

        plan = [("LONG", 100.0, 50.0, 100000.0, i) for i in range(100, 200)]
        candles = self._flat_candles(300, 100, 100)

        for cap in (1, 3, 5):
            closed, open_count = sc._run_capped(
                plan, candles, cap, "TEST",
                entry_of=lambda p: (p[0], p[1], p[2], p[3], p[4]),
            )
            assert closed == []
            assert open_count == cap, f"max_open={cap} must admit exactly {cap}, got {open_count}"

    def test_cap_releases_slot_after_exit(self):
        """Once a capped-in trade closes, a later signal may be admitted."""
        sc = _load_strategy_compare()

        # Candles dip to 50 at bar 120 only, so the first trade exits there.
        candles = self._flat_candles(300, 100, 100)
        candles[120] = {"open": 100, "high": 100, "low": 40, "close": 100}

        plan = [("LONG", 100.0, 50.0, 100000.0, 100), ("LONG", 100.0, 50.0, 100000.0, 130)]
        closed, open_count = sc._run_capped(
            plan, candles, 1, "TEST",
            entry_of=lambda p: (p[0], p[1], p[2], p[3], p[4]),
        )

        assert len(closed) == 1, "first trade exits at bar 120"
        assert closed[0]["exit_idx"] == 120
        assert open_count == 1, "second signal (bar 130) admitted after the slot freed"

    def test_unresolved_trades_are_counted_not_dropped(self):
        """A trade open at the window end is reported, not silently discarded."""
        sc = _load_strategy_compare()

        plan = [("LONG", 100.0, 50.0, 100000.0, 100)]
        candles = self._flat_candles(300, 100, 100)

        closed, open_count = sc._run_capped(
            plan, candles, 3, "TEST",
            entry_of=lambda p: (p[0], p[1], p[2], p[3], p[4]),
        )

        assert closed == []
        assert open_count == 1

    def test_degenerate_setup_is_not_counted_as_open(self):
        """An invalid setup returns None from simulate_trade — not an open trade."""
        sc = _load_strategy_compare()

        plan = [("LONG", 100.0, 150.0, 200.0, 100)]  # stop ABOVE entry for a LONG
        candles = self._flat_candles(300, 100, 100)

        closed, open_count = sc._run_capped(
            plan, candles, 3, "TEST",
            entry_of=lambda p: (p[0], p[1], p[2], p[3], p[4]),
        )

        assert closed == []
        assert open_count == 0, "invalid geometry must not be reported as an open trade"


class TestVWAPStopPlacement:
    """Bug 4: VWAP stop must sit beyond the deviation extreme (prev candle)."""

    def _make_candles(self, n=120, base=100.0):
        return [{"open": base + i * 0.01, "high": base + i * 0.01 + 0.5,
                 "low": base + i * 0.01 - 0.5, "close": base + i * 0.01,
                 "volume": 1000.0} for i in range(n)]

    def test_vwap_stop_below_prev_low_for_long(self):
        """LONG: stop must be below prev candle's low, not current candle's low."""
        from backend.indicators.vwap import detect_vwap_reversion

        # Build a scenario where prev candle has a deep wick below the band
        # and current candle has a shallow wick (reversal candle).
        # prev: closed below band, low = 95.0 (deep wick)
        # curr: closes back above band, low = 98.0 (shallow wick)
        candles = self._make_candles(100, 100.0)

        # Inject a deviation: make candle 97 (prev) drop low, candle 98 reverse
        # We need to control ATR and VWAP precisely, so use a simpler approach:
        # just call the function and check the stop is derived from prev["low"].

        # With ATR ~ 1.0 (high-low range), we can set:
        atr = 1.0
        # If prev closes below band and current closes above, the signal fires
        # and stop should be prev["low"] - 1.5 * atr.

        # The test asserts the LOGIC directly by inspecting the signal's stop
        # relative to prev's low.
        # We'll construct a specific scenario:
        # - Most candles have small range
        # - One candle (index i-1) has a deep low
        # - Next candle (index i) closes back above the band

        # Simpler: just verify the code uses prev["low"] by monkeypatching
        # and checking the calculation path.
        from backend.indicators import vwap as vwap_mod

        # Spy on the calculate_vwap to return a known value
        original_calculate = vwap_mod.calculate_vwap
        vwap_mod.calculate_vwap = lambda candles, lookback: [100.0] * len(candles)

        try:
            # Craft candles where prev has low=95, current has low=98
            candles = self._make_candles(100, 100.0)
            candles[-2]["low"] = 95.0   # prev candle's deep wick (deviation extreme)
            candles[-2]["close"] = 97.0 # prev closed below band (100 - 1.5*1.0 = 98.5)
            candles[-1]["low"] = 98.0   # current candle's shallow wick
            candles[-1]["close"] = 99.0 # current closed back above band (>= 98.5)

            atr_series = [1.0] * len(candles)
            signals = detect_vwap_reversion(candles, atr_series, deviation_mult=1.5)

            long_signals = [s for s in signals if s.direction == "LONG"]
            assert len(long_signals) >= 1, "Must detect at least one LONG signal"
            sig = long_signals[-1]
            # stop must be prev["low"] - 1.5*atr = 95.0 - 1.5 = 93.5
            # NOT current["low"] - 1.5*atr = 98.0 - 1.5 = 96.5
            assert sig.stop_loss == pytest.approx(93.5), (
                f"VWAP LONG stop should be 93.5 (prev low - 1.5*ATR), got {sig.stop_loss}"
            )
        finally:
            vwap_mod.calculate_vwap = original_calculate

    def test_vwap_stop_above_prev_high_for_short(self):
        """SHORT: stop must be above prev candle's high, not current candle's high."""
        from backend.indicators.vwap import detect_vwap_reversion
        from backend.indicators import vwap as vwap_mod

        original_calculate = vwap_mod.calculate_vwap
        vwap_mod.calculate_vwap = lambda candles, lookback: [100.0] * len(candles)

        try:
            candles = self._make_candles(100, 100.0)
            # prev candle: closed above band (100 + 1.5*1.0 = 101.5), high = 105.0
            candles[-2]["high"] = 105.0
            candles[-2]["close"] = 103.0
            # current candle: high = 102.0 (shallow), close = 101.0 (back below band)
            candles[-1]["high"] = 102.0
            candles[-1]["close"] = 101.0

            atr_series = [1.0] * len(candles)
            signals = detect_vwap_reversion(candles, atr_series, deviation_mult=1.5)

            short_signals = [s for s in signals if s.direction == "SHORT"]
            assert len(short_signals) >= 1, "Must detect at least one SHORT signal"
            sig = short_signals[-1]
            # stop must be prev["high"] + 1.5*atr = 105.0 + 1.5 = 106.5
            # NOT current["high"] + 1.5*atr = 102.0 + 1.5 = 103.5
            assert sig.stop_loss == pytest.approx(106.5), (
                f"VWAP SHORT stop should be 106.5 (prev high + 1.5*ATR), got {sig.stop_loss}"
            )
        finally:
            vwap_mod.calculate_vwap = original_calculate


class TestOrderBlockLookahead:
    """Bug 2: fvg_within_order_block must NOT match future Order Blocks."""

    def _make_ob(self, direction, formed_at_idx, confirm_at_idx=None):
        from backend.indicators.order_block import OrderBlock
        if confirm_at_idx is None:
            confirm_at_idx = formed_at_idx
        return OrderBlock(
            direction=direction,
            zone_min=99.0, zone_max=101.0,
            midpoint=100.0, high=101.0, low=99.0,
            formed_at_idx=formed_at_idx,
            confirm_at_idx=confirm_at_idx,
        )

    def test_future_ob_does_not_count_as_confluence(self):
        """An OB forming after current_idx must not match."""
        from backend.indicators.order_block import fvg_within_order_block

        obs = [
            self._make_ob("BULLISH", formed_at_idx=200),  # future OB
            self._make_ob("BULLISH", formed_at_idx=10),   # recent past OB
        ]

        # FVG at index 50: the future OB (200) must not count
        result = fvg_within_order_block(
            fvg_zone_min=99.5, fvg_zone_max=100.5,
            fvg_direction="BULLISH",
            obs=obs,
            lookback=50,
            current_idx=50,
        )
        # Should still match via the past OB (formed_at_idx=10, within lookback)
        assert result is True, "Should match the past OB at idx 10"

    def test_only_future_ob_no_match(self):
        """If the ONLY OB is in the future, return False."""
        from backend.indicators.order_block import fvg_within_order_block

        obs = [
            self._make_ob("BULLISH", formed_at_idx=100),  # future OB only
        ]

        result = fvg_within_order_block(
            fvg_zone_min=99.5, fvg_zone_max=100.5,
            fvg_direction="BULLISH",
            obs=obs,
            lookback=50,
            current_idx=50,  # OB at 100 is in the future
        )
        assert result is False, "Future-only OB must not count as confluence"

    def test_recent_past_ob_counts(self):
        """A past OB within lookback should match."""
        from backend.indicators.order_block import fvg_within_order_block

        obs = [
            self._make_ob("BULLISH", formed_at_idx=30),  # 20 bars ago
        ]

        result = fvg_within_order_block(
            fvg_zone_min=99.5, fvg_zone_max=100.5,
            fvg_direction="BULLISH",
            obs=obs,
            lookback=50,
            current_idx=50,
        )
        assert result is True

    def test_unconfirmed_ob_does_not_count(self):
        """An OB whose pivot/BOS confirmations land AFTER current_idx must not match.

        This is the gap `confirm_at_idx` exists to close: the OB candle sits at
        bar 10, but it only becomes knowable at bar 20. An FVG at bar 15 must
        not see it as confluence.
        """
        from backend.indicators.order_block import fvg_within_order_block

        obs = [
            self._make_ob("BULLISH", formed_at_idx=10, confirm_at_idx=20),
        ]

        result = fvg_within_order_block(
            fvg_zone_min=99.5, fvg_zone_max=100.5,
            fvg_direction="BULLISH",
            obs=obs,
            lookback=50,
            current_idx=15,
        )
        assert result is False, "OB not confirmed until bar 20 must not match at bar 15"

        # Once confirmed, it does count.
        result_after = fvg_within_order_block(
            fvg_zone_min=99.5, fvg_zone_max=100.5,
            fvg_direction="BULLISH",
            obs=obs,
            lookback=50,
            current_idx=20,
        )
        assert result_after is True, "OB confirmed at bar 20 should match at bar 20"

    def test_ob_outside_lookback_no_match(self):
        """An OB too far in the past should not match."""
        from backend.indicators.order_block import fvg_within_order_block

        obs = [
            self._make_ob("BULLISH", formed_at_idx=5),  # 95 bars ago
        ]

        result = fvg_within_order_block(
            fvg_zone_min=99.5, fvg_zone_max=100.5,
            fvg_direction="BULLISH",
            obs=obs,
            lookback=50,
            current_idx=100,
        )
        assert result is False, "OB outside lookback window must not match"

    def test_direction_mismatch_no_match(self):
        """Bearish FVG should not match a bullish OB."""
        from backend.indicators.order_block import fvg_within_order_block

        obs = [
            self._make_ob("BULLISH", formed_at_idx=30),
        ]

        result = fvg_within_order_block(
            fvg_zone_min=99.5, fvg_zone_max=100.5,
            fvg_direction="BEARISH",
            obs=obs,
            lookback=50,
            current_idx=50,
        )
        assert result is False, "Bearish FVG must not match a bullish OB"


class TestORBBasics:
    """Sanity checks for the ORB range logic."""

    def _make_candles(self, n=100):
        """Candles with a clear opening range then breakout."""
        candles = []
        for i in range(n):
            # First 3 bars: tight range 99-101
            # After bar 3: breakout to 105
            if i < 3:
                candles.append({"open": 100.0, "high": 101.0, "low": 99.0,
                                "close": 100.0, "volume": 1000.0})
            else:
                candles.append({"open": 101.0, "high": 105.0, "low": 101.0,
                                "close": 105.0, "volume": 1000.0})
        return candles

    def test_orb_detects_long_breakout(self):
        from backend.indicators.orb import detect_orb

        candles = self._make_candles(100)
        atr_series = [2.0] * len(candles)
        signals = detect_orb(
            candles, atr_series, range_length=3, rr_min=2.0,
            min_range_atr=0.5, max_range_atr=3.0,
            session_candles=12, start_idx=0, buffer_atr=0.25,
        )
        longs = [s for s in signals if s.direction == "LONG"]
        assert len(longs) > 0, "Should detect a LONG breakout"
        sig = longs[0]
        # Entry above range_high (101), stop below range_low (99)
        assert sig.entry_price > sig.range_high
        assert sig.stop_loss < sig.range_low

    def test_orb_rejects_too_tight_range(self):
        """A range narrower than min_range_atr should be rejected."""
        from backend.indicators.orb import detect_orb

        # Range width = 0.2 (99.9 to 100.1), ATR = 2.0 → range_width_atr = 0.1
        # min_range_atr = 0.5 → rejected
        candles = []
        for i in range(100):
            if i < 3:
                candles.append({"open": 100.0, "high": 100.1, "low": 99.9,
                                "close": 100.0, "volume": 1000.0})
            else:
                candles.append({"open": 100.1, "high": 102.0, "low": 100.1,
                                "close": 102.0, "volume": 1000.0})

        atr_series = [2.0] * len(candles)
        signals = detect_orb(
            candles, atr_series, range_length=3, rr_min=2.0,
            min_range_atr=0.5, max_range_atr=3.0,
            session_candles=12, start_idx=0, buffer_atr=0.25,
        )
        assert len(signals) == 0, "Too-tight range should produce no signals"


class TestObFvgLiveScanner:
    """OB+FVG as wired into scan_symbol (signal_service.detect_obfvg_candidates).

    The backtest enters each gap at the first 5m bar after its forming hour
    closes. The live scanner has no historical index to enter at, so it bounds
    eligibility by RECENCY instead: only FVGs on the last `max_age_bars` closed
    1h bars may fire. These tests pin that contract — an unbounded scan would
    re-enter gaps days old at today's price, a trade that was never measured.
    """

    @staticmethod
    def _candles(n, base=100.0, step=1.0):
        out = []
        for i in range(n):
            out.append({"open": base, "high": base + 1.0, "low": base - 1.0,
                        "close": base + 0.5, "volume": 10.0, "step": step})
        return out

    @staticmethod
    def _patched(monkeypatch, fvgs, obs):
        """Force detect_fvg / detect_order_blocks so tests don't depend on
        synthetic candles actually forming a gap + an order block."""
        import backend.services.signal_service as ss

        class _FakeFVG:
            def __init__(self, i, direction, zmin, zmax):
                self.direction, self.zone_min, self.zone_max = direction, zmin, zmax
                self.midpoint = (zmin + zmax) / 2
                self.bar_index = i

        monkeypatch.setattr(ss, "detect_fvg",
                            lambda k1h, symbol="": fvgs)
        monkeypatch.setattr(ss, "detect_order_blocks",
                            lambda k5m, **kw: obs)
        monkeypatch.setattr(ss, "evaluate_fvg_entry", _real_eval)
        return _FakeFVG

    def test_requires_order_block_confluence(self, monkeypatch):
        """An FVG with NO overlapping order block must not produce a candidate."""
        import backend.services.signal_service as ss
        from backend.indicators.order_block import OrderBlock
        from backend.indicators.fvg import evaluate_fvg_entry as _real_eval

        class _F:
            def __init__(self):
                self.direction, self.zone_min, self.zone_max = "BULLISH", 100.0, 110.0
                self.midpoint, self.bar_index = 105.0, 199

        monkeypatch.setattr(ss, "detect_fvg", lambda k1h, symbol="": [_F()])
        monkeypatch.setattr(ss, "detect_order_blocks", lambda k5m, **kw: [])
        out = ss.detect_obfvg_candidates(self._candles(200), self._candles(200), 5.0, 3.0)
        assert out == [], "no order blocks -> no confluence -> no candidate"

    def test_fires_when_order_block_overlaps_zone(self, monkeypatch):
        import backend.services.signal_service as ss
        from backend.indicators.order_block import OrderBlock

        class _F:
            def __init__(self):
                self.direction, self.zone_min, self.zone_max = "BULLISH", 100.0, 110.0
                self.midpoint, self.bar_index = 105.0, 199

        ob = OrderBlock(direction="BULLISH", zone_min=101.0, zone_max=106.0,
                        midpoint=103.5, high=106.0, low=101.0,
                        formed_at_idx=180, confirm_at_idx=190)

        monkeypatch.setattr(ss, "detect_fvg", lambda k1h, symbol="": [_F()])
        monkeypatch.setattr(ss, "detect_order_blocks", lambda k5m, **kw: [ob])
        out = ss.detect_obfvg_candidates(self._candles(200), self._candles(200), 5.0, 3.0)
        assert len(out) == 1
        direction, zmin, zmax, sl, tp, rr = out[0]
        assert direction == "BULLISH"
        # Entry is the zone midpoint; stop is one ATR below the zone floor.
        assert sl == 100.0 - 5.0
        assert rr == pytest.approx(3.0)

    def test_opposite_direction_order_block_is_not_confluence(self, monkeypatch):
        import backend.services.signal_service as ss
        from backend.indicators.order_block import OrderBlock

        class _F:
            def __init__(self):
                self.direction, self.zone_min, self.zone_max = "BULLISH", 100.0, 110.0
                self.midpoint, self.bar_index = 105.0, 199

        ob = OrderBlock(direction="BEARISH", zone_min=101.0, zone_max=106.0,
                        midpoint=103.5, high=106.0, low=101.0,
                        formed_at_idx=180, confirm_at_idx=190)

        monkeypatch.setattr(ss, "detect_fvg", lambda k1h, symbol="": [_F()])
        monkeypatch.setattr(ss, "detect_order_blocks", lambda k5m, **kw: [ob])
        out = ss.detect_obfvg_candidates(self._candles(200), self._candles(200), 5.0, 3.0)
        assert out == [], "a bearish OB does not confirm a bullish gap"

    def test_freshness_window_excludes_older_gaps(self, monkeypatch):
        """The measured edge was entered at formation. A gap from 10 bars ago
        is out of scope at max_age_bars=2."""
        import backend.services.signal_service as ss
        from backend.indicators.order_block import OrderBlock

        ob = OrderBlock(direction="BULLISH", zone_min=101.0, zone_max=106.0,
                        midpoint=103.5, high=106.0, low=101.0,
                        formed_at_idx=180, confirm_at_idx=190)

        def _mk(i):
            class _F:
                direction, zone_min, zone_max = "BULLISH", 100.0, 110.0
                midpoint, bar_index = 105.0, i
            return _F()

        monkeypatch.setattr(ss, "detect_order_blocks", lambda k5m, **kw: [ob])

        k1h = self._candles(200)
        for age, expected in ((0, 1), (1, 1), (2, 1), (3, 0), (50, 0)):
            idx = 199 - age
            monkeypatch.setattr(ss, "detect_fvg", lambda k, symbol="", idx=idx: [_mk(idx)])
            out = ss.detect_obfvg_candidates(self._candles(200), k1h, 5.0, 3.0,
                                             max_age_bars=2)
            assert len(out) == expected, \
                f"gap aged {age} bars should give {expected} candidate(s), got {len(out)}"

    def test_order_block_confirming_after_the_gap_is_rejected(self, monkeypatch):
        """confirm_at_idx must bound visibility — an OB that only confirms after
        the FVG cannot manufacture confluence."""
        import backend.services.signal_service as ss
        from backend.indicators.order_block import OrderBlock

        class _F:
            direction, zone_min, zone_max = "BULLISH", 100.0, 110.0
            midpoint, bar_index = 105.0, 199

        # Confirmed at 260, beyond current_idx (199) -> invisible.
        ob = OrderBlock(direction="BULLISH", zone_min=101.0, zone_max=106.0,
                        midpoint=103.5, high=106.0, low=101.0,
                        formed_at_idx=250, confirm_at_idx=260)

        monkeypatch.setattr(ss, "detect_fvg", lambda k1h, symbol="": [_F()])
        monkeypatch.setattr(ss, "detect_order_blocks", lambda k5m, **kw: [ob])
        assert ss.detect_obfvg_candidates(self._candles(200), self._candles(200), 5.0, 3.0) == []

    def test_zero_atr_yields_nothing(self, monkeypatch):
        import backend.services.signal_service as ss
        from backend.indicators.order_block import OrderBlock

        class _F:
            direction, zone_min, zone_max = "BULLISH", 100.0, 110.0
            midpoint, bar_index = 105.0, 199

        ob = OrderBlock(direction="BULLISH", zone_min=101.0, zone_max=106.0,
                        midpoint=103.5, high=106.0, low=101.0,
                        formed_at_idx=180, confirm_at_idx=190)

        monkeypatch.setattr(ss, "detect_fvg", lambda k1h, symbol="": [_F()])
        monkeypatch.setattr(ss, "detect_order_blocks", lambda k5m, **kw: [ob])
        assert ss.detect_obfvg_candidates(self._candles(200), self._candles(200), 0.0, 3.0) == []


class TestObFvgSettingsPlumbing:
    """The toggle must actually gate the scan, and the volume filter must read
    1h candles for OBFVG (it is a 1h pattern, not a 5m one)."""

    def test_toggle_gates_the_scan(self):
        import backend.services.signal_service as ss
        src = inspect.getsource(ss.SignalService.scan_symbol)
        # Default is False because OBFVG showed -0.34R/trade in production (15 trades).
        # Must still be toggleable — test verifies the gate exists, not the default.
        assert 'self.settings.get("strategy_obfvg_enabled"' in src, \
            "OBFVG must be gated on its toggle, like the other three strategies"

    def test_volume_filter_uses_1h_candles_for_obfvg(self):
        import backend.services.signal_service as ss
        src = inspect.getsource(ss.SignalService.scan_symbol)
        assert 'strategy_type in ("FVG", "OBFVG")' in src, \
            "OBFVG is a 1h pattern — a bare == 'FVG' check would route it to 5m volume"

    def test_settings_round_trip_defaults(self):
        from backend.schemas.settings import TradingSettingsResponse
        s = TradingSettingsResponse()
        assert s.strategy_obfvg_enabled is False
        assert s.strategy_obfvg_max_age_bars == 2
        assert s.max_aggregate_risk_pct == 3.0

    def test_update_schema_accepts_the_new_keys(self):
        from backend.schemas.settings import TradingSettingsUpdate
        body = TradingSettingsUpdate(strategy_obfvg_enabled=False,
                                     strategy_obfvg_max_age_bars=5)
        assert body.model_dump(exclude_unset=True) == {
            "strategy_obfvg_enabled": False, "strategy_obfvg_max_age_bars": 5,
        }
