#!/usr/bin/env python3
"""Backtest a funding-rate CONTRARIAN mean-reversion strategy (research only).

Hypothesis (literature, NOT this repo's falsified funding-as-directional-signal):
extreme NEGATIVE funding on Binance USDT-M perps marks crowded shorts; longs
entered after the print are hypothesised to earn a positive 24h drift as shorts
are squeezed. Published anchors: Stratmill BTCUSDT 2019-2026 n=53 funding <=
-0.02% -> +2.87% avg net 24h, 71.7% win; Aijmr +0.49-0.51% avg 24h on the
negative tail only (asymmetric).

Standalone research harness — does NOT touch backend/backtest.py, live code,
or binance_service.py. Reuses:

  - backend/backtest_data.py  — paginated fetch_klines + align_window over one
    [start, end) window, still-forming bars dropped.
  - backend/services/binance_service.py — BinanceService.get_klines_window for
    1h klines (shared rate limiter).
  - backend/core/cost_model.py — account-calibrated taker costs: 0.045% fee +
    0.01% slippage per side = FEE_ROUND_TRIP 0.11% of notional.
  - backend/indicators/atr.py — calculate_atr_series (lookahead-free ATR(14)).

Funding history is fetched HERE via httpx against the public endpoint
  GET https://fapi.binance.com/fapi/v1/fundingRate?symbol=X&startTime=&endTime=
      &limit=1000
paginated by advancing startTime past the last fundingTime (limit=1000 covers
~333 days of 8h prints per call). fundingRate history depth varies by listing
date; actual per-symbol coverage is printed.

Strategy spec (all LONG-only):
  - Signal: funding print with fundingRate <= threshold.
    Thresholds: -0.01% (-0.0001), -0.02% (-0.0002, primary), -0.03% (-0.0003).
  - Entry: open of the first 1h bar with open_time STRICTLY greater than the
    fundingTime — the print is only known at settlement, so nothing at or
    before fundingTime may be used. With hour-aligned funding times this is
    the bar opening ~1h after the print.
  - One position per symbol per variant-run; signals arriving while a position
    is open are IGNORED (not queued) — documented choice.
  - Exits (each run separately):
      "24h"   — exit at the open of the 1h bar 24h after entry.
      "72h"   — exit at the open of the 1h bar 72h after entry.
      "norm72"— exit at the open of the first 1h bar strictly after the next
                funding print with rate > -0.01% (normalization), capped at
                72h; if no normalizing print, the 72h cap applies.
    If the scheduled exit falls past the last fetched bar, the position closes
    at the last bar's CLOSE and is flagged reason="eod".
  - Stop (run separately): none, or entry - 2*ATR(14) computed on 1h bars at
    the entry bar (lookahead-free series). Intrabar: stop checked on every bar
    from the entry bar onward; a bar opening at/below the stop gap-fills at
    its adverse OPEN, otherwise fills at the stop. Stop-before-time-exit: a
    bar whose open_time >= scheduled exit_time exits AT ITS OPEN before that
    bar's range is inspected.
  - Funding carry: longs RECEIVE funding when the rate is negative. For every
    funding event with entry_open < fundingTime < exit_time, income +=
    -fundingRate * notional. This is real carry and is included in net.
  - Costs: FEE_ROUND_TRIP (0.11%) of notional subtracted per trade.

Units: primary reporting is % of notional per trade (gross price move %,
funding carry %, fees %, net %). For stop variants R is also reported with
1R = entry-to-stop distance. For no-stop variants no R basis exists — % of
notional is the only unit and is labelled as such. "Total net return" is the
sum of per-trade net % on equal-notional-per-trade (no compounding).

Cells with <20 closed trades are inconclusive.

Usage:
    python scripts/funding_contrarian_backtest.py                 # 365d, all symbols
    python scripts/funding_contrarian_backtest.py --days 180 --symbols BTCUSDT
"""
import argparse
import asyncio
import bisect
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.backtest_data import (  # noqa: E402
    align_window, bars_expected, fetch_klines, window_for_days,
)
from backend.core.cost_model import FEE_ROUND_TRIP  # noqa: E402
from backend.indicators.atr import calculate_atr_series  # noqa: E402
from backend.services.binance_service import BinanceService  # noqa: E402

FUNDING_BASE = "https://fapi.binance.com"
FUNDING_LIMIT = 1000
HOUR_MS = 3_600_000

DEFAULT_SYMBOLS = ("BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,"
                   "ADAUSDT,LINKUSDT,LTCUSDT,AVAXUSDT")
THRESHOLDS = {"m1bp": -0.0001, "m2bp": -0.0002, "m3bp": -0.0003}
EXIT_KINDS = ("24h", "72h", "norm72")
STOP_KINDS = ("nostop", "atr2")
ATR_PERIOD = 14
ATR_STOP_MULT = 2.0
NORM_EXIT_RATE = -0.0001   # "funding normalizes" = print > -0.01%
MAX_HOLD_MS = 72 * HOUR_MS
MIN_TRADES = 20


# ------------------------------------------------------------ funding fetch

async def fetch_funding_history(client: httpx.AsyncClient, symbol: str,
                                start_ms: int, end_ms: int) -> list[dict]:
    """All fundingRate prints for `symbol` in [start_ms, end_ms).

    Paginates by advancing startTime past the last returned fundingTime —
    limit=1000 covers ~333 days of 8h prints per call. Symbols listed later
    simply return their full available history; coverage is reported per
    symbol. Returns sorted unique [{fundingTime, fundingRate}].
    """
    out: dict[int, float] = {}
    cursor = start_ms
    while cursor < end_ms:
        r = await client.get(
            f"{FUNDING_BASE}/fapi/v1/fundingRate",
            params={"symbol": symbol, "startTime": cursor,
                    "endTime": end_ms, "limit": FUNDING_LIMIT},
            timeout=30.0)
        r.raise_for_status()
        page = r.json()
        if not page:
            break
        for row in page:
            ft = int(row["fundingTime"])
            if start_ms <= ft < end_ms:
                out[ft] = float(row["fundingRate"])
        last_ft = int(page[-1]["fundingTime"])
        if len(page) < FUNDING_LIMIT or last_ft <= cursor:
            break
        cursor = last_ft + 1
        await asyncio.sleep(0.15)
    return [{"fundingTime": ft, "fundingRate": rate}
            for ft, rate in sorted(out.items())]


# ------------------------------------------------------------------- fetch

async def fetch_symbol(binance, symbol: str, interval: str,
                       start_ms: int, end_ms: int) -> list[dict]:
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            candles = await fetch_klines(
                lambda s, i, a, b: binance.get_klines_window(s, i, a, b),
                symbol, interval, start_ms, end_ms)
            candles = align_window({interval: candles}, start_ms, end_ms)[interval]
            if len(candles) < bars_expected(interval, _DAYS) * 0.9:
                raise RuntimeError(
                    f"{symbol} {interval}: {len(candles)} bars for {_DAYS}d "
                    f"window, expected ~{bars_expected(interval, _DAYS)}")
            return candles
        except Exception as exc:  # noqa: BLE001 - retry once then report
            last_exc = exc
            if attempt == 1:
                await asyncio.sleep(1.0)
    raise last_exc  # type: ignore[misc]


_DAYS = 365  # set in main; fetch_symbol sanity-checks against it


# ------------------------------------------------------------------ trades

@dataclass
class Trade:
    symbol: str
    threshold: str
    exit_kind: str
    stop_kind: str
    signal_time: int          # fundingTime of the signal print
    entry_time: int           # open_time of entry bar
    exit_time: int            # open_time of exit bar, or close_time for eod
    entry: float
    exit: float
    stop: float | None
    risk: float               # entry - stop (price), 0.0 when no stop
    gross_pct: float          # (exit-entry)/entry * 100
    funding_pct: float        # sum(-rate) * 100 over held prints
    fee_pct: float            # FEE_ROUND_TRIP * 100
    net_pct: float            # gross + funding - fee
    n_funding_events: int
    reason: str               # 24h/72h/norm72/eod/stop/sl_gap


def _exit_time_for(exit_kind: str, entry_time: int, signal_idx: int,
                   funding: list[dict], open_times: list[int]) -> int:
    """Scheduled exit bar open_time (ms) for one exit variant."""
    if exit_kind == "24h":
        return entry_time + 24 * HOUR_MS
    if exit_kind == "72h":
        return entry_time + MAX_HOLD_MS
    # norm72: first bar open strictly after the next print > NORM_EXIT_RATE,
    # capped at entry + 72h.
    cap = entry_time + MAX_HOLD_MS
    for ev in funding[signal_idx + 1:]:
        ft = ev["fundingTime"]
        if ft <= entry_time:
            continue
        if ev["fundingRate"] > NORM_EXIT_RATE:
            j = bisect.bisect_right(open_times, ft)
            if j < len(open_times):
                return min(open_times[j], cap)
            return cap
    return cap


def simulate_symbol(symbol: str, candles: list[dict],
                    funding: list[dict]) -> dict[tuple, list[Trade]]:
    """Run every (threshold, exit, stop) variant over one symbol's data.

    Each variant is an independent strategy run with its own one-position-
    at-a-time rule. Returns {(thr_key, exit_kind, stop_kind): [Trade]}.
    """
    open_times = [c["open_time"] for c in candles]
    atr = calculate_atr_series(candles, ATR_PERIOD)
    f_times = [e["fundingTime"] for e in funding]

    results: dict[tuple, list[Trade]] = {}
    for thr_key, thr in THRESHOLDS.items():
        signal_idxs = [i for i, e in enumerate(funding)
                       if e["fundingRate"] <= thr]
        for exit_kind in EXIT_KINDS:
            for stop_kind in STOP_KINDS:
                key = (thr_key, exit_kind, stop_kind)
                trades: list[Trade] = []
                busy_until = -1  # entry_time of the open position; -1 = flat
                for si in signal_idxs:
                    sig = funding[si]
                    ft = sig["fundingTime"]
                    if ft <= busy_until:
                        continue  # in position: signal ignored, not queued
                    # entry = first 1h bar open STRICTLY after fundingTime
                    ei = bisect.bisect_right(open_times, ft)
                    if ei >= len(candles):
                        continue
                    entry_bar = candles[ei]
                    entry = entry_bar["open"]
                    entry_time = entry_bar["open_time"]

                    stop = None
                    if stop_kind == "atr2":
                        a = atr[ei] if ei < len(atr) else 0.0
                        if a <= 0:
                            continue  # ATR warmup: cannot size the stop
                        stop = entry - ATR_STOP_MULT * a
                        if stop <= 0:
                            continue

                    sched_exit = _exit_time_for(
                        exit_kind, entry_time, si, funding, open_times)

                    # walk bars from entry; stop-first intrabar ordering,
                    # time exits fill at the bar OPEN before its range.
                    exit_idx = None
                    exit_price = None
                    exit_ts = None
                    reason = exit_kind
                    for j in range(ei, len(candles)):
                        bar = candles[j]
                        if j > ei and bar["open_time"] >= sched_exit:
                            exit_idx, exit_price = j, bar["open"]
                            exit_ts = bar["open_time"]
                            break
                        if stop is not None and bar["low"] <= stop:
                            gap = bar["open"] <= stop
                            exit_idx = j
                            exit_price = bar["open"] if gap else stop
                            exit_ts = bar["open_time"]
                            reason = "sl_gap" if gap else "stop"
                            break
                    if exit_idx is None:
                        # ran off the end of fetched data: close at last close
                        last = candles[-1]
                        exit_idx = len(candles) - 1
                        exit_price = last["close"]
                        exit_ts = last["close_time"] or last["open_time"]
                        reason = "eod"

                    # funding carry: prints strictly inside the hold
                    lo = bisect.bisect_right(f_times, entry_time)
                    hi = bisect.bisect_left(f_times, exit_ts)
                    held = funding[lo:hi]
                    funding_frac = sum(-e["fundingRate"] for e in held)

                    gross_frac = (exit_price - entry) / entry
                    fee_frac = FEE_ROUND_TRIP
                    net_frac = gross_frac + funding_frac - fee_frac
                    risk = (entry - stop) if stop is not None else 0.0
                    trades.append(Trade(
                        symbol=symbol, threshold=thr_key,
                        exit_kind=exit_kind, stop_kind=stop_kind,
                        signal_time=ft, entry_time=entry_time,
                        exit_time=exit_ts, entry=entry, exit=exit_price,
                        stop=stop, risk=risk,
                        gross_pct=100.0 * gross_frac,
                        funding_pct=100.0 * funding_frac,
                        fee_pct=100.0 * fee_frac,
                        net_pct=100.0 * net_frac,
                        n_funding_events=len(held), reason=reason))
                    busy_until = exit_ts  # signals at/before this are ignored
                results[key] = trades
    return results


# ------------------------------------------------------------------ report

@dataclass
class VariantAgg:
    key: tuple
    trades: list[Trade] = field(default_factory=list)

    def stats(self) -> dict:
        n = len(self.trades)
        if not n:
            return {"n": 0}
        gross = sum(t.gross_pct for t in self.trades)
        fund = sum(t.funding_pct for t in self.trades)
        fee = sum(t.fee_pct for t in self.trades)
        net = sum(t.net_pct for t in self.trades)
        wins = sum(1 for t in self.trades if t.net_pct > 0)
        eod = sum(1 for t in self.trades if t.reason == "eod")
        nets = [t.net_pct for t in self.trades]
        mean_net = net / n
        if n > 1:
            var = sum((x - mean_net) ** 2 for x in nets) / (n - 1)
            sd = var ** 0.5
            t_stat = (mean_net / (sd / n ** 0.5)) if sd > 0 else 0.0
        else:
            sd, t_stat = 0.0, 0.0
        out = {
            "n": n,
            "win_pct": 100.0 * wins / n,
            "avg_gross_pct": gross / n,
            "avg_funding_pct": fund / n,
            "avg_fee_pct": fee / n,
            "avg_net_pct": mean_net,
            "sd_net_pct": sd,
            "t_net": t_stat,
            "total_net_pct": net,          # sum of per-trade % on notional
            "eod": eod,
        }
        # R stats only where a stop defines 1R
        stopped = [t for t in self.trades if t.risk > 0]
        if stopped:
            gross_r = sum((t.exit - t.entry) / t.risk for t in stopped)
            fee_r = sum(FEE_ROUND_TRIP * t.entry / t.risk for t in stopped)
            net_r = sum(
                (t.exit - t.entry) / t.risk
                + (t.funding_pct / 100.0) * t.entry / t.risk
                - FEE_ROUND_TRIP * t.entry / t.risk
                for t in stopped)
            out.update({
                "n_r": len(stopped),
                "avg_net_r": net_r / len(stopped),
                "total_net_r": net_r,
                "gross_r": gross_r, "fee_r": fee_r,
            })
        return out


def print_report(all_results: dict[str, dict[tuple, list[Trade]]],
                 funding_coverage: dict[str, tuple[int, int]]) -> None:
    variants: dict[tuple, VariantAgg] = {}
    for sym, res in all_results.items():
        for key, trades in res.items():
            variants.setdefault(key, VariantAgg(key)).trades.extend(trades)

    print("\n=== FUNDING CONTRARIAN — pooled across symbols ===")
    print("(net % = per-trade % of notional; funding carry included; "
          "fees 0.11% RT; <20 trades = INCONCLUSIVE)")
    header = (f"{'thr':<6} {'exit':<7} {'stop':<7} {'n':>4} {'win%':>6} "
              f"{'avgGross%':>9} {'avgFund%':>9} {'avgNet%':>8} "
              f"{'totNet%':>8} {'avgNetR':>8} {'totNetR':>8} {'eod':>4} "
              f"{'t':>6}")
    print(header)
    print("-" * len(header))
    for key in sorted(variants):
        thr_key, exit_kind, stop_kind = key
        s = variants[key].stats()
        if not s["n"]:
            continue
        tag = "  <20 INCONCLUSIVE" if s["n"] < MIN_TRADES else ""
        avg_r = f"{s['avg_net_r']:+.3f}" if "avg_net_r" in s else "  n/a "
        tot_r = f"{s['total_net_r']:+.2f}" if "total_net_r" in s else "  n/a "
        print(f"{thr_key:<6} {exit_kind:<7} {stop_kind:<7} {s['n']:>4} "
              f"{s['win_pct']:>5.1f}% {s['avg_gross_pct']:>+8.3f}% "
              f"{s['avg_funding_pct']:>+8.3f}% {s['avg_net_pct']:>+7.3f}% "
              f"{s['total_net_pct']:>+7.2f}% {avg_r:>8} {tot_r:>8} "
              f"{s['eod']:>4} {s['t_net']:>+6.2f}{tag}")

    print("\n=== per-symbol breakdown (net %, n) ===")
    for key in sorted(variants):
        thr_key, exit_kind, stop_kind = key
        rows = []
        for sym in sorted(all_results):
            trades = all_results[sym].get(key, [])
            if trades:
                net = sum(t.net_pct for t in trades)
                rows.append(f"{sym}:{len(trades)}/{net:+.2f}%")
        if rows:
            print(f"{thr_key} {exit_kind:<7} {stop_kind:<7}  " + "  ".join(rows))

    print("\n=== funding coverage per symbol ===")
    for sym in sorted(funding_coverage):
        first, last = funding_coverage[sym]
        span_d = (last - first) / 86_400_000 if last > first else 0
        print(f"{sym:<10} prints {span_d:6.1f}d  first={first} last={last}")
    print(f"\nCost model: FEE_ROUND_TRIP={FEE_ROUND_TRIP * 100:.3f}% of "
          f"notional per trade (0.045% taker + 0.01% slippage per side).")


async def main() -> None:
    global _DAYS
    ap = argparse.ArgumentParser(
        description="Funding-rate contrarian mean-reversion backtest")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=365,
                    help="funding window in days (default 365)")
    args = ap.parse_args()
    _DAYS = args.days

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    # window_for_days aligns `end` down to the last CLOSED 5m bar, so nothing
    # still-forming enters either the funding or the kline fetch.
    start_ms, end_ms = window_for_days(args.days)
    start_ms = int(start_ms)
    end_ms = int(end_ms)
    # klines need the same window; funding start defines signal coverage
    print(f"Funding-contrarian backtest: {args.days}d window, "
          f"{len(symbols)} symbols, thresholds={list(THRESHOLDS)}")
    print(f"exits={EXIT_KINDS} stops={STOP_KINDS} "
          f"(18 variant runs per symbol)")

    binance = BinanceService()
    all_results: dict[str, dict[tuple, list[Trade]]] = {}
    funding_coverage: dict[str, tuple[int, int]] = {}
    funding_events: dict[str, list[dict]] = {}
    try:
        async with httpx.AsyncClient() as client:
            for sym in symbols:
                try:
                    funding = await fetch_funding_history(
                        client, sym, start_ms, end_ms)
                except Exception as exc:  # noqa: BLE001
                    print(f"{sym}: FUNDING FETCH FAILED — {exc}",
                          file=sys.stderr)
                    continue
                funding_events[sym] = funding
                if funding:
                    funding_coverage[sym] = (
                        funding[0]["fundingTime"], funding[-1]["fundingTime"])
                    span = (funding[-1]["fundingTime"]
                            - funding[0]["fundingTime"]) / 86_400_000
                    n_sig = sum(1 for e in funding
                                if e["fundingRate"] <= THRESHOLDS["m2bp"])
                    print(f"{sym}: {len(funding)} prints over {span:.1f}d, "
                          f"{n_sig} <= -0.02% signals", flush=True)
                else:
                    print(f"{sym}: no funding prints in window", flush=True)
                    funding_coverage[sym] = (0, 0)
                    continue

                try:
                    candles = await fetch_symbol(
                        binance, sym, "1h", start_ms, end_ms)
                except Exception as exc:  # noqa: BLE001
                    print(f"{sym}: KLINE FETCH FAILED — {exc}",
                          file=sys.stderr)
                    continue
                all_results[sym] = simulate_symbol(sym, candles, funding)
                n_tr = sum(len(v) for v in all_results[sym].values())
                print(f"{sym}: {len(candles)} 1h bars, "
                      f"{n_tr} variant-trades total", flush=True)
    finally:
        await binance.close()

    print_report(all_results, funding_coverage)


if __name__ == "__main__":
    asyncio.run(main())
