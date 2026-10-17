"""The shared daily-limit rules.

`used_r`, `limit_r` and "which day is today" were each computed in two or three
places with nothing keeping them in agreement. Three concrete failures followed:

  - a `daily_loss_limit_r=9999` row clamped in the breaker but not in the
    dashboard, so the panel showed 9999R remaining while the bot had halted
  - the breaker resolved the trading day in UTC and both dashboard endpoints in
    LOCAL time, so on the UTC+2 VPS they disagreed for two hours every night
  - `exchange_sync` hardcoded `-3.0` in its latch, ignoring the configured limit

Design spec: docs/superpowers/specs/2026-10-18-daily-limit-single-source-design.md
"""
import pytest


# ---------------------------------------------------------------- limit_r_from

class TestLimitRFrom:
    def test_a_normal_negative_setting_yields_its_positive_magnitude(self):
        from backend.core.daily_reset import limit_r_from
        assert limit_r_from({"daily_loss_limit_r": -3.0}) == pytest.approx(3.0)

    def test_an_out_of_range_value_falls_back_to_the_default(self):
        """9999 is the exact value exploited before the 2026-10-17 hardening.
        It must clamp, not disable the breaker."""
        from backend.core.daily_reset import limit_r_from
        assert limit_r_from({"daily_loss_limit_r": 9999}) == pytest.approx(3.0)
        assert limit_r_from({"daily_loss_limit_r": 0}) == pytest.approx(3.0)
        assert limit_r_from({"daily_loss_limit_r": -0.0}) == pytest.approx(3.0)

    def test_the_upper_bound_is_inclusive_and_above_it_falls_back(self):
        from backend.core.daily_reset import MAX_DAILY_LOSS_LIMIT_R, limit_r_from
        assert limit_r_from({"daily_loss_limit_r": -MAX_DAILY_LOSS_LIMIT_R}) \
            == pytest.approx(MAX_DAILY_LOSS_LIMIT_R)
        assert limit_r_from({"daily_loss_limit_r": -20.1}) == pytest.approx(3.0)

    def test_a_non_numeric_value_falls_back_instead_of_raising(self):
        """Previously `abs(float(...))` RAISED here. That was contained inside
        the breaker, but the dashboard now calls the same helper -- an
        exception would surface as a 500 on /dashboard/today."""
        from backend.core.daily_reset import limit_r_from
        for junk in ("abc", None, [], {}, object()):
            assert limit_r_from({"daily_loss_limit_r": junk}) == pytest.approx(3.0), junk

    def test_a_missing_key_falls_back(self):
        from backend.core.daily_reset import limit_r_from
        assert limit_r_from({}) == pytest.approx(3.0)
        assert limit_r_from(None) == pytest.approx(3.0)

    def test_nan_and_inf_fall_back_rather_than_disabling_the_breaker(self):
        """The negated range check is what makes this safe: NaN fails every
        comparison, so it takes the fallback. Rewriting it as
        `if limit <= 0 or limit > MAX` lets NaN through, and a NaN limit makes
        every `used_r >= limit` False -- the breaker silently off."""
        from backend.core.daily_reset import limit_r_from
        for junk in (float("nan"), float("inf"), float("-inf"), "nan", "inf"):
            assert limit_r_from({"daily_loss_limit_r": junk}) == pytest.approx(3.0), junk


# ----------------------------------------------------------------- used_r_from

class TestUsedRFrom:
    def _summary(self, total_pnl_r):
        from types import SimpleNamespace
        return SimpleNamespace(total_pnl_r=total_pnl_r)

    def test_a_winning_day_consumes_nothing(self):
        """A winning day must NEVER trip the breaker. This is the single most
        important property of the whole module."""
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(4.5)) == pytest.approx(0.0)
        assert used_r_from(self._summary(0.01)) == pytest.approx(0.0)

    def test_a_losing_day_consumes_its_magnitude(self):
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(-2.9)) == pytest.approx(2.9)
        assert used_r_from(self._summary(-0.01)) == pytest.approx(0.01)

    def test_an_exactly_flat_day_consumes_nothing(self):
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(0.0)) == pytest.approx(0.0)

    def test_no_summary_yet_consumes_nothing(self):
        """The pre-first-trade-of-the-day case: there is no row at all."""
        from backend.core.daily_reset import used_r_from
        assert used_r_from(None) == pytest.approx(0.0)

    def test_a_null_pnl_does_not_crash_the_breaker(self):
        """total_pnl_r is Numeric; a fresh un-flushed row reads as None."""
        from backend.core.daily_reset import used_r_from
        assert used_r_from(self._summary(None)) == pytest.approx(0.0)


# -------------------------------------------------------------------- today_utc

class TestTodayUtc:
    def test_it_resolves_the_day_in_utc_and_returns_that_date(self, monkeypatch):
        """23:30 UTC on a UTC+2 host is 01:30 LOCAL the next day, so a
        local-time resolution returns 2026-03-16 while the breaker returns
        2026-03-15. Asserting the tz ARGUMENT is what actually pins the
        contract -- a fake that ignored tz would let a naive `datetime.now()`
        pass, and the `date.today()` path is only caught by calendar luck."""
        from datetime import datetime, timezone
        import backend.core.daily_reset as dr

        base = datetime(2026, 3, 15, 23, 30, tzinfo=timezone.utc)
        seen = {}

        class _FixedUTC(datetime):
            @classmethod
            def now(cls, tz=None):
                seen["tz"] = tz
                return base.astimezone(tz) if tz is not None else base.astimezone()

        monkeypatch.setattr(dr, "datetime", _FixedUTC)
        assert dr.today_utc() == base.date()
        assert seen["tz"] is timezone.utc, "the trading day must be resolved in UTC"

    def test_the_module_does_not_resolve_the_day_locally(self):
        """Guards the `date.today()` path directly, which the fake-clock test
        above cannot catch on its own.

        Checked as an AST CALL rather than as a substring: the prose in
        `today_utc`'s docstring must be able to NAME `date.today()` in order to
        explain why it is not used, and a text search would forbid exactly that.
        """
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2]
               / "backend/core/daily_reset.py").read_text()
        called = set()
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "today"):
                called.add(ast.unparse(node.func))
        assert "date.today" not in called, f"local day resolution: {sorted(called)}"


# --------------------------------------------------------- DailyResetService

class TestServiceDelegatesToHelpers:
    """These must fail if the delegation is reverted.

    The previous version of these tests was vacuous: `session_factory=None`
    makes `get_daily_stats` return early via `_zero_stats`, which hardcodes
    `used_r = 0.0` and never reaches `used_r_from`, so the assertion was
    `0.0 == 0.0`. And `"limit_r_from" in getsource(...)` was satisfied by a
    docstring mention rather than the code. Both defects were confirmed by
    mutation: reverting either delegation left the suite fully green.
    """

    def test_get_limit_r_delegates_to_the_shared_clamp(self):
        """Behavioural: the service's answer must be the helper's answer across
        the whole input domain, not just at one convenient value. A duplicated
        body would drift on exactly the malformed inputs this guards."""
        import inspect
        from backend.core.daily_reset import (DailyResetService, limit_r_from,
                                              MAX_DAILY_LOSS_LIMIT_R)
        # The CODE delegates, not merely a docstring mentioning the name: strip
        # the docstring before checking, or the mention satisfies the assertion.
        body = inspect.getsource(DailyResetService.get_limit_r).split('"""')[-1]
        assert "limit_r_from" in body, \
            "get_limit_r must call limit_r_from in its body, not just name it"

        for raw in (-3.0, -5.0, -MAX_DAILY_LOSS_LIMIT_R, 0, -0.0, 9999, -20.1,
                    "abc", None, float("nan"), float("inf")):
            svc = DailyResetService(settings={"daily_loss_limit_r": raw})
            assert svc.get_limit_r() == pytest.approx(limit_r_from(
                {"daily_loss_limit_r": raw})), f"raw={raw!r}"

    def test_get_daily_stats_uses_used_r_from_and_today_utc(self):
        """Source-contract: with no session factory the value path is never
        reached, so assert the code delegates rather than asserting a tautology.
        The AST walk ignores docstrings for the same reason."""
        import ast
        import inspect
        import textwrap
        from backend.core.daily_reset import DailyResetService

        # textwrap.dedent, not inspect.cleandoc: getsource returns an indented
        # `def`, and cleandoc treats the first line as already-dedented, which
        # leaves the rest of the body misaligned and unparseable.
        src = textwrap.dedent(inspect.getsource(DailyResetService.get_daily_stats))
        called = {n.func.id for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "used_r_from" in called, "get_daily_stats must CALL used_r_from"
        assert "today_utc" in called, "get_daily_stats must CALL today_utc"
        assert "datetime" not in called, \
            "get_daily_stats must not resolve the day inline — use today_utc()"

    def test_a_winning_day_reports_zero_through_the_real_summary_path(self):
        """Closes the gap the tautology left: exercise `get_daily_stats` with an
        actual summary row so `used_r_from` is reached for real. A winning day
        must report 0.0 — the property that stops a winning day tripping the
        breaker."""
        import asyncio
        from types import SimpleNamespace
        from backend.core.daily_reset import DailyResetService, used_r_from

        class _FakeSession:
            """Exposes exactly the attributes `get_daily_stats` reads off the
            `DailySummary` row it selects — no more, so adding a read to the
            method without extending this fake fails the test."""

            def __init__(self, summary):
                self._summary = summary

            async def execute(self, *a, **k):
                return SimpleNamespace(scalar_one_or_none=lambda: self._summary)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        def _row(total_pnl_r):
            return SimpleNamespace(date=None, total_trades=4, wins=2, losses=2,
                                   total_pnl_r=total_pnl_r, total_pnl_usdt=1.0,
                                   daily_loss_limit_hit=False)

        async def scenario(total_pnl_r):
            svc = DailyResetService(
                session_factory=lambda: _FakeSession(_row(total_pnl_r)),
                settings={})
            return await svc.get_daily_stats()

        for total in (4.5, 0.0, -2.9):
            stats = asyncio.run(scenario(total))
            assert stats["used_r"] == pytest.approx(used_r_from(
                SimpleNamespace(total_pnl_r=total))), f"total={total!r}"
            # The rest of the row is passed through untouched.
            assert stats["total_trades"] == 4
            assert stats["wins"] == 2 and stats["losses"] == 2
            assert stats["remaining_r"] == pytest.approx(
                stats["limit_r"] - stats["used_r"])

        # And the load-bearing property, stated directly.
        assert asyncio.run(scenario(4.5))["used_r"] == pytest.approx(0.0), \
            "a winning day must never consume R"
        assert asyncio.run(scenario(-2.9))["used_r"] == pytest.approx(2.9)

    def test_a_null_total_pnl_r_still_raises_in_the_display_field(self):
        """Pins what the delegation did and did NOT change.

        `used_r_from` tolerates a NULL `total_pnl_r` (returns 0.0) where the old
        inline `max(0.0, -float(...))` raised — but `get_daily_stats` is not
        rescued by that: the display field `float(summary.total_pnl_r)` on the
        next line raises first, and it is outside this de-duplication's scope.
        Asserted explicitly so nobody reads this class as evidence that a NULL
        summary row is now survivable."""
        import asyncio
        from types import SimpleNamespace
        from backend.core.daily_reset import DailyResetService

        class _FakeSession:
            def __init__(self, summary):
                self._summary = summary

            async def execute(self, *a, **k):
                return SimpleNamespace(scalar_one_or_none=lambda: self._summary)

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        row = SimpleNamespace(date=None, total_trades=0, wins=0, losses=0,
                              total_pnl_r=None, total_pnl_usdt=0.0,
                              daily_loss_limit_hit=False)

        async def scenario():
            svc = DailyResetService(
                session_factory=lambda: _FakeSession(row), settings={})
            return await svc.get_daily_stats()

        with pytest.raises(TypeError):
            asyncio.run(scenario())

    def test_the_module_resolves_the_day_in_exactly_one_place(self):
        """Every breaker decision reads the same trading day. Each call site
        resolving it separately is how the breaker and a display path ended up
        disagreeing about which day it was."""
        import ast
        from pathlib import Path
        src = (Path(__file__).resolve().parents[2]
               / "backend/core/daily_reset.py").read_text()
        tree = ast.parse(src)
        # Only `.date()` calls: a legitimate future `datetime.now(timezone.utc)`
        # TIMESTAMP elsewhere in the module is not a duplicate day resolution.
        inline = [n for n in ast.walk(tree)
                  if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr == "date"
                      and isinstance(n.func.value, ast.Call)
                      and isinstance(n.func.value.func, ast.Attribute)
                      and n.func.value.func.attr == "now")]
        assert len(inline) == 1, \
            f"day resolved inline elsewhere: {[ast.unparse(n) for n in inline]}"

        # ...and the one remaining site must BE today_utc, so deleting the call
        # from today_utc and adding one elsewhere cannot pass.
        fn = next(n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "today_utc")
        assert fn.lineno <= inline[0].lineno <= fn.end_lineno, \
            "the only inline day resolution must be inside today_utc itself"


# --------------------------------------------------- dashboard/breaker parity

@pytest.fixture()
def auth(client, test_user):
    from conftest import TEST_PASSWORD
    res = client.post("/api/auth/login",
                      json={"username": test_user.username,
                            "password": TEST_PASSWORD})
    assert res.status_code == 200, res.text
    client.cookies.set("fubot_token", res.json()["token"])
    return client


class TestDashboardAgreesWithBreaker:
    def test_the_dashboard_module_no_longer_recomputes_the_rules(self):
        """Source contract. `date.today()` and an inline `max(0.0, -...)` are
        exactly how the two divergences were introduced; if either reappears the
        dashboard starts disagreeing with the breaker again."""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parents[2]
               / "backend/routers/dashboard.py").read_text()
        assert "date.today()" not in src, \
            "the trading day must be resolved in UTC via today_utc(), not locally"
        assert "max(0.0, -float(" not in src, \
            "used_r must come from used_r_from(), not an inline copy"

    def test_the_dashboard_and_the_breaker_report_the_same_limit(self, auth):
        """The live divergence: a 9999 row clamps in the breaker but not in the
        dashboard, which then renders 9999R remaining while the bot has halted.

        Both the DB row and the service's settings dict are set to 9999 so the
        comparison isolates the CLAMPING bug — the settings *source* still
        differs by design and is deliberately out of scope."""
        from types import SimpleNamespace

        from backend.config import settings as cfg
        from backend.core.daily_reset import DailyResetService
        from backend.models.setting import Setting
        from conftest import run_async
        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        async def _write(limit):
            """Replace every `daily_loss_limit_r` row with `limit`, returning
            the rows that were there before so they can be put back.

            The dev database DOES carry a real -3.0 row, so this snapshots and
            restores rather than deleting — dropping it would silently move the
            breaker's allowance for every later test.

            Own NullPool engine: the shared pool is loop-bound and the `client`
            fixture already drove it on the TestClient's loop, so the pooled
            connection would arrive attached to another loop (the pattern
            established in test_strategy_breakdown)."""
            engine = create_async_engine(cfg.DATABASE_URL, poolclass=NullPool)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    existing = (await db.execute(
                        select(Setting).where(Setting.key == "daily_loss_limit_r")
                    )).scalars().all()
                    previous = [r.value for r in existing]
                    await db.execute(
                        delete(Setting).where(Setting.key == "daily_loss_limit_r"))
                    if limit is not None:
                        db.add(Setting(key="daily_loss_limit_r",
                                       value={"value": limit}))
                    await db.commit()
                    return previous
            finally:
                await engine.dispose()

        async def _restore(previous):
            engine = create_async_engine(cfg.DATABASE_URL, poolclass=NullPool)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                    await db.execute(
                        delete(Setting).where(Setting.key == "daily_loss_limit_r"))
                    for value in previous:
                        db.add(Setting(key="daily_loss_limit_r", value=value))
                    await db.commit()
            finally:
                await engine.dispose()

        previous = run_async(_write(9999))
        try:
            body = auth.get("/api/dashboard/today").json()

            # The breaker is fed the SAME daily row the dashboard read (its
            # counters are echoed in the response), so `used_r` and
            # `remaining_r` are real comparisons — a null factory reports
            # used_r 0.0 and would disagree for a reason that has nothing to do
            # with the clamp.
            row = SimpleNamespace(
                total_pnl_r=body["pnl_r"],
                total_pnl_usdt=body["pnl_usdt"],
                total_trades=body["trades_count"],
                wins=body["wins"],
                losses=body["losses"],
                daily_loss_limit_hit=False)

            class _Session:
                async def execute(self, *a, **k):
                    return SimpleNamespace(scalar_one_or_none=lambda: row)

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *exc):
                    return False

            svc = DailyResetService(session_factory=lambda: _Session(),
                                    settings={"daily_loss_limit_r": 9999})
            breaker = run_async(svc.get_daily_stats())
            assert breaker["limit_r"] == pytest.approx(3.0), \
                "the 9999 row must already be rejected by the breaker"
            assert body["daily_limit_status"]["limit_r"] \
                == pytest.approx(breaker["limit_r"]), \
                f"dashboard {body['daily_limit_status']} vs breaker {breaker}"
            assert body["daily_limit_status"]["used_r"] \
                == pytest.approx(breaker["used_r"])
            assert body["daily_limit_status"]["remaining_r"] \
                == pytest.approx(breaker["remaining_r"])
        finally:
            run_async(_restore(previous))
