"""Regression coverage for keeping paper results out of live risk guards."""
from types import SimpleNamespace

import pytest


class _Result:
    def __init__(self, value=None, rows=None):
        self.value = value
        self.rows = rows or []

    def scalar_one_or_none(self):
        return self.value

    def fetchall(self):
        return self.rows


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_daily_stats_use_mode_scoped_risk_state_not_shared_summary():
    from backend.core.daily_reset import DailyResetService, today_utc

    # An all-mode display summary could reflect -4R; the live Trade query and
    # latch must remain scoped to live only.
    from backend.models.daily_risk_state import DailyRiskState
    live_trade = SimpleNamespace(pnl_r=-1.0, pnl_usdt=-10.0)
    live_latch = DailyRiskState(date=today_utc(), mode="live", limit_hit=False)
    session = _Session([_Result(value=live_latch), _Result(rows=[(live_trade,)])])
    guard = DailyResetService(
        session_factory=lambda: session,
        settings={"daily_loss_limit_r": -3.0, "bot_mode": "live"},
    )

    stats = await guard.get_daily_stats(mode="live")

    assert stats["total_trades"] == 1
    assert stats["total_pnl_r"] == -1.0
    assert stats["used_r"] == 1.0
    assert stats["limit_hit"] is False
    assert "live" in str(session.statements[0].compile().params.values())


@pytest.mark.asyncio
async def test_daily_stats_apply_mode_reset_cutoff():
    from datetime import datetime, timedelta, timezone

    from backend.core.daily_reset import DailyResetService, today_utc
    from backend.models.daily_risk_state import DailyRiskState

    reset_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    state = DailyRiskState(date=today_utc(), mode="live", limit_hit=False,
                           reset_at=reset_at)
    session = _Session([_Result(value=state), _Result(rows=[
        (SimpleNamespace(pnl_r=-0.5, pnl_usdt=-1.0),),
    ])])
    guard = DailyResetService(
        session_factory=lambda: session,
        settings={"daily_loss_limit_r": -3.0, "bot_mode": "live"},
    )

    stats = await guard.get_daily_stats(mode="live")

    assert stats["used_r"] == 0.5
    assert stats["total_trades"] == 1
    params = session.statements[1].compile().params
    assert any(value == reset_at for value in params.values())


@pytest.mark.asyncio
async def test_manual_daily_reset_persists_active_mode_cutoff(monkeypatch):
    from types import SimpleNamespace

    from backend.models.daily_risk_state import DailyRiskState
    import backend.routers.bot as bot_router

    class Result:
        rowcount = 1

        def scalar_one_or_none(self):
            return None

    class DB:
        def __init__(self):
            self.statements = []
            self.rows = []

        async def execute(self, statement):
            self.statements.append(statement)
            return Result()

        def add(self, row):
            self.rows.append(row)

        async def commit(self):
            pass

    db = DB()
    daily = SimpleNamespace(reset_modes=[])
    daily.clear_latch = lambda mode: daily.reset_modes.append(mode)
    monkeypatch.setattr(bot_router, "_bot_engine",
                        SimpleNamespace(mode="live", daily_reset=daily))

    async def admin_user(*args):
        return SimpleNamespace(username="admin")

    monkeypatch.setattr(bot_router, "get_current_user_from_cookie", admin_user)

    await bot_router.reset_daily_limit(None, db)

    state = next(row for row in db.rows if isinstance(row, DailyRiskState))
    assert state.mode == "live"
    assert state.limit_hit is False
    assert state.reset_at is not None
    assert state.reset_at.tzinfo is not None
    assert daily.reset_modes == ["live"]


@pytest.mark.asyncio
async def test_weekly_live_guard_filters_paper_trades_out():
    from backend.core.weekly_drawdown import WeeklyDrawdownGuard

    reset_row = SimpleNamespace(value=None)
    session = _Session([_Result(value=reset_row), _Result(rows=[])])
    guard = WeeklyDrawdownGuard(
        session_factory=lambda: session,
        settings={"weekly_drawdown_limit_r": 5.0},
    )

    assert await guard.check_weekly_drawdown(mode="live") is True

    sql = str(session.statements[1].compile())
    assert "trades.mode" in sql
    assert "live" in str(session.statements[1].compile().params.values())


@pytest.mark.asyncio
async def test_weekly_reset_cutoff_isolated_by_mode():
    from datetime import datetime, timedelta, timezone
    from backend.core.weekly_drawdown import WeeklyDrawdownGuard

    live_reset = datetime.now(timezone.utc) - timedelta(minutes=2)
    paper_reset = live_reset - timedelta(minutes=1)
    # Each mode loads its own persisted cutoff and uses that cutoff in its query.
    session = _Session([
        _Result(value=SimpleNamespace(value=live_reset.isoformat())),
        _Result(rows=[]),
        _Result(value=SimpleNamespace(value=paper_reset.isoformat())),
        _Result(rows=[]),
    ])
    guard = WeeklyDrawdownGuard(
        session_factory=lambda: session,
        settings={"weekly_drawdown_limit_r": 5.0},
    )

    assert await guard.check_weekly_drawdown(mode="live") is True
    assert await guard.check_weekly_drawdown(mode="paper") is True

    assert guard._reset_from_by_mode == {"live": live_reset, "paper": paper_reset}
    assert live_reset in session.statements[1].compile().params.values()
    assert paper_reset in session.statements[3].compile().params.values()
