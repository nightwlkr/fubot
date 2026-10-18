"""Acceptance tests for backtest trustworthiness.

`backend/backtest.py` shipped with five defects that made its output unusable.
Each test here names the defect it pins and FAILS if that defect returns.

  1. the 1000-bar page cap truncated 5m data to 3.5 days regardless of --days
  2. no fee modelling, so expectancy was GROSS while fee drag is the binding constraint
  3. MTF series aligned 1:1 by index across spans of 41.7 / 3.5 / 10.4 days
  4. entries filled at a 1h candle open while signals came from 5m and 15m
  5. the fill model did not match live LIMIT-at-midpoint with R:R re-validation

Defects 2, 4 and 5 are covered by Tasks 2-4, which append to this file.

Spec: docs/superpowers/specs/2026-10-19-backtest-harness-trustworthiness-design.md
"""
from datetime import datetime, timedelta, timezone

import pytest

MS_5M = 5 * 60 * 1000
MS_15M = 15 * 60 * 1000
MS_1H = 60 * 60 * 1000


def synth(start_ms: int, step_ms: int, n: int, close: float = 100.0):
    """Build `n` synthetic candles on a fixed grid."""
    out = []
    for i in range(n):
        out.append({
            "open_time": start_ms + i * step_ms,
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1000.0,
            "close_time": start_ms + (i + 1) * step_ms - 1,
        })
    return out


def forming(open_time: int, step_ms: int, close: float = 100.0) -> dict:
    """A single candle that has OPENED but is not yet closed at `open_time`."""
    return {
        "open_time": open_time,
        "open": close, "high": close + 1, "low": close - 1,
        "close": close, "volume": 1000.0,
        "close_time": open_time + step_ms - 1,
    }


# ------------------------------------------------------- 1. pagination / volume

class TestPagination:
    def test_a_45_day_window_yields_a_full_45_days_of_5m_bars(self):
        """DEFECT 1. The old code did `limit = min(days * 24, 1000)` for EVERY
        interval, so 5m was always 1000 bars = 3.47 days no matter what --days
        said. 45 days of 5m is 45 * 288 = 12,960 bars."""
        from backend.backtest_data import bars_expected
        assert bars_expected("5m", 45) == 45 * 288
        assert bars_expected("1h", 45) == 45 * 24
        assert bars_expected("15m", 45) == 45 * 96

    def test_the_fetch_pages_until_the_window_is_covered(self):
        """A page cap must never decide how much history we get; the WINDOW
        must. Assert on the paging arithmetic, not on the network."""
        from backend.backtest_data import page_ranges
        end = 1_700_000_000_000
        start = end - 45 * 86_400_000
        ranges = page_ranges(start, end, page_size=1500)
        covered = sum(b - a for a, b in ranges)
        assert covered >= end - start, "pages must cover the whole window"
        assert len(ranges) > 1, "45 days cannot fit in one 1500-bar page"

    def test_pages_are_contiguous_and_ascending(self):
        from backend.backtest_data import page_ranges
        end = 1_700_000_000_000
        start = end - 10 * 86_400_000
        ranges = page_ranges(start, end, page_size=1500)
        assert ranges[0][0] == start, "pages must start at the window start"
        assert ranges[-1][1] == end, "pages must end at the window end"
        for (a1, b1), (a2, b2) in zip(ranges, ranges[1:]):
            assert a2 <= b1, "pages must not leave a gap"
            assert (a1, b1) < (a2, b2), "pages must ascend"


# ------------------------------------------------- 3. multi-timeframe alignment

class TestWindowAlignment:
    def test_all_intervals_are_pinned_to_one_wall_clock_window(self):
        """DEFECT 3. The old code fetched 1000 bars of EACH interval, giving
        spans of 41.7 / 3.5 / 10.4 days, then mapped them 1:1 BY INDEX -- so 1h
        bar 500 (hour 500) aligned to 5m bar 500 (hour 41.7). Pin the window and
        the spans agree by construction.

        Each series is padded on BOTH sides of the window so an `align_window`
        that does no filtering at all still fails these assertions."""
        from backend.backtest_data import align_window
        end = 1_700_000_000_000
        start = end - 45 * 86_400_000
        pad = 12 * MS_1H
        k1h = synth(start - pad, MS_1H, 12 + 45 * 24 + 12)
        k5m = synth(start - pad, MS_5M, pad // MS_5M + 45 * 288 + 12 * 12)
        k15m = synth(start - pad, MS_15M, pad // MS_15M + 45 * 96 + 12 * 4)
        aligned = align_window({"1h": k1h, "5m": k5m, "15m": k15m}, start, end)
        for tf, series in aligned.items():
            assert series, f"{tf}: window should not be empty"
            assert series[0]["open_time"] >= start, f"{tf}: pre-window candles kept"
            assert series[-1]["close_time"] < end, f"{tf}: post-window candles kept"
            assert series == sorted(series, key=lambda c: c["open_time"]), \
                f"{tf}: series must ascend"
        # All three now span the SAME wall-clock window.
        spans = {tf: (s[0]["open_time"], s[-1]["close_time"])
                 for tf, s in aligned.items()}
        assert len(set(spans.values())) == 1, \
            f"intervals span different windows: {spans}"

    def test_the_still_forming_last_candle_is_dropped(self):
        """The final bar of each series is still open; using it is lookahead."""
        from backend.backtest_data import align_window
        end = 1_700_000_000_000
        start = end - 3 * 86_400_000
        # The last candle OPENS before `end` but is not closed until after it,
        # so it must not be treated as knowable.
        stray = end - MS_5M // 2
        candles = synth(start, MS_5M, 100) + [forming(stray, MS_5M)]
        aligned = align_window({"5m": candles}, start, end)["5m"]
        assert stray not in {c["open_time"] for c in aligned}, \
            "the candle straddling `end` must have been dropped"
        assert aligned[-1]["close_time"] < end, \
            "a candle still forming at `end` must not be in the data"

    def test_alignment_joins_on_open_time_not_index(self):
        """The actual bug: index-proportional slicing. Two series that start at
        DIFFERENT wall-clock times must not be silently stitched together."""
        from backend.backtest_data import align_window
        end = 1_700_000_000_000
        start = end - 2 * 86_400_000
        k5m = synth(start, MS_5M, 500)          # covers start .. start+41h
        k1h = synth(start + 10 * MS_1H, MS_1H, 30)  # starts 10h LATER
        aligned = align_window({"5m": k5m, "1h": k1h}, start, end)
        overlap = ({c["open_time"] for c in aligned["5m"]}
                   & {c["open_time"] for c in aligned["1h"]})
        # Only genuinely co-timed candles may overlap.
        assert all(t >= start + 10 * MS_1H for t in overlap)
        assert overlap, "sanity: the two series do genuinely overlap"