#!/usr/bin/env python3
"""PHASE3_STEP7 — wipe paper-trading data for a fresh 72h run.

Clears trades, positions, orders, signals, equity_log, and daily_summary.
Users, symbols, and settings are preserved.

Usage: python scripts/clear_test_data.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import delete

from backend.models.daily_summary import DailySummary
from backend.models.equity_log import EquityLog
from backend.models.order import Order
from backend.models.position import Position
from backend.models.signal import Signal
from backend.models.trade import Trade
from backend.database import AsyncSessionLocal


async def clear_test_data():
    async with AsyncSessionLocal() as db:
        # children first (FK constraints)
        await db.execute(delete(Trade))
        await db.execute(delete(Position))
        await db.execute(delete(Order))
        await db.execute(delete(Signal))
        await db.execute(delete(EquityLog))
        await db.execute(delete(DailySummary))
        await db.commit()
    print("Paper-trading data cleared: trades, positions, orders, signals, "
          "equity_log, daily_summary.")


if __name__ == "__main__":
    asyncio.run(clear_test_data())
