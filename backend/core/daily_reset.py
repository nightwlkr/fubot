"""Daily reset service — tracks cumulative daily P&L, enforces -3R hard stop."""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.daily_summary import DailySummary


class DailyResetService:
    """Tracks cumulative daily P&L in R units, enforces -3R hard stop, resets at UTC midnight."""

    def __init__(
        self,
        db: AsyncSession,
        settings: dict,
        bot_engine=None,  # Optional reference to bot engine for stop trigger
        alert_service=None,  # Optional alert service
    ):
        self.db = db
        self.settings = settings
        self.bot = bot_engine
        self.alerts = alert_service
        self._limit_hit_today = False

    def get_limit_r(self) -> float:
        """Get the daily loss limit in R (positive value)."""
        return abs(self.settings.get("daily_loss_limit_r", -3.0))

    async def get_daily_stats(self) -> dict:
        """Returns today's trading stats."""
        today = datetime.now(timezone.utc).date()

        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()

        limit_r = self.get_limit_r()

        if summary:
            used_r = abs(float(summary.total_pnl_r))
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

        # Warning at 80% of limit
        if used >= limit * 0.8 and used < limit:
            if self.alerts:
                await self.alerts.daily_limit_warning(used, limit)

        return True

    async def _trigger_daily_stop(self):
        """Emergency stop: bot stops trading for the rest of the day."""
        self._limit_hit_today = True

        today = datetime.now(timezone.utc).date()
        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()
        if summary:
            summary.daily_loss_limit_hit = True
            await self.db.commit()

        if self.bot:
            await self.bot.stop()

        if self.alerts:
            stats = await self.get_daily_stats()
            await self.alerts.daily_limit_hit(stats["used_r"], stats["limit_r"])

    async def update_daily_summary(self, pnl_r: float, pnl_usdt: float) -> None:
        """Called after each trade closes to update the daily summary."""
        today = datetime.now(timezone.utc).date()

        result = await self.db.execute(
            select(DailySummary).where(DailySummary.date == today)
        )
        summary = result.scalar_one_or_none()

        if not summary:
            summary = DailySummary(date=today)
            self.db.add(summary)

        summary.total_trades += 1
        summary.total_pnl_r += pnl_r
        summary.total_pnl_usdt += pnl_usdt

        if pnl_r > 0:
            summary.wins += 1
            if pnl_r > summary.best_trade_r:
                summary.best_trade_r = pnl_r
        elif pnl_r < 0:
            summary.losses += 1
            if pnl_r < summary.worst_trade_r:
                summary.worst_trade_r = pnl_r
        else:
            summary.breakeven += 1

        await self.db.commit()

    async def check_and_reset(self):
        """Called at UTC midnight to reset the daily tracking."""
        now = datetime.now(timezone.utc)
        if now.hour == 0 and now.minute < 5:
            if self._limit_hit_today:
                logger.info("New UTC day — resetting daily loss limit flag.")
                self._limit_hit_today = False
