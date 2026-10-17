#!/usr/bin/env python3
"""Aggregate the strategy comparison output into a clean summary table."""
import re
import sys
from collections import defaultdict

def parse_results(filename):
    """Parse the strategy_compare.py output into per-symbol, per-strategy stats."""
    results = defaultdict(lambda: defaultdict(dict))
    current_symbol = None
    current_strategy = None

    with open(filename) as f:
        lines = f.readlines()

    for i, line in enumerate(lines):
        # Symbol line
        m = re.match(r'^\s{2}([A-Z]+USDT)\s*$', line)
        if m:
            current_symbol = m.group(1)
            continue

        # Strategy header
        if 'A. VWAP Mean Reversion:' in line:
            current_strategy = 'VWAP'
            continue
        elif 'B. ORB (15min range):' in line:
            current_strategy = 'ORB'
            continue
        elif 'C. OB+FVG Confluence:' in line:
            current_strategy = 'OB+FVG'
            continue

        if not current_symbol or not current_strategy:
            continue

        # Trade stats
        m = re.match(r'\s+Trades:\s+(\d+)', line)
        if m:
            results[current_symbol][current_strategy]['trades'] = int(m.group(1))
            continue

        m = re.match(r'\s+Win rate:\s+([\d.]+)%\s+\((\d+)W\s*/\s*(\d+)L\)', line)
        if m:
            results[current_symbol][current_strategy]['win_rate'] = float(m.group(1))
            results[current_symbol][current_strategy]['wins'] = int(m.group(2))
            results[current_symbol][current_strategy]['losses'] = int(m.group(3))
            continue

        m = re.match(r'\s+Gross R:\s+([+-][\d.]+)\s+\|\s+Net R:\s+([+-][\d.]+)', line)
        if m:
            results[current_symbol][current_strategy]['gross_r'] = float(m.group(1))
            results[current_symbol][current_strategy]['net_r'] = float(m.group(2))
            continue

        m = re.match(r'\s+Expectancy:\s+([+-][\d.]+)R gross\s+\|\s+([+-][\d.]+)R net', line)
        if m:
            results[current_symbol][current_strategy]['gross_expectancy'] = float(m.group(1))
            results[current_symbol][current_strategy]['net_expectancy'] = float(m.group(2))
            continue

    return results


def aggregate(results):
    """Compute aggregate stats across all symbols for each strategy."""
    agg = defaultdict(lambda: {
        'symbols': 0, 'trades': 0, 'wins': 0, 'losses': 0,
        'gross_r': 0.0, 'net_r': 0.0, 'positive_symbols': 0, 'negative_symbols': 0,
        'positive_exp_sum': 0.0
    })

    strategies = ['VWAP', 'ORB', 'OB+FVG']
    for sym_data in results.values():
        for strat in strategies:
            if strat in sym_data and sym_data[strat].get('trades', 0) > 0:
                agg[strat]['symbols'] += 1
                agg[strat]['trades'] += sym_data[strat].get('trades', 0)
                agg[strat]['wins'] += sym_data[strat].get('wins', 0)
                agg[strat]['losses'] += sym_data[strat].get('losses', 0)
                agg[strat]['gross_r'] += sym_data[strat].get('gross_r', 0.0)
                agg[strat]['net_r'] += sym_data[strat].get('net_r', 0.0)
                # net expectancy is the decision metric (strategy_compare reports both)
                exp = sym_data[strat].get('net_expectancy', 0.0)
                if exp > 0:
                    agg[strat]['positive_symbols'] += 1
                    agg[strat]['positive_exp_sum'] += exp
                else:
                    agg[strat]['negative_symbols'] += 1

    return agg


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else '/tmp/strategy_30sym.txt'
    results = parse_results(filename)
    agg = aggregate(results)

    print("=" * 80)
    print("AGGREGATE RESULTS ACROSS ALL TESTED SYMBOLS")
    print("=" * 80)
    print()

    strategies = ['VWAP', 'ORB', 'OB+FVG']
    print(f"{'Strategy':<15} {'Syms':>5} {'Trades':>7} {'Win%':>6} {'GrossR':>9} {'NetR':>9} {'NetExp':>8} {'+Syms':>6} {'-Syms':>6}")
    print("-" * 80)

    for strat in strategies:
        a = agg[strat]
        if a['trades'] == 0:
            continue
        win_rate = (a['wins'] / a['trades'] * 100) if a['trades'] > 0 else 0
        net_exp = (a['net_r'] / a['trades']) if a['trades'] > 0 else 0
        print(f"{strat:<15} {a['symbols']:>5} {a['trades']:>7} {win_rate:>5.1f}% "
              f"{a['gross_r']:>+9.2f} {a['net_r']:>+9.2f} {net_exp:>+7.3f}R "
              f"{a['positive_symbols']:>6} {a['negative_symbols']:>6}")

    print()
    print("=" * 80)
    print("PER-SYMBOL BREAKDOWN (expectancy > 0 = positive edge)")
    print("=" * 80)
    print()
    print(f"{'Symbol':<10} {'VWAP':>10} {'ORB':>10} {'OB+FVG':>10}  {'Best':<10}")
    print("-" * 60)

    for sym in sorted(results.keys()):
        row = f"{sym:<10}"
        best = None
        best_exp = -999

        for strat in strategies:
            exp = results[sym][strat].get('net_expectancy', None)
            trades = results[sym][strat].get('trades', 0)
            if trades == 0 or exp is None:
                row += f" {'—':>10}"
            else:
                row += f" {exp:>+9.2f}R"
                if exp > best_exp:
                    best_exp = exp
                    best = strat

        if best and best_exp > 0:
            row += f"  {best:<10}"
        else:
            row += f"  {'none':<10}"

        print(row)

    print()
    print("=" * 80)
    print("RECOMMENDATION")
    print("=" * 80)
    print()

    for strat in strategies:
        a = agg[strat]
        if a['trades'] == 0:
            continue
        net_exp = (a['net_r'] / a['trades']) if a['trades'] > 0 else 0
        pos_pct = (a['positive_symbols'] / a['symbols'] * 100) if a['symbols'] > 0 else 0

        status = "✅ IMPLEMENT" if net_exp > 0 and pos_pct >= 40 else "❌ SKIP"
        print(f"{strat:<15}: Net expectancy {net_exp:+.3f}R | {pos_pct:.0f}% symbols positive | {status}")
        print()


if __name__ == '__main__':
    main()
