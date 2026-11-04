#!/usr/bin/env python3
"""Test BOS-based trading strategy on historical data.

This script demonstrates the BOS strategy concept and validates it on
historical Binance data. The strategy enters on confirmed BOS signals,
uses structural stops at the broken level, and takes profit at 3× R:R.

Usage:
    python scripts/test_bos_strategy.py --symbol BTCUSDT --days 60 --interval 5m

Expected output:
    - Net R/trade expectancy
    - Win rate
    - Max drawdown
    - Number of trades
    - Per-day performance
"""
import argparse
import sys
from datetime import datetime, timedelta

# Add backend to path
sys.path.insert(0, ".")

from backend.indicators.bos import detect_bos
from backend.indicators.bos_strategy import detect_bos_signals, evaluate_bos_entry
from backend.indicators.pivots import find_pivot_highs_lows
from backend.indicators.atr import calculate_atr
from backend.indicators.trend import analyze_trend_bias
from backend.indicators.volume_profile import get_volume_confirmed_filter
from backend.core.cost_model import LIVE as COST_MODEL


def analyze_bos_strategy(
    symbol: str,
    interval: str,
    days: int = 60,
    rr_min: float = 4.0,
    atr_stop_mult: float = 1.5,
):
    """
    Analyze BOS strategy performance on historical data.

    Args:
        symbol: Trading pair (e.g., BTCUSDT)
        interval: Timeframe (5m, 15m, 1h)
        days: Number of days of historical data
        rr_min: Minimum R:R ratio
        atr_stop_mult: ATR multiplier for minimum stop distance

    Returns:
        Dictionary with performance metrics
    """
    print(f"Analyzing {symbol} on {interval} timeframe...")
    print(f"Data window: {days} days")
    print(f"R:R minimum: {rr_min}")
    print(f"ATR stop multiplier: {atr_stop_mult}")
    print("-" * 80)

    # Import backtest data fetching
    from backend.backtest_data import fetch_klines, window_for_days

    # Fetch historical data
    start_ms, end_ms = window_for_days(days)
    candles = fetch_klines(symbol, interval, start_ms, end_ms)

    if not candles:
        print(f"ERROR: No data fetched for {symbol} on {interval}")
        return None

    print(f"Fetched {len(candles)} candles ({days} days)")
    print(f"Price range: {candles[0]['close']:.2f} → {candles[-1]['close']:.2f}")
    print()

    # Calculate ATR
    closes = [c["close"] for c in candles]
    atr = calculate_atr(closes, period=14)

    if atr is None or atr <= 0:
        print("ERROR: ATR calculation failed")
        return None

    atr_pct = (atr / candles[0]["close"]) * 100
    print(f"ATR(14): {atr:.4f} ({atr_pct:.2f}% of price)")
    print()

    # Detect BOS signals
    signals = detect_bos_signals(
        candles,
        symbol=symbol,
        interval=interval,
        atr=atr,
        rr_min=rr_min,
        atr_stop_mult=atr_stop_mult,
    )

    print(f"Detected {len(signals)} BOS signals")
    if len(signals) > 0:
        print(f"Signal distribution:")
        bullish = sum(1 for s in signals if s.direction == "BULLISH")
        bearish = sum(1 for s in signals if s.direction == "BEARISH")
        print(f"  Bullish: {bullish}")
        print(f"  Bearish: {bearish}")
        print()

        # Show recent signals
        print("Recent signals:")
        for signal in signals[-5:]:
            direction_str = "LONG" if signal.direction == "BULLISH" else "SHORT"
            print(f"  [{signal.formed_at}] {direction_str} @ {signal.entry_price:.2f}")
            print(f"    Stop: {signal.stop_loss:.2f} | TP: {signal.take_profit:.2f}")
            print(f"    R:R: {signal.rr_ratio:.2f} | Risk: {signal.risk_distance:.4f}")
        print()

    # Simulate trading
    print("Simulating trades...")
    print("-" * 80)

    trades = []
    in_position = False
    position = None
    trades_count = 0
    wins = 0
    losses = 0
    breakevens = 0
    total_pnl_r = 0.0
    max_drawdown = 0.0
    peak_r = 0.0

    # Track signals that have been filled
    filled_signals = []

    for i, candle in enumerate(candles):
        current_price = candle["close"]
        current_high = candle["high"]
        current_low = candle["low"]

        # Check if any pending signals are filled
        for signal in filled_signals[:]:
            # Check if signal is filled
            if signal.direction == "BULLISH":
                filled = current_price >= signal.entry_price
            else:
                filled = current_price <= signal.entry_price

            if filled:
                # Enter trade
                entry = signal.entry_price
                stop = signal.stop_loss
                tp = signal.take_profit
                risk = signal.risk_distance

                in_position = True
                position = {
                    "entry": entry,
                    "stop": stop,
                    "tp": tp,
                    "risk": risk,
                    "direction": signal.direction,
                    "entry_idx": signal.index,
                    "filled_idx": i,
                }

                filled_signals.remove(signal)
                break

        # Check exits if in position
        if in_position and position is not None:
            pnl_r = 0.0
            exit_reason = None

            if position["direction"] == "BULLISH":
                # Check for stop loss
                if current_price <= position["stop"]:
                    pnl_r = -(position["risk"] / position["risk"])  # -1R
                    exit_reason = "STOP_LOSS"
                # Check for take profit
                elif current_price >= position["tp"]:
                    pnl_r = position["risk"] / position["risk"]  # +1R
                    exit_reason = "TAKE_PROFIT"
            else:  # BEARISH
                # Check for stop loss
                if current_price >= position["stop"]:
                    pnl_r = -(position["risk"] / position["risk"])  # -1R
                    exit_reason = "STOP_LOSS"
                # Check for take profit
                elif current_price <= position["tp"]:
                    pnl_r = position["risk"] / position["risk"]  # +1R
                    exit_reason = "TAKE_PROFIT"

            if exit_reason:
                trades_count += 1
                trades.append({
                    "idx": i,
                    "direction": position["direction"],
                    "entry": position["entry"],
                    "exit": current_price,
                    "pnl_r": pnl_r,
                    "exit_reason": exit_reason,
                })

                if pnl_r > 0:
                    wins += 1
                elif pnl_r < 0:
                    losses += 1
                else:
                    breakevens += 1

                total_pnl_r += pnl_r

                # Calculate drawdown
                peak_r += pnl_r
                if peak_r > max_drawdown:
                    max_drawdown = peak_r

                in_position = False
                position = None

        # Detect new signals (skip current candle)
        if i < len(candles) - 1:
            # Get next candle
            next_candle = candles[i + 1]

            # Detect BOS on next candle
            next_signals = detect_bos_signals(
                candles[:i+2],  # Include current and next candle
                symbol=symbol,
                interval=interval,
                atr=atr,
                rr_min=rr_min,
                atr_stop_mult=atr_stop_mult,
            )

            # Add new signals to filled_signals
            for signal in next_signals:
                # Only add signals that haven't been filled yet
                if not any(s.index == signal.index for s in filled_signals):
                    filled_signals.append(signal)

    # Print results
    print("\n" + "=" * 80)
    print("BOS STRATEGY RESULTS")
    print("=" * 80)
    print(f"Total trades: {trades_count}")
    print(f"  Wins: {wins}")
    print(f"  Losses: {losses}")
    print(f"  Breakevens: {breakevens}")
    print()

    if trades_count > 0:
        win_rate = wins / trades_count * 100
        avg_pnl_r = total_pnl_r / trades_count
        net_r = total_pnl_r

        print(f"Win rate: {win_rate:.1f}%")
        print(f"Average R per trade: {avg_pnl_r:.3f}")
        print(f"Net R: {net_r:.3f}")
        print(f"Max drawdown: {max_drawdown:.3f}")
        print()

        # Fee calculation
        total_round_trip_fees_r = 0.0
        for trade in trades:
            entry = trade["entry"]
            exit = trade["exit"]
            risk = trade["pnl_r"]  # ±1R
            fee_r = COST_MODEL.round_trip_in_r(entry, risk)
            total_round_trip_fees_r += fee_r

        net_r_after_fees = total_pnl_r - total_round_trip_fees_r

        print(f"Total round-trip fees: {total_round_trip_fees_r:.3f} R")
        print(f"Net R after fees: {net_r_after_fees:.3f}")
        print()

        # Sharpe ratio approximation
        if len(trades) >= 2:
            returns = [t["pnl_r"] for t in trades]
            avg_return = sum(returns) / len(returns)
            variance = sum((r - avg_return) ** 2 for r in returns) / len(returns)
            std_dev = variance ** 0.5

            if std_dev > 0:
                sharpe = avg_return / std_dev * (252 ** 0.5)  # Annualized
                print(f"Sharpe ratio (annualized): {sharpe:.2f}")
        print()

        # Per-day performance
        print("Per-day performance:")
        for day in range(days):
            day_trades = [t for t in trades if t["idx"] >= day * (24 * 60 // 5) and t["idx"] < (day + 1) * (24 * 60 // 5)]
            day_pnl_r = sum(t["pnl_r"] for t in day_trades)
            print(f"  Day {day + 1}: {len(day_trades)} trades, {day_pnl_r:.3f} R")
    else:
        print("No trades generated - strategy may be too selective")
        print("Consider:")
        print("  1. Lowering rr_min from 4.0 to 3.0")
        print("  2. Reducing atr_stop_mult from 1.5 to 1.0")
        print("  3. Using a longer timeframe (1h instead of 5m)")
        print("  4. Adding trend filters")

    return {
        "trades_count": trades_count,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "win_rate": win_rate if trades_count > 0 else 0.0,
        "avg_pnl_r": avg_pnl_r if trades_count > 0 else 0.0,
        "net_r": total_pnl_r,
        "net_r_after_fees": net_r_after_fees if trades_count > 0 else 0.0,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe if len(trades) >= 2 else 0.0,
    }


def main():
    parser = argparse.ArgumentParser(description="Test BOS-based trading strategy")
    parser.add_argument("--symbol", default="BTCUSDT", help="Trading pair (default: BTCUSDT)")
    parser.add_argument("--interval", default="5m", choices=["5m", "15m", "1h"], help="Timeframe (default: 5m)")
    parser.add_argument("--days", type=int, default=60, help="Number of days of data (default: 60)")
    parser.add_argument("--rr-min", type=float, default=4.0, help="Minimum R:R ratio (default: 4.0)")
    parser.add_argument("--atr-mult", type=float, default=1.5, help="ATR stop multiplier (default: 1.5)")

    args = parser.parse_args()

    results = analyze_bos_strategy(
        symbol=args.symbol,
        interval=args.interval,
        days=args.days,
        rr_min=args.rr_min,
        atr_stop_mult=args.atr_mult,
    )

    if results is None:
        sys.exit(1)

    # Save results to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"bos_strategy_{args.symbol}_{args.interval}_{args.days}d_{timestamp}.json"

    import json
    with open(filename, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to: {filename}")
    print()

    # Exit with error code if strategy is negative
    if results["net_r_after_fees"] < 0:
        print("WARNING: Strategy has negative net R after fees")
        sys.exit(1)
    elif results["net_r_after_fees"] > 0:
        print("SUCCESS: Strategy is profitable!")
        sys.exit(0)
    else:
        print("NEUTRAL: Strategy is break-even")
        sys.exit(0)


if __name__ == "__main__":
    main()
