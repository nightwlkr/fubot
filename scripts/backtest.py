#!/usr/bin/env python3
"""Backtest the FUBOT strategy over historical klines — validate before trusting a paper run.

Replays the live scan pipeline (trend/volume/R:R/ATR-stop filters, FVG on 1h,
3-candle on 5m, CHoCH on 15m) over N days of candles, simulating SL/TP exits.

Usage:
    python scripts/backtest.py --symbol BTCUSDT --days 30
    python scripts/backtest.py --symbol ETHUSDT --days 60 --neutral --strength STRONG
    python scripts/backtest.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --days 30

Exit code 0 always (report only — no live wiring, no DB writes).
"""
import argparse
import asyncio
import sys
from pathlib import Path

# repo root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.backtest import run_backtest  # noqa: E402
from backend.backtest import FEE_ROUND_TRIP  # noqa: E402


async def main() -> None:
    ap = argparse.ArgumentParser(description="Backtest FUBOT strategy")
    ap.add_argument("--symbol", default="BTCUSDT", help="single symbol")
    ap.add_argument("--symbols", default=None, help="comma-separated list (overrides --symbol)")
    ap.add_argument("--days", type=int, default=30, help="days of 1h history")
    ap.add_argument("--rr-min", type=float, default=3.0)
    ap.add_argument("--risk-percent", type=float, default=1.0)
    ap.add_argument("--balance", type=float, default=10000.0)
    ap.add_argument("--neutral", action="store_true", help="allow NEUTRAL-bias trades")
    ap.add_argument("--strength", default="MODERATE", choices=["WEAK", "MODERATE", "STRONG"])
    ap.add_argument("--no-volume", action="store_true", help="disable the volume-confirmation filter")
    ap.add_argument("--end-days-ago", type=int, default=0,
                    help="shift the window backward N days (disjoint out-of-sample slices)")
    args = ap.parse_args()

    symbols = ([s.strip() for s in args.symbols.split(",")]
               if args.symbols else [args.symbol])
    settings = {
        "rr_min": args.rr_min,
        "risk_percent": args.risk_percent,
        "backtest_balance": args.balance,
        "trade_neutral_markets": args.neutral,
        "trade_min_trend_strength": args.strength,
        "volume_filter_enabled": not args.no_volume,
    }

    print(f"Backtest: {', '.join(symbols)} | {args.days}d | "
          f"rr_min={args.rr_min} neutral={args.neutral} strength={args.strength}\n")

    header = (f"{'symbol':<10} {'sig':>4} {'closed':>6} {'win%':>6} "
              f"{'totalR':>8} {'netR':>8} {'expR':>7} {'expNetR':>8} "
              f"{'trades/d':>8}")
    print(header)
    print("-" * len(header))

    total_r = 0.0
    total_net_r = 0.0
    total_closed = 0
    for sym in symbols:
        res = await run_backtest(sym, days=args.days, settings=settings,
                                 end_days_ago=args.end_days_ago)
        s = res.summary()
        total_r += s["total_r"]
        total_net_r += s["net_r"]
        total_closed += s["closed"]
        print(f"{s['symbol']:<10} {s['signals']:>4} {s['closed']:>6} "
              f"{s['win_rate']:>5.1f}% {s['total_r']:>8.2f} "
              f"{s['net_r']:>8.2f} {s['expectancy_r']:>7.3f} "
              f"{s['expectancy_net_r']:>8.3f} {s['trades_per_day']:>8.1f}")
        if s["skipped"]:
            skips = ", ".join(f"{k}={v}" for k, v in sorted(s["skipped"].items()))
            print(f"{'':10} skipped: {skips}")

    print("-" * len(header))
    avg_exp = (total_r / total_closed) if total_closed else 0.0
    avg_exp_net = (total_net_r / total_closed) if total_closed else 0.0
    print(f"{'TOTAL':<10} {'':>4} {total_closed:>6} {'':>6} {total_r:>8.2f} "
          f"{total_net_r:>8.2f} {avg_exp:>7.3f} {avg_exp_net:>8.3f}")
    print(f"\nRound-trip cost charged: {FEE_ROUND_TRIP * 100:.3f}% of notional. "
          "totalR/expR are GROSS — trust netR/expNetR.")
    print("Expectancy > 0 = positive edge. Compare settings to find the best config.")


if __name__ == "__main__":
    asyncio.run(main())
