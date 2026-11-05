#!/usr/bin/env python3
"""Backtest the 3CANDLE strategy at HIGHER detection timeframes (15m, 1h).

Standalone research harness — does NOT touch backend/backtest.py's scan clock,
signal_service.py, or any live path. Reuses:

  - backend/backtest_data.py  — paginated, interval-aware fetching pinned to
    one [start, end) window (same as run_backtest).
  - backend/backtest.py       — Backtester._passes_filters (the live filter
    chain), fill_at_midpoint / rr_at_fill (live LIMIT-at-midpoint semantics),
    _resolve_exit (same OHLC rules: stop before target, gap at adverse open,
    entry candle cannot earn target or update trailing), fee_in_r / the
    account-calibrated 0.11% round-trip cost model.
  - backend/indicators/three_candle.py — the PRODUCTION detector and entry
    evaluator, unchanged.

What differs from the 5m harness, deliberately:

  - Scan clock = the close of each DETECT-interval bar (15m or 1h), and the
    pattern is detected on that interval's last 3 closed bars — the same
    "last 3 bars at scan time" rule live applies on 5m.
  - Trend/regime/ATR context moves up one timeframe, mirroring how live uses
    1h context for 5m signals:
        15m detection -> 1h context   (same context live already uses)
        1h  detection -> 4h context
    Context choice rationale: the context interval should be ~4x the
    detection interval so SMA50/ADX describe a genuinely higher-order
    trend, matching the 5m->1h ratio live runs (12x is impractical: 4h is
    the next standard interval and keeps SMA200 reachable inside 45d).
  - signal_max_age_minutes stays at the live 60 — at 1h that means the fill
    LIMIT has roughly one bar of life, which is the honest mirror of live
    semantics, not a tuning knob.

Deployed-config mirror: 3CANDLE only, atr_stop_mult=1.5, min_risk_pct=0.005,
rr_min=3.0, MODERATE strength, regime filter on, neutral allowed, volume off,
BE + profit-lock trailing as in production defaults. Cells with <20 closed
trades are inconclusive.

Usage:
    python scripts/three_candle_tf_backtest.py --interval 15m --days 45
    python scripts/three_candle_tf_backtest.py --interval 1h --days 45 \
        --symbols APEUSDT,BNBUSDT,BTCUSDT,LINKUSDT,OPUSDT,SOLUSDT,UNIUSDT
"""
import argparse
import asyncio
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.backtest import (  # noqa: E402
    Backtester, BacktestResult, BacktestTrade, FEE_ROUND_TRIP,
    fee_in_r, fill_at_midpoint, rr_at_fill,
)
from backend.backtest_data import (  # noqa: E402
    INTERVAL_MS, align_window, bars_expected, fetch_klines, last_closed_before,
    window_for_days,
)
from backend.indicators.atr import calculate_atr  # noqa: E402
from backend.indicators.regime import analyze_market_regime  # noqa: E402
from backend.indicators.three_candle import (  # noqa: E402
    detect_three_candles, evaluate_three_candle_entry,
)
from backend.indicators.trend import analyze_trend_bias  # noqa: E402
from backend.services.binance_service import BinanceService  # noqa: E402

# Context interval per detection interval. See module docstring.
CONTEXT_FOR = {"15m": "1h", "1h": "4h"}
DETECT_CHOICES = ["15m", "1h"]

DEFAULT_SYMBOLS = ("APEUSDT,BNBUSDT,BTCUSDT,LINKUSDT,OPUSDT,SOLUSDT,UNIUSDT,"
                   "APTUSDT,ARBUSDT,SUIUSDT,XRPUSDT")

# Mirror of the deployed 5m config (analysis/backtest-45d-2026-11-05.md, run D2).
SETTINGS = {
    "rr_min": 3.0,
    "backtest_atr_stop_mult": 1.5,
    "min_risk_pct": 0.005,
    "trade_min_trend_strength": "MODERATE",
    "trade_neutral_markets": True,
    "volume_filter_enabled": False,
    "regime_filter_enabled": True,
    # 3CANDLE only — the other strategies are not part of this question.
    "strategy_fvg_enabled": False,
    "strategy_choch_enabled": False,
    "strategy_obfvg_enabled": False,
}


async def _fetch(binance, symbol: str, interval: str,
                 start_ms: int, end_ms: int) -> list[dict]:
    """fetch_klines with one retry on transient Binance failure."""
    for attempt in (1, 2):
        try:
            candles = await fetch_klines(
                lambda s, i, a, b: binance.get_klines_window(s, i, a, b),
                symbol, interval, start_ms, end_ms)
            return align_window({interval: candles}, start_ms, end_ms)[interval]
        except Exception:
            if attempt == 2:
                raise
            await asyncio.sleep(2.0)
    return []


def run_tf_backtest(symbol: str, detect: list[dict], ctx: list[dict],
                    interval: str, bt: Backtester) -> BacktestResult:
    """Replay 3CANDLE detection on `detect` bars with `ctx` trend context.

    `detect` and `ctx` must both be sorted, closed-bar-only candle lists over
    the same window (align_window output). The scan clock is each detect
    bar's close; detection sees only bars closed by then — no lookahead.
    """
    result = BacktestResult(symbol=symbol, candles=len(detect))
    busy_until_ms = -1
    ctx_idx = -1
    det_idx = -1
    context_cache: dict[int, tuple] = {}

    for scan in detect:
        close_time = scan.get("close_time")
        if close_time is None:
            continue
        at_ms = int(close_time) + 1
        det_idx = last_closed_before(detect, at_ms)
        ctx_idx = last_closed_before(ctx, at_ms)
        # Mirror live's minimum-history gate (60 context bars) plus the 3
        # detect bars a pattern needs.
        if det_idx < 2 or ctx_idx < 59:
            continue
        if at_ms < busy_until_ms:
            continue

        if ctx_idx not in context_cache:
            ctx_u = ctx[:ctx_idx + 1]
            context_cache[ctx_idx] = (
                calculate_atr(ctx_u, 14) or 0.0,
                analyze_trend_bias([c["close"] for c in ctx_u]),
                analyze_market_regime(
                    [c["high"] for c in ctx_u],
                    [c["low"] for c in ctx_u],
                    [c["close"] for c in ctx_u],
                ),
            )
        atr, bias, regime = context_cache[ctx_idx]

        det_u = detect[:det_idx + 1]
        for p in detect_three_candles(det_u[-3:], symbol, interval):
            e = evaluate_three_candle_entry(
                p, atr, bt.rr_min, atr_stop_mult=bt.atr_stop_mult)
            if not e["is_valid"]:
                continue
            direction = "LONG" if p.direction == "BULLISH" else "SHORT"
            zone = (p.entry_zone_min, p.entry_zone_max)
            entry = (zone[0] + zone[1]) / 2.0
            sl = e["stop_loss"]
            vol_window = det_u[-15:-5] if len(det_u) > 15 else det_u
            if not bt._passes_filters(direction, e["rr_ratio"], entry, sl,
                                      bias, vol_window, result.skipped,
                                      regime=regime):
                continue
            filled = fill_at_midpoint(
                detect, det_idx, zone[0], zone[1], bt.signal_max_age_ms,
                direction=direction,
                penetration_bps=bt.entry_fill_penetration_bps)
            if filled is None:
                result.skipped["no_fill"] = result.skipped.get("no_fill", 0) + 1
                continue
            fill_idx, fill = filled
            if rr_at_fill(sl, e["take_profit"], direction, fill,
                          bt.rr_min) < bt.rr_min:
                result.skipped["rr_at_fill"] = result.skipped.get("rr_at_fill", 0) + 1
                continue
            trade = BacktestTrade(
                symbol=symbol, strategy="3CANDLE", direction=direction,
                entry=fill, stop=sl, take_profit=e["take_profit"],
                entry_idx=fill_idx, timeframe=interval,
                signal_open_time=detect[det_idx]["open_time"])
            risk_dist = abs(fill - sl)
            if risk_dist <= 0:
                continue
            trade._risk_dist = risk_dist  # type: ignore[attr-defined]
            result.trades.append(trade)
            bt._resolve_exit(trade, detect)
            if trade.exit_idx is None:
                busy_until_ms = float("inf")
            else:
                exit_close = detect[trade.exit_idx].get("close_time")
                busy_until_ms = (float("inf") if exit_close is None
                                 else int(exit_close) + 1)
            break  # one position per symbol, same as the 5m harness

    return result


async def run_symbol(binance, symbol: str, interval: str, days: int,
                     end_days_ago: int, bt: Backtester) -> BacktestResult:
    ctx_tf = CONTEXT_FOR[interval]
    start_ms, end_ms = window_for_days(days)
    if end_days_ago:
        end_ms -= end_days_ago * 86_400_000
        start_ms -= end_days_ago * 86_400_000
    detect = await _fetch(binance, symbol, interval, start_ms, end_ms)
    ctx = await _fetch(binance, symbol, ctx_tf, start_ms, end_ms)
    for tf, candles in ((interval, detect), (ctx_tf, ctx)):
        if len(candles) < bars_expected(tf, days) * 0.9:
            raise RuntimeError(
                f"{symbol} {tf}: got {len(candles)} bars for a {days}d window, "
                f"expected ~{bars_expected(tf, days)} — pagination failed")
    return run_tf_backtest(symbol, detect, ctx, interval, bt)


def cell_stats(res: BacktestResult, days_effective: float) -> dict:
    """Per-symbol metrics incl. median stop distance and fee drag in R."""
    closed = res.closed
    n = len(closed)
    if not n:
        return {"symbol": res.symbol, "closed": 0}
    stop_pcts = []
    fee_rs = []
    for t in closed:
        stop_pcts.append(abs(t.entry - t.stop) / t.entry * 100.0)
        risk = getattr(t, "_risk_dist", abs(t.entry - t.stop))
        fee_rs.append(fee_in_r(t.entry, risk) if risk > 0 else float("inf"))
    wins = sum(1 for t in closed if t.pnl_r > 0)
    total_r = sum(t.pnl_r for t in closed)
    total_net = sum(t.net_r for t in closed)
    return {
        "symbol": res.symbol,
        "closed": n,
        "win_rate": 100.0 * wins / n,
        "gross_r": total_r,
        "net_r": total_net,
        "exp_r": total_r / n,
        "exp_net_r": total_net / n,
        "median_stop_pct": statistics.median(stop_pcts),
        "mean_fee_r": statistics.mean(f for f in fee_rs if f != float("inf")),
        "trades_per_day": n / days_effective,
        "skipped": res.skipped,
    }


def print_table(rows: list[dict], interval: str, ctx_tf: str, days: int) -> None:
    inconclusive = "  (<20 trades — INCONCLUSIVE)"
    print(f"\n=== 3CANDLE @ {interval} detection | context {ctx_tf} | "
          f"{days}d window | deployed config ===")
    header = (f"{'symbol':<10} {'closed':>6} {'win%':>6} {'grossR':>8} "
              f"{'netR':>8} {'expR':>7} {'expNetR':>8} {'medStop%':>8} "
              f"{'feeDragR':>8} {'tr/d':>6}")
    print(header)
    print("-" * len(header))
    for r in rows:
        if not r.get("closed"):
            print(f"{r['symbol']:<10} {'0':>6}   (no closed trades)")
            continue
        tag = inconclusive if r["closed"] < 20 else ""
        print(f"{r['symbol']:<10} {r['closed']:>6} {r['win_rate']:>5.1f}% "
              f"{r['gross_r']:>8.2f} {r['net_r']:>8.2f} {r['exp_r']:>7.3f} "
              f"{r['exp_net_r']:>8.3f} {r['median_stop_pct']:>7.3f}% "
              f"{r['mean_fee_r']:>8.3f} {r['trades_per_day']:>6.2f}{tag}")
        if r.get("skipped"):
            skips = ", ".join(f"{k}={v}" for k, v in sorted(r["skipped"].items()))
            print(f"{'':10} skipped: {skips}")
    closed = [r for r in rows if r.get("closed")]
    n = sum(r["closed"] for r in closed)
    if n:
        gross = sum(r["gross_r"] for r in closed)
        net = sum(r["net_r"] for r in closed)
        # pooled fee drag = gross - net per trade on average
        print("-" * len(header))
        print(f"{'TOTAL':<10} {n:>6} {'':>6} {gross:>8.2f} {net:>8.2f} "
              f"{gross / n:>7.3f} {net / n:>8.3f} "
              f"{'':>8} {gross / n - net / n:>8.3f}")
    print(f"Round-trip cost model: {FEE_ROUND_TRIP * 100:.3f}% of notional "
          "(0.11% account-calibrated). expNetR is the number to trust.")


async def main() -> None:
    ap = argparse.ArgumentParser(
        description="3CANDLE backtest at 15m/1h detection timeframes")
    ap.add_argument("--interval", required=True, choices=DETECT_CHOICES,
                    help="detection/execution interval (15m or 1h)")
    ap.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--end-days-ago", type=int, default=0,
                    help="shift window backward N days (disjoint slices)")
    ap.add_argument("--json-out", default=None,
                    help="optional path to dump per-symbol rows as JSON")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    ctx_tf = CONTEXT_FOR[args.interval]
    # Effective trading days: the 60-bar context warmup consumes the head of
    # the window (60h = 2.5d on 1h ctx, 10d on 4h ctx).
    warmup_days = 60 * INTERVAL_MS[ctx_tf] / 86_400_000
    days_eff = max(1.0, args.days - warmup_days)

    print(f"3CANDLE TF backtest: detect={args.interval} context={ctx_tf} "
          f"days={args.days} symbols={len(symbols)} "
          f"effective~{days_eff:.1f}d (60-bar context warmup excluded)")
    print(f"Settings: {SETTINGS}")

    bt = Backtester(settings=dict(SETTINGS))
    rows = []
    binance = BinanceService()
    try:
        for sym in symbols:
            try:
                res = await run_symbol(binance, sym, args.interval,
                                       args.days, args.end_days_ago, bt)
            except Exception as exc:  # keep one bad symbol from killing the run
                print(f"{sym}: FETCH/RUN FAILED — {exc}")
                rows.append({"symbol": sym, "closed": 0, "error": str(exc)})
                continue
            rows.append(cell_stats(res, days_eff))
            r = rows[-1]
            if r.get("closed"):
                print(f"{sym}: {r['closed']} closed, "
                      f"expNetR {r['exp_net_r']:+.3f}", flush=True)
            else:
                print(f"{sym}: 0 closed trades", flush=True)
    finally:
        await binance.close()

    print_table(rows, args.interval, ctx_tf, args.days)

    if args.json_out:
        import json
        Path(args.json_out).write_text(json.dumps(
            {"interval": args.interval, "context": ctx_tf,
             "days": args.days, "settings": SETTINGS, "rows": rows},
            indent=2, default=str))
        print(f"\nWrote {args.json_out}")


if __name__ == "__main__":
    asyncio.run(main())
