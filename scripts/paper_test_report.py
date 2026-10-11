#!/usr/bin/env python3
"""PHASE3_STEP7 — formatted report for a paper-trading window.

Usage:
    python scripts/paper_test_report.py --start "2026-10-11 23:00" --end "2026-10-14 23:00"
    (dates are UTC; --start/--end optional — defaults cover everything recorded)
"""
import argparse
import asyncio
import sys
import os
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func, select

from backend.models.daily_summary import DailySummary
from backend.models.equity_log import EquityLog
from backend.models.position import Position
from backend.models.signal import Signal
from backend.models.trade import Trade
from backend.database import AsyncSessionLocal


def parse_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


async def report(start: datetime | None, end: datetime | None):
    async with AsyncSessionLocal() as db:
        trades = (await db.execute(select(Trade))).scalars().all()
        trades = [t for t in trades
                  if (start is None or t.closed_at and t.closed_at.astimezone(timezone.utc) >= start)
                  and (end is None or t.closed_at and t.closed_at.astimezone(timezone.utc) <= end)]
        signals = (await db.execute(select(Signal))).scalars().all()
        signals = [s for s in signals
                   if (start is None or s.created_at.astimezone(timezone.utc) >= start)
                   and (end is None or s.created_at.astimezone(timezone.utc) <= end)]
        positions = (await db.execute(select(Position))).scalars().all()
        open_positions = [p for p in positions if p.status == "OPEN"]
        equity = (await db.execute(select(func.count(EquityLog.id)))).scalar()
        summaries = (await db.execute(select(DailySummary))).scalars().all()

    wins = [t for t in trades if float(t.pnl_r) > 0]
    losses = [t for t in trades if float(t.pnl_r) < 0]
    total_r = sum(float(t.pnl_r) for t in trades)

    print("=" * 62)
    print("FUBOT PAPER TRADING REPORT")
    window = f"{start or 'beginning'} → {end or 'now'} (UTC)"
    print(f"Window: {window}")
    print(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 62)

    print(f"\nSignals: {len(signals)} total  "
          f"({sum(1 for s in signals if s.status == 'PENDING')} pending, "
          f"{sum(1 for s in signals if s.status == 'TRIGGERED')} triggered, "
          f"{sum(1 for s in signals if s.status == 'SKIPPED')} skipped, "
          f"{sum(1 for s in signals if s.status == 'EXPIRED')} expired)")

    print(f"\nTrades: {len(trades)}  |  Wins: {len(wins)}  Losses: {len(losses)}")
    if trades:
        win_rate = 100 * len(wins) / len(trades)
        print(f"Win rate: {win_rate:.1f}%  |  Total: {total_r:+.2f}R  "
              f"(best {max(float(t.pnl_r) for t in trades):+.2f}R  "
              f"worst {min(float(t.pnl_r) for t in trades):+.2f}R)")
        for t in sorted(trades, key=lambda t: t.closed_at or t.created_at):
            print(f"  {t.closed_at:%m-%d %H:%M} {t.symbol:<9} {t.strategy_type:<8} "
                  f"{t.exit_reason:<7} {float(t.pnl_r):+.2f}R  ${float(t.pnl_usdt):+,.2f}")

    print(f"\nOpen positions: {len(open_positions)}")
    for p in open_positions:
        print(f"  {p.symbol:<9} {p.side:<5} entry {float(p.entry_price):.4f}  "
              f"SL {float(p.stop_loss):.4f}  TP {float(p.take_profit):.4f}")

    print(f"\nDaily summaries: {len(summaries)}")
    for s in sorted(summaries, key=lambda s: s.date):
        flag = " ⛔ LIMIT HIT" if s.daily_loss_limit_hit else ""
        print(f"  {s.date}  trades {s.total_trades:>3}  {float(s.total_pnl_r):+.2f}R  "
              f"${float(s.total_pnl_usdt):+,.2f}{flag}")

    print(f"\nEquity snapshots: {equity}")
    print("\nHealth checklist:")
    print(f"  {'✅' if not open_positions else '⚠️ '} no stuck open positions (>24h needs review)")
    print(f"  {'✅' if total_r > -3.0 else '⛔'} daily loss limit respected (total {total_r:+.2f}R)")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default=None, help='UTC "YYYY-MM-DD HH:MM"')
    p.add_argument("--end", default=None, help='UTC "YYYY-MM-DD HH:MM"')
    a = p.parse_args()
    asyncio.run(report(parse_utc(a.start) if a.start else None,
                       parse_utc(a.end) if a.end else None))
