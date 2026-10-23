"""PHASE6_STEP2 — validate the -3R daily loss entry breaker.

Per FUBOT_STEPS PHASE6_STEP2 (Option B — direct DB manipulation):
  - three -1R losses → check_and_enforce_limit() returns False, scan loop stays alive
  - warning alert at ≥ 80% of the limit
  - wins must NOT count toward the limit (a winning day keeps trading)
  - midnight UTC reset clears the latch
"""
import asyncio
from datetime import datetime, timezone

import pytest

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.core.daily_reset import DailyResetService
from backend.models.daily_summary import DailySummary
from backend.models.daily_risk_state import DailyRiskState
from backend.models.trade import Trade
from backend.services.alert_service import AlertService

TODAY = datetime.now(timezone.utc).date()


class RecordingBot:
    """Stand-in for BotEngine.stop() — records the call."""

    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


async def _build():
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # clean slate for today's summary
    async with Session() as db:
        await db.execute(delete(DailySummary).where(DailySummary.date == TODAY))
        await db.execute(delete(DailyRiskState).where(DailyRiskState.date == TODAY))
        await db.execute(delete(Trade).where(Trade.symbol == "DRTEST"))
        await db.commit()

    alerts = AlertService()
    fired = []

    async def fake_signal(message, level="INFO", symbol=None):
        fired.append((level, message))

    async def fake_broadcast(payload):
        pass

    alerts.signal = fake_signal
    alerts._broadcast = fake_broadcast

    bot = RecordingBot()
    svc = DailyResetService(session_factory=Session,
                            settings={"daily_loss_limit_r": -3.0},
                            bot_engine=bot, alert_service=alerts)
    return engine, Session, svc, fired, bot


async def _cleanup(engine):
    async with engine.begin() as conn:
        await conn.execute(DailySummary.__table__.delete().where(DailySummary.date == TODAY))
        await conn.execute(DailyRiskState.__table__.delete().where(DailyRiskState.date == TODAY))
        await conn.execute(Trade.__table__.delete().where(Trade.symbol == "DRTEST"))
    await engine.dispose()


async def _record_trade(Session, svc, pnl_r, pnl_usdt):
    """Record a real mode-tagged close before notifying the display aggregate."""
    async with Session() as db:
        db.add(Trade(
            symbol="DRTEST", side="LONG", entry_price=100, exit_price=100,
            quantity=1, pnl_usdt=pnl_usdt, pnl_r=pnl_r,
            exit_reason="TEST", strategy_type="TEST", mode="live",
            closed_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    await svc.update_daily_summary(pnl_r, pnl_usdt)


def test_daily_loss_limit_blocks_entries_until_next_utc_day(monkeypatch):
    async def scenario():
        engine, Session, svc, fired, bot = await _build()
        try:
            # 1R, 2R, 2.9R — still allowed (used < limit)
            for r in (-1.0, -1.0, -0.9):
                await _record_trade(Session, svc, r, -50 * abs(r))
                assert await svc.check_and_enforce_limit() is True

            stats = await svc.get_daily_stats()
            assert stats["used_r"] == 2.9
            assert stats["remaining_r"] == pytest.approx(0.1)
            assert bot.stopped is False

            # third full -1R loss → 3.9R ≥ 3R → block entries for today
            await _record_trade(Session, svc, -1.0, -50)
            allowed = await svc.check_and_enforce_limit()
            assert allowed is False, "-3R must block trading"
            assert bot.stopped is False, (
                "the daily breaker must block entries without killing the scan loop; "
                "the loop needs to remain alive to observe the next UTC day"
            )

            stats = await svc.get_daily_stats()
            assert stats["limit_hit"] is True
            assert stats["remaining_r"] == pytest.approx(0.0)

            # latched: enforcement keeps refusing
            assert await svc.check_and_enforce_limit() is False

            # Once UTC advances, the still-running engine can clear the latch
            # and continue with the new day's independent summary.
            import backend.core.daily_reset as dr
            from datetime import timedelta
            monkeypatch.setattr(dr, "today_utc", lambda: TODAY + timedelta(days=1))
            assert await svc.check_and_enforce_limit() is True
            assert svc._limit_hit_today is False

            # the CRITICAL alert fired
            assert any(lvl == "CRITICAL" and "DAILY LOSS LIMIT HIT" in msg
                       for lvl, msg in fired)
        finally:
            await _cleanup(engine)
    asyncio.run(scenario())


def test_warning_fires_at_80_percent_of_limit():
    async def scenario():
        engine, Session, svc, fired, bot = await _build()
        try:
            # 2.4R used = 80% of 3R → warning, but trading still allowed
            await _record_trade(Session, svc, -2.4, -120)
            assert await svc.check_and_enforce_limit() is True
            assert any(lvl == "WARNING" and "Daily limit warning" in msg
                       for lvl, msg in fired)
            assert bot.stopped is False
        finally:
            await _cleanup(engine)
    asyncio.run(scenario())


def test_persisted_daily_limit_latch_survives_service_restart():
    async def scenario():
        engine, Session, svc, fired, bot = await _build()
        try:
            # Model a breaker tripped by unrealized loss before any trade has
            # closed: the daily summary row must be created so the latch
            # survives a service reconstruction.
            positions = {"BTCUSDT": {"unrealizedPnl": -3.0, "quantity": 1.0,
                                     "initialRiskDistance": 1.0}}
            assert await svc.check_and_enforce_limit(positions) is False
            async with Session() as db:
                result = await db.execute(
                    select(DailySummary).where(DailySummary.date == TODAY)
                )
                summary = result.scalar_one()
                assert summary.daily_loss_limit_hit is True
                assert float(summary.total_pnl_r) == 0.0

            restarted = DailyResetService(
                session_factory=Session,
                settings={"daily_loss_limit_r": -3.0},
                bot_engine=RecordingBot(),
                alert_service=svc.alerts,
            )
            assert await restarted.check_and_enforce_limit() is False
            assert restarted._limit_hit_today is True
            assert restarted._limit_hit_date == TODAY
        finally:
            await _cleanup(engine)
    asyncio.run(scenario())


def test_wins_do_not_count_toward_the_loss_limit():
    async def scenario():
        engine, Session, svc, fired, bot = await _build()
        try:
            # a big WINNING day must never trip the -3R stop
            await _record_trade(Session, svc, +10.0, +1000)
            assert await svc.check_and_enforce_limit() is True
            stats = await svc.get_daily_stats()
            assert stats["used_r"] == 0.0, "wins must not count as used R"
            assert stats["remaining_r"] == 3.0
            assert bot.stopped is False
        finally:
            await _cleanup(engine)
    asyncio.run(scenario())


def test_midnight_reset_clears_the_latch():
    async def scenario():
        engine, Session, svc, fired, bot = await _build()
        try:
            await _record_trade(Session, svc, -5.0, -250)
            assert await svc.check_and_enforce_limit() is False
            assert svc._limit_hit_today is True

            # Same UTC day → latch stays set
            svc.check_and_reset()
            assert svc._limit_hit_today is True, "latch persists within the same day"

            # Simulate the UTC day boundary: the latch was tripped on an
            # earlier date, so the next check_and_reset clears it.
            import backend.core.daily_reset as dr
            from datetime import date as _date

            svc._limit_hit_date = _date(2026, 1, 1)  # earlier day
            svc.check_and_reset()
            assert svc._limit_hit_today is False, "latch clears at the UTC day boundary"
        finally:
            await _cleanup(engine)
    asyncio.run(scenario())
