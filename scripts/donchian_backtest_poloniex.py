#!/usr/bin/env python3
"""Ensemble Donchian trend-following backtest — Poloniex USDT-M perps.

Variant of scripts/donchian_backtest.py for the Poloniex venue comparison
(analysis/venue-comparison-2026-11-05.md). Same strategy spec, same windows
and sanitize rules; differences:

  - Data source: Poloniex V3 public API
    GET https://api.poloniex.com/v3/market/candles?symbol=<BASE>_USDT_PERP
    &interval={HOUR_4,DAY_1}&sTime=<ms>&eTime=<ms>&limit=500 (max 500/page,
    paginated backward on eTime). Row layout: [low, high, open, close,
    quoteVol, baseVol, ?, openTime, closeTime].
  - Symbols: Poloniex lists only 12 of the 15 Binance universe names
    (no DOTUSDT/NEARUSDT/OPUSDT perps; ADA/LINK/SUI/UNI were re-listed
    2026-06..08 and have <4 months of history). DEFAULT_SYMBOLS is the
    8-name subset with >= ~4y of daily history.
  - Costs: Poloniex VIP2 futures taker 0.050% + 0.01% slippage per side =
    0.12% round trip (vs Binance modeled 0.11%). POL_COST_MODEL below.

Usage mirrors the Binance script:
    python scripts/donchian_backtest_poloniex.py --interval 1d --days 730
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

INTERVAL_MS = {"4h": 4 * 3_600_000, "1d": 86_400_000}
POL_INTERVAL = {"4h": "HOUR_4", "1d": "DAY_1"}
POL_API = "https://api.poloniex.com/v3/market/candles"


class _PolCost:
    """Round-trip cost model mirroring backend.core.cost_model.CostModel's
    interface, priced for Poloniex VIP2 taker fills."""
    round_trip = 2 * (0.00050 + 0.00010)   # 0.05% taker + 0.01% slip, per side

    @staticmethod
    def round_trip_in_r(entry: float, risk_dist: float) -> float:
        if risk_dist <= 0:
            return float("inf")
        return (entry * _PolCost.round_trip) / risk_dist


COST_MODEL = _PolCost

# Strategy spec is identical to scripts/donchian_backtest.py (read its
# docstring): Donchian ensemble lookbacks {10,20,55}, >=2-of-3 channel
# conviction, fill next bar open, 20-bar opposite-extreme initial stop,
# 10-bar ratcheting trail, no TP. Only the data source, symbol set and
# cost model differ here.

LOOKBACKS = (10, 20, 55)
MIN_CHANNEL_HITS = 2          # conviction filter: >=2 of 3 tops/bottoms
STOP_LOOKBACK = 20            # initial stop = mid (20-bar) opposite extreme
TRAIL_LOOKBACK = 10           # trailing stop = shortest (10-bar) channel
WARMUP_BARS = 70              # > max lookback so channels are warm on bar 1
MIN_TRADES = 20               # cells below this are inconclusive
# A 20-bar opposite extreme can sit arbitrarily close to the fill (tight
# range), making risk -> ~0 and any normal move an enormous R multiple (seen:
# UNIUSDT 2024-06-18 long, risk ~0.01% -> +123R on a +1.4% notional move).
# --min-risk-pct drops signals whose |entry - init_stop| / entry is below the
# floor; 0.0 keeps the spec-faithful raw run.

# Poloniex USDT-M perps with >= ~4y of daily kline history as of 2026-11-05
# (see analysis/venue-comparison-2026-11-05.md for listing dates). ADA/LINK/
# SUI/UNI exist but were re-listed 2026-06..08 — too little history for the
# 730d window; DOT/NEAR/OP have no Poloniex perp.
DEFAULT_SYMBOLS = ("BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT,"
                   "LTCUSDT,AVAXUSDT")
DEFAULT_DAYS = {"4h": 180, "1d": 730}
CACHE_DIR = Path("/tmp/donchian_klines_pol")


# ------------------------------------------------------------------ channels

def channel_extremes(candles: list[dict]) -> dict[int, tuple[list, list]]:
    """For each lookback N: (tops, bottoms) where tops[i] = max high of the N
    bars strictly before i, bottoms[i] = min low of the N bars before i.
    Index i < N -> None (not enough history)."""
    n = len(candles)
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    out = {}
    for N in LOOKBACKS:
        tops: list[float | None] = [None] * n
        bots: list[float | None] = [None] * n
        for i in range(N, n):
            tops[i] = max(highs[i - N:i])
            bots[i] = min(lows[i - N:i])
        out[N] = (tops, bots)
    return out


# ---------------------------------------------------------------- data clean

def sanitize_candles(candles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop corrupt bars (see module docstring). Returns (kept, dropped)."""
    n = len(candles)
    closes = [c["close"] for c in candles]
    keep = [True] * n
    for i, c in enumerate(candles):
        o, h, l, cl = c["open"], c["high"], c["low"], c["close"]
        if min(o, h, l, cl) <= 0 or h < l:
            keep[i] = False
            continue
        refs = [closes[j] for j in (i - 1, i + 1) if 0 <= j < n]
        if not refs:
            continue
        ref = sorted(refs)[len(refs) // 2]
        if ref > 0 and (h > 2.2 * ref or l < ref / 2.2):
            keep[i] = False
    return ([c for i, c in enumerate(candles) if keep[i]],
            [c for i, c in enumerate(candles) if not keep[i]])


# -------------------------------------------------------------------- trades

@dataclass
class Trade:
    symbol: str
    side: str            # "long" | "short"
    entry_i: int         # bar index of the fill (its open)
    exit_i: int
    entry_time: int
    exit_time: int
    entry: float
    exit: float
    init_stop: float     # frozen 20-bar opposite extreme at signal time
    risk: float          # |entry - init_stop|, price distance
    bars_held: int = 0
    gross_r: float = 0.0
    fee_r: float = 0.0
    net_r: float = 0.0
    pct_gross: float = 0.0   # % return on notional, before costs
    pct_net: float = 0.0     # % return on notional, after 0.11% RT
    reason: str = ""         # "trail" (only exit path besides window end)


@dataclass
class SymbolResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    candles: int = 0
    dropped_bars: int = 0


def simulate(candles: list[dict], symbol: str,
             window_start_ms: int,
             min_risk_pct: float = 0.0) -> list[Trade]:
    """Replay the ensemble Donchian rules over one symbol's closed bars.

    `candles` includes WARMUP_BARS of pre-window history; only bars with
    open_time >= window_start_ms may generate signals.
    """
    n = len(candles)
    closes = [c["close"] for c in candles]
    ch = channel_extremes(candles)
    tops = {N: ch[N][0] for N in LOOKBACKS}
    bots = {N: ch[N][1] for N in LOOKBACKS}

    trades: list[Trade] = []
    pos: Trade | None = None
    stop = 0.0                    # stop in force for `pos` (ratchets)
    pending_entry: dict | None = None   # fills at next bar open
    pending_exit = False          # fills at next bar open

    def channel_at(i: int, table: dict[int, list]) -> dict[int, float]:
        return {N: table[N][i] for N in LOOKBACKS}

    for k in range(n):
        c = candles[k]
        o, h, l = c["open"], c["high"], c["low"]

        # 1. scheduled exit fills at this bar's OPEN
        if pending_exit and pos is not None:
            sign = 1.0 if pos.side == "long" else -1.0
            pos.exit = o
            pos.exit_i = k
            pos.exit_time = c["open_time"]
            pos.bars_held = k - pos.entry_i
            pos.gross_r = sign * (o - pos.entry) / pos.risk
            pos.fee_r = COST_MODEL.round_trip_in_r(pos.entry, pos.risk)
            pos.net_r = pos.gross_r - pos.fee_r
            pos.pct_gross = sign * (o - pos.entry) / pos.entry * 100.0
            pos.pct_net = pos.pct_gross - COST_MODEL.round_trip * 100.0
            pos.reason = "trail"
            trades.append(pos)
            pos = None
            stop = 0.0
        pending_exit = False

        # 2. scheduled entry fills at this bar's OPEN
        if pending_entry is not None and pos is None:
            side = pending_entry["side"]
            sig_i = pending_entry["signal_i"]
            init_stop = pending_entry["init_stop"]
            pending_entry = None
            entry = o
            risk = ((entry - init_stop) if side == "long"
                    else (init_stop - entry))
            if risk > 0 and (min_risk_pct <= 0
                             or 100.0 * risk / entry >= min_risk_pct):
                pos = Trade(
                    symbol=symbol, side=side, entry_i=k, exit_i=k,
                    entry_time=c["open_time"], exit_time=c["open_time"],
                    entry=entry, exit=entry, init_stop=init_stop,
                    risk=risk)
                # stop in force during the entry bar = initial stop
                stop = init_stop
            # degenerate fill (open already through the stop): drop signal

        # 3. intrabar stop check on THIS bar against the stop set at the
        #    previous bar's close. Detection on OHLC, execution next open.
        if pos is not None:
            touched = ((l <= stop) if pos.side == "long"
                       else (h >= stop))
            if touched:
                pending_exit = True

        # 4. at THIS bar's close: trail update for an open position, then
        #    (only if flat) signal evaluation for the NEXT bar's open.
        if pos is not None:
            # trailing channel uses the TRAIL_LOOKBACK table: recompute from
            # raw lows/highs of the prior TRAIL_LOOKBACK bars (excl. bar k).
            if k >= TRAIL_LOOKBACK:
                if pos.side == "long":
                    trail = min(candles[j]["low"]
                                for j in range(k - TRAIL_LOOKBACK, k))
                    stop = max(stop, trail)
                else:
                    trail = max(candles[j]["high"]
                                for j in range(k - TRAIL_LOOKBACK, k))
                    stop = min(stop, trail)
        elif pending_entry is None and k + 1 < n \
                and c["open_time"] >= window_start_ms:
            # signal on bar k's close; needs all channels at k
            t_k = channel_at(k, tops)
            b_k = channel_at(k, bots)
            if all(v is not None for v in t_k.values()) \
                    and all(v is not None for v in b_k.values()):
                up_hits = sum(1 for N in LOOKBACKS if closes[k] > t_k[N])
                dn_hits = sum(1 for N in LOOKBACKS if closes[k] < b_k[N])
                side = None
                if up_hits >= MIN_CHANNEL_HITS and dn_hits < MIN_CHANNEL_HITS:
                    side = "long"
                elif dn_hits >= MIN_CHANNEL_HITS and up_hits < MIN_CHANNEL_HITS:
                    side = "short"
                if side is not None:
                    # initial stop = STOP_LOOKBACK opposite channel extreme,
                    # frozen from the signal bar's prior bars.
                    if k >= STOP_LOOKBACK:
                        if side == "long":
                            init_stop = min(
                                candles[j]["low"]
                                for j in range(k - STOP_LOOKBACK, k))
                        else:
                            init_stop = max(
                                candles[j]["high"]
                                for j in range(k - STOP_LOOKBACK, k))
                        pending_entry = {"side": side, "signal_i": k,
                                         "init_stop": init_stop}

    # window end: close any open position at the last bar's close
    if pos is not None:
        last = candles[-1]
        sign = 1.0 if pos.side == "long" else -1.0
        pos.exit = last["close"]
        pos.exit_i = n - 1
        pos.exit_time = last["open_time"]
        pos.bars_held = pos.exit_i - pos.entry_i
        pos.gross_r = sign * (pos.exit - pos.entry) / pos.risk
        pos.fee_r = COST_MODEL.round_trip_in_r(pos.entry, pos.risk)
        pos.net_r = pos.gross_r - pos.fee_r
        pos.pct_gross = sign * (pos.exit - pos.entry) / pos.entry * 100.0
        pos.pct_net = pos.pct_gross - COST_MODEL.round_trip * 100.0
        pos.reason = "window_end"
        trades.append(pos)
    return trades


# -------------------------------------------------------------------- fetch

def _pol_symbol(symbol: str) -> str:
    """BTCUSDT -> BTC_USDT_PERP (Poloniex futures convention)."""
    return f"{symbol[:-4]}_USDT_PERP" if symbol.endswith("USDT") else symbol


def _pol_get(path: str) -> dict:
    req = urllib.request.Request(
        POL_API + "?" + path, headers={"User-Agent": "fubot-research"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_poloniex_candles(symbol: str, interval: str,
                           fetch_start_ms: int, end_ms: int) -> list[dict]:
    """Paginate /v3/market/candles backward on eTime (max 500/page).

    Returns candle dicts {open_time, open, high, low, close} covering
    [fetch_start_ms, end_ms); the still-forming bar at end_ms is excluded
    because end_ms is already the next bar's open time.
    """
    pol_sym = _pol_symbol(symbol)
    iv = POL_INTERVAL[interval]
    rows: dict[int, list] = {}
    cursor_end = end_ms
    for _ in range(40):  # safety bound; ~20 pages covers 10k daily bars
        qs = (f"symbol={pol_sym}&interval={iv}"
              f"&sTime={fetch_start_ms}&eTime={cursor_end}&limit=500")
        payload = _pol_get(qs)
        if payload.get("code") != 200:
            raise RuntimeError(f"Poloniex {pol_sym} {iv}: {payload}")
        data = payload.get("data") or []
        if not data:
            break
        for row in data:
            rows[int(row[7])] = row
        oldest = min(int(r[7]) for r in data)
        if len(data) < 500 or oldest <= fetch_start_ms:
            break
        cursor_end = oldest
        time.sleep(0.35)  # be gentle on the IP-based market-data RL
    candles = []
    for ts in sorted(rows):
        if fetch_start_ms <= ts < end_ms:
            row = rows[ts]
            candles.append({
                "open_time": ts,
                "open": float(row[2]),
                "high": float(row[1]),
                "low": float(row[0]),
                "close": float(row[3]),
            })
    return candles


async def fetch_symbol(symbol: str, interval: str,
                       fetch_start_ms: int, end_ms: int,
                       use_cache: bool) -> list[dict]:
    cache = CACHE_DIR / f"{symbol}_{interval}_{fetch_start_ms}_{end_ms}.json"
    if use_cache and cache.exists():
        data = json.loads(cache.read_text())
        return [dict(c) for c in data]
    last_exc: Exception | None = None
    candles: list[dict] = []
    for attempt in (1, 2):
        try:
            candles = await asyncio.to_thread(
                fetch_poloniex_candles, symbol, interval,
                fetch_start_ms, end_ms)
            break
        except Exception as exc:  # noqa: BLE001 - retry once then report
            last_exc = exc
            if attempt == 1:
                print(f"  [{symbol}] fetch failed ({exc}); retrying once",
                      file=sys.stderr)
                await asyncio.sleep(1.5)
    if not candles:
        raise last_exc  # type: ignore[misc]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(candles))
    return candles


def window_bounds(days: int, interval: str,
                  end_days_ago: int = 0) -> tuple[int, int]:
    """[start_ms, end_ms): `days` of `interval` ending at the last CLOSED bar
    of that interval (exclusive end = open time of the still-forming bar),
    shifted `end_days_ago` days into the past for disjoint windows."""
    bar = INTERVAL_MS[interval]
    end = (int(time.time() * 1000) // bar) * bar
    end -= end_days_ago * 86_400_000
    start = end - days * 86_400_000
    return start, end


async def run(symbols: list[str], interval: str, days: int,
              use_cache: bool, end_days_ago: int = 0,
              min_risk_pct: float = 0.0) -> list[SymbolResult]:
    results: list[SymbolResult] = []
    start_ms, end_ms = window_bounds(days, interval, end_days_ago)
    fetch_start = start_ms - WARMUP_BARS * INTERVAL_MS[interval]
    for sym in symbols:
        print(f"[{sym}] fetching {interval} ~{days}d (Poloniex) ...",
              file=sys.stderr)
        try:
            candles = await fetch_symbol(
                sym, interval, fetch_start, end_ms, use_cache)
        except Exception as exc:  # noqa: BLE001 - skip bad symbols
            print(f"  [{sym}] FAILED: {exc}", file=sys.stderr)
            continue
        candles, dropped = sanitize_candles(candles)
        if dropped:
            print(f"  [{sym}] dropped {len(dropped)} corrupt bars",
                  file=sys.stderr)
        # keep only bars inside the scored window for signal gating;
        # warmup bars remain in the array for channel math
        trades = simulate(candles, sym, start_ms, min_risk_pct)
        res = SymbolResult(symbol=sym, trades=trades,
                           candles=len(candles),
                           dropped_bars=len(dropped))
        results.append(res)
        print(f"  {sym}: {len(candles)} bars, {len(trades)} trades",
              file=sys.stderr)
    return results


# -------------------------------------------------------------------- stats

def pooled_stats(trades: list[Trade]) -> dict:
    n = len(trades)
    if not n:
        return {"trades": 0}
    wins = sum(1 for t in trades if t.net_r > 0)
    gross = sum(t.gross_r for t in trades)
    net = sum(t.net_r for t in trades)
    gp = sum(t.net_r for t in trades if t.net_r > 0)
    gl = -sum(t.net_r for t in trades if t.net_r <= 0)
    # equity curve in exit-time order for max drawdown in R
    ordered = sorted(trades, key=lambda t: t.exit_time)
    eq = peak = mdd = 0.0
    for t in ordered:
        eq += t.net_r
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    longs = sum(1 for t in trades if t.side == "long")
    shorts = n - longs
    bars = [t.bars_held for t in trades]
    return {
        "trades": n,
        "win_pct": round(100 * wins / n, 1),
        "longs": longs, "shorts": shorts,
        "gross_r": round(gross, 2),
        "net_r": round(net, 2),
        "expNetR": round(net / n, 3),
        "profit_factor": round(gp / gl, 2) if gl > 0 else float("inf"),
        "max_dd_r": round(mdd, 2),
        "avg_bars_held": round(sum(bars) / n, 1),
        "pct_net_avg": round(sum(t.pct_net for t in trades) / n, 3),
        "inconclusive": n < MIN_TRADES,
    }


def print_report(results: list[SymbolResult], interval: str, days: int,
                 label: str = ""):
    all_trades = [t for r in results for t in r.trades]
    pooled = pooled_stats(all_trades)
    dropped = sum(r.dropped_bars for r in results)
    print(f"\n=== DONCHIAN ensemble {interval} {label or ('~' + str(days) + 'd')} — POOLED ===")
    if dropped:
        print(f"(dropped {dropped} corrupt kline bars across "
              f"{sum(1 for r in results if r.dropped_bars)} symbols)")
    if pooled["trades"]:
        flag = "  [INCONCLUSIVE <20 trades]" if pooled["inconclusive"] else ""
        print(f"trades={pooled['trades']} (L{pooled['longs']}/"
              f"S{pooled['shorts']}) win%={pooled['win_pct']} "
              f"grossR={pooled['gross_r']} netR={pooled['net_r']} "
              f"expNetR={pooled['expNetR']} "
              f"PF={pooled['profit_factor']} "
              f"maxDD={pooled['max_dd_r']}R "
              f"avgBars={pooled['avg_bars_held']} "
              f"avgNet%/trade={pooled['pct_net_avg']}{flag}")
    else:
        print("no trades")

    print(f"\n=== DONCHIAN {interval} — per-symbol ===")
    print(f"{'symbol':<10} {'trades':>7} {'win%':>6} {'netR':>9} "
          f"{'expNetR':>8} {'PF':>6} {'maxDD':>7}  flag")
    for r in sorted(results, key=lambda x: x.symbol):
        s = pooled_stats(r.trades)
        if not s["trades"]:
            print(f"{r.symbol:<10} {0:>7} {'-':>6} {'-':>9} {'-':>8} "
                  f"{'-':>6} {'-':>7}  no trades")
            continue
        flag = "INCONCLUSIVE" if s["inconclusive"] else ""
        print(f"{r.symbol:<10} {s['trades']:>7} {s['win_pct']:>6} "
              f"{s['net_r']:>9} {s['expNetR']:>8} {s['profit_factor']:>6} "
              f"{s['max_dd_r']:>7}  {flag}")

    # exit reason mix
    reasons: dict[str, int] = {}
    for t in all_trades:
        reasons[t.reason] = reasons.get(t.reason, 0) + 1
    if reasons:
        print(f"\nexit reasons: {reasons}")
    if pooled.get("inconclusive"):
        print(f"\nNOTE: pooled cell has {pooled['trades']} trades "
              f"(<{MIN_TRADES}) — treat as inconclusive.")


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="Ensemble Donchian trend backtest (4h / 1d)")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--interval", default=None, choices=["4h", "1d"],
                    help="default: 4h->180d, 1d->730d; omit to run both")
    ap.add_argument("--days", type=int, default=None,
                    help="override window length in days")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore /tmp/donchian_klines cache")
    ap.add_argument("--end-days-ago", type=int, default=0,
                    help="shift window backward N days (disjoint slices)")
    ap.add_argument("--min-risk-pct", type=float, default=0.0,
                    help="drop entries whose stop distance is below this "
                         "%% of entry (0 = spec-faithful, no floor)")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    intervals = [args.interval] if args.interval else ["4h", "1d"]
    for interval in intervals:
        days = args.days or DEFAULT_DAYS[interval]
        results = await run(symbols, interval, days, not args.no_cache,
                            args.end_days_ago, args.min_risk_pct)
        label = (f"~{days}d" if not args.end_days_ago
                 else f"{days}d ending {args.end_days_ago}d ago")
        if args.min_risk_pct:
            label += f" minRisk>={args.min_risk_pct}%"
        print_report(results, interval, days, label)


if __name__ == "__main__":
    asyncio.run(main())
