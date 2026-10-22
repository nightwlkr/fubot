#!/usr/bin/env python3
"""Prototype strategy backtest comparison — no live changes, pure expectancy numbers.

Tests 3 candidate strategies on the same 5m candles with identical fee modeling:
  A. VWAP mean reversion (fade deviations back to VWAP)
  B. ORB (opening range breakout, 15min range, 1h session blocks)
  C. OB+FVG confluence (existing FVG filtered by Order Block proximity)

Methodology notes (kept explicit so the numbers are interpretable):
  - 5m candles are the execution series for ALL strategies; stops/targets size
    off per-index ATR(5m) via `calculate_atr_series` (LOOKAHEAD-FREE: bar i
    uses only candles[0..i]). A per-index ATR(1h) series is used for C.
  - The 1h series is fetched for EXACTLY the same wall-clock window as 5m and
    the still-forming (last) candle of each series is dropped.
  - Strategy C maps an FVG's 1h bar to a 5m index via open_time matching.
  - Every strategy enters at its own defined entry price and R is the
    entry-to-stop distance AT THAT ENTRY, so R-multiples are comparable.
  - pnl_r is GROSS of fees; net_R is net of taker fee + slippage both sides.
    The summary reports BOTH — net_R is the number to trust.
  - Trade overlap is capped at `max_open_per_symbol` (default 3, matching the
    bot's max_positions). Trades still open at the last bar are EXCLUDED from
    stats and reported separately — they are not wins or losses.
  - The 1000-bar Binance cap means --days 45 gives ~3.5 days of 5m data, not
    45. A loud warning is printed when the cap binds.

Usage:
    python scripts/strategy_compare.py --symbols BTCUSDT,ETHUSDT,SOLUSDT --days 45
    python scripts/strategy_compare.py --days 30 --rr-min 2.0
"""
import argparse
import asyncio
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.indicators.atr import calculate_atr_series
from backend.indicators.vwap import detect_vwap_reversion
from backend.indicators.orb import detect_orb
from backend.indicators.order_block import detect_order_blocks, fvg_within_order_block
from backend.indicators.fvg import detect_fvg, evaluate_fvg_entry
from backend.core.cost_model import DEFAULT_SLIPPAGE, TAKER_FEE

# Keep prototype comparisons aligned with the account-calibrated shared model.
FEE_RATE = TAKER_FEE
SLIPPAGE = DEFAULT_SLIPPAGE
ROUND_TRIP_COST = (FEE_RATE + SLIPPAGE) * 2

# Binance klines max per request
KLINCE_MAX = 1000

# Epsilon for R:R comparison — evaluate_fvg_entry recomputes rr_ratio from
# floats, so rr_ratio == rr_min only up to float error (~1e-15). Without this
# boundary setups are silently dropped.
RR_EPSILON = 1e-9

# An FVG at 1h bar_index is built from bars i-2, i-1 and i, so it only exists at
# the CLOSE of hour i. One 1h bar spans twelve 5m bars, so the close is
# fvg_idx_5m + 11 and the first tradable 5m bar is fvg_idx_5m + 12.
FVG_FORMATION_BARS = 12


def fvg_entry_idx_5m(fvg_idx_5m: int) -> int:
    """First 5m bar at which an FVG detected at 1h bar `fvg_idx_5m` is tradable."""
    return fvg_idx_5m + FVG_FORMATION_BARS


async def fetch_aligned_klines(binance, symbol: str, days: int) -> tuple[list[dict], list[dict]]:
    """Fetch 5m and 1h candles covering the SAME wall-clock window, ending at
    the same time.

    - 5m: up to 1000 bars (~3.5 days at Binance's un-paginated limit).
    - 1h: same wall-clock span, aligned to the 5m window.
    - The still-forming last bar of each series is dropped.
    """
    n_5m = min(days * 288, KLINCE_MAX)
    k5m = await binance.get_klines(symbol, "5m", n_5m)
    if not k5m:
        return [], []
    k5m = k5m[:-1]  # drop the still-forming candle

    first_5m_open = k5m[0]["open_time"]
    last_5m_open = k5m[-1]["open_time"]
    span_1h = (last_5m_open - first_5m_open) / 3600000.0 + 1
    n_1h = min(int(span_1h) + 12, KLINCE_MAX)
    n_1h = max(n_1h, 60)

    k1h = await binance.get_klines(symbol, "1h", n_1h)
    if not k1h:
        return k5m, []
    k1h = k1h[:-1]  # drop the still-forming candle

    k1h = [k for k in k1h if first_5m_open <= k["open_time"] <= last_5m_open]

    return k5m, k1h


def simulate_trade(
    direction: str,
    entry: float,
    stop: float,
    tp: float,
    candles: list[dict],
    start_idx: int,
) -> Optional[dict]:
    """Simulate one trade against 5m candles. Returns dict with exit info.

    The caller owns entry-price semantics; R is recomputed here from the
    entry-to-stop distance actually taken.

    Returns None ONLY for an invalid setup (degenerate geometry). A trade that
    is still open at the last bar returns a dict with `exit_idx=None` and
    `open_until=len(candles)` — it occupied a slot to the end of the window and
    must be counted, not silently dropped. `open_until` is the bar index at
    which the trade stops occupying an overlap slot (its exit bar, or the end of
    the window when unresolved).
    """
    if stop == tp or (direction == "LONG" and stop >= entry) or (direction == "SHORT" and stop <= entry):
        return None

    initial_risk_dist = abs(entry - stop)
    if initial_risk_dist <= 0:
        return None

    entry_adj = entry * (1 + SLIPPAGE) if direction == "LONG" else entry * (1 - SLIPPAGE)

    for i in range(start_idx + 1, len(candles)):
        c = candles[i]
        hi, lo = c["high"], c["low"]

        exit_price = None
        if direction == "LONG":
            if lo <= stop:
                exit_price = stop
            elif hi >= tp:
                exit_price = tp
        else:
            if hi >= stop:
                exit_price = stop
            elif lo <= tp:
                exit_price = tp

        if exit_price is not None:
            exit_adj = exit_price * (1 - SLIPPAGE) if direction == "LONG" else exit_price * (1 + SLIPPAGE)
            pnl_pct = ((exit_adj - entry_adj) / entry_adj
                       if direction == "LONG" else (entry_adj - exit_adj) / entry_adj)
            pnl_net_pct = pnl_pct - FEE_RATE * 2  # entry + exit taker fee
            pnl_r = pnl_pct * entry_adj / initial_risk_dist
            pnl_net_r = pnl_net_pct * entry_adj / initial_risk_dist
            return {
                "exit_reason": "SL_HIT" if exit_price == stop else "TP_HIT",
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "pnl_net_pct": pnl_net_pct,
                "pnl_r": pnl_r,
                "pnl_net_r": pnl_net_r,
                "exit_idx": i,
                "open_until": i,
            }

    # Still open when the window ends: no pnl fields, occupies a slot to the end.
    return {
        "exit_reason": "OPEN",
        "exit_price": None,
        "pnl_pct": None,
        "pnl_net_pct": None,
        "pnl_r": None,
        "pnl_net_r": None,
        "exit_idx": None,
        "open_until": len(candles),
    }


def _run_capped(
    signals: list,
    candles: list[dict],
    max_open: int,
    strategy: str,
    entry_of,
) -> tuple[list[dict], int]:
    """Walk signals in ascending index order, simulating each against the series.

    Overlap cap: `open_until` holds, per open trade, the bar index at which it
    releases its slot (its exit bar, or len(candles) when unresolved). A slot is
    occupied from the trade's entry bar until that index, so a trade blocks new
    entries for as long as it is actually open — pruning on ENTRY indices
    instead would empty the set on every signal, since signals arrive in
    ascending order and every stored entry index is then already in the past.

    It is a LIST, not a set: several unresolved trades all carry
    open_until == len(candles), and a set would collapse them into one slot.

    Returns (closed_trades, still_open_count). Unresolved trades are counted in
    the second value and excluded from the P&L stats.
    """
    closed: list[dict] = []
    open_until: list[int] = []
    open_count = 0

    for sig in signals:
        direction, entry, stop, tp, entry_idx = entry_of(sig)

        # Release slots whose trade has closed at or before this entry bar.
        open_until = [u for u in open_until if u > entry_idx]
        if len(open_until) >= max_open:
            continue

        result = simulate_trade(direction, entry, stop, tp, candles, entry_idx)
        if result is None:
            continue  # degenerate setup, not a trade

        open_until.append(result["open_until"])

        if result["exit_idx"] is None:
            open_count += 1  # still open at the window end — counted, not scored
        else:
            closed.append({**result, "strategy": strategy, "direction": direction})

    return closed, open_count


def run_vwap_backtest(k5m: list[dict], atr_5m_series: list[float], rr_min: float,
                      max_open: int = 3) -> tuple[list[dict], int]:
    """Strategy A: VWAP mean reversion. Returns (closed_trades, open_trades_count)."""
    signals = detect_vwap_reversion(
        k5m, atr_5m_series,
        deviation_mult=1.5,
        rr_min=rr_min,
        lookback=96,
        start_idx=60,
    )

    return _run_capped(
        signals, k5m, max_open, "VWAP",
        entry_of=lambda s: (s.direction, s.entry_price, s.stop_loss, s.take_profit, s.formed_at_idx),
    )


def run_orb_backtest(k5m: list[dict], atr_5m_series: list[float], rr_min: float,
                     max_open: int = 3) -> tuple[list[dict], int]:
    """Strategy B: Opening Range Breakout. Returns (closed_trades, open_trades_count)."""
    signals = detect_orb(
        k5m, atr_5m_series,
        range_length=3,
        rr_min=rr_min,
        min_range_atr=0.5,
        max_range_atr=3.0,
        session_candles=12,
        start_idx=60,
        buffer_atr=0.25,
    )

    return _run_capped(
        signals, k5m, max_open, "ORB",
        entry_of=lambda s: (s.direction, s.entry_price, s.stop_loss, s.take_profit, s.formed_at_idx),
    )


def run_obfvg_backtest(k5m: list[dict], k1h: list[dict], atr_1h_series: list[float],
                       rr_min: float, max_open: int = 3) -> tuple[list[dict], int]:
    """Strategy C: OB+FVG confluence. Returns (closed_trades, open_trades_count)."""
    if not k5m or not k1h or not atr_1h_series:
        return [], 0

    fvgs_1h = detect_fvg(k1h, "")
    obs_5m = detect_order_blocks(k5m, left_bars=2, right_bars=2, start_idx=30)

    open_to_5m_idx = {c["open_time"]: i for i, c in enumerate(k5m)}

    # Build the capped-trade plan first (FVG → 5m entry bar), then simulate.
    plan = []
    for fvg in fvgs_1h:
        atr_1h = atr_1h_series[fvg.bar_index] if fvg.bar_index < len(atr_1h_series) else 0.0
        if atr_1h <= 0:
            continue

        e = evaluate_fvg_entry(fvg, atr_1h, rr_min)
        # evaluate_fvg_entry builds TP from entry + risk*rr_min, so rr_ratio
        # == rr_min only up to float error. Gate on risk_distance > 0 plus
        # an epsilon on the R:R check.
        if not e["is_valid"] and e["rr_ratio"] < rr_min - RR_EPSILON:
            continue
        if e["rr_ratio"] <= 0:
            continue

        fvg_open_time = k1h[fvg.bar_index]["open_time"]
        fvg_idx_5m = open_to_5m_idx.get(fvg_open_time)
        if fvg_idx_5m is None or fvg_idx_5m + 1 >= len(k5m):
            continue

        if not fvg_within_order_block(
            fvg.zone_min, fvg.zone_max, fvg.direction,
            obs_5m, lookback=50, current_idx=fvg_idx_5m,
        ):
            continue

        # LOOKAHEAD GUARD: an FVG at 1h bar_index uses bars i-2, i-1 and i, so
        # the zone only EXISTS at the close of hour i — which is 5m index
        # fvg_idx_5m + 11. The confluence check above is correct at formation,
        # but the trade must be entered from the first 5m bar AFTER that close.
        # Entering at fvg_idx_5m + 1 (the start of the still-forming hour) let
        # the exit scan and the stop/TP geometry use a gap that had not formed
        # yet, inflating C's expectancy from +0.315R to +0.402R.
        entry_idx_5m = fvg_entry_idx_5m(fvg_idx_5m)
        if entry_idx_5m + 1 >= len(k5m):
            continue

        entry_price = e["entry_price"]
        stop = e["stop_loss"]
        tp = e["take_profit"]
        if (fvg.direction == "BULLISH" and stop >= entry_price) or \
           (fvg.direction == "BEARISH" and stop <= entry_price):
            continue

        plan.append((fvg.direction, entry_price, stop, tp, entry_idx_5m))

    # FVGs are emitted in ascending bar_index order and the 5m map is monotonic,
    # so the plan is already in ascending entry order; sort defensively anyway
    # since _run_capped's cap relies on that ordering.
    plan.sort(key=lambda p: p[4])

    return _run_capped(
        plan, k5m, max_open, "OB+FVG",
        entry_of=lambda p: (
            "LONG" if p[0] == "BULLISH" else "SHORT",
            p[1], p[2], p[3], p[4],
        ),
    )


def print_strategy_results(strategy_name: str, trades: list[dict], open_count: int = 0):
    """Print summary stats for one strategy."""
    if not trades:
        print(f"  {strategy_name}: 0 closed trades" + (f" ({open_count} open, excluded)" if open_count else ""))
        return

    n = len(trades)
    wins = sum(1 for t in trades if t["pnl_r"] > 0)
    losses = sum(1 for t in trades if t["pnl_r"] < 0)
    total_r = sum(t["pnl_r"] for t in trades)
    total_net_r = sum(t["pnl_net_r"] for t in trades)
    total_net_pct_sum = sum(t["pnl_net_pct"] for t in trades) * 100
    win_rate = wins / n * 100 if n > 0 else 0
    expectancy_r = total_r / n if n > 0 else 0
    expectancy_net_r = total_net_r / n if n > 0 else 0

    tp_hits = sum(1 for t in trades if t["exit_reason"] == "TP_HIT")
    sl_hits = sum(1 for t in trades if t["exit_reason"] == "SL_HIT")

    avg_win_r = (sum(t["pnl_r"] for t in trades if t["pnl_r"] > 0) / wins) if wins > 0 else 0
    avg_loss_r = (sum(t["pnl_r"] for t in trades if t["pnl_r"] < 0) / losses) if losses > 0 else 0

    print(f"  {strategy_name}:")
    tail = f"  (+{open_count} open, excluded)" if open_count else ""
    print(f"    Trades: {n}{tail}  (TP hits: {tp_hits}, SL hits: {sl_hits})")
    print(f"    Win rate: {win_rate:.1f}%  ({wins}W / {losses}L)")
    print(f"    Gross R: {total_r:+.2f}  |  Net R: {total_net_r:+.2f}")
    print(f"    Sum of per-trade net %: {total_net_pct_sum:+.2f}%  (not compounded)")
    print(f"    Expectancy: {expectancy_r:+.3f}R gross | {expectancy_net_r:+.3f}R net")
    print(f"    Avg win: {avg_win_r:+.2f}R  |  Avg loss: {avg_loss_r:+.2f}R")
    print(f"    Fee drag: {ROUND_TRIP_COST*100:.2f}% per trade")


async def main() -> None:
    ap = argparse.ArgumentParser(description="Compare 3 prototype strategies")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT,ADAUSDT,DOGEUSDT")
    ap.add_argument("--days", type=int, default=45, help="days of history (5m capped at 1000 bars = ~3.5 days)")
    ap.add_argument("--rr-min", type=float, default=2.0)
    ap.add_argument("--max-open", type=int, default=3, help="max concurrent trades per symbol (matches bot max_positions)")
    args = ap.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]

    n_5m_requested = min(args.days * 288, KLINCE_MAX)
    actual_days = n_5m_requested / 288.0

    print(f"\n{'='*70}")
    print(f"Prototype Strategy Backtest Comparison")
    print(f"{'='*70}")
    print(f"Symbols: {', '.join(symbols)}")
    print(f"Period: {n_5m_requested} 5m bars (~{actual_days:.1f}d)")
    if args.days * 288 > KLINCE_MAX:
        print(f"  ⚠️  WARNING: --days {args.days} requested {args.days*288} bars but Binance caps at {KLINCE_MAX}.")
        print(f"     Actual window is ~{actual_days:.1f} days, NOT {args.days}.")
    print(f"Fee model: {FEE_RATE*100:.3f}% taker x 2 + {SLIPPAGE*100:.3f}% slippage x 2 = {ROUND_TRIP_COST*100:.3f}% round-trip")
    print(f"R:R target: {args.rr_min}:1  |  Max open/symbol: {args.max_open}")
    print(f"ATR: per-index series (lookahead-free)")
    print()

    from backend.services.binance_service import BinanceService
    binance = BinanceService()

    agg = {s: {"trades": 0, "wins": 0, "total_r": 0.0, "total_net_r": 0.0,
               "pos_symbols": 0, "neg_symbols": 0, "symbols": 0, "open": 0}
           for s in ("VWAP", "ORB", "OB+FVG")}

    try:
        for sym in symbols:
            print(f"\n{'-'*70}")
            print(f"  {sym}")
            print(f"{'-'*70}")

            k5m, k1h = await fetch_aligned_klines(binance, sym, args.days)
            if not k5m or not k1h:
                print(f"  Failed to fetch aligned candles for {sym}")
                continue

            # Per-index ATR series (LOOKAHEAD-FREE)
            atr_5m_series = calculate_atr_series(k5m, 14)
            atr_1h_series = calculate_atr_series(k1h, 14)

            span_days = (k5m[-1]["open_time"] - k5m[0]["open_time"]) / 86400000.0
            atr_5m_last = atr_5m_series[-1] if atr_5m_series else 0.0
            atr_1h_last = atr_1h_series[-1] if atr_1h_series else 0.0
            print(f"  5m bars: {len(k5m)}  |  1h bars: {len(k1h)}  |  span: {span_days:.1f}d")
            print(f"  ATR(5m) last: {atr_5m_last:.6f}  |  ATR(1h) last: {atr_1h_last:.6f}")
            print()

            trades_a, open_a = run_vwap_backtest(k5m, atr_5m_series, args.rr_min, args.max_open)
            trades_b, open_b = run_orb_backtest(k5m, atr_5m_series, args.rr_min, args.max_open)
            trades_c, open_c = run_obfvg_backtest(k5m, k1h, atr_1h_series, args.rr_min, args.max_open)

            print_strategy_results("A. VWAP Mean Reversion", trades_a, open_a)
            print()
            print_strategy_results("B. ORB (15min range)", trades_b, open_b)
            print()
            print_strategy_results("C. OB+FVG Confluence", trades_c, open_c)

            for name, trades, open_count in (("VWAP", trades_a, open_a),
                                              ("ORB", trades_b, open_b),
                                              ("OB+FVG", trades_c, open_c)):
                if not trades:
                    continue
                agg[name]["trades"] += len(trades)
                agg[name]["wins"] += sum(1 for t in trades if t["pnl_r"] > 0)
                agg[name]["total_r"] += sum(t["pnl_r"] for t in trades)
                agg[name]["total_net_r"] += sum(t["pnl_net_r"] for t in trades)
                agg[name]["symbols"] += 1
                agg[name]["open"] += open_count
                net_exp = sum(t["pnl_net_r"] for t in trades) / len(trades)
                if net_exp > 0:
                    agg[name]["pos_symbols"] += 1
                else:
                    agg[name]["neg_symbols"] += 1

    finally:
        await binance.close()

    print(f"\n{'='*70}")
    print("AGGREGATE (net expectancy = the decision metric)")
    print(f"{'='*70}")
    hdr = f"{'Strategy':<10} {'Syms':>5} {'Trades':>7} {'Win%':>6} {'NetR':>9} {'NetExp':>8} {'+Syms':>6} {'-Syms':>6} {'Open':>5}"
    print(hdr)
    print("-" * len(hdr))
    for name, a in agg.items():
        if a["trades"] == 0:
            continue
        wr = a["wins"] / a["trades"] * 100
        net_exp = a["total_net_r"] / a["trades"]
        print(f"{name:<10} {a['symbols']:>5} {a['trades']:>7} {wr:>5.1f}% "
              f"{a['total_net_r']:>+9.2f} {net_exp:>+7.3f}R {a['pos_symbols']:>6} {a['neg_symbols']:>6} {a['open']:>5}")
    print("\nExpectancy (net) > 0 = positive edge after fees.\n")


if __name__ == "__main__":
    asyncio.run(main())
