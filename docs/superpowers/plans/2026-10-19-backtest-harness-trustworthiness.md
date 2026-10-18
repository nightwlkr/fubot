# Backtest Harness Trustworthiness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `backend/backtest.py` measure what live trading actually does, so strategy results can be trusted.

**Architecture:** A new `backend/backtest_data.py` owns candle fetching and window alignment (paginated; all intervals pinned to one wall-clock window; series joined on `open_time`, never by index). `backend/backtest.py` keeps signal detection and exits, but gains a live-shaped fill model (LIMIT at zone midpoint, R:R re-validated at the fill) and a cost model reporting `net_r` alongside gross.

**Tech Stack:** Python 3.13, httpx (async), pandas-free plain dicts, pytest.

**Design spec:** `docs/superpowers/specs/2026-10-19-backtest-harness-trustworthiness-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/backtest_data.py` | Fetching + window alignment. One window, joined on `open_time`. | **Create** |
| `backend/backtest.py` | Signal detection, fill model, exits, cost model. | Modify |
| `scripts/backtest.py` | CLI. Reports gross **and** net. | Modify |
| `tests/backend/test_backtest_trust.py` | One acceptance test per defect + lookahead detection. | **Create** |

**Deliberate deviation from the spec's wording:** the spec says fold the fetch "into `backend/backtest.py`". A 289-line file that owns signal detection, exits, fetching *and* window alignment would be four responsibilities in one place. The *intent* — one supported harness, not two — is preserved: the panel's `analysis/panel/` modules are research-only and this new module supersedes their fetch for all production use.

**Not touched:** `analysis/panel/*` (kept as the record of what cycle 1 concluded), and all live trading code.

---

## Task 1: Paginated fetch + window alignment

**Files:**
- Create: `backend/backtest_data.py`
- Create: `tests/backend/test_backtest_trust.py`

- [ ] **Step 1: Write the failing test**

Create `tests/backend/test_backtest_trust.py`:

```python
"""Acceptance tests for backtest trustworthiness.

`backend/backtest.py` shipped with five defects that made its output unusable.
Each test here names the defect it pins and FAILS if that defect returns.

  1. the 1000-bar page cap truncated 5m data to 3.5 days regardless of --days
  2. no fee modelling, so expectancy was GROSS while fee drag is the binding constraint
  3. MTF series aligned 1:1 by index across spans of 41.7 / 3.5 / 10.4 days
  4. entries filled at a 1h candle open while signals came from 5m and 15m
  5. the fill model did not match live LIMIT-at-midpoint with R:R re-validation

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
        for (a1, b1), (a2, b2) in zip(ranges, ranges[1:]):
            assert a2 <= b1, "pages must not leave a gap"
            assert (a1, b1) < (a2, b2), "pages must ascend"


# ------------------------------------------------- 3. multi-timeframe alignment

class TestWindowAlignment:
    def test_all_intervals_are_pinned_to_one_wall_clock_window(self):
        """DEFECT 3. The old code fetched 1000 bars of EACH interval, giving
        spans of 41.7 / 3.5 / 10.4 days, then mapped them 1:1 BY INDEX -- so 1h
        bar 500 (hour 500) aligned to 5m bar 500 (hour 41.7). Pin the window and
        the spans agree by construction."""
        from backend.backtest_data import align_window
        end = 1_700_000_000_000
        start = end - 45 * 86_400_000
        k1h = synth(start, MS_1H, 45 * 24)
        k5m = synth(start, MS_5M, 45 * 288)
        k15m = synth(start, MS_15M, 45 * 96)
        aligned = align_window({"1h": k1h, "5m": k5m, "15m": k15m}, start, end)
        for tf, series in aligned.items():
            assert series[0]["open_time"] >= start - MS_1H, tf
            assert series[-1]["open_time"] <= end, tf

    def test_the_still_forming_last_candle_is_dropped(self):
        """The final bar of each series is still open; using it is lookahead."""
        from backend.backtest_data import align_window
        end = 1_700_000_000_000
        start = end - 3 * 86_400_000
        series = {"5m": synth(start, MS_5M, 100)}
        aligned = align_window(series, start, end)["5m"]
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py -q`

Expected: FAIL — `ModuleNotFoundError: No module named 'backend.backtest_data'`

- [ ] **Step 3: Implement the data layer**

Create `backend/backtest_data.py`:

```python
"""Candle fetching and window alignment for the backtest harness.

Owns ONE responsibility: getting the right candles for a window. Everything
downstream can then assume all intervals span the same wall-clock time and can
be joined on `open_time`.

Two defects this exists to prevent, both of which shipped in
`backend/backtest.py`:

  - a single-page fetch capped 5m history at 1000 bars = 3.47 days, so `--days
    45` silently tested 3.5 days
  - each interval was fetched independently, producing spans of 41.7 / 3.5 /
    10.4 days that were then stitched together BY INDEX, so the trend filter
    read a different month than the entries it filtered
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Iterable, Optional

logger = logging.getLogger(__name__)

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
    "1d": 24 * 60 * 60_000,
}

BARS_PER_DAY = {tf: 86_400_000 // ms for tf, ms in INTERVAL_MS.items()}

# Binance's public klines endpoint accepts up to 1500. This is a PAGE size, not
# a result size — the window, not the page, decides how much history we get.
PAGE_SIZE = 1500

# Round trip: LIMIT entry (maker) + market exit (taker).
FEE_ROUND_TRIP = 0.0007


def bars_expected(interval: str, days: int) -> int:
    """How many bars a full `days` window holds on `interval`."""
    return BARS_PER_DAY[interval] * days


def page_ranges(start_ms: int, end_ms: int,
                page_size: int = PAGE_SIZE) -> list[tuple[int, int]]:
    """Contiguous, ascending [start, end) ranges covering the whole window.

    Steps by `page_size` 5m-bars regardless of the interval being fetched, so a
    45-day window yields ~9 pages instead of the single truncated page the old
    code got.
    """
    step = page_size * INTERVAL_MS["5m"]
    out, cur = [], start_ms
    while cur < end_ms:
        nxt = min(cur + step, end_ms)
        out.append((cur, nxt))
        cur = nxt
    return out


def align_window(series_by_tf: dict[str, list[dict]],
                 start_ms: int, end_ms: int) -> dict[str, list[dict]]:
    """Restrict every interval to [start_ms, end_ms), drop still-forming bars,
    and sort ascending. Series are NOT stitched together — each keeps its own
    open_time, so callers join explicitly rather than by index."""
    out: dict[str, list[dict]] = {}
    for tf, candles in series_by_tf.items():
        kept = [c for c in candles
                if start_ms <= c["open_time"] < end_ms
                and c.get("close_time", c["open_time"]) < end_ms]
        out[tf] = sorted(kept, key=lambda c: c["open_time"])
    return out


def index_by_time(candles: list[dict]) -> dict[int, int]:
    """open_time -> index, for O(1) joins across intervals."""
    return {c["open_time"]: i for i, c in enumerate(candles)}


def last_closed_before(candles: list[dict], t_ms: int) -> int:
    """Index of the last candle fully closed at `t_ms`, or -1."""
    idx = -1
    for i, c in enumerate(candles):
        if c.get("close_time", c["open_time"]) < t_ms:
            idx = i
        else:
            break
    return idx


async def fetch_klines(fetch_page: Callable[[str, int, int], list[dict]],
                       symbol: str, interval: str, start_ms: int, end_ms: int,
                       sleep: float = 0.12) -> list[dict]:
    """Every candle for `symbol`/`interval` in [start_ms, end_ms), paginated.

    `fetch_page(symbol, interval, start_ms, end_ms)` returns one page. Injected so
    tests can drive paging without the network.
    """
    out: dict[int, dict] = {}
    for a, b in page_ranges(start_ms, end_ms):
        page = await fetch_page(symbol, interval, a, b)
        if not page:
            break
        for c in page:
            out[c["open_time"]] = c
        if len(page) < PAGE_SIZE:
            break
        await asyncio.sleep(sleep)
    return [out[t] for t in sorted(out)]


def window_for_days(days: int, now_ms: Optional[int] = None) -> tuple[int, int]:
    """[start_ms, end_ms) for the last `days`, ending at the last CLOSED 5m bar
    so nothing still-forming enters the data."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    end = (now_ms // INTERVAL_MS["5m"]) * INTERVAL_MS["5m"]
    return end - days * 86_400_000, end
```

**Note on `page_ranges`:** this is the final form — the earlier draft had a dead
first assignment to `nxt`. Steps of `1500 × 5m = 5.2 days` per page.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py -q`

Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/backtest_data.py tests/backend/test_backtest_trust.py
git commit -m "feat: paginated kline fetch pinned to one wall-clock window

backend/backtest.py fetched each interval with limit=min(days*24,1000), which
caps 5m at 1000 bars = 3.47 days no matter what --days said, and gave the three
series spans of 41.7/3.5/10.4 days that were then stitched together BY INDEX --
so the trend filter read a different month than the entries it filtered.

fetch_klines pages until the window is covered; align_window restricts every
interval to the same [start, end) and drops still-forming bars; series keep
their own open_time so callers join explicitly."
```

---

## Task 2: Live-shaped fill model

**Files:**
- Modify: `backend/backtest.py` (`run`, lines 245-258)
- Test: `tests/backend/test_backtest_trust.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/backend/test_backtest_trust.py`:

```python
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
        # zone 99.0-101.0 -> midpoint 100.0; the next bar trades down to 99.5
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
        for c in k5m:                      # price stays far above the zone
            c["low"], c["high"] = 200.0, 201.0
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0, max_age_ms=60 * MS_5M) is None

    def test_a_signal_older_than_the_window_never_fills(self):
        """Live expires stale signals (`signal_max_age_minutes`). An old signal
        filling at today's price is not a trade the bot would have taken."""
        from backend.backtest import fill_at_midpoint
        end = 1_700_000_000_000
        start = end - 600 * MS_5M
        k5m = synth(start, MS_5M, 600, close=100.0)
        k5m[500]["low"], k5m[500]["high"] = 99.5, 100.5
        assert fill_at_midpoint(k5m, 1, 99.0, 101.0, max_age_ms=2 * MS_5M) is None

    def test_r_r_is_revalidated_at_the_fill_price_not_at_the_signal(self):
        """Live re-checks R:R at the ACTUAL fill and skips if it no longer
        qualifies. A signal that looked 3:1 and filled at 2:1 must be skipped."""
        from backend.backtest import rr_at_fill
        # stop 1.0 away; a fill 3.0 away gives 3:1 with TP at +10
        assert rr_at_fill(100.0, 1.0, 10.0, "LONG", 100.0, 3.0) >= 3.0
        # filled 5.0 from the stop -> only 2:1
        assert rr_at_fill(100.0, 1.0, 10.0, "LONG", 100.0, 5.0) < 3.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py::TestFillModel -q`

Expected: FAIL — `ImportError: cannot import name 'fill_at_midpoint'`

- [ ] **Step 3: Implement the fill model**

Add to `backend/backtest.py`, above `class Backtester`:

```python
# Live entry semantics, mirrored so the backtest measures what the bot does.
# `StrategyService._enter_with_limit` places a LIMIT at the entry-zone midpoint,
# waits up to `signal_max_age_minutes`, then RE-VALIDATES R:R at the price that
# actually filled and skips the signal if it no longer qualifies.
DEFAULT_SIGNAL_MAX_AGE_MIN = 60


def fill_at_midpoint(k5m: list[dict], signal_idx: int,
                     zone_lo: float, zone_hi: float,
                     max_age_ms: int) -> Optional[tuple[int, float]]:
    """First bar at/after `signal_idx` whose range touches the zone midpoint.

    Returns `(bar_index, fill_price)` or None. The fill price is the MIDPOINT,
    not the bar's open — that is what a resting LIMIT at the midpoint gets.
    Scanning starts at `signal_idx + 1`: the signal bar itself is already closed
    when the signal exists, so filling on it would be lookahead.
    """
    midpoint = (zone_lo + zone_hi) / 2.0
    deadline = k5m[signal_idx]["open_time"] + max_age_ms
    for i in range(signal_idx + 1, len(k5m)):
        c = k5m[i]
        if c["open_time"] > deadline:
            return None
        if c["low"] <= midpoint <= c["high"]:
            return i, midpoint
    return None


def rr_at_fill(sl: float, tp: float, direction: str,
               fill: float, rr_min: float) -> float:
    """R:R actually achieved at `fill`, mirroring the live re-check."""
    risk = abs(fill - sl)
    if risk <= 0:
        return 0.0
    reward = abs(tp - fill)
    return reward / risk
```

Then in `Backtester.run`, replace the block at lines 245-258:

```python
                # fill at the NEXT candle's open (no lookahead)
                fill = k1h[i + 1]["open"] if i + 1 < len(k1h) else entry
                trade = BacktestTrade(
                    symbol=symbol, strategy=strategy, direction=direction,
                    entry=fill, stop=sl, take_profit=tp, entry_idx=i + 1)
```

with:

```python
                # Live fill semantics: a LIMIT resting at the zone midpoint on
                # the signal's OWN interval, not the next 1h open. Filling on the
                # wrong interval was a 12x time-scale mismatch.
                tf_candles = {"1h": k1h, "5m": k5m, "15m": k15m}[tf]
                sig_idx = self._source_index(tf_candles, k1h[i]["open_time"])
                filled = fill_at_midpoint(
                    tf_candles, sig_idx, zone_lo, zone_hi, self.signal_max_age_ms)
                if filled is None:
                    result.skipped["no_fill"] = result.skipped.get("no_fill", 0) + 1
                    continue
                fill_idx, fill = filled
                if rr_at_fill(sl, tp, direction, fill, self.rr_min) < self.rr_min:
                    result.skipped["rr_at_fill"] = result.skipped.get("rr_at_fill", 0) + 1
                    continue
                trade = BacktestTrade(
                    symbol=symbol, strategy=strategy, direction=direction,
                    entry=fill, stop=sl, take_profit=tp, entry_idx=fill_idx)
```

**Four supporting edits in the same task — all required, none optional:**

1. `__init__` gains:
   ```python
   self.signal_max_age_ms = int(
       self.settings.get("signal_max_age_minutes", DEFAULT_SIGNAL_MAX_AGE_MIN)) * 60_000
   ```

2. `_detect_candidates` must return the entry **zone** as well as the point. Change
   each appended tuple from a 5-tuple to a 6-tuple `(strategy, direction, e, vol_src,
   tf, zone)`: FVG uses the imbalance bounds, 3CANDLE the pattern high/low, CHoCH
   `ch.pullback_entry_zone`. Where a detector exposes no zone, fall back to
   `(entry * 0.995, entry * 1.005)` — that weakens the match to live, so **say so in
   your report** if you rely on it.

3. In `run`, unpack `for strategy, direction, e, vol_src, tf, zone in cands:` and set
   `zone_lo, zone_hi = zone`.

4. Add the index helper the new code calls:
   ```python
   def _source_index(self, candles: list[dict], at_ms: int) -> int:
       """Index in `candles` of the last bar fully closed at `at_ms`.

       This is the whole point of the fix: it maps a wall-clock instant into
       whichever interval the signal came from, instead of the old
       proportional index arithmetic that stitched together series spanning
       different periods.
       """
       return last_closed_before(candles, at_ms)
   ```
   with `last_closed_before` imported from `backend.backtest_data`.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py -q`

Expected: PASS

- [ ] **Step 5: Run the existing backtest tests to check for regressions**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/ -q -k "backtest"`

Expected: PASS. If a test asserted the old `k1h[i+1]["open"]` fill, update it to the
new semantics and note it — that test was pinning defect 4.

- [ ] **Step 6: Commit**

```bash
git add backend/backtest.py tests/backend/test_backtest_trust.py
git commit -m "fix: backtest fills mirror live LIMIT-at-midpoint semantics

Entries filled at k1h[i+1]['open'] while 3CANDLE signals come from 5m and CHoCH
from 15m -- a 12x time-scale mismatch. Live places a LIMIT at the zone midpoint,
expires stale signals, and re-validates R:R at the price that actually filled.

Now: fill on the signal's OWN interval, at the midpoint, within
signal_max_age_minutes, skipping the signal when the fill no longer meets
rr_min. Unfilled and revalidated-out signals are counted, not dropped."
```

---

## Task 3: Cost model

**Files:**
- Modify: `backend/backtest.py` (`BacktestTrade`, `BacktestResult.summary`)
- Test: `tests/backend/test_backtest_trust.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/backend/test_backtest_trust.py`:

```python
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

    def test_a_wide_stop_is_pays_more_in_fees_than_a_tight_one(self):
        """Fees are a fraction of NOTIONAL, but R divides by the risk distance.
        The same 0.07% therefore costs more R when the stop is tight -- which is
        exactly the effect cycle 1 measured (fee drag 4x at tight stops)."""
        from backend.backtest import fee_in_r
        tight = fee_in_r(entry=100.0, risk_dist=0.5)
        wide = fee_in_r(entry=100.0, risk_dist=5.0)
        assert tight > wide
        assert tight == pytest.approx(100.0 * 0.0007 / 0.5)

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
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py::TestCostModel -q`

Expected: FAIL — no `fee_in_r`, no `net_r`

- [ ] **Step 3: Implement the cost model**

Add to `backend/backtest.py`, near the top after the imports:

```python
from backend.backtest_data import FEE_ROUND_TRIP


def fee_in_r(entry: float, risk_dist: float) -> float:
    """Round-trip cost expressed in R.

    Fees are a fraction of NOTIONAL; R divides by the risk distance. So the same
    0.07% costs 4x more R at a 0.5% stop than at a 2% stop — the effect panel
    cycle 1 measured as the dominant cost drag.
    """
    if risk_dist <= 0:
        return float("inf")
    return (entry * FEE_ROUND_TRIP) / risk_dist
```

Add `net_r` to `BacktestTrade`:

```python
    pnl_r: float = 0.0
    net_r: float = 0.0

    def finalise(self) -> None:
        """Compute net R once the exit is known."""
        risk = getattr(self, "_risk_dist", abs(self.entry - self.stop))
        self.net_r = self.pnl_r - fee_in_r(self.entry, risk)
```

Call `t.finalise()` at the end of `Backtester._close`. Add to `BacktestResult`:

```python
    @property
    def total_net_r(self) -> float:
        return sum(t.net_r for t in self.closed)

    @property
    def expectancy_net_r(self) -> float:
        n = len(self.closed)
        return (self.total_net_r / n) if n else 0.0
```

and to `summary()`:

```python
            "net_r": round(self.total_net_r, 2),
            "expectancy_net_r": round(self.expectancy_net_r, 3),
            "fee_round_trip": FEE_ROUND_TRIP,
            "trust": "net_r is the number to trust; total_r is gross of costs",
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/backtest.py tests/backend/test_backtest_trust.py
git commit -m "feat: charge round-trip costs and report net expectancy

The harness had no fee modelling at all, so expectancy was GROSS -- while panel
cycle 1 concluded fee drag, not exit geometry, is the binding constraint. A
gross-only harness rates net-losing strategies as winners.

fee_in_r expresses the 0.07% round trip in R, which correctly makes a tight stop
pay ~4x the R cost of a wide one. Every summary now carries net_r alongside
total_r and says which one to trust."
```

---

## Task 4: Lookahead detection

**Files:**
- Test: `tests/backend/test_backtest_trust.py`

This task adds **no production code**. It proves the harness's no-lookahead claim is
testable rather than asserted.

- [ ] **Step 1: Write the test**

Append to `tests/backend/test_backtest_trust.py`:

```python
# ------------------------------------------------------- lookahead detectability

class TestLookaheadIsDetectable:
    """A "we use only candles[0..i]" comment is a claim, not a guarantee.

    This is the defect that inflated OB+FVG expectancy from +0.315R to +0.402R
    in the first harness. The test below proves the suite CAN detect lookahead: a
    deliberately-lookaheaded variant must produce different numbers. If it ever
    produces the SAME numbers, the suite has stopped being able to see the bug.
    """

    def test_a_lookaheaded_variant_produces_different_numbers(self):
        import backend.backtest as bt

        lookaheaded = bt.LookaheadProbeBacktester()   # added by this task
        honest = bt.Backtester()

        from tests.backend.test_backtest_trust import synth as _s
        end = 1_700_000_000_000
        start = end - 4000 * MS_1H
        k1h = _s(start, MS_1H, 4000, close=100.0)
        for i, c in enumerate(k1h):
            # a deterministic wobble so indicators have something to chew on
            c["close"] = 100.0 + (i % 17) * 0.5 - (i % 7) * 0.3
            c["open"] = c["close"]
            c["high"] = c["close"] + 0.4
            c["low"] = c["close"] - 0.4
        k5m = _s(start, MS_5M, 4000 * 12, close=100.0)
        k15m = _s(start, MS_15M, 4000 * 4, close=100.0)

        a = honest.run("X", k1h, k5m, k15m).summary()
        b = lookaheaded.run("X", k1h, k5m, k15m).summary()
        assert (a["total_r"], a["closed"]) != (b["total_r"], b["closed"]), \
            "a lookaheaded variant produced IDENTICAL results — the suite can no " \
            "longer detect lookahead, so every no-lookahead claim is unfalsifiable"


- [ ] **Step 2: Add the probe to `backend/backtest.py`**

```python
class LookaheadProbeBacktester(Backtester):
    """Deliberately broken variant used ONLY by tests.

    Computes ATR over the WHOLE series instead of `candles[0..i]`, which is the
    exact shape of the original OB+FVG lookahead bug. It exists so the test suite
    can prove it is capable of detecting lookahead at all — a suite that cannot
    fail this way cannot be trusted when it says "no lookahead".
    """

    def _detect_candidates(self, k1h, k5m, k15m, at_idx):
        return super()._detect_candidates(k1h, k5m, k15m, len(k1h) - 1)
```

- [ ] **Step 3: Run**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py -q`

Expected: PASS, with the lookahead test proving a lookaheaded variant diverges.

- [ ] **Step 4: Prove the lookahead test can fail**

Temporarily make `Backtester._detect_candidates` itself look ahead (pass
`len(k1h) - 1` instead of `at_idx`), run the test, confirm it FAILS, then restore.

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/test_backtest_trust.py::TestLookaheadIsDetectable -q`

Expected: FAIL when the production code looks ahead, PASS when restored. **Report
the actual output of both.**

- [ ] **Step 5: Commit**

```bash
git add backend/backtest.py tests/backend/test_backtest_trust.py
git commit -m "test: prove the harness can actually detect lookahead

A deliberately-lookaheaded probe must produce different numbers from the honest
run. If they match, the suite has lost the ability to see lookahead and every
no-lookahead claim becomes unfalsifiable -- which is how the original OB+FVG
result reported +0.402R when it was +0.315R."
```

---

## Task 5: Wire the CLI and document

**Files:**
- Modify: `scripts/backtest.py`
- Modify: `backend/AGENTS.md`, `tests/AGENTS.md`, `AGENTS.md`

- [ ] **Step 1: Wire `run_backtest` to the data layer**

In `backend/backtest.py`, replace `run_backtest` (lines 271-289):

```python
async def run_backtest(symbol: str, days: int = 30, settings: Optional[dict] = None,
                       binance=None) -> BacktestResult:
    """Fetch a full `days` window (paginated) and run the backtest."""
    created = False
    if binance is None:
        from backend.services.binance_service import BinanceService
        binance = BinanceService()
        created = True
    try:
        start_ms, end_ms = window_for_days(days)
        series = {}
        for tf in ("1h", "5m", "15m"):
            series[tf] = await fetch_klines(
                lambda s, i, a, b: binance.get_klines_window(s, i, a, b),
                symbol, tf, start_ms, end_ms)
        series = align_window(series, start_ms, end_ms)
        for tf, candles in series.items():
            if len(candles) < bars_expected(tf, days) * 0.9:
                raise RuntimeError(
                    f"{symbol} {tf}: got {len(candles)} bars for a {days}d window, "
                    f"expected ~{bars_expected(tf, days)} — pagination failed")
        bt = Backtester(settings=settings)
        return bt.run(symbol, series["1h"], series["5m"], series["15m"])
    finally:
        if created:
            await binance.close()
```

If `BinanceService` has no `get_klines_window(symbol, interval, start_ms, end_ms)`,
add it — a thin wrapper over the existing public klines call that passes
`startTime`/`endTime`/`limit`. Check the existing `get_klines` signature first and
reuse its request helper rather than opening a new httpx client.

- [ ] **Step 2: Report net in the CLI**

In `scripts/backtest.py`, print both. Find the summary-printing block and add:

```python
        print(f"{r['symbol']:>12}  n={r['closed']:>4}  gross {r['total_r']:>+8.2f}R  "
              f"NET {r['net_r']:>+8.2f}R  exp_net {r['expectancy_net_r']:>+7.3f}R  "
              f"win {r['win_rate']:>5.1f}%")
```

and label the block `NET expectancy (after 0.07% round trip — the number to trust)`.

- [ ] **Step 3: Full suite + leak check**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pytest tests/backend/ -q`

Baseline before this branch: **477 passing**. The new module adds tests; nothing may
break. Report the real number.

Verify no DB leaks — these tests must not touch the database at all:

```bash
PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -c "
import asyncio
from sqlalchemy import select, func
from backend.database import AsyncSessionLocal, engine
from backend.models.trade import Trade
from backend.models.daily_summary import DailySummary
async def m():
    async with AsyncSessionLocal() as db:
        t=(await db.execute(select(func.count()).select_from(Trade))).scalar()
        d=(await db.execute(select(func.count()).select_from(DailySummary))).scalar()
    await engine.dispose(); print(f'trades={t} daily_summary={d}')
asyncio.run(m())"
```

Expected: `trades=48 daily_summary=0`.

- [ ] **Step 4: A real smoke run (network required)**

Run: `PYTHONPATH=. /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 scripts/backtest.py --symbols BTCUSDT,ETHUSDT --days 45`

Expected: no `RuntimeError` about bar count (which is what proves pagination works),
and both gross and net printed. **Paste the actual output in your report.** If it
raises, do not paper over it — that is the defect this whole branch exists to fix.

- [ ] **Step 5: DOX documentation**

Add to `backend/AGENTS.md` under `## Local Contracts`:

```markdown
- **The backtest harness measures net-of-fee expectancy over a paginated, single-window dataset** (2026-10-19): `backend/backtest.py` shipped with five defects that made its output unusable — a single-page fetch capped 5m at 1000 bars (3.47 days regardless of `--days`); **no fee modelling at all**, so expectancy was GROSS while panel cycle 1 concluded fee drag, not exit geometry, is the binding constraint; 1h/5m/15m fetched independently (spanning 41.7/3.5/10.4 days) then stitched **by index**, so the trend filter read a different month than its entries; entries filled at a 1h candle open while 3CANDLE signals come from 5m; and a fill model that ignored live's LIMIT-at-midpoint, signal expiry and R:R re-check at the fill. `backend/backtest_data.py` now owns fetching (paginated, all intervals pinned to one `[start, end)`, still-forming bars dropped, series joined on `open_time` — never by index). Costs are charged via `fee_in_r` and every summary reports `net_r` alongside gross. **`net_r` is the number to trust.** `tests/backend/test_backtest_trust.py` pins one acceptance test per defect, including `TestLookaheadIsDetectable`, which proves the suite CAN detect lookahead by requiring a deliberately-lookaheaded probe to produce different numbers — without it, every no-lookahead claim is unfalsifiable, which is how OB+FVG once reported +0.402R when it was +0.315R.
```

Add to `tests/AGENTS.md`:

```markdown
- `test_backtest_trust.py` — one acceptance test per backtest defect, named after the defect it pins: pagination (a 45d window must yield 12,960 5m bars, which is exactly what the old 1000-bar page cap silently truncated), window alignment (all intervals pinned to one wall-clock span, still-forming bars dropped, series joined on `open_time`), the fill model (fills taken from the signal's own interval, at the zone midpoint, within `signal_max_age_minutes`, with R:R re-validated at the fill), the cost model (`net_r <= gross_r` always; a wide stop pays less in R than a tight one), and lookahead detectability. Pure functions over synthetic candles — no network, no database.
```

Update the test count in `tests/AGENTS.md` and `AGENTS.md` from the real Step 3 output.

- [ ] **Step 6: Commit**

```bash
git add scripts/backtest.py backend/AGENTS.md tests/AGENTS.md AGENTS.md
git commit -m "feat: wire the CLI to net-of-fee reporting; document the harness fixes"
```

---

## Rollback

Each task is an independent commit. Task 1 (new module) is additive and safe to
revert alone. Tasks 2 and 3 change backtest numbers — reverting them restores the
*broken* behaviour, so revert only if the harness is mid-investigation.

**This branch changes no live trading code.** Nothing here can affect the running bot.