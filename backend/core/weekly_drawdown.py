"""Weekly drawdown guard — pauses trading after excessive weekly losses.

Added 2026-10-19 as part of fubot profitability fix. Production data showed
a -13.82R disaster day (Oct 15) with no weekly circuit breaker, meaning one
bad session could erase weeks of gains.

This module tracks rolling 7-day PnL in R units and halts the bot when the
weekly loss exceeds the configured threshold (default: -5R).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.models.trade import Trade

logger = logging.getLogger(__name__)

DEFAULT_WEEKLY_DRAWDOWN_LIMIT_R = 5.0
MAX_WEEKLY_DRAWDOWN_LIMIT_R = 20.0


class WeeklyDrawdownGuard:
    """Monitors weekly PnL and can halt the bot if losses exceed threshold."""

    def __init__(
        self,
        session_factory: Callable,
        settings: Optional[dict] = None,
        bot_engine=None,
        alert_service=None,
    ):
        self.session_factory = session_factory
        self.settings = settings or {}
        self.bot = bot_engine
        self.alerts = alert_service
        self._weekly_limit_hit = False
        self._weekly_limit_date: Optional[datetime] = None
        # Set by reset_api endpoint to grant a grace period after manual reset
        # so the DB-based loss check doesn't immediately re-trigger on the
        # same day's historical losses. Cleared at UTC midnight.
        self._manual_reset_time: Optional[datetime] = None
        # Track when the user last requested a full reset. Trades closed after
        # this point are counted in the weekly calculation — everything before
        # is ignored. This lets the user "start fresh" without waiting for the
        # natural Mon-Sun boundary.
        self._reset_from: Optional[datetime] = None

    def get_weekly_limit_r(self) -> float:
        """Get the weekly drawdown limit in R (positive value)."""
        raw = self.settings.get("weekly_drawdown_limit_r", DEFAULT_WEEKLY_DRAWDOWN_LIMIT_R)
        try:
            limit = abs(float(raw))
        except (TypeError, ValueError):
            logger.error("weekly_drawdown_limit_r=%r is not a number — falling back to %sR",
                        raw, DEFAULT_WEEKLY_DRAWDOWN_LIMIT_R)
            return DEFAULT_WEEKLY_DRAWDOWN_LIMIT_R
        if not (0 < limit <= MAX_WEEKLY_DRAWDOWN_LIMIT_R):
            logger.error("weekly_drawdown_limit_r=%r out of range — falling back to %sR",
                        raw, DEFAULT_WEEKLY_DRAWDOWN_LIMIT_R)
            return DEFAULT_WEEKLY_DRAWDOWN_LIMIT_R
        return limit

    async def check_weekly_drawdown(self) -> bool:
        """Check if weekly drawdown exceeds limit.

        Returns True if trading should CONTINUE (within limits),
        False if trading should HALT (limit exceeded).

        NOTE: Uses UTC day-based counting (resets at midnight) rather than
        rolling 7-day window. This allows manual reset at the start of each
        trading week without losing historical data.
        """
        # Check if we already hit the limit this week
        now = datetime.now(timezone.utc)
        today = now.date()

        # Clear manual reset grace at UTC midnight
        if self._manual_reset_time is not None:
            if self._manual_reset_time.date() < today:
                self._manual_reset_time = None

        # Reset if it's a new day (simple weekly: Mon-Sun based on UTC)
        if self._weekly_limit_date and self._weekly_limit_date.date() < today:
            self._weekly_limit_hit = False
            self._weekly_limit_date = None
            self._manual_reset_time = None
            logger.info("New trading day — resetting weekly drawdown counter")

        if self._weekly_limit_hit:
            logger.warning("Weekly drawdown limit already hit, skipping trade")
            return False

        # If manually reset today, grant a grace period so the DB-based loss
        # check doesn't immediately re-trigger on the same day's historical
        # losing trades. The guard will resume normal DB checks at the next
        # UTC day boundary.
        if self._manual_reset_time is not None and self._manual_reset_time.date() == today:
            logger.info("Weekly guard within post-reset grace period — skipping DB loss check")
            return True

        # Determine the start of the weekly window.
        # If a full reset was requested, use that timestamp as the cutoff.
        # Otherwise use the natural Monday boundary.
        now = datetime.now(timezone.utc)
        if self._reset_from is not None:
            week_start = self._reset_from.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            monday = now - timedelta(days=now.weekday())
            week_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)

        async with self.session_factory() as session:
            result = await session.execute(
                select(Trade.pnl_r)
                .where(Trade.closed_at >= week_start)
                .where(Trade.pnl_r.isnot(None))
            )
            trades = result.fetchall()

        total_pnl = sum(float(r[0]) for r in trades if r[0] is not None)
        total_loss = sum(abs(float(r[0])) for r in trades if float(r[0]) < 0)

        limit = self.get_weekly_limit_r()
        logger.debug("Weekly PnL: %.2fR (losses: %.2fR / %.1fR limit)", total_pnl, total_loss, limit)

        # Halt on net weekly loss exceeding the limit
        if total_pnl <= -limit:
            logger.error("WEEKLY DRAWDOWN LIMIT HIT: %.2fR net loss exceeds %.1fR limit — halting bot",
                        total_pnl, limit)
            self._weekly_limit_hit = True
            self._weekly_limit_date = now

            if self.bot:
                await self.bot.stop()
            if self.alerts:
                await self.alerts.critical(
                    f"Weekly drawdown limit hit: {total_loss:.1f}R loss "
                    f"(limit: {limit:.1f}R). Bot halted."
                )
            return False

        return True

    def manual_reset(self, full_reset: bool = False) -> None:
        """Clear the weekly limit flag.

        ``full_reset=True`` records a reset point so all historical trades
        before now are ignored in future weekly PnL calculations. This lets the
        user start fresh without waiting for the Mon-Sun boundary.
        ``full_reset=False`` grants a same-day grace period (previous behavior).
        """
        now = datetime.now(timezone.utc)
        self._weekly_limit_hit = False
        self._weekly_limit_date = None
        if full_reset:
            self._reset_from = now
            self._manual_reset_time = None
            logger.info("Weekly drawdown guard FULLY reset — historical trades ignored from %s", now)
        else:
            self._manual_reset_time = now
            self._reset_from = None
            logger.info("Weekly drawdown guard manually reset (grace through UTC midnight)")

    async def get_weekly_stats(self) -> dict:
        """Get weekly PnL statistics (current UTC week, Mon-Sun)."""
        now = datetime.now(timezone.utc)
        monday = now - timedelta(days=now.weekday())
        monday = monday.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)

        async with self.session_factory() as session:
            # Total trades this week (Mon-Sun UTC)
            result = await session.execute(
                select(Trade)
                .where(Trade.closed_at >= monday)
            )
            trades = result.fetchall()

            if not trades:
                return {
                    "weekly_pnl_r": 0.0,
                    "weekly_trades": 0,
                    "weekly_wins": 0,
                    "weekly_losses": 0,
                    "weekly_win_rate": 0.0,
                    "limit_hit": self._weekly_limit_hit,
                }

            trades_list = [t[0] for t in trades]
            pnls = [float(t.pnl_r) for t in trades_list if t.pnl_r is not None]
            wins = len([p for p in pnls if p > 0])
            losses = len([p for p in pnls if p < 0])

            return {
                "weekly_pnl_r": sum(pnls),
                "weekly_trades": len(pnls),
                "weekly_wins": wins,
                "weekly_losses": losses,
                "weekly_win_rate": wins / len(pnls) * 100 if pnls else 0,
                "limit_hit": self._weekly_limit_hit,
            }
