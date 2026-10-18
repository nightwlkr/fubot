"""Acceptance tests for backtest trustworthiness.

`backend/backtest.py` shipped with five defects that made its output unusable.
Each test here names the defect it pins and FAILS if that defect returns.

  1. the 1000-bar page cap truncated 5m data to 3.5 days regardless of --days
  2. no fee modelling, so expectancy was GROSS while fee drag is the binding constraint
  3. MTF series aligned 1:1 by index across spans of 41.7 / 3.5 / 10.4 days
  4. entries filled at a 1h candle open while signals came from 5m and 15m
  5. the fill model did not match live LIMIT-at-midpoint with R:R re-validation

Defects 2, 4 and 5 are covered by Tasks 2-4, which append to this file.

Note on guard style: the harness defects are asserted BEHAVIOURALLY, by
truncation length, series identity and boundary position. Two earlier versions
banned substrings of `backtest.py`'s module TEXT — which fired on prose
describing the defect and were defeated by re-spacing the expression.

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


# ------------------------------------- 3 & 4. behavioural guards on the harness

def _mtf_series(n_1h=120):
    """Three intervals folded from ONE 5m path, so they share a price history.

    5m is the base grid; 15m folds 3 and 1h folds 12, all from the same start.
    So 1h bar `i` opens at exactly `i * 12` 5m bars and `i * 4` 15m bars —
    which is what makes the bar-count assertion below a wall-clock property.
    """
    import random
    rnd = random.Random(26)
    px, p5 = 100.0, []
    for _ in range(n_1h * 12):
        o = px
        c = o + rnd.gauss(-0.05, 0.8)
        h = max(o, c) + abs(rnd.gauss(0, 0.4))
        l = min(o, c) - abs(rnd.gauss(0, 0.4))
        p5.append((o, h, l, c))
        px = c

    def fold(stride):
        return [(p5[i][0], max(g[1] for g in p5[i:i + stride]),
                 min(g[2] for g in p5[i:i + stride]), p5[i + stride - 1][3])
                for i in range(0, len(p5) - stride + 1, stride)]

    def mk(bars, step):
        start = 1_700_000_000_000
        return [{"open_time": start + i * step, "open": o, "high": h, "low": l,
                 "close": c, "volume": 1000.0,
                 "close_time": start + i * step + step - 1}
                for i, (o, h, l, c) in enumerate(bars)]

    return mk(fold(12), MS_1H), mk(p5, MS_5M), mk(fold(3), MS_15M)


class TestHarnessAlignment:
    """Both were once raw substring bans over `backtest.py`'s module TEXT.

    A text ban is a trap in both directions: it fires on prose describing the
    defect (it already forced one docstring rewrite) and it is trivially evaded
    by re-spacing the expression. These are the BEHAVIOURAL replacements — each
    fails on the defect it names regardless of how the code is written.
    """

    def test_detection_is_truncated_by_wall_clock_not_by_proportional_index(self):
        """DEFECT 3. At `at_idx = i`, the detector must see exactly the bars that
        had CLOSED by `k1h[i]["open_time"]` — `i * 12` of 5m and `i * 4` of
        15m. Proportional indexing produced some unrelated count that happens
        to depend only on the two series' LENGTHS, which is what mapped 1h bar
        500 onto 5m bar 500 (hour 500 vs hour 41.7).

        Measured by handing the backtester FIVE TIMES as many 15m bars as 1h
        bars: a proportional scheme sees 5x the 1h count scaled by its own
        length and lands somewhere else entirely."""
        import backend.backtest as btmod
        from backend.backtest import Backtester
        k1h, k5m, k15m = _mtf_series()
        bt = Backtester(settings={"rr_min": 3.0, "trade_min_trend_strength": "WEAK",
                                  "trade_neutral_markets": True})
        orig = {"k5m": btmod.detect_three_candles, "k15m": btmod.detect_choch}
        seen = {}

        def wrap(fn, key):
            def inner(candles, *a, **kw):
                seen[key] = len(candles)
                return fn(candles, *a, **kw)
            return inner

        btmod.detect_three_candles = wrap(orig["k5m"], "n5m")
        btmod.detect_choch = wrap(orig["k15m"], "n15m")
        try:
            for i in (70, 90, 110):
                seen.clear()
                bt._detect_candidates(k1h, k5m, k15m, i)
                assert seen["n5m"] == i * 12, (
                    f"at 1h bar {i} the 3-candle detector saw {seen['n5m']} 5m "
                    f"bars, expected {i * 12}")
                assert seen["n15m"] == i * 4, (
                    f"at 1h bar {i} the CHoCH detector saw {seen['n15m']} 15m "
                    f"bars, expected {i * 4}")
        finally:
            btmod.detect_three_candles = orig["k5m"]
            btmod.detect_choch = orig["k15m"]

    def test_the_filter_bias_is_computed_from_the_detectors_own_view(self):
        """The bias the filters read must come from the SAME truncated 1h view
        the detectors see — `k1h[:k1h_idx + 1]`, which EXCLUDES the still-forming
        detection bar `at_idx`.

        `run` used to recompute it as `analyze_trend_bias(k1h[:i + 1])`, giving
        the filters one bar the detector could not see — lookahead in the filter
        that no other test could reach, because `_detect_candidates` returns the
        bias it computed and `run` had no other way to get one."""
        import backend.backtest as btmod
        from backend.backtest import Backtester
        k1h, k5m, k15m = _mtf_series()
        bt = Backtester(settings={"rr_min": 3.0, "trade_min_trend_strength": "WEAK",
                                  "trade_neutral_markets": True})
        orig = btmod.analyze_trend_bias
        seen = {}

        def recording(closes, *a, **kw):
            seen["n"] = len(closes)
            return orig(closes, *a, **kw)

        btmod.analyze_trend_bias = recording
        try:
            # Drive the REAL `run` loop (the old code recomputed the bias there,
            # so a test that only called `_detect_candidates` could not see it).
            # For every 1h bar `i` the loop evaluates, the bias must see exactly
            # `i` closes: `last_closed_before` at k1h[i]["open_time"] returns
            # i-1, so bar `i` — still forming — is excluded. The old code passed
            # `k1h[:i + 1]` and saw i+1.
            recorded = []
            btmod.analyze_trend_bias = lambda closes, *a, **kw: (
                recorded.append(len(closes)) or orig(closes, *a, **kw))
            bt.run("X", k1h, k5m, k15m)
            assert recorded, "no bias was computed at all"
            # `run` starts at bar 60 and strides by `vol_stride` = 10.
            evaluated = [i for i in range(60, len(k1h)) if i % 10 == 0]
            assert max(recorded) == max(evaluated), (
                f"the widest bias view was {max(recorded)} closes; the last bar "
                f"the loop evaluates is index {max(evaluated)} and is still "
                f"forming, so the bias must see exactly that many")
            assert min(recorded) == min(evaluated), \
                "every evaluated bar must exclude itself from the bias"
        finally:
            btmod.analyze_trend_bias = orig

    def test_a_fill_is_attempted_on_the_series_the_signal_came_from(self):
        """DEFECT 4. The series handed to `fill_at_midpoint` must BE the signal's
        own list — asserted by IDENTITY (`is`), not by length or content.

        This replaces a source-text ban on the old `k1h[i + 1]["open"]` fill.
        Identity is the only assertion that can actually fail: two of the three
        series can share a length, and a re-implemented wrong-interval lookup
        would still satisfy any looser check."""
        import backend.backtest as btmod
        from backend.backtest import Backtester
        k1h, k5m, k15m = _mtf_series()
        real_fill = btmod.fill_at_midpoint
        seen = {}

        def recording_fill(series, signal_idx, lo, hi, max_age_ms):
            seen.setdefault("series", []).append(series)
            return real_fill(series, signal_idx, lo, hi, max_age_ms)

        btmod.fill_at_midpoint = recording_fill
        try:
            base = {"rr_min": 3.0, "trade_min_trend_strength": "WEAK",
                    "trade_neutral_markets": True, "volume_filter_enabled": False}
            runs = {
                "FVG": Backtester(settings={**base, "strategy_3candle_enabled": False,
                                            "strategy_choch_enabled": False}),
                "3CANDLE": Backtester(settings={**base, "strategy_fvg_enabled": False,
                                                "strategy_choch_enabled": False}),
                "CHOC": Backtester(settings={**base, "strategy_fvg_enabled": False,
                                             "strategy_3candle_enabled": False}),
            }
            expected = {"FVG": k1h, "3CANDLE": k5m, "CHOC": k15m}
            for name, bt in runs.items():
                seen.clear()
                r = bt.run("X", k1h, k5m, k15m)
                assert seen["series"], f"{name}: no fill was even attempted"
                assert r.trades, f"{name}: fixture produced no trades"
                wrong = [s for s in seen["series"] if s is not expected[name]]
                assert not wrong, (
                    f"{name} signals must fill on {expected[name]!r}; "
                    f"{len(wrong)} attempt(s) used a different series")
        finally:
            btmod.fill_at_midpoint = real_fill


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
    def test_a_fill_happens_at_the_zone_midpoint_when_price_reaches_it(self):
        """Live places a LIMIT at the zone midpoint; the fill is that price.

        Every other bar is parked AWAY from the zone. The previous version built
        `synth(close=100.0)` — which already emits low=99/high=101 — and so
        touched the [99,101] zone on bar 1, making "the fill is at index 2"
        true by accident rather than by the scan finding bar 2 first."""
        from backend.backtest import fill_at_midpoint
        end = 1_700_000_000_000
        start = end - 60 * MS_5M
        k5m = synth(start, MS_5M, 12, close=100.0)
        for c in k5m:
            c["low"], c["high"] = 200.0, 201.0
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

    def test_expiry_is_measured_from_the_signals_close_not_its_open(self):
        """Live only learns a signal once its bar has CLOSED, so
        `signal_max_age_minutes` counts from the CLOSE. Measuring from
        `open_time` grants the signal up to one extra bar's life.

        HONEST SCOPE: the offset between the two deadlines is `MS_5M - 1` ms,
        so the rules only diverge for a window that is not a whole multiple of
        the bar size. With a round `signal_max_age_minutes` (60 min = 12 5m bars)
        they agree, which is why this test uses a deliberately unaligned window —
        a whole-bar window cannot tell the two implementations apart, and
        claiming otherwise would be a test that passes for the wrong reason."""
        from backend.backtest import fill_at_midpoint
        start = 1_700_000_000_000 - 600 * MS_5M
        k5m = synth(start, MS_5M, 600, close=100.0)
        for c in k5m:
            c["low"], c["high"] = 200.0, 201.0
        k5m[4]["low"], k5m[4]["high"] = 99.5, 100.5
        # Bar 1 opens at `start` and closes at `start + MS_5M - 1`. A window of
        # 2.5 bars from the CLOSE ends at start+3.5 bars, reaching bar 4; the
        # same window from the OPEN ends at start+3 bars and does not.
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0,
                                max_age_ms=2 * MS_5M + MS_5M // 2) == (4, pytest.approx(100.0)), \
            "bar 4 is inside 2.5 bars of the signal's CLOSE"
        # And the window is genuinely bounded — a shorter one still excludes it.
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0,
                                max_age_ms=2 * MS_5M) is None, \
            "bar 4 is outside 2 bars of the close"

    def test_a_signal_bar_with_no_close_time_never_fills(self):
        """Fail closed. An unreadable signal age must not read as a fresh signal
        — the same posture as `align_window` and `last_closed_before` dropping
        candles with no `close_time`."""
        from backend.backtest import fill_at_midpoint
        end = 1_700_000_000_000
        start = end - 60 * MS_5M
        k5m = synth(start, MS_5M, 12, close=100.0)
        for c in k5m:
            c["low"], c["high"] = 200.0, 201.0
        k5m[2]["low"], k5m[2]["high"] = 99.5, 100.5
        k5m[1].pop("close_time")
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
        assert rr_at_fill(97.0, 109.0, "LONG", 102.0, 3.0) < 3.0
        # A fill that moves TOWARD the stop/target can IMPROVE the ratio — the
        # re-check is not a blanket penalty, it is a re-measurement.
        assert rr_at_fill(97.0, 109.0, "LONG", 98.0, 3.0) > 3.0
        # No readable risk at the fill (stop exactly at the fill) yields 0.0, so
        # the caller skips rather than dividing by zero.
        assert rr_at_fill(100.0, 109.0, "LONG", 100.0, 3.0) == 0.0

# ------------------------------------------------------------- 2. the cost model

class TestCostModel:
    def test_costs_are_actually_charged(self):
        """DEFECT 2. There was no fee modelling at all: expectancy was GROSS.
        Panel cycle 1 concluded fee drag, not exit geometry, is the binding
        constraint, so a gross-only harness rates losing strategies as winners."""
        import inspect
        import backend.backtest as bt
        assert "FEE_ROUND_TRIP" in inspect.getsource(bt), \
            "the harness must charge round-trip costs"

    def test_net_r_is_never_better_than_gross(self):
        from backend.backtest import BacktestTrade
        t = BacktestTrade(symbol="X", strategy="S", direction="LONG",
                          entry=100.0, stop=99.0, take_profit=103.0,
                          entry_idx=0)
        t.exit_idx, t.exit_price, t.exit_reason = 5, 103.0, "TP_HIT"
        t._risk_dist = 1.0
        t.finalise()
        assert t.net_r <= t.pnl_r, "costs can only reduce"
        assert t.net_r < t.pnl_r, "a zero-cost harness is the defect"

    def test_a_wide_stop_pays_more_in_fees_than_a_tight_one(self):
        """Fees are a fraction of NOTIONAL, but R divides by the risk distance.
        The same 0.07% therefore costs more R when the stop is tight — which is
        exactly the effect cycle 1 measured (fee drag 4x at tight stops)."""
        from backend.backtest import fee_in_r
        tight = fee_in_r(entry=100.0, risk_dist=0.5)
        wide = fee_in_r(entry=100.0, risk_dist=5.0)
        assert tight > wide
        assert tight == pytest.approx(100.0 * 0.0007 / 0.5)

    def test_the_real_close_path_charges_the_cost(self):
        """The two tests above call `finalise()` themselves, so they stay green
        if `_close` forgets to — and a harness that never calls it reports gross
        expectancy from `run()`. Drive the actual exit scan."""
        from backend.backtest import Backtester, BacktestTrade
        series = [
            {"open_time": 0, "close_time": MS_5M - 1,
             "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0},
            {"open_time": MS_5M, "close_time": 2 * MS_5M - 1,
             "open": 100.0, "high": 103.5, "low": 99.5, "close": 103.0},
        ]
        t = BacktestTrade(symbol="X", strategy="S", direction="LONG",
                          entry=100.0, stop=99.0, take_profit=103.0,
                          entry_idx=0, timeframe="5m")
        t._risk_dist = 1.0
        Backtester()._resolve_exit(t, series)
        assert t.exit_reason == "TP_HIT"
        assert t.pnl_r == pytest.approx(3.0)
        assert t.net_r == pytest.approx(3.0 - 100.0 * 0.0007 / 1.0)

    def test_the_summary_reports_net_and_flags_it_as_the_number_to_trust(self):
        from backend.backtest import BacktestResult, BacktestTrade
        res = BacktestResult(symbol="X", candles=100)
        t = BacktestTrade(symbol="X", strategy="S", direction="LONG",
                          entry=100.0, stop=99.0, take_profit=103.0,
                          entry_idx=0)
        t.exit_idx, t.exit_price, t.exit_reason = 5, 103.0, "TP_HIT"
        t._risk_dist = 1.0
        t.finalise()
        res.trades.append(t)
        s = res.summary()
        assert "net_r" in s and "expectancy_net_r" in s
        assert s["net_r"] < s["total_r"]
