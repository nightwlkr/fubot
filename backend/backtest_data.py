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
from typing import Callable, Optional

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


def page_ranges(start_ms: int, end_ms: int, interval: str = "5m",
                page_size: int = PAGE_SIZE) -> list[tuple[int, int]]:
    """Contiguous, ascending [start, end) ranges covering the whole window.

    Steps by `page_size` bars OF `interval`. Stepping by a fixed 5m duration
    regardless of interval was wrong: a 1500-bar page is 5.2 days on 5m but
    62 days on 1h, so a single page covered the whole window for coarse
    intervals while truncating fine ones.
    """
    step = page_size * INTERVAL_MS[interval]
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


async def fetch_klines(fetch_page: Callable[[str, str, int, int], list[dict]],
                       symbol: str, interval: str, start_ms: int, end_ms: int,
                       sleep: float = 0.12) -> list[dict]:
    """Every candle for `symbol`/`interval` in [start_ms, end_ms), paginated.

    `fetch_page(symbol, interval, start_ms, end_ms)` returns one page. Injected so
    tests can drive paging without the network.

    Advances through every range `page_ranges` produces rather than stopping on a
    short page. A `len(page) < PAGE_SIZE` break assumed the remote returns exactly
    `PAGE_SIZE` on any non-final page, which is false whenever the server honours
    the requested time range: on a coarse interval a full range yields far fewer
    bars than the cap, so that heuristic truncated 1h/15m/4h/1d to a single page.
    An empty page is likewise NOT a terminator -- stopping there would silently
    truncate, which is the exact defect class this module exists to prevent. The
    loop is bounded because `page_ranges` is a finite list, so the worst case of a
    misbehaving remote is a few extra requests, never a hang.
    """
    ranges = page_ranges(start_ms, end_ms, interval)
    out: dict[int, dict] = {}
    for n, (a, b) in enumerate(ranges):
        page = await fetch_page(symbol, interval, a, b)
        for c in page:
            out[c["open_time"]] = c
        if n < len(ranges) - 1:
            await asyncio.sleep(sleep)
    return [out[t] for t in sorted(out)]


def window_for_days(days: int, now_ms: Optional[int] = None) -> tuple[int, int]:
    """[start_ms, end_ms) for the last `days`, ending at the last CLOSED 5m bar
    so nothing still-forming enters the data."""
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    end = (now_ms // INTERVAL_MS["5m"]) * INTERVAL_MS["5m"]
    return end - days * 86_400_000, end