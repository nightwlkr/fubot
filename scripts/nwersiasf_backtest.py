#!/usr/bin/env python3
"""Backtest the NWERSIASF strategy (geraked/tradingview strategies/NWERSIASF.pine, MIT).

Standalone research harness — does NOT touch backend/backtest.py or live code.
Reuses backend/backtest_data.py for paginated candle fetching and
backend/core/cost_model.py for the account-calibrated round-trip cost.

Pine v5 rules ported faithfully (defaults, repaint=false endpoint path):
  NWE: h=8.0, mult=3.0, lookback 500.
       w(i) = exp(-(i^2)/(h^2*2)) for i=0..499; center = sum(close[i]*w(i))/sum(w(i))
       mae = SMA(abs(close - center), 499) * 3.0
       upper = center + mae ; lower = center - mae
  RSI(5) Wilder/RMA
  ASF: avgTR = SMA(TR,14); m=0.5
       longSL  = signal_bar.low  - avgTR * 0.5
       shortSL = signal_bar.high + avgTR * 0.5
  long  = low[1]  < lower[1]  and close > open
          and close < lower + 0.5*(upper-lower) and rsi[1] < 30
  short = high[1] > upper[1]  and close < open
          and close > upper - 0.5*(upper-lower) and rsi[1] > 70
  TP = fill +/- 1.5 * (fill - SL)   (bbrsi convention: Pine uses `inn = close`
       at signal time; we use the actual next-bar-open fill like bbrsi_backtest)
  Entries fill at NEXT bar open (strategy.entry default); one position per
  symbol; extra signals while in position are ignored (pyramiding=1).

Exit simulation per bar OHLC identical to bbrsi_backtest.py: pending fills at
bar open; gap-through fills at the adverse open; when SL and TP are both
inside one bar's range the SL is checked first (conservative — OHLC cannot
order them). Round-trip cost charged in R via CostModel.round_trip_in_r
(0.11% round trip, same math as backend/backtest.py's fee_in_r).

Warmup: NWE needs 500 closes of history and the 499-bar MAE window, so the
first evaluable bar is ~997 bars into the fetched series.

Usage:
    python scripts/nwersiasf_backtest.py --symbols BTCUSDT --days 45 --interval 15m
    python scripts/nwersiasf_backtest.py --days 45 --interval 1h
"""
import argparse
import asyncio
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.backtest_data import (  # noqa: E402
    align_window, bars_expected, fetch_klines, window_for_days,
)
from backend.core.cost_model import LIVE as COST_MODEL  # noqa: E402

NWE_H = 8.0
NWE_MULT = 3.0
NWE_LEN = 500          # coefs i=0..499 -> needs 500 closes of history
MAE_LEN = 499          # ta.sma(..., 499)
RSI_LEN = 5
ATR_LEN = 14
ATR_MULT = 0.5
TP_COEF = 1.5
RSI_LOWER, RSI_UPPER = 30.0, 70.0

DEFAULT_SYMBOLS = "APEUSDT,BNBUSDT,BTCUSDT,LINKUSDT,OPUSDT,SOLUSDT,UNIUSDT"


# ---------------------------------------------------------------- indicators

def sma(values: list[float | None], period: int) -> list[float | None]:
    """SMA over trailing `period` NON-None values (Pine ta.sma semantics)."""
    out: list[float | None] = [None] * len(values)
    buf: list[float] = []
    acc = 0.0
    for i, v in enumerate(values):
        if v is None:
            continue
        buf.append(v)
        acc += v
        if len(buf) > period:
            acc -= buf.pop(0)
        if len(buf) == period:
            out[i] = acc / period
    return out


def nwe_envelope(closes: list[float]) -> tuple[list[float | None],
                                              list[float | None],
                                              list[float | None]]:
    """Non-repainting endpoint Nadaraya-Watson envelope.

    Returns (center, upper, lower); upper/lower are None until MAE_LEN valid
    centers exist (first valid index ~997 for a full window).
    """
    n = len(closes)
    coefs = [math.exp(-(i * i) / (NWE_H * NWE_H * 2.0)) for i in range(NWE_LEN)]
    den = sum(coefs)
    center: list[float | None] = [None] * n
    # center[i] = sum_{j=0..NWE_LEN-1} closes[i-j] * coefs[j] / den
    for i in range(NWE_LEN - 1, n):
        acc = 0.0
        base = i - NWE_LEN + 1
        for j in range(NWE_LEN):
            acc += closes[base + j] * coefs[NWE_LEN - 1 - j]
        center[i] = acc / den

    dev = [None if c is None else abs(closes[i] - c)
           for i, c in enumerate(center)]
    mae = sma(dev, MAE_LEN)
    upper: list[float | None] = [None] * n
    lower: list[float | None] = [None] * n
    for i in range(n):
        c, m = center[i], mae[i]
        if c is not None and m is not None:
            upper[i] = c + NWE_MULT * m
            lower[i] = c - NWE_MULT * m
    return center, upper, lower


def rma(values: list[float | None], period: int) -> list[float | None]:
    """Wilder RMA as Pine ta.rma: seeded with SMA of first `period` values."""
    out: list[float | None] = [None] * len(values)
    buf: list[float] = []
    prev: float | None = None
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


def atr_sma(candles: list[dict], period: int = ATR_LEN) -> list[float | None]:
    """SMA of true range — Pine smoothing='SMA' branch of the ASF."""
    n = len(candles)
    tr: list[float | None] = [None] * n
    for i in range(n):
        h, l = candles[i]["high"], candles[i]["low"]
        if i == 0:
            tr[i] = h - l
            continue
        pc = candles[i - 1]["close"]
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    return sma(tr, period)


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

def simulate(candles: list[dict]) -> list[Trade]:
    """Replay NWERSIASF over one symbol's candle list. Returns closed trades."""
    closes = [c["close"] for c in candles]
    _, upper, lower = nwe_envelope(closes)
    rsi = rsi_wilder(closes, RSI_LEN)
    atr = atr_sma(candles, ATR_LEN)

    n = len(candles)
    trades: list[Trade] = []
    pos: Trade | None = None
    pending_entry: dict | None = None   # fills at next bar open

    def bands_at(i: int) -> tuple[float, float]:
        lo, hi = lower[i], upper[i]
        assert lo is not None and hi is not None
        return lo, hi

    def close_trade(t: Trade, exit_price: float, exit_i: int, reason: str) -> None:
        sign = 1.0 if t.side == "long" else -1.0
        t.exit = exit_price
        t.exit_i = exit_i
        t.gross_r = sign * (exit_price - t.entry) / t.risk
        t.fee_r = COST_MODEL.round_trip_in_r(t.entry, t.risk)
        t.net_r = t.gross_r - t.fee_r
        t.reason = reason
        trades.append(t)

    for i in range(1, n):
        o, h, l, c = (candles[i][k] for k in ("open", "high", "low", "close"))

        # --- fills at this bar's OPEN -------------------------------------
        if pending_entry is not None:
            side = pending_entry["side"]
            sl_sig = pending_entry["sl"]
            sym = pending_entry["symbol"]
            entry = o
            risk = (entry - sl_sig) if side == "long" else (sl_sig - entry)
            pending_entry = None
            if risk > 0:
                tp = (entry + TP_COEF * risk) if side == "long" \
                    else (entry - TP_COEF * risk)
                pos = Trade(
                    symbol=sym, side=side, entry_i=i, exit_i=i,
                    entry=entry, exit=entry, sl=sl_sig, tp=tp, risk=risk,
                    stop_pct=100.0 * abs(entry - sl_sig) / entry,
                )
            # degenerate fill (open already beyond SL): skip, stay flat

        # --- intrabar SL/TP on this bar (entry bar included) ---------------
        if pos is not None:
            sl_hit = (l <= pos.sl) if pos.side == "long" else (h >= pos.sl)
            tp_hit = (h >= pos.tp) if pos.side == "long" else (l <= pos.tp)
            gap_sl = (o <= pos.sl) if pos.side == "long" else (o >= pos.sl)
            if sl_hit:
                fill = o if gap_sl else pos.sl
                close_trade(pos, fill, i, "sl_gap" if gap_sl else "sl")
                pos = None
            elif tp_hit:
                gap_tp = (o >= pos.tp) if pos.side == "long" else (o <= pos.tp)
                fill = o if gap_tp else pos.tp
                close_trade(pos, fill, i, "tp_gap" if gap_tp else "tp")
                pos = None

        # --- signal evaluation at THIS bar's close (needs i+1 to exist) ----
        # Requires bands/rsi/atr at i AND i-1, and i+1 < n for the fill.
        if pos is None and pending_entry is None and i + 1 < n:
            if (lower[i] is None or upper[i] is None
                    or lower[i - 1] is None or upper[i - 1] is None):
                continue
            lo_i, hi_i = bands_at(i)
            lo_p, hi_p = bands_at(i - 1)
            r_i, r_p = rsi[i], rsi[i - 1]
            a_i = atr[i]
            if r_i is not None and r_p is not None and a_i is not None:
                l_prev, h_prev = candles[i - 1]["low"], candles[i - 1]["high"]
                bull = (c > o)
                bear = (c < o)
                long_sig = (l_prev < lo_p and bull
                            and c < lo_i + 0.5 * (hi_i - lo_i)
                            and r_p < RSI_LOWER)
                short_sig = (h_prev > hi_p and bear
                             and c > hi_i - 0.5 * (hi_i - lo_i)
                             and r_p > RSI_UPPER)
                if long_sig or short_sig:
                    side = "long" if long_sig else "short"
                    # SL anchored to the SIGNAL bar (i), fixed before fill
                    sl_sig = ((l - a_i * ATR_MULT) if long_sig
                              else (h + a_i * ATR_MULT))
                    pending_entry = {"side": side, "sl": sl_sig,
                                     "symbol": candles[i].get("symbol", "")}

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
              end_days_ago: int = 0):
    global _DAYS
    _DAYS = days
    from backend.services.binance_service import BinanceService
    binance = BinanceService()
    try:
        start_ms, end_ms = window_for_days(days)
        if end_days_ago:
            end_ms -= end_days_ago * 86_400_000
            start_ms -= end_days_ago * 86_400_000

        per_symbol: dict[str, VariantResult] = {}
        for sym in symbols:
            print(f"[{sym}] fetching {interval} {days}d ...", file=sys.stderr)
            candles = await fetch_symbol(binance, sym, interval,
                                         start_ms, end_ms)
            vr = VariantResult("nwersiasf")
            vr.trades = simulate(candles)
            per_symbol[sym] = vr
            s = vr.stats()
            print(f"  nwersiasf: {s}", file=sys.stderr)
        return per_symbol
    finally:
        await binance.close()


def print_report(per_symbol: dict[str, VariantResult],
                 interval: str, days: int):
    names = sorted({v.name for v in per_symbol.values()}) or ["nwersiasf"]
    print(f"\n=== NWERSIASF {interval} {days}d — per-symbol ===")
    header = (f"{'symbol':<10} " + " ".join(
        f"{n + '_trades':>22} {n + '_netR':>10} {n + '_expNetR':>10}"
        for n in names))
    print(header)
    for sym in sorted(per_symbol):
        row = f"{sym:<10} "
        for n in names:
            vr = per_symbol[sym]
            s = vr.stats()
            row += (f"{s['trades']:>22} {s['net_r']:>10} "
                    f"{s['expNetR']:>10}  ")
        print(row)

    print(f"\n=== NWERSIASF {interval} {days}d — TOTAL ===")
    for n in names:
        all_trades = [t for v in per_symbol.values() for t in v.trades]
        ntr = len(all_trades)
        wins = sum(1 for t in all_trades if t.gross_r > 0)
        gross = sum(t.gross_r for t in all_trades)
        net = sum(t.net_r for t in all_trades)
        fees = sum(t.fee_r for t in all_trades)
        if ntr:
            print(f"{n}: trades={ntr} win%={100*wins/ntr:.1f} "
                  f"grossR={gross:.2f} netR={net:.2f} feeR={fees:.2f} "
                  f"expNetR={net/ntr:.3f}")
        else:
            print(f"{n}: no trades")
        stops = sorted(t.stop_pct for t in all_trades)
        if stops:
            med = stops[len(stops) // 2]
            print(f"    stop-distance %: min={stops[0]:.3f} "
                  f"median={med:.3f} "
                  f"p90={stops[int(0.9*(len(stops)-1))]:.3f} "
                  f"max={stops[-1]:.3f}")
            reasons = {}
            for t in all_trades:
                reasons[t.reason] = reasons.get(t.reason, 0) + 1
            print(f"    exit reasons: {reasons}")


async def main() -> None:
    ap = argparse.ArgumentParser(description="NWERSIASF strategy backtest")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--interval", default="15m",
                    choices=["5m", "15m", "1h"])
    ap.add_argument("--end-days-ago", type=int, default=0,
                    help="shift window backward N days (disjoint slices)")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    per_symbol = await run(symbols, args.days, args.interval,
                           args.end_days_ago)
    print_report(per_symbol, args.interval, args.days)


if __name__ == "__main__":
    asyncio.run(main())
