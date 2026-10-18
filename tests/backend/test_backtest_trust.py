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


# ------------------------------------------------------------ 1. real paging

def range_fetch_page(calls: list | None = None):
    """A `fetch_page` that HONOURS the requested range, like a real endpoint.

    Returns every bar of `interval` whose open_time falls in [a, b). This is the
    behaviour that broke the `len(page) < PAGE_SIZE` heuristic: on a coarse
    interval a whole range yields far fewer than 1500 bars, which a naive
    implementation reads as "last page".
    """
    from backend.backtest_data import INTERVAL_MS

    async def fetch_page(symbol: str, interval: str, a: int, b: int):
        if calls is not None:
            calls.append((symbol, interval, a, b))
        ms = INTERVAL_MS[interval]
        first = -(-a // ms) * ms  # first bar with open_time >= a
        return [
            {"open_time": t, "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0, "volume": 1000.0, "close_time": t + ms - 1}
            for t in range(first, b, ms)
        ]
    return fetch_page


class TestFetchKlines:
    def test_no_page_can_request_more_bars_than_the_remote_cap(self):
        """No range may ask for more bars than the endpoint will return, or the
        overflow is silently dropped and the window is never revisited. This is
        what the interval-aware step fixes: a fixed 5m step over-requests by 12x
        on 1m and loses 6,000 bars per page. This is the ONLY test that pins the
        interval-aware step: both `page_ranges` tests above use the default
        interval, and for 5m a fixed 5m step is exactly correct."""
        from backend.backtest_data import INTERVAL_MS, PAGE_SIZE, page_ranges
        end = 1_700_000_000_000
        start = end - 45 * 86_400_000
        for tf, ms in INTERVAL_MS.items():
            for a, b in page_ranges(start, end, tf):
                assert (b - a) // ms <= PAGE_SIZE, (
                    f"{tf} page asks for {(b - a) // ms} bars, cap is {PAGE_SIZE}"
                )

    @pytest.mark.parametrize("interval", ["1m", "1h", "5m", "15m"])
    def test_every_interval_gets_its_full_window(self, interval):
        """The defect this pins: `page_ranges` stepped by a fixed 5m duration, so
        a 1500-bar page was 5.2 days on 5m but 62 days on 1h. Combined with a
        `len(page) < PAGE_SIZE` break, every interval but 5m stopped after ONE
        page -- a 45-day window returned 500/125 bars of 15m/1h instead of
        4320/1080. Recreates defect 3 (mismatched spans) at smaller scale."""
        import asyncio
        from backend.backtest_data import bars_expected, fetch_klines
        end = 1_700_000_000_000
        start = end - 45 * 86_400_000
        got = asyncio.run(fetch_klines(range_fetch_page(), "BTCUSDT",
                                       interval, start, end, sleep=0.0))
        assert len(got) == bars_expected(interval, 45), \
            f"{interval}: got {len(got)} bars, want {bars_expected(interval, 45)}"

    def test_result_is_ascending_and_deduplicated(self):
        """Overlapping ranges must not duplicate, and output must ascend."""
        import asyncio
        from backend.backtest_data import fetch_klines
        end = 1_700_000_000_000
        start = end - 10 * 86_400_000
        got = asyncio.run(fetch_klines(range_fetch_page(), "BTCUSDT",
                                       "15m", start, end, sleep=0.0))
        times = [c["open_time"] for c in got]
        assert times == sorted(times), "output must ascend by open_time"
        assert len(times) == len(set(times)), "overlapping pages must not duplicate"

    def test_result_is_filtered_to_the_window_even_if_the_remote_ignores_it(self):
        """DEFECT 1's shape, silently. The only production fetch method is
        `get_klines(symbol, interval, limit)` — it has NO time parameters, so an
        adapter built naively on it returns the newest 1500 bars for EVERY range.
        Dedup then collapses them and you get 1500 recent candles with no error.
        `fetch_klines` must enforce its own contract, not trust the adapter."""
        import asyncio
        from backend.backtest_data import fetch_klines

        end = 1_700_000_000_000
        start = end - 45 * 86_400_000

        async def ignores_range(symbol, interval, a, b):
            # Deliberately ignores [a, b) and returns RECENT bars, as a naive
            # limit-based adapter would. These are all AFTER the window ends.
            recent = range(end, end + 1500 * 60_000, 60_000)
            return [{"open_time": t, "close_time": t + 59_999} for t in recent]

        got = asyncio.run(fetch_klines(ignores_range, "BTCUSDT", "1m",
                                       start, end, sleep=0.0))
        assert got == [], (
            f"candles outside the window must be dropped, got {len(got)}"
        )

    def test_an_empty_page_mid_window_neither_hangs_nor_truncates_silently(self):
        """A short or empty page is NOT a terminator. Breaking on one is the exact
        silent-truncation defect; the loop must instead keep advancing through the
        finite ranges and return everything it did get."""
        import asyncio
        from backend.backtest_data import fetch_klines

        from backend.backtest_data import bars_expected, page_ranges
        calls: list = []
        inner = range_fetch_page()

        # 5m over 12 days spans 3 pages, so page 2 is genuinely mid-window.
        end = 1_700_000_000_000
        start = end - 12 * 86_400_000
        pages = page_ranges(start, end, "5m")
        assert len(pages) >= 3, "this test needs a multi-page window"

        async def flaky(symbol, interval, a, b):
            calls.append((symbol, interval, a, b))
            if len(calls) == 2:
                return []          # a mid-window hole
            return await inner(symbol, interval, a, b)

        got = asyncio.run(fetch_klines(flaky, "BTCUSDT", "5m",
                                       start, end, sleep=0.0))
        assert len(calls) == len(pages), "must walk every range, hole or not"
        assert got, "must return what it did get"
        assert len(got) < bars_expected("5m", 12), \
            "the hole must cost bars -- if not, the stub is not being exercised"
        assert max(c["open_time"] for c in got) >= pages[-1][0], \
            "paging continued past the hole; it did not stop there"


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

    def test_alignment_sorts_a_descending_series(self):
        """The spec mandates that align_window "sorts ascending", but nothing
        tested it: `synth()` only ever emits ascending candles, so deleting the
        `sorted()` call left the whole suite green. Paginated fetches can arrive
        out of order, so the ordering is load-bearing rather than cosmetic."""
        from backend.backtest_data import align_window
        end = 1_700_000_000_000
        start = end - 3 * 86_400_000
        candles = synth(start, MS_5M, 100)
        shuffled = list(reversed(candles))
        aligned = align_window({"5m": shuffled}, start, end)["5m"]
        times = [c["open_time"] for c in aligned]
        assert times == sorted(times), "align_window must sort ascending"

    def test_align_window_leaves_each_series_open_time_untouched(self):
        """`align_window` must NOT stitch series together. The index-proportional
        slicing that did so lived in `backtest.py`, not here — what this module
        owes is that each series keeps its own open_time so a consumer can join
        explicitly. Two series starting at DIFFERENT wall-clock times must come
        out still starting at different times."""
        from backend.backtest_data import align_window
        end = 1_700_000_000_000
        start = end - 2 * 86_400_000
        k5m = synth(start, MS_5M, 500)             # covers start .. start+41h
        k1h = synth(start + 10 * MS_1H, MS_1H, 30)  # starts 10h LATER
        aligned = align_window({"5m": k5m, "1h": k1h}, start, end)
        assert aligned["5m"][0]["open_time"] == start
        assert aligned["1h"][0]["open_time"] == start + 10 * MS_1H
        assert aligned["5m"][0]["open_time"] != aligned["1h"][0]["open_time"]


# ------------------------------------------------- 3. defect 3, still in backtest.py

def test_backtest_no_longer_aligns_series_by_proportional_index():
    """DEFECT 3 lived here: `_detect_candidates` sliced each lower interval at its
    own length scaled by the 1h index — so 1h bar 500 aligned to 5m bar 500, hour
    500 against hour 41.7. The harness now truncates each interval by WALL-CLOCK
    time through `_source_index`, so the trend filter and the entries it filters
    read the same instant.

    The guard is a raw substring over the module TEXT, so this docstring must not
    quote the arithmetic it bans — a document that describes the defect verbatim
    trips its own guard (the same class of mistake the repo's other text-based
    guards hit). The expression is written out in the commit message instead."""
    import pathlib

    import backend.backtest as bt
    src = pathlib.Path(bt.__file__).read_text()
    assert "(at_idx + 1) // len(k1h)" not in src, \
        "series are still being aligned by proportional index"


# ------------------------------------------------------- 4. untested public helpers

class TestHelpers:
    def test_last_closed_before(self):
        """-1 before any candle has closed; the right index once one has. Also
        pins the fail-closed rule: a candle with NO close_time is treated as NOT
        closed rather than defaulting to open_time (which would look closed)."""
        from backend.backtest_data import last_closed_before
        start = 1_700_000_000_000
        candles = synth(start, MS_5M, 10)  # bar i closes at start+(i+1)*5m-1
        assert last_closed_before(candles, start) == -1, \
            "nothing is closed at the very first open_time"
        # Bar i closes at start + (i+1)*5m - 1, so at t = start + 5m exactly one
        # bar has closed, and at t = start + 50m all ten have.
        assert last_closed_before(candles, start + MS_5M) == 0
        assert last_closed_before(candles, start + 10 * MS_5M) == 9
        # Fail closed: unknown close_time must not read as already-closed.
        blind = [dict(c) for c in candles[:3]]
        for c in blind:
            c.pop("close_time")
        assert last_closed_before(blind, start + 10 * MS_5M) == -1

    def test_window_for_days_ends_on_a_5m_boundary_and_spans_exactly_days(self):
        """This is the single place the anti-lookahead boundary is chosen, so it
        is pinned directly: the window must end ON a 5m boundary (never mid-bar)
        and span exactly `days`."""
        from backend.backtest_data import INTERVAL_MS, window_for_days
        ms_5m = INTERVAL_MS["5m"]
        # A deliberately unaligned "now" (mid-bar).
        now = 1_700_000_000_000 + 137_000
        start, end = window_for_days(30, now_ms=now)
        assert end % ms_5m == 0, "window must end on a 5m boundary"
        assert end <= now, "must not reach into the future"
        assert end - start == 30 * 86_400_000, "must span exactly `days`"


# ------------------------------------------------------- 4 & 5. the fill model

class TestFillModel:
    def test_the_fill_comes_from_the_signals_own_interval(self):
        """DEFECT 4. The old code filled at `k1h[i + 1]["open"]` while 3CANDLE
        signals come from 5m and CHoCH from 15m — a 12x time-scale mismatch."""
        import inspect

        import backend.backtest as bt
        src = inspect.getsource(bt.Backtester.run)
        assert 'k1h[i + 1]["open"]' not in src, \
            "fills must not be taken from the 1h series"

    def test_a_fill_happens_at_the_zone_midpoint_when_price_reaches_it(self):
        """Live places a LIMIT at the zone midpoint; the fill is that price."""
        from backend.backtest import fill_at_midpoint
        end = 1_700_000_000_000
        start = end - 60 * MS_5M
        k5m = synth(start, MS_5M, 12, close=100.0)
        k5m[2]["low"], k5m[2]["high"] = 99.5, 100.5
        idx, price = fill_at_midpoint(k5m, 1, 99.0, 101.0, max_age_ms=60 * MS_5M)
        assert idx == 2
        assert price == pytest.approx(100.0)

    def test_no_fill_when_price_never_reaches_the_zone(self):
        from backend.backtest import fill_at_midpoint
        end = 1_700_000_000_000
        start = end - 60 * MS_5M
        k5m = synth(start, MS_5M, 12, close=100.0)
        for c in k5m:
            c["low"], c["high"] = 200.0, 201.0
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0, max_age_ms=60 * MS_5M) is None

    def test_a_signal_older_than_the_window_never_fills(self):
        """Live expires stale signals (`signal_max_age_minutes`). An old signal
        filling at today's price is not a trade the bot would have taken."""
        from backend.backtest import fill_at_midpoint
        end = 1_700_000_000_000
        start = end - 600 * MS_5M
        k5m = synth(start, MS_5M, 600, close=100.0)
        # Price parks far above the zone for the whole series, so the ONLY bar
        # that could fill is bar 500 — 499 bars after the signal.
        for c in k5m:
            c["low"], c["high"] = 200.0, 201.0
        k5m[500]["low"], k5m[500]["high"] = 99.5, 100.5
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0, max_age_ms=2 * MS_5M) is None
        # ...and it IS reachable when the window is wide enough, so the
        # assertion above is about the AGE and not about bar 500 being special.
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0,
                                max_age_ms=600 * MS_5M) == (500, pytest.approx(100.0))

    def test_the_scan_starts_after_the_signal_bar_not_on_it(self):
        """ANTI-LOOKAHEAD. The signal exists only once its bar has CLOSED, so
        scanning from `signal_idx` itself would fill on information that did not
        exist when the signal was raised. Nothing above catches this: the other
        tests either place the touchable bar after the signal or rely on the age
        deadline, and this module's `synth()` writes `close_time`, so a bar is
        always "closed" the instant it is addressed."""
        from backend.backtest import fill_at_midpoint
        end = 1_700_000_000_000
        start = end - 60 * MS_5M
        k5m = synth(start, MS_5M, 12, close=100.0)
        # The SIGNAL bar itself touches the zone. It is closed, but it was not
        # knowable until after it closed — so it cannot also be the fill bar.
        k5m[1]["low"], k5m[1]["high"] = 99.5, 100.5
        # The next bar also touches it, so the fill must land there (index 2),
        # never on the signal bar (index 1).
        k5m[2]["low"], k5m[2]["high"] = 99.5, 100.5
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0,
                                max_age_ms=60 * MS_5M) == (2, pytest.approx(100.0))

    def test_r_r_is_revalidated_at_the_fill_price_not_at_the_signal(self):
        """Live re-checks R:R at the ACTUAL fill and skips if it no longer
        qualifies. A setup planned at 3:1 around a 100.0 midpoint (stop 97.0,
        target 109.0) is exactly 3.0 there, but a LIMIT that fills only when the
        market comes back UP to 102.0 has widened its risk to 5.0 and left 7.0
        of reward — 1.4:1. Live skips that (`gate 4` at the real fill), so the
        backtest must skip it too or it counts trades the bot never took."""
        from backend.backtest import rr_at_fill
        assert rr_at_fill(97.0, 109.0, "LONG", 100.0, 3.0) == pytest.approx(3.0)
        assert rr_at_fill(97.0, 109.0, "LONG", 100.0, 3.0) >= 3.0
        assert rr_at_fill(97.0, 109.0, "LONG", 102.0, 3.0) < 3.0
        # A fill that moves TOWARD the stop/target can IMPROVE the ratio — the
        # re-check is not a blanket penalty, it is a re-measurement.
        assert rr_at_fill(97.0, 109.0, "LONG", 98.0, 3.0) > 3.0
        # No readable risk at the fill (stop exactly at the fill) yields 0.0, so
        # the caller skips rather than dividing by zero.
        assert rr_at_fill(100.0, 109.0, "LONG", 100.0, 3.0) == 0.0