"""Daily reset service — tracks cumulative daily P&L, enforces -3R hard stop.

DB access: session factory + short-lived sessions (the service is called from
both the engine's scan loop and request handlers concurrently).
"""
import logging
from datetime import datetime, date, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select

from backend.models.daily_summary import DailySummary

logger = logging.getLogger(__name__)

# The -3R daily stop is the project's hard loss boundary. These bound what a
# single settings row can do to it.
DEFAULT_DAILY_LOSS_LIMIT_R = 3.0
MAX_DAILY_LOSS_LIMIT_R = 20.0


def today_utc() -> date:
    """The trading day.

    Every breaker decision is made in UTC, so every consumer must resolve the
    day in UTC too. Resolving it LOCALLY is a real divergence, not a cosmetic
    one. On a host EAST of UTC (the VPS runs Europe/Berlin, UTC+2), local time
    runs ahead: between 22:00 and 24:00 UTC it is already the next calendar day
    there, so `date.today()` returns TOMORROW. A consumer resolving it locally
    looks up tomorrow's `daily_summary` -- a row that does not exist yet -- and
    renders an empty, fully-remaining day for two hours every night while the
    breaker is enforcing today's real, over-limit row.
    """
    return datetime.now(timezone.utc).date()


def limit_r_from(settings: Optional[dict]) -> float:
    """Daily loss limit in R (positive), rejecting anything outside `(0, MAX]`.

    SINGLE SOURCE for the limit. Shared by the breaker and every display path so
    a malformed row cannot make the panel disagree with the thing that halts
    trading. Before this was shared, the dashboard parsed the setting unclamped
    and rendered "limit 9999R / remaining 9999R" while the breaker -- correctly
    clamping to 3.0 -- had already stopped the bot for the day.

    Fail-closed: an out-of-range or unparseable row is REJECTED to the 3.0
    default, NOT clamped to the bound -- a row of 9999 must not buy a 9999R
    allowance by sitting above MAX. The range test is NEGATED on purpose
    (`not (0 < limit <= MAX)`) so NaN, which compares False against everything,
    takes the fallback; the `limit <= 0 or limit > MAX` rewrite would let NaN
    through and silently disable the breaker on real money.
    """
    raw = (settings or {}).get("daily_loss_limit_r",
                               -DEFAULT_DAILY_LOSS_LIMIT_R)
    try:
        limit = abs(float(raw))
    except (TypeError, ValueError):
        logger.error("daily_loss_limit_r=%r is not a number — falling back to "
                     "%sR", raw, DEFAULT_DAILY_LOSS_LIMIT_R)
        return DEFAULT_DAILY_LOSS_LIMIT_R
    if not (0 < limit <= MAX_DAILY_LOSS_LIMIT_R):
        logger.error("daily_loss_limit_r=%r is out of range (0, %s] — falling "
                     "back to %sR", raw, MAX_DAILY_LOSS_LIMIT_R,
                     DEFAULT_DAILY_LOSS_LIMIT_R)
        return DEFAULT_DAILY_LOSS_LIMIT_R
    return limit


def used_r_from(summary: Optional[Any]) -> float:
    """R consumed against today's limit.

    NET semantics, deliberately: only a losing day consumes R, and a winning day
    can never trip the breaker. Gross-loss counting (wins never offset) was
    proposed and declined -- at the live 53% loss rate it halts after ~3 losses
    (roughly 5-6 trades) against a `max_daily_trades` of 12, which would halve
    the rate at which the per-strategy sample accumulates. See
    docs/superpowers/specs/2026-10-18-daily-limit-single-source-design.md

    `summary` is duck-typed -- anything exposing `.total_pnl_r` (the
    `DailySummary` ORM row in production, a `SimpleNamespace` in tests). It may
    be None (no row yet -- the pre-first-trade case) or have a null
    `total_pnl_r` (a fresh un-flushed Numeric column).

    Unlike `limit_r_from` this does NOT swallow a malformed non-numeric
    `total_pnl_r`: the column is `Numeric` and only ever holds Decimal/float,
    and raising here fails CLOSED -- the trade check aborts rather than
    proceeding with an unknown used_r.
    """
    if summary is None:
        return 0.0
    return max(0.0, -float(summary.total_pnl_r or 0))


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
        # Daily-loss latch. Date-based so it self-clears at the UTC day
        # boundary regardless of when the loop last ran — the old in-memory
        # bool was never cleared (check_and_reset had no caller), so a -3R trip
        # blocked trading until a service restart.
        self._limit_hit_today = False
        self._limit_hit_date: Optional[date] = None

    def get_limit_r(self) -> float:
        """Get the daily loss limit in R (positive value).

        CLAMPED here as well as on the settings schema. This is the last line of
        defence — a bad DB row (hand-edited, restored from an old backup, or
        written before the schema had bounds) must not be able to switch the
        -3R stop off. Before this, `daily_loss_limit_r=9999` made the limit
        9999R and the hard stop never fired.

        The clamping itself lives in `limit_r_from` so the dashboard and the
        48h exchange-sync share it verbatim rather than re-deriving it.
        """
        return limit_r_from(self.settings)

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
        today = today_utc()

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
            # must never trip the -3R stop. The rule lives in `used_r_from`.
            used_r = used_r_from(summary)
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
        # Self-clear the latch at the UTC day boundary (date-based).
        self.check_and_reset()

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
        today = today_utc()
        self._limit_hit_date = today

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

        today = today_utc()
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

    def check_and_reset(self):
        """Clear the daily-loss latch at the UTC day boundary.

        Date-based (not a time window): the latch is cleared whenever the
        current UTC date differs from the date it was tripped. Called from
        check_and_enforce_limit on every trade check, so it self-clears even
        though no dedicated scheduler invokes it.
        """
        if not self._limit_hit_today:
            return
        today = today_utc()
        if self._limit_hit_date is None or today > self._limit_hit_date:
            logger.info("New UTC day — resetting daily loss limit flag.")
            self._limit_hit_today = False
            self._limit_hit_date = None
