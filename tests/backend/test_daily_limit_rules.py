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

# tests/backend/<this file> -> parents[2] is the repo root. Resolved from
# __file__, not the CWD, so the source-contract tests below pass however pytest
# was invoked.
REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]


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
        src = (REPO_ROOT / "backend/core/daily_reset.py").read_text()
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
        src = (REPO_ROOT / "backend/core/daily_reset.py").read_text()
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

def _own_engine():
    """A private NullPool engine, per phase.

    The shared `AsyncSessionLocal` pool is loop-bound and the `client` fixture
    has already driven it on the TestClient's loop, so touching it from
    `run_async`'s fresh loop raises "Future attached to a different loop". A
    NullPool engine opens its own connection per use and is never shared across
    loops — the pattern established in test_strategy_breakdown."""
    from backend.config import settings as cfg
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    return create_async_engine(cfg.DATABASE_URL, poolclass=NullPool)


def set_limit_rows(value):
    """Replace every `daily_loss_limit_r` row with a bare scalar `value`.

    `None` deletes every such row. Returns the prior row values so they can be
    restored — the dev database carries a real -3.0 row, so snapshotting is not
    optional: deleting it would silently move the breaker's allowance for every
    later test.

    The written value is a BARE SCALAR because that is what
    `PUT /settings/trading` actually writes (`Setting(key=key, value=value)`),
    so this is the shape production has.
    """
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from backend.models.setting import Setting

    async def _run():
        engine = _own_engine()
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                existing = (await db.execute(
                    select(Setting).where(Setting.key == "daily_loss_limit_r")
                )).scalars().all()
                previous = [r.value for r in existing]
                await db.execute(
                    delete(Setting).where(Setting.key == "daily_loss_limit_r"))
                if value is not None:
                    db.add(Setting(key="daily_loss_limit_r", value=value))
                await db.commit()
                return previous
        finally:
            await engine.dispose()

    from conftest import run_async
    return run_async(_run())


def restore_limit_rows(previous):
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from backend.models.setting import Setting

    async def _run():
        engine = _own_engine()
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await db.execute(
                    delete(Setting).where(Setting.key == "daily_loss_limit_r"))
                for value in previous:
                    db.add(Setting(key="daily_loss_limit_r", value=value))
                await db.commit()
        finally:
            await engine.dispose()

    from conftest import run_async
    run_async(_run())


def seed_daily_rows(rows):
    """Insert the given DailySummary rows, replacing any row for the same date."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker

    async def _run():
        engine = _own_engine()
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                for row in rows:
                    await db.execute(
                        delete(type(row)).where(type(row).date == row.date))
                for row in rows:
                    db.add(row)
                await db.commit()
        finally:
            await engine.dispose()

    from conftest import run_async
    run_async(_run())


def purge_daily_rows(days):
    """Delete every DailySummary row for the given dates. ALWAYS call this in a
    `finally` — daily_summary is the breaker's own input, so a leaked row here
    changes the live allowance (tests/AGENTS.md: no test may leak a row)."""
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from backend.models.daily_summary import DailySummary

    async def _run():
        engine = _own_engine()
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                await db.execute(delete(DailySummary).where(DailySummary.date.in_(list(days))))
                await db.commit()
        finally:
            await engine.dispose()

    from conftest import run_async
    run_async(_run())


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
        src = (REPO_ROOT / "backend/routers/dashboard.py").read_text()
        assert "date.today()" not in src, \
            "the trading day must be resolved in UTC via today_utc(), not locally"
        assert "max(0.0, -float(" not in src, \
            "used_r must come from used_r_from(), not an inline copy"

    def test_the_dashboard_resolves_no_trading_day_inline(self):
        """AST, not substring: `date.today()` was only the ORIGINAL mistake —
        `datetime.now().date()` is the naive-tz equivalent and passes a text
        ban. Targeting the `.now(...).date()` shape catches both. dashboard.py
        legitimately calls datetime.now(timezone.utc) for an equity-curve cutoff,
        which is a timestamp and must NOT trip this."""
        import ast
        src = (REPO_ROOT / "backend/routers/dashboard.py").read_text()
        inline = [ast.unparse(n) for n in ast.walk(ast.parse(src))
                  if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                      and n.func.attr == "date"
                      and isinstance(n.func.value, ast.Call)
                      and n.func.value.func.attr == "now")]
        assert not inline, f"trading day resolved inline: {inline}"

    def test_the_dashboard_and_the_breaker_report_the_same_limit(self, auth):
        """The live divergence: a 9999 row clamps in the breaker but not in the
        dashboard, which then renders 9999R remaining while the bot has halted.

        The DB row is written as a BARE SCALAR 9999 — the shape
        `PUT /settings/trading` actually produces (`Setting(key=key,
        value=value)`). The previous version of this test seeded
        `{"value": 9999}`, a shape production never writes, so it proved the
        clamp worked on a row that could not occur.

        Both the DB row and the service's settings dict are set to 9999 so the
        comparison isolates the CLAMPING bug — the settings *source* still
        differs by design and is deliberately out of scope."""
        from types import SimpleNamespace

        from backend.core.daily_reset import DailyResetService
        from conftest import run_async

        previous = set_limit_rows(9999)
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
            restore_limit_rows(previous)

    def test_a_dict_shaped_setting_row_agrees_with_the_breaker(self, auth):
        """The shape the previous test used to seed, kept as its own case — and
        it is the one that was WRONG.

        `load_bot_settings`/`get_trading_settings` accept SCALARS ONLY: a dict
        row is discarded and the loader's own default (-3.0) applies, so the
        breaker enforces 3R. The old dashboard display dug the inner value out
        anyway (`setting.value.get("value")`) and rendered 5R — the panel whose
        entire job is to tell the truth about the allowance overstating it by
        two thirds. Production never writes this shape, but "production never
        writes it" is how the six-way divergence survived in the first place.

        Asserted in both directions: the dashboard must report the breaker's
        3.0, NOT the 5.0 the dict carries, and 3.0 must be exactly what
        `limit_r_from` produces from the loader's default."""
        from backend.core.daily_reset import limit_r_from

        # What the loader's scalars-only rule leaves for the breaker to consume.
        loader_default = -3.0

        previous = set_limit_rows({"value": -5.0})
        try:
            limit_r = auth.get("/api/dashboard/today").json()[
                "daily_limit_status"]["limit_r"]
            assert limit_r == pytest.approx(3.0), \
                f"a dict-shaped row must not buy a {5.0}R allowance, got {limit_r}"
            assert limit_r == pytest.approx(limit_r_from(
                {"daily_loss_limit_r": loader_default})), \
                "the dashboard must land on the breaker's answer for the default"
        finally:
            restore_limit_rows(previous)

    def test_the_endpoint_reads_the_utc_day_not_the_local_day(self, auth):
        """Runtime proof for the defect the source-contract test only proxies.

        Seeds a LOSING row for the UTC day and a very different WINNING row for
        the day a local-time resolution would pick (at 23:30 UTC on a UTC+2 host
        date.today() returns the NEXT day), then asserts the endpoint reports the
        UTC one. Closes two gaps at once: it proves the endpoint resolves the day
        in UTC, and it makes used_r/remaining_r non-trivial instead of the
        0.0 == 0.0 the dev DB's flat current-day row currently produces.
        """
        from datetime import date, timedelta
        from backend.core.daily_reset import today_utc

        def row(day, pnl_r):
            from backend.models.daily_summary import DailySummary
            return DailySummary(date=day, total_trades=7, wins=3, losses=3,
                                breakeven=1, total_pnl_r=pnl_r, total_pnl_usdt=1.0,
                                worst_trade_r=-1.0, best_trade_r=1.5,
                                daily_loss_limit_hit=False)

        utc_day = today_utc()
        decoy_day = utc_day + timedelta(days=1)
        days = [utc_day, decoy_day]

        # -2.5R is deliberately UNDER the 3R limit so that even if something
        # reads this row mid-test it cannot trip the breaker. Do not scale this
        # value up.
        seed_daily_rows([row(utc_day, -2.5), row(decoy_day, 9.0)])
        try:
            # Pin the process TZ east of UTC so `date.today()` is guaranteed to
            # differ from the UTC day. Without this the test only bites on a host
            # that already happens to run ahead of UTC (the VPS does; a CI box on
            # UTC would resolve the same day either way and pass vacuously).
            # time.tzset() is what makes the C-level localtime that
            # `date.today()` reads honour the change; restored in the finally.
            import os
            import time
            previous_tz = os.environ.get("TZ")
            os.environ["TZ"] = "Asia/Jerusalem"  # UTC+2/+3, like Europe/Berlin
            time.tzset()
            assert date.today() != utc_day, \
                "the pinned TZ did not take effect — this test would be vacuous"
            try:
                body = auth.get("/api/dashboard/today").json()
            finally:
                if previous_tz is None:
                    os.environ.pop("TZ", None)
                else:
                    os.environ["TZ"] = previous_tz
                time.tzset()
            assert body["trades_today"] == 7, body
            assert body["pnl_r"] == pytest.approx(-2.5), \
                f"the endpoint read the wrong day: {body}"
            assert body["daily_limit_status"]["used_r"] == pytest.approx(2.5), body
            assert body["daily_limit_status"]["remaining_r"] == pytest.approx(0.5), body
        finally:
            purge_daily_rows(days)


# ------------------------------------------------------- exchange_sync latch

def _sync_sql():
    """The raw-SQL recompute `exchange_sync` runs, as it appears in source."""
    src = (REPO_ROOT / "backend/routers/settings.py").read_text()
    start = src.index("UPDATE daily_summary SET")
    return src[start:src.index('"""', start)]


def _latch_expression():
    """The `daily_loss_limit_hit` expression alone.

    Narrowed to this clause rather than the whole statement so the `-3.0` ban
    below cannot be satisfied or broken by an unrelated literal elsewhere in the
    GROUP BY (there is none today; asserting over the full statement would be a
    test of today's incidental formatting, not of the latch).
    """
    for line in _sync_sql().splitlines():
        if "daily_loss_limit_hit" in line:
            return line.split("daily_loss_limit_hit", 1)[1].lstrip().lstrip("=").strip()
    raise AssertionError("no daily_loss_limit_hit clause in the sync statement")


class TestExchangeSyncLatch:
    def test_the_latch_threshold_is_not_a_hardcoded_literal(self):
        """It was `total_r <= -3.0`, so setting the limit to -5 left the sync
        latching days at -3 — the configured limit was ignored entirely.
        Bound-parameter plumbing is pinned separately by
        test_the_computed_limit_is_what_gets_bound."""
        expr = _latch_expression()
        assert "-3.0" not in expr, "the sync must bind :limit_r, not hardcode -3.0"
        assert ":limit_r" in expr

    def test_the_bound_limit_goes_through_the_shared_clamp(self):
        """Whatever row is in the DB, the sync and the breaker must latch on the
        same number.

        AST, not substring: the sync's prose mentions the old `-3.0` literal, and
        a docstring mention must not satisfy a delegation check.
        """
        import ast
        import inspect
        import textwrap
        import backend.routers.settings as s
        called = {n.func.id for n in ast.walk(ast.parse(
            textwrap.dedent(inspect.getsource(s.exchange_sync))))
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        assert "limit_r_from" in called, \
            "the sync must CALL limit_r_from, not merely name it"

    def test_the_computed_limit_is_what_gets_bound(self):
        """The last hardcoded-literal vector, and the one that re-opens this exact
        bug: writing `{"cutoff": cutoff, "limit_r": 3.0}` passes every other test
        in this class. #1 only bans `-3.0` inside the clause, #4 supplies
        `limit_r` itself, and #3 only proves `limit_r_from` is *called* — nothing
        proved its *result* reached the statement. This closes that plumbing."""
        import ast
        import inspect
        import textwrap
        import backend.routers.settings as s
        tree = ast.parse(textwrap.dedent(inspect.getsource(s.exchange_sync)))
        bound = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Dict)
                 and any(getattr(k, "value", None) == "limit_r" for k in n.keys)]
        assert bound, "the sync must bind a limit_r param"
        # The binding value must be the NAME the helper's result is assigned to,
        # so `3.0`, `-3.0`, `limit_r_from(...)` inline or `DEFAULT_...` all fail.
        value = bound[0].values[[getattr(k, "value", None) == "limit_r"
                                 for k in bound[0].keys].index(True)]
        assert isinstance(value, ast.Name) and value.id == "limit_r", (
            "the limit_r param must bind the limit_r_from() result, not a "
            f"literal — got {ast.unparse(value)!r}")

    def test_the_winning_day_is_excluded_by_the_positive_limit_not_a_floor(self):
        """What the expression actually does, stated plainly.

        `COALESCE(-t.total_r, 0)` is NOT a `max(0, ...)`: on a +4.5R day it
        evaluates to -4.5. The day fails to latch because `limit_r` is strictly
        POSITIVE, so `-4.5 >= 3.0` is false. The `COALESCE` guards a NULL
        `total_r` (a day with trades but no summed R), which would otherwise make
        the comparison NULL and leave `daily_loss_limit_hit` NULL too.

        The equivalence with `used_r_from` rests on `limit_r_from` only ever
        returning values in (0, MAX] — pinned by TestLimitRFrom above — so both
        spellings agree on every input the limit can actually take."""
        from backend.core.daily_reset import (MAX_DAILY_LOSS_LIMIT_R,
                                              DEFAULT_DAILY_LOSS_LIMIT_R)

        # Every value the clamp can return, across the whole domain it accepts.
        for limit in (1e-9, 1.0, DEFAULT_DAILY_LOSS_LIMIT_R, MAX_DAILY_LOSS_LIMIT_R):
            assert limit > 0, "the whole argument above rests on this"

    def test_the_expression_excludes_winning_days_and_honours_the_bound_limit(self):
        """Behavioural, against the real DB — but it WRITES nothing.

        The extracted expression is evaluated over synthetic day totals instead
        of by seeding `trades` (a Trade row needs a closed position, a strategy
        type and a symbol, and a leaked one shows up on the Performance page).
        This proves the two properties the source-contract tests above can only
        assert textually: that the configured limit is the bound parameter rather
        than a constant, and that a winning day never latches — via the
        POSITIVITY of the limit, not via a floor in the expression (see
        test_the_winning_day_is_excluded_by_the_positive_limit_not_a_floor).
        """
        from sqlalchemy import text
        from conftest import run_async

        expr = _latch_expression()

        async def _latch(total_r, limit_r):
            engine = _own_engine()
            try:
                async with engine.connect() as conn:
                    # total_r is cast to numeric because that is what the real
                    # GROUP BY produces (SUM over a Numeric column); leaving the
                    # VALUES literal untyped would make Postgres infer its type
                    # from the comparison instead, so the test could pass or fail
                    # on a cast rule rather than on the latch.
                    hit = (await conn.execute(text(
                        f"SELECT ({expr}) FROM (VALUES "
                        f"(CAST(:total_r AS numeric))) AS t(total_r)"),
                        {"total_r": total_r, "limit_r": limit_r})).scalar()
                    return bool(hit)
            finally:
                await engine.dispose()

        # A winning day latches at NO limit, because -total_r is negative and the
        # limit is positive. Stated for several limits, since this would only
        # hold while the bound limit stays > 0.
        for limit in (1.0, 3.0, 5.0):
            assert run_async(_latch(4.5, limit)) is False, \
                f"a winning day must never latch (limit={limit})"
            assert run_async(_latch(0.0, limit)) is False, \
                f"a flat day must never latch (limit={limit})"

        # A NULL total_r (COALESCE's reason to exist) must not leave the latch
        # NULL/indeterminate.
        assert run_async(_latch(None, 3.0)) is False

        # The configured limit is honoured in BOTH directions: -3.5R latches at
        # 3R, does not at 5R, and -5.5R latches at 5R.
        assert run_async(_latch(-3.5, 3.0)) is True
        assert run_async(_latch(-3.5, 5.0)) is False, \
            "raising the limit to -5R must stop the sync latching at -3R"
        assert run_async(_latch(-5.5, 5.0)) is True

    def test_the_comparison_is_the_breakers_own_spelling(self):
        """The breaker tests `used_r >= limit`; the sync must test the same
        quantity with the same direction. `total_r <= -3.0` is the mirror-image
        spelling of that question, and the two are only equivalent while the
        threshold stays a POSITIVE number."""
        assert "COALESCE(-t.total_r, 0) >= :limit_r" in _latch_expression(), \
            "the sync must compare -total_r against the limit with >="

    def test_the_sync_still_sets_absolute_aggregates_rather_than_accumulating(self):
        """`update_daily_summary` is an increment; this statement is not.

        Calling the accumulator from the sync would double count, so the two
        paths must stay separate — `test_defects_2026_10_17.py` pins the
        increment half behaviourally; this pins the recompute half structurally
        (the statement is a bare `UPDATE ... FROM (SELECT ... GROUP BY)` that
        never adds to what is already in the row).
        """
        sql = _sync_sql()
        assert "+ t." not in sql, "the sync must SET aggregates, never add to them"
        assert "t.total_r" in sql and "SUM(pnl_r)" in sql, sql


# ------------------------------------------------- re-duplication guard

class TestTheRulesExistInExactlyOnePlace:
    def test_the_used_r_formula_is_written_once_in_the_repo(self):
        """The whole point of the change. A second inline copy is free to drift
        back out of sync with the breaker, which is the defect this removes."""
        import pathlib
        hits = [p for p in (REPO_ROOT / "backend").rglob("*.py")
                if "max(0.0, -float(" in p.read_text()]
        # daily_reset.used_r_from is the single legitimate home.
        assert [p.name for p in hits] == ["daily_reset.py"], \
            f"used_r is re-implemented in {[str(p) for p in hits]}"

    def test_no_module_calls_date_today(self):
        """AST, not substring, and that is deliberate: the docstrings introduced
        by this very change NAME `date.today()` in order to warn against it (see
        `today_utc`'s docstring and routers/settings.py). A text ban would fail on
        the documentation of the fix. Walking for a zero-arg `.today()` attribute
        call distinguishes a call from the phrase."""
        import ast
        offenders = []
        for p in (REPO_ROOT / "backend").rglob("*.py"):
            tree = ast.parse(p.read_text())
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "today" and not n.args):
                    offenders.append(f"{p}:{n.lineno}")
        assert offenders == [], f"local-day resolution at {offenders}"
