"""Daily reset service — tracks cumulative daily P&L, enforces -3R hard stop.

DB access: session factory + short-lived sessions (the service is called from
both the engine's scan loop and request handlers concurrently).
"""
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from sqlalchemy import select

from backend.models.daily_summary import DailySummary

logger = logging.getLogger(__name__)


class DailyResetService:
    """Tracks cumulative daily PnL in R units, enforces -3R hard stop, resets at UTC midnight."""

    def __init__(
        self,
        session_factory: Optional[Callable] = None,
        settings: Optional[dict] = None,
        bot_engine=None,  # Optional reference to bot engine for stop trigger
        alert_service=None,  # Optional alert service
    ):
        self.session_factory = session_factory
        self.settings = settings or {}
        self.bot = bot_engine
        self.alerts = alert_service
        self._limit_hit_today = False

    def get_limit_r(self) -> float:
        """Get the daily loss limit in R (positive value)."""
        return abs(self.settings.get("daily_loss_limit_r", -3.0))

    def _zero_stats(self, today) -> dict:
        limit_r = self.get_limit_r()
        return {
            "date": today.isoformat(),
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl_r": 0.0,
            "total_pnl_usdt": 0.0,
            "used_r": 0.0,
            "limit_r": limit_r,
            "remaining_r": limit_r,
            "limit_hit": False,
        }

    async def get_daily_stats(self) -> dict:
        """Returns today's trading stats."""
        today = datetime.now(timezone.utc).date()

        if self.session_factory is None:
            return self._zero_stats(today)

        async with self.session_factory() as session:
            result = await session.execute(
                select(DailySummary).where(DailySummary.date == today)
            )
            summary = result.scalar_one_or_none()

        limit_r = self.get_limit_r()

        if summary:
            # Only LOSSES count toward the daily loss limit — a winning day
            # must never trip the -3R stop (abs() would count wins as used).
            used_r = max(0.0, -float(summary.total_pnl_r))
            return {
                "date": today.isoformat(),
                "total_trades": summary.total_trades,
                "wins": summary.wins,
                "losses": summary.losses,
                "total_pnl_r": float(summary.total_pnl_r),
                "total_pnl_usdt": float(summary.total_pnl_usdt),
                "used_r": used_r,
                "limit_r": limit_r,
                "remaining_r": max(0.0, limit_r - used_r),
                "limit_hit": summary.daily_loss_limit_hit,
            }

        return self._zero_stats(today)

    async def check_and_enforce_limit(self) -> bool:
        """Called before every new trade. Returns True if trading is allowed."""
        if self._limit_hit_today:
            return False

        stats = await self.get_daily_stats()
        limit = stats["limit_r"]
        used = stats["used_r"]

        if used >= limit:
            await self._trigger_daily_stop()
            return False

        # Warning at 80% of limit (epsilon: 3.0 * 0.8 == 2.4000000000000004)
        if used >= limit * 0.8 - 1e-9 and used < limit:
            if self.alerts:
                await self.alerts.daily_limit_warning(used, limit)

        return True

    async def _trigger_daily_stop(self):
        """Emergency stop: bot stops trading for the rest of the day."""
        self._limit_hit_today = True

        today = datetime.now(timezone.utc).date()
        if self.session_factory is not None:
            async with self.session_factory() as session:
                result = await session.execute(
                    select(DailySummary).where(DailySummary.date == today)
                )
                summary = result.scalar_one_or_none()
                if summary:
                    summary.daily_loss_limit_hit = True
                    await session.commit()

        if self.bot:
            await self.bot.stop()

        if self.alerts:
            stats = await self.get_daily_stats()
            await self.alerts.daily_limit_hit(stats["used_r"], stats["limit_r"])

    async def update_daily_summary(self, pnl_r: float, pnl_usdt: float) -> None:
        """Called after each trade closes to update the daily summary."""
        if self.session_factory is None:
            return

        today = datetime.now(timezone.utc).date()
        async with self.session_factory() as session:
            result = await session.execute(
                select(DailySummary).where(DailySummary.date == today)
            )
            summary = result.scalar_one_or_none()

            if not summary:
                # NOTE: column defaults fire at INSERT, not construction —
                # a fresh object's counters are None until flushed, so pass
                # explicit initial values (mirrors the migration's server defaults)
                summary = DailySummary(date=today, total_trades=0, wins=0, losses=0,
                                       breakeven=0, total_pnl_r=0.0, total_pnl_usdt=0.0,
                                       worst_trade_r=0.0, best_trade_r=0.0,
                                       daily_loss_limit_hit=False)
                session.add(summary)

            # Numeric columns come back as Decimal — coerce to float before math
            summary.total_trades = int(summary.total_trades or 0) + 1
            summary.total_pnl_r = float(summary.total_pnl_r or 0) + pnl_r
            summary.total_pnl_usdt = float(summary.total_pnl_usdt or 0) + pnl_usdt

            if pnl_r > 0:
                summary.wins = int(summary.wins or 0) + 1
                if pnl_r > float(summary.best_trade_r or 0):
                    summary.best_trade_r = pnl_r
            elif pnl_r < 0:
                summary.losses = int(summary.losses or 0) + 1
                if pnl_r < float(summary.worst_trade_r or 0):
                    summary.worst_trade_r = pnl_r
            else:
                summary.breakeven = int(summary.breakeven or 0) + 1

            await session.commit()

    async def check_and_reset(self):
        """Called at UTC midnight to reset the daily tracking."""
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute < 5:
            if self._limit_hit_today:
                logger.info("New UTC day — resetting daily loss limit flag.")
                self._limit_hit_today = False
