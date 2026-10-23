"""Regression tests for persisted weekly drawdown guard state."""
from datetime import datetime, timedelta, timezone
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
    def __init__(self, reset_row, trade_rows=None):
        self.reset_row = reset_row
        self.trade_rows = trade_rows or []
        self.calls = 0
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, statement):
        self.calls += 1
        self.statements.append(statement)
        if self.calls == 1:
            return _Result(value=self.reset_row)
        return _Result(rows=self.trade_rows)


def test_week_start_uses_monday_utc_boundary():
    from datetime import date, datetime, timezone
    from backend.core.daily_reset import week_start_utc

    assert week_start_utc(date(2026, 10, 23)) == datetime(
        2026, 10, 19, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_weekly_guard_restores_full_reset_timestamp_after_restart():
    from backend.core.weekly_drawdown import WeeklyDrawdownGuard

    # Keep the reset on today's UTC date; subtracting two hours can cross
    # midnight and correctly expire the persisted cutoff before this assertion.
    reset_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    session = _Session(SimpleNamespace(value=reset_at.isoformat()))
    guard = WeeklyDrawdownGuard(
        session_factory=lambda: session,
        settings={"weekly_drawdown_limit_r": 5.0},
    )

    assert await guard.check_weekly_drawdown() is True

    assert guard._reset_from_by_mode["live"] == reset_at
    assert session.calls == 2
    trade_query_params = session.statements[1].compile().params
    assert reset_at in trade_query_params.values()

    assert await guard.check_weekly_drawdown() is True
    assert session.calls == 3  # the reset setting is restored only once


@pytest.mark.asyncio
async def test_unknown_r_trade_counts_as_full_weekly_loss():
    from backend.core.weekly_drawdown import WeeklyDrawdownGuard

    stopped = []
    alerted = []

    class Bot:
        async def stop(self):
            stopped.append(True)

    class Alerts:
        async def critical(self, message):
            alerted.append(message)

    session = _Session(None, trade_rows=[(None,)])
    guard = WeeklyDrawdownGuard(
        session_factory=lambda: session,
        settings={"weekly_drawdown_limit_r": 1.0},
        bot_engine=Bot(),
        alert_service=Alerts(),
    )

    assert await guard.check_weekly_drawdown(mode="live") is False
    assert stopped == [True]
    assert alerted and "1.0R" in alerted[0]
    assert "pnl_r IS NOT NULL" not in str(session.statements[1].compile())


@pytest.mark.asyncio
async def test_weekly_status_counts_unknown_r_as_loss():
    from backend.core.weekly_drawdown import WeeklyDrawdownGuard

    class TradeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement):
            return _Result(rows=[(SimpleNamespace(pnl_r=None),)])

    guard = WeeklyDrawdownGuard(
        session_factory=lambda: TradeSession(),
        settings={"weekly_drawdown_limit_r": 5.0},
    )

    stats = await guard.get_weekly_stats(mode="live")

    assert stats["weekly_trades"] == 1
    assert stats["weekly_losses"] == 1
    assert stats["weekly_pnl_r"] == -1.0


class _RouteResult:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _RouteSession:
    def __init__(self, setting=None, fail_commit=False):
        self.setting = setting
        self.fail_commit = fail_commit
        self.added = []
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement):
        self.statements.append(statement)
        return _RouteResult(self.setting)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("database unavailable")
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_weekly_reset_endpoint_updates_existing_setting(monkeypatch):
    from backend.routers import bot

    setting = SimpleNamespace(key="weekly_reset_from_paper", value="old")
    db = _RouteSession(setting=setting)
    guard = SimpleNamespace(reset_calls=[])
    guard.manual_reset = lambda full_reset, mode: guard.reset_calls.append((full_reset, mode))
    monkeypatch.setattr(bot, "_bot_engine", SimpleNamespace(weekly_guard=guard, mode="paper"))
    monkeypatch.setattr(bot, "get_current_user_from_cookie", _authenticated_user)

    result = await bot.reset_weekly_drawdown_guard(_request(), db)
    second_result = await bot.reset_weekly_drawdown_guard(_request(), db)

    assert result["ok"] is True
    assert second_result["ok"] is True
    assert setting.value != "old"
    assert db.commits == 2
    assert setting.key == "weekly_reset_from_paper"
    assert guard.reset_calls == [(True, "paper"), (True, "paper")]


@pytest.mark.asyncio
async def test_weekly_reset_endpoint_does_not_reset_guard_if_persistence_fails(monkeypatch):
    from fastapi import HTTPException
    from backend.routers import bot

    db = _RouteSession(setting=SimpleNamespace(key="weekly_reset_from_paper", value="old"),
                       fail_commit=True)
    guard = SimpleNamespace(reset_calls=[])
    guard.manual_reset = lambda full_reset, mode: guard.reset_calls.append((full_reset, mode))
    monkeypatch.setattr(bot, "_bot_engine", SimpleNamespace(weekly_guard=guard, mode="paper"))
    monkeypatch.setattr(bot, "get_current_user_from_cookie", _authenticated_user)

    with pytest.raises(HTTPException) as exc:
        await bot.reset_weekly_drawdown_guard(_request(), db)

    assert exc.value.status_code == 503
    assert db.rollbacks == 1
    assert guard.reset_calls == []


def _request():
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/bot/reset-weekly-guard",
                    "headers": [], "query_string": b"", "server": ("test", 80),
                    "client": ("test", 1234), "scheme": "http"})


async def _authenticated_user(request, db):
    return SimpleNamespace(username="tester")
