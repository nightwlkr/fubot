"""Weekly drawdown guard — pauses trading after excessive weekly losses.

Added 2026-10-19 as part of fubot profitability fix. Production data showed
a -13.82R disaster day (Oct 15) with no weekly circuit breaker, meaning one
bad session could erase weeks of gains.

This module tracks rolling 7-day PnL in R units and halts the bot when the
weekly loss exceeds the configured threshold (default: -5R).
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from backend.core import risk_units
from backend.core.daily_reset import week_start_utc
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
        self._weekly_mode_latches: dict[str, datetime] = {}
        self._active_mode = "live"
        # Set by reset_api endpoint to grant a grace period after manual reset
        # so the DB-based loss check doesn't immediately re-trigger on the
        # same day's historical losses. Cleared at UTC midnight.
        self._manual_reset_times: dict[str, datetime] = {}
        # Track when the user last requested a full reset. Trades closed after
        # this point are counted in the weekly calculation — everything before
        # is ignored. This lets the user "start fresh" without waiting for the
        # natural Mon-Sun boundary.
        self._reset_from_by_mode: dict[str, datetime] = {}
        self._reset_from_loaded_modes: set[str] = set()

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

    async def check_weekly_drawdown(self, mode: str = "live") -> bool:
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
        self._active_mode = mode
        prior_hit = self._weekly_mode_latches.get(mode)
        if prior_hit is not None and prior_hit.date() < today:
            self._weekly_mode_latches.pop(mode, None)
            prior_hit = None
        self._weekly_limit_hit = prior_hit is not None
        self._weekly_limit_date = prior_hit

        # Full resets are persisted so they survive a service restart. Restore
        # the cutoff before calculating the weekly window, or the first scan
        # after restart counts the historical losses the user reset past.
        if mode not in self._reset_from_loaded_modes:
            reset_from = await self._get_reset_from(mode)
            if reset_from is not None:
                self._reset_from_by_mode[mode] = reset_from
            self._reset_from_loaded_modes.add(mode)

        # A persisted reset only applies through its UTC calendar day.
        reset_from = self._reset_from_by_mode.get(mode)
        if reset_from is not None and reset_from.date() < today:
            self._reset_from_by_mode.pop(mode, None)
            reset_from = None

        # Clear manual reset grace at UTC midnight
        manual_reset_time = self._manual_reset_times.get(mode)
        if manual_reset_time is not None and manual_reset_time.date() < today:
            self._manual_reset_times.pop(mode, None)
            manual_reset_time = None

        if self._weekly_limit_hit:
            logger.warning("Weekly drawdown limit already hit, skipping trade")
            return False

        # If manually reset today, grant a grace period so the DB-based loss
        # check doesn't immediately re-trigger on the same day's historical
        # losing trades. The guard will resume normal DB checks at the next
        # UTC day boundary.
        if manual_reset_time is not None and manual_reset_time.date() == today:
            logger.info("Weekly guard within post-reset grace period — skipping DB loss check")
            return True

        # Determine the start of the weekly window.
        # If a full reset was requested, use that exact timestamp as the cutoff.
        # Otherwise use the natural Monday boundary.
        now = datetime.now(timezone.utc)
        if reset_from is not None:
            week_start = reset_from
        else:
            week_start = week_start_utc(today)

        async with self.session_factory() as session:
            result = await session.execute(
                select(Trade.pnl_r)
                .where(Trade.closed_at >= week_start)
                .where(Trade.mode == mode)
            )
            trades = result.fetchall()

        pnls = [risk_units.daily_r_contribution(r[0]) for r in trades]
        total_pnl = sum(pnls)
        total_loss = sum(abs(pnl) for pnl in pnls if pnl < 0)

        limit = self.get_weekly_limit_r()
        logger.debug("Weekly PnL: %.2fR (losses: %.2fR / %.1fR limit)", total_pnl, total_loss, limit)

        # Halt on net weekly loss exceeding the limit
        if total_pnl <= -limit:
            logger.error("WEEKLY DRAWDOWN LIMIT HIT: %.2fR net loss exceeds %.1fR limit — halting bot",
                        total_pnl, limit)
            self._weekly_limit_hit = True
            self._weekly_limit_date = now
            self._weekly_mode_latches[mode] = now

            if self.bot:
                await self.bot.stop()
            if self.alerts:
                await self.alerts.critical(
                    f"Weekly drawdown limit hit: {total_loss:.1f}R loss "
                    f"(limit: {limit:.1f}R). Bot halted."
                )
            return False

        return True

    def manual_reset(self, full_reset: bool = False, mode: str = "live") -> None:
        """Clear the weekly limit flag.

        ``full_reset=True`` records a reset point so all historical trades
        before now are ignored in future weekly PnL calculations. This lets the
        user start fresh without waiting for the Mon-Sun boundary.
        ``full_reset=False`` grants a same-day grace period (previous behavior).
        """
        now = datetime.now(timezone.utc)
        self._weekly_limit_hit = False
        self._weekly_limit_date = None
        self._weekly_mode_latches.pop(mode, None)
        self._reset_from_loaded_modes.add(mode)
        if full_reset:
            self._reset_from_by_mode[mode] = now
            self._manual_reset_times.pop(mode, None)
            logger.info("Weekly drawdown guard FULLY reset — historical trades ignored from %s", now)
            # Persist the reset point to DB so the dashboard also sees it
            try:
                asyncio.ensure_future(self._save_reset_to_db(now, mode))
            except Exception:
                pass
        else:
            self._manual_reset_times[mode] = now
            self._reset_from_by_mode.pop(mode, None)
            logger.info("Weekly drawdown guard manually reset (grace through UTC midnight)")

    async def _save_reset_to_db(self, reset_at: datetime, mode: str = "live") -> None:
        """Clear this mode's persisted weekly reset at next UTC midnight."""
        try:
            tomorrow = (reset_at + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            wait = (tomorrow - datetime.now(timezone.utc)).total_seconds()
            await asyncio.sleep(wait)
        except Exception:
            pass
        try:
            async with self.session_factory() as session:
                from sqlalchemy import update
                from backend.models.setting import Setting  # noqa: F811
                await session.execute(
                    update(Setting).where(
                        Setting.key == f"weekly_reset_from_{mode}"
                    ).values(value=None)
                )
                await session.commit()
        except Exception:
            pass

    async def _get_reset_from(self, mode: str = "live"):
        """Load a mode cutoff, preserving the legacy live reset on upgrade."""
        try:
            async with self.session_factory() as session:
                from sqlalchemy import select as _select
                from backend.models.setting import Setting  # noqa: F811
                row = (await session.execute(_select(Setting).where(
                    Setting.key == f"weekly_reset_from_{mode}"
                ))).scalar_one_or_none()
                if row is None and mode == "live":
                    row = (await session.execute(_select(Setting).where(
                        Setting.key == "weekly_reset_from"
                    ))).scalar_one_or_none()
                if row and row.value:
                    return datetime.fromisoformat(row.value)
        except Exception:
            pass
        return None

    async def get_weekly_stats(self, mode: str = "live") -> dict:
        """Get weekly PnL statistics (current UTC week, Mon-Sun)."""
        now = datetime.now(timezone.utc)
        monday = week_start_utc(now.date())
        if mode not in self._reset_from_loaded_modes:
            reset_from = await self._get_reset_from(mode)
            if reset_from is not None and reset_from.date() == now.date():
                self._reset_from_by_mode[mode] = reset_from
            self._reset_from_loaded_modes.add(mode)
        reset_from = self._reset_from_by_mode.get(mode)
        if reset_from is not None and reset_from.date() == now.date():
            monday = reset_from

        async with self.session_factory() as session:
            # Total trades this week (Mon-Sun UTC)
            result = await session.execute(
                select(Trade)
                .where(Trade.closed_at >= monday)
                .where(Trade.mode == mode)
            )
            trades = result.fetchall()

            if not trades:
                return {
                    "weekly_pnl_r": 0.0,
                    "weekly_trades": 0,
                    "weekly_wins": 0,
                    "weekly_losses": 0,
                    "weekly_win_rate": 0.0,
                    "limit_hit": mode in self._weekly_mode_latches,
                }

            trades_list = [t[0] for t in trades]
            pnls = [risk_units.daily_r_contribution(t.pnl_r) for t in trades_list]
            wins = len([p for p in pnls if p > 0])
            losses = len([p for p in pnls if p < 0])

            return {
                "weekly_pnl_r": sum(pnls),
                "weekly_trades": len(pnls),
                "weekly_wins": wins,
                "weekly_losses": losses,
                "weekly_win_rate": wins / len(pnls) * 100 if pnls else 0,
                "limit_hit": mode in self._weekly_mode_latches,
            }
