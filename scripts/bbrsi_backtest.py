#!/usr/bin/env python3
"""Backtest the BBRSI strategy (geraked/tradingview strategies/BBRSI.pine, MIT).

Standalone research harness — does NOT touch backend/backtest.py or live code.
Reuses backend/backtest_data.py for paginated candle fetching and
backend/core/cost_model.py for the account-calibrated round-trip cost.

Pine v5 rules ported faithfully (defaults):
  bbBasis = SMA(close,20); dev = 2.0 * ta.stdev(close,20)   (ta.stdev biased
           -> population stdev, /n not /(n-1))
  RSI(3) Wilder/RMA
  longSignal  = rsi[1]<30 and close[1]<bbLower[1] and rsi>30 and close>bbLower
                and rsi<50 and close<bbBasis
  shortSignal = mirror at 70 / bbUpper / 50
  longSL  = bbLower - 1.5*(bbBasis - bbLower)  (= bbBasis - 2.5*dev)
  shortSL = bbUpper + 1.5*(bbUpper - bbBasis)
  TP = fill +/- 1.5 * (fill - SL)   (Pine uses the ACTUAL entry fill price)
  Entries fill at NEXT bar open (strategy.entry default); one position per
  symbol; extra signals while in position are ignored (pyramiding=1).
  Optional closeBbBasis exit: ta.crossover(close, bbBasis) closes longs,
  ta.crossunder closes shorts — filled at the NEXT bar open like every other
  strategy order. Tested as a separate variant.

Exit simulation per bar OHLC: pending basis exit fills at bar open first;
otherwise gap-through fills at the adverse open; when SL and TP are both
inside one bar's range the SL is checked first (conservative — OHLC cannot
order them). Round-trip cost charged in R via CostModel.round_trip_in_r,
identical math to backend/backtest.py's fee_in_r.

Usage:
    python scripts/bbrsi_backtest.py --symbols BTCUSDT,ETHUSDT --days 45 --interval 15m
    python scripts/bbrsi_backtest.py --days 45 --interval 1h --close-on-basis
"""
import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.backtest_data import (  # noqa: E402
    align_window, bars_expected, fetch_klines, window_for_days,
)
from backend.core.cost_model import LIVE as COST_MODEL  # noqa: E402

BB_LEN = 20
BB_MULT = 2.0
RSI_LEN = 3
RSI_UPPER, RSI_MID, RSI_LOWER = 70.0, 50.0, 30.0
SL_COEF = 1.5
TP_COEF = 1.5

DEFAULT_SYMBOLS = "APEUSDT,BNBUSDT,BTCUSDT,LINKUSDT,OPUSDT,SOLUSDT,UNIUSDT"


# ---------------------------------------------------------------- indicators

def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    acc = 0.0
    for i, v in enumerate(values):
        acc += v
        if i >= period:
            acc -= values[i - period]
        if i >= period - 1:
            out[i] = acc / period
    return out


def stdev_pop(values: list[float], period: int) -> list[float | None]:
    """Pine ta.stdev(close, n) with default biased=true -> population /n."""
    out: list[float | None] = [None] * len(values)
    for i in range(period - 1, len(values)):
        win = values[i - period + 1:i + 1]
        m = sum(win) / period
        out[i] = (sum((x - m) ** 2 for x in win) / period) ** 0.5
    return out


def rma(values: list[float | None], period: int) -> list[float | None]:
    """Wilder RMA as Pine ta.rma: seeded with SMA of first `period` values."""
    out: list[float | None] = [None] * len(values)
    seed = period - 1
    buf: list[float] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        buf.append(v)
        if len(buf) < period:
            continue
        if len(buf) == period:
            prev = sum(buf) / period
        else:
            prev = (prev * (period - 1) + v) / period  # type: ignore[operator]
        out[i] = prev
    return out


def rsi_wilder(closes: list[float], period: int = RSI_LEN) -> list[float | None]:
    changes: list[float | None] = [None] * len(closes)
    for i in range(1, len(closes)):
        changes[i] = closes[i] - closes[i - 1]
    ups = rma([None if c is None else max(c, 0.0) for c in changes], period)
    downs = rma([None if c is None else -min(c, 0.0) for c in changes], period)
    out: list[float | None] = [None] * len(closes)
    for i in range(len(closes)):
        u, d = ups[i], downs[i]
        if u is None or d is None:
            continue
        out[i] = 100.0 if d == 0 else (0.0 if u == 0 else 100 - 100 / (1 + u / d))
    return out


# ------------------------------------------------------------------ trades

@dataclass
class Trade:
    symbol: str
    side: str            # "long" | "short"
    entry_i: int         # index of entry bar (fill at its open)
    exit_i: int
    entry: float
    exit: float
    sl: float
    tp: float
    risk: float          # |entry - sl|, price distance
    gross_r: float = 0.0
    net_r: float = 0.0
    fee_r: float = 0.0
    reason: str = ""
    stop_pct: float = 0.0  # |entry-sl|/entry * 100


@dataclass
class VariantResult:
    name: str
    trades: list[Trade] = field(default_factory=list)

    def stats(self) -> dict:
        n = len(self.trades)
        wins = sum(1 for t in self.trades if t.gross_r > 0)
        gross = sum(t.gross_r for t in self.trades)
        net = sum(t.net_r for t in self.trades)
        fees = sum(t.fee_r for t in self.trades)
        return {
            "trades": n,
            "win_pct": round(100 * wins / n, 1) if n else 0.0,
            "gross_r": round(gross, 2),
            "net_r": round(net, 2),
            "fee_r": round(fees, 2),
            "expNetR": round(net / n, 3) if n else 0.0,
        }


# ------------------------------------------------------------- simulation

def simulate(candles: list[dict], close_on_basis: bool) -> list[Trade]:
    """Replay BBRSI over one symbol's candle list. Returns closed trades."""
    closes = [c["close"] for c in candles]
    basis = sma(closes, BB_LEN)
    dev = [None if b is None else BB_MULT * s
           for b, s in zip(basis, stdev_pop(closes, BB_LEN))]
    # bbLower/bbUpper implied by basis +/- dev
    rsi = rsi_wilder(closes, RSI_LEN)

    n = len(candles)
    trades: list[Trade] = []
    pos: Trade | None = None
    pending_entry: dict | None = None   # fills at next bar open
    pending_basis_exit = False          # fills at next bar open

    # warmup: need basis/dev/rsi defined at i and i-1
    start = 0
    for i in range(n):
        if (basis[i] is not None and dev[i] is not None and rsi[i] is not None
                and i > 0 and basis[i - 1] is not None
                and dev[i - 1] is not None and rsi[i - 1] is not None):
            start = i
            break

    def bb_at(i: int) -> tuple[float, float, float]:
        b, d = basis[i], dev[i]
        assert b is not None and d is not None
        return b, b - d, b + d  # basis, lower, upper

    def close_trade(t: Trade, exit_price: float, exit_i: int, reason: str) -> None:
        sign = 1.0 if t.side == "long" else -1.0
        t.exit = exit_price
        t.exit_i = exit_i
        t.gross_r = sign * (exit_price - t.entry) / t.risk
        t.fee_r = COST_MODEL.round_trip_in_r(t.entry, t.risk)
        t.net_r = t.gross_r - t.fee_r
        t.reason = reason
        trades.append(t)

    for i in range(start, n):
        o, h, l, c = (candles[i][k] for k in ("open", "high", "low", "close"))

        # --- fills at this bar's OPEN -------------------------------------
        if pos is not None and pending_basis_exit:
            close_trade(pos, o, i, "basis_cross")
            pos = None
            pending_basis_exit = False

        if pending_entry is not None:
            side = pending_entry["side"]
            sl_sig = pending_entry["sl"]
            sym = pending_entry["symbol"]
            entry = o
            if side == "long":
                risk = entry - sl_sig
            else:
                risk = sl_sig - entry
            pending_entry = None
            if risk > 0:
                if side == "long":
                    tp = entry + TP_COEF * risk
                else:
                    tp = entry - TP_COEF * risk
                pos = Trade(
                    symbol=sym, side=side, entry_i=i, exit_i=i,
                    entry=entry, exit=entry, sl=sl_sig, tp=tp, risk=risk,
                    stop_pct=100.0 * abs(entry - sl_sig) / entry,
                )
            # degenerate fill (open already beyond SL): skip, stay flat

        # --- intrabar SL/TP on this bar (entry bar included) ---------------
        if pos is not None:
            # check the bar the position is exposed to; entry filled at open
            sl_hit = (l <= pos.sl) if pos.side == "long" else (h >= pos.sl)
            tp_hit = (h >= pos.tp) if pos.side == "long" else (l <= pos.tp)
            gap_sl = (o <= pos.sl) if pos.side == "long" else (o >= pos.sl)
            if sl_hit:
                # gap-through fills at the adverse open when open is beyond SL
                fill = o if gap_sl else pos.sl
                close_trade(pos, fill, i, "sl_gap" if gap_sl else "sl")
                pos = None
            elif tp_hit:
                gap_tp = (o >= pos.tp) if pos.side == "long" else (o <= pos.tp)
                fill = o if gap_tp else pos.tp
                close_trade(pos, fill, i, "tp_gap" if gap_tp else "tp")
                pos = None

        # --- basis-cross exit scheduled from THIS bar's close --------------
        if pos is not None and close_on_basis and i > 0:
            b_now = basis[i]
            b_prev = basis[i - 1]
            if b_now is not None and b_prev is not None:
                if pos.side == "long" and c > b_now and closes[i - 1] <= b_prev:
                    pending_basis_exit = True
                elif pos.side == "short" and c < b_now and closes[i - 1] >= b_prev:
                    pending_basis_exit = True

        # --- signal evaluation at THIS bar's close (needs i+1 to exist) ----
        if pos is None and pending_entry is None and i + 1 < n:
            b_i, lo_i, hi_i = bb_at(i)
            b_p, lo_p, hi_p = bb_at(i - 1)
            r_i, r_p = rsi[i], rsi[i - 1]
            assert r_i is not None and r_p is not None
            long_sig = (r_p < RSI_LOWER and closes[i - 1] < lo_p
                        and r_i > RSI_LOWER and c > lo_i
                        and r_i < RSI_MID and c < b_i)
            short_sig = (r_p > RSI_UPPER and closes[i - 1] > hi_p
                         and r_i < RSI_UPPER and c < hi_i
                         and r_i > RSI_MID and c > b_i)
            if long_sig or short_sig:
                side = "long" if long_sig else "short"
                sl_sig = (lo_i - SL_COEF * (b_i - lo_i)) if long_sig \
                    else (hi_i + SL_COEF * (hi_i - b_i))
                pending_entry = {"side": side, "sl": sl_sig,
                                 "symbol": candles[i].get("symbol", "")}

    # mark symbol on trades (set at fill time would be cleaner; do it now)
    for t in trades:
        t.symbol = candles[0].get("symbol", "") if candles else ""
    return trades


# ------------------------------------------------------------------- fetch

async def fetch_symbol(binance, symbol: str, interval: str,
                       start_ms: int, end_ms: int) -> list[dict]:
    """fetch_klines with one retry on transient failure."""
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
            for c in candles:
                c["symbol"] = symbol
            return candles
        except Exception as exc:  # noqa: BLE001 - retry once then report
            last_exc = exc
            if attempt == 1:
                print(f"  [{symbol}] fetch failed ({exc}); retrying once",
                      file=sys.stderr)
                await asyncio.sleep(1.0)
    raise last_exc  # type: ignore[misc]


_DAYS = 45  # set in main; fetch_symbol sanity-checks against it


async def run(symbols: list[str], days: int, interval: str,
              close_on_basis_only: bool, end_days_ago: int = 0):
    global _DAYS
    _DAYS = days
    from backend.services.binance_service import BinanceService
    binance = BinanceService()
    try:
        start_ms, end_ms = window_for_days(days)
        if end_days_ago:
            end_ms -= end_days_ago * 86_400_000
            start_ms -= end_days_ago * 86_400_000

        per_symbol: dict[str, dict[str, VariantResult]] = {}
        for sym in symbols:
            print(f"[{sym}] fetching {interval} {days}d ...", file=sys.stderr)
            candles = await fetch_symbol(binance, sym, interval,
                                         start_ms, end_ms)
            variants = {}
            if close_on_basis_only:
                variants["basis_exit"] = VariantResult("basis_exit")
                variants["basis_exit"].trades = simulate(candles, True)
            else:
                for name, cob in (("no_basis_exit", False),
                                  ("basis_exit", True)):
                    vr = VariantResult(name)
                    vr.trades = simulate(candles, cob)
                    variants[name] = vr
            per_symbol[sym] = variants
            for name, vr in variants.items():
                s = vr.stats()
                print(f"  {name}: {s}", file=sys.stderr)
        return per_symbol
    finally:
        await binance.close()


def print_report(per_symbol: dict[str, dict[str, VariantResult]],
                 interval: str, days: int):
    variant_names = next(iter(next(iter(per_symbol.values())).keys())) \
        if per_symbol else []
    # collect all variant names across symbols
    names = sorted({n for v in per_symbol.values() for n in v})
    print(f"\n=== BBRSI {interval} {days}d — per-symbol ===")
    header = (f"{'symbol':<10} " + " ".join(
        f"{n + '_trades':>22} {n + '_netR':>10} {n + '_expNetR':>10}"
        for n in names))
    print(header)
    totals = {n: {"trades": 0, "wins": 0, "gross": 0.0, "net": 0.0,
                  "fees": 0.0, "stops": []} for n in names}
    for sym in sorted(per_symbol):
        row = f"{sym:<10} "
        for n in names:
            vr = per_symbol[sym][n]
            s = vr.stats()
            row += (f"{s['trades']:>22} {s['net_r']:>10} "
                    f"{s['expNetR']:>10}  ")
            tt = totals[n]
            tt["trades"] += s["trades"]
            tt["wins"] += sum(1 for t in vr.trades if t.gross_r > 0)
            tt["gross"] += sum(t.gross_r for t in vr.trades)
            tt["net"] += sum(t.net_r for t in vr.trades)
            tt["fees"] += sum(t.fee_r for t in vr.trades)
            tt["stops"] += [t.stop_pct for t in vr.trades]
        print(row)

    print(f"\n=== BBRSI {interval} {days}d — TOTAL ===")
    for n in names:
        tt = totals[n]
        ntr = tt["trades"]
        gross = sum(t.gross_r for sym in per_symbol.values()
                    for t in sym[n].trades)
        net = sum(t.net_r for sym in per_symbol.values()
                  for t in sym[n].trades)
        fees = sum(t.fee_r for sym in per_symbol.values()
                   for t in sym[n].trades)
        wins = sum(1 for sym in per_symbol.values()
                   for t in sym[n].trades if t.gross_r > 0)
        stops = sorted(tt["stops"])
        med = stops[len(stops) // 2] if stops else 0.0
        print(f"{n}: trades={ntr} win%={100*wins/ntr:.1f} "
              f"grossR={gross:.2f} netR={net:.2f} feeR={fees:.2f} "
              f"expNetR={net/ntr:.3f}" if ntr else f"{n}: no trades")
        if stops:
            print(f"    stop-distance %: min={stops[0]:.3f} "
                  f"median={med:.3f} p90={stops[int(0.9*(len(stops)-1))]:.3f} "
                  f"max={stops[-1]:.3f}")
            reasons = {}
            for sym in per_symbol.values():
                for t in sym[n].trades:
                    reasons[t.reason] = reasons.get(t.reason, 0) + 1
            print(f"    exit reasons: {reasons}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="BBRSI strategy backtest")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--interval", default="15m",
                    choices=["5m", "15m", "1h"])
    ap.add_argument("--close-on-basis", action="store_true",
                    help="run ONLY the close-on-basis-cross variant "
                         "(default: run both variants)")
    ap.add_argument("--end-days-ago", type=int, default=0,
                    help="shift window backward N days (disjoint slices)")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    per_symbol = await run(symbols, args.days, args.interval,
                           args.close_on_basis, args.end_days_ago)
    print_report(per_symbol, args.interval, args.days)


if __name__ == "__main__":
    asyncio.run(main())
