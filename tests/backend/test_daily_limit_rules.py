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

# asyncpg refuses a string for a timestamptz/date parameter, so the synthetic
# `closed_at` literals below are parsed into real objects. Every one of them is
# written with an explicit `+00` offset, so the instant is unambiguous whatever
# the host's zone is.
from datetime import date, datetime
from datetime import timezone as _tz

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

    @pytest.mark.parametrize("junk", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_pnl_fails_closed_rather_than_freeing_the_day(self, junk):
        """A NaN total silently yielded used_r 0.0, because max(0.0, nan) is
        0.0 — disabling the breaker for the day. This is the exact mirror of the
        NaN-limit case `limit_r_from` already rejects: an unusable number must
        HALT, never release."""
        from backend.core.daily_reset import used_r_from
        used = used_r_from(self._summary(junk))
        assert used > 3.0, f"a non-finite pnl must exceed any real limit, got {used}"

    def test_a_non_finite_pnl_actually_halts_the_breaker(self):
        """`used > 3.0` is only meaningful if the breaker's own comparison treats
        it as over-limit. Closes the gap between the helper's contract and the
        caller that enforces it: `check_and_enforce_limit` must refuse to trade."""
        import asyncio
        from types import SimpleNamespace
        from backend.core.daily_reset import DailyResetService

        row = SimpleNamespace(total_trades=3, wins=1, losses=2, total_pnl_r=float("nan"),
                               total_pnl_usdt=float("nan"), daily_loss_limit_hit=False)

        class _Session:
            async def execute(self, *a, **k):
                from types import SimpleNamespace
                return SimpleNamespace(scalar_one_or_none=lambda: row)

            async def commit(self):
                return None

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        svc = DailyResetService(session_factory=lambda: _Session(),
                                settings={})
        # get_daily_stats renders `float(summary.total_pnl_r)` for display, which
        # is NaN but does not raise, so the breaker path is reached.
        assert asyncio.run(svc.check_and_enforce_limit()) is False, \
            "an unreadable daily total must STOP trading, not allow it"

    def test_unrealized_loss_alert_reports_combined_usage_and_fires_once(self):
        """The halt threshold includes open losses, so its alert must report
        the same amount and a latched check must not alert again."""
        import asyncio
        from backend.core.daily_reset import DailyResetService

        class Alerts:
            def __init__(self):
                self.hits = []
            async def daily_limit_hit(self, used_r, limit_r):
                self.hits.append((used_r, limit_r))

        class Bot:
            async def stop(self):
                pass

        async def scenario():
            alerts = Alerts()
            svc = DailyResetService(settings={"daily_loss_limit_r": -3.0},
                                    bot_engine=Bot(), alert_service=alerts)
            async def stats():
                return {"used_r": 0.0, "limit_r": 3.0}
            svc.get_daily_stats = stats
            positions = {"BTCUSDT": {"unrealizedPnl": -3.0,
                                     "entryPrice": 100.0, "quantity": 1.0,
                                     "initialRiskDistance": 1.0}}

            assert await svc.check_and_enforce_limit(positions) is False
            assert await svc.check_and_enforce_limit(positions) is False
            assert alerts.hits == [(3.0, 3.0)]

        asyncio.run(scenario())


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
        dashboard starts disagreeing with the breaker again.

        The clamp check is AST, not a substring: `used_r_from` no longer spells
        `max(0.0, -float(`, and a text ban on a string the single legitimate
        implementation no longer contains cannot fail."""
        import ast
        src = (REPO_ROOT / "backend/routers/dashboard.py").read_text()
        assert "date.today()" not in src, \
            "the trading day must be resolved in UTC via today_utc(), not locally"
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "max"):
                arg = ast.unparse(node.args[0]) if node.args else ""
                assert "total_pnl_r" not in arg, \
                    f"used_r must come from used_r_from(), got max({arg})"

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

    # NOTE: a runtime end-to-end proof that `/dashboard/today` reads the UTC day
    # used to live here. It was removed deliberately. It could only be made
    # non-vacuous by pinning the process TZ to a zone east of UTC, which only
    # forces `date.today()` off the UTC date while UTC's time-of-day is between
    # 21:00 and 24:00 — so it failed for 21 hours of every 24. The property is
    # still pinned two other ways: structurally by
    # `test_the_dashboard_resolves_no_trading_day_inline` (AST, no inline
    # `.today()` / `.now().date()` anywhere in the module), and behaviourally by
    # `TestTodayUtc::test_it_resolves_the_day_in_utc_and_returns_that_date`, which
    # asserts the `tz` ARGUMENT to `today_utc()` against a frozen clock and so is
    # valid at any wall-clock time.


# ------------------------------------------------------- exchange_sync latch

def _sync_sql():
    """The raw-SQL recompute `exchange_sync` runs, as it appears in source."""
    src = (REPO_ROOT / "backend/routers/settings.py").read_text()
    start = src.index("UPDATE daily_summary SET")
    return src[start:src.index('"""', start)]


def _sync_subquery():
    """The `FROM (SELECT ... FROM trades ... GROUP BY ...)` subquery, alone.

    Scoped to the subquery deliberately. The statement's SET clause carries a
    long explanatory comment that NAMES the old `DATE(closed_at)` expression in
    order to warn against reintroducing it, so any substring test taken over the
    whole statement would fail on its own documentation — exactly the trap
    `test_no_module_calls_date_today` documents for the prose about
    `date.today()`.

    Located by paren matching from `FROM (SELECT` rather than by a line range, so
    reformatting the statement cannot silently change what is asserted on.
    """
    sql = _sync_sql()
    i = sql.index("FROM (SELECT")
    depth = 0
    for j in range(sql.index("(", i), len(sql)):
        if sql[j] == "(":
            depth += 1
        elif sql[j] == ")":
            depth -= 1
            if depth == 0:
                return sql[sql.index("(", i):j + 1]
    raise AssertionError("unbalanced parentheses in the sync subquery")


def _subquery_part(pattern, label):
    """Regex the named clause out of the extracted subquery."""
    import re
    match = re.search(pattern, _sync_subquery(), re.S)
    assert match, f"no {label} in the sync subquery:\n{_sync_subquery()}"
    return match.group(1).strip()


def _group_key_expression():
    """The expression the sync keys its per-day aggregate on (the `as d` alias).

    This is what the UPDATE's `WHERE daily_summary.date = t.d` joins on, so it
    must be the same day the accumulator wrote the row under — UTC.
    """
    return _subquery_part(r"SELECT\s+(.*?)\s+as\s+d\b", "group key (the `as d` alias)")


def _where_predicate():
    """The subquery's WHERE clause, verbatim.

    Anchored on `FROM trades` rather than on a bare `WHERE`: the SELECT list
    contains `COUNT(*) FILTER (WHERE pnl_r > 0)` and an unanchored non-greedy
    match starts inside the first FILTER and runs to the end of the subquery.
    """
    return _subquery_part(r"FROM\s+trades\s+WHERE\s+(.*?)\s+GROUP\s+BY",
                          "WHERE predicate")


def _group_by_expression():
    """The expression in GROUP BY, which must equal the `as d` key."""
    return _subquery_part(r"GROUP\s+BY\s+(.*?)\s*\)\s*$", "GROUP BY")


def _eval_synthetic_day_part(sql, rows, params, tz="Europe/Berlin"):
    """Evaluate a fragment of the sync's own SQL over synthetic `closed_at` rows.

    Runs against real Postgres — the point of Bug A and Bug B is timezone and
    date semantics, which a Python re-implementation of the expression would
    simply agree with by construction. The session TimeZone is set explicitly so
    the result cannot depend on the host: the dev box is `Asia/Yerevan` and the
    production VPS is `Europe/Berlin`, and neither is what the assertion should
    depend on.

    `Europe/Berlin` is the DEFAULT because it is the zone the bug actually
    happened in. (`Asia/Tokyo` is +9, which is far enough east that both of the
    boundary instants in the UTC-day test shift onto the *same* local day, so it
    would not demonstrate the split at all.)

    It writes NOTHING. The rows come from a `VALUES` list rather than from
    `trades`, because seeding that table would leak a Trade row into the shared
    dev database and onto the Performance page (tests/AGENTS.md).

    Both the old (`:cutoff`) and new (`:first_day`) params are supplied, so the
    assertion tests the PREDICATE that is actually in the source rather than
    testing which param name happens to be spelled there.
    """
    from sqlalchemy import text
    from conftest import run_async

    def _ts(text):
        return datetime.fromisoformat(text).astimezone(_tz.utc)

    async def _run():
        engine = _own_engine()
        try:
            async with engine.connect() as conn:
                # `SET TIME ZONE` takes no bind parameter, so the zone is
                # interpolated. It comes from this file's own literals, never
                # from input, and is asserted below rather than trusted.
                await conn.execute(text(f"SET LOCAL TIME ZONE '{tz}'"))
                # Assert the pinned zone took: otherwise a bug in the test's own
                # plumbing would make every assertion below pass vacuously.
                got = (await conn.execute(
                    text("SELECT current_setting('TimeZone')"))).scalar()
                assert got.lower() == tz.lower(), f"tz not applied: {got}"
                result = await conn.execute(
                    text(sql),
                    {**params,
                     # A cutoff shaped like the bug: 48h back from 23:02 on Oct 17,
                     # i.e. Oct 15 23:02 — which is what truncates Oct 15.
                     "cutoff": _ts("2026-10-15 23:02:00+00"),
                     "first_day": date(2026, 10, 15),
                     "limit_r": 3.0})
                return result.all()
        finally:
            await engine.dispose()

    return run_async(_run())


def _synthetic_group(rows, session_tz="Europe/Berlin"):
    """Group synthetic `closed_at` values by the sync's REAL group key + filter.

    Returns `{utc_day: [closed_at, ...]}`. This is the production query's
    grouping and filtering logic verbatim, with only `FROM trades` replaced by a
    `VALUES` list — so a regression in either the key or the predicate shows up
    here rather than needing a seeded trade to reproduce.
    """
    values = ", ".join(f"(CAST(:c{i} AS timestamptz))" for i in range(len(rows)))
    params = {f"c{i}": datetime.fromisoformat(r) for i, r in enumerate(rows)}
    sql = (f"SELECT {_group_key_expression()} AS d, "
           f"string_agg(CAST(:_label AS text), '|') AS rows "
           f"FROM (VALUES {values}) AS v(closed_at) "
           f"WHERE {_where_predicate()} "
           f"GROUP BY 1 ORDER BY 1")
    params["_label"] = "x"
    groups = {}
    for day, _label in _eval_synthetic_day_part(sql, rows, params, tz=session_tz):
        groups[str(day)] = day
    return groups


class TestExchangeSyncGroupsByTheUtcDay:
    def test_the_group_key_is_utc_not_the_session_timezone(self):
        """Bug A. `closed_at` is `timestamptz`, so a bare `DATE(closed_at)`
        resolves in the SESSION `TimeZone`, while `daily_summary.date` is
        written in UTC by the accumulator. On the production VPS
        (`Europe/Berlin`, UTC+2) the two disagree for four hours a night.

        The consequence is worse than "matches no rows": the group key becomes
        the LOCAL day, so `WHERE daily_summary.date = t.d` matches TODAY'S UTC
        row and overwrites it with a PARTIAL day — which on a losing day pushes
        `used_r` back under the limit and silently re-opens an allowance the
        breaker already consumed.

        Both instants below are on Oct 17 in UTC and are 2 hours apart, straddling
        the 22:00-24:00 UTC window in which a host east of UTC disagrees with
        them. `Europe/Berlin` (+2 in October) is the session zone because it is
        the production VPS's own zone — the zone the bug happened in — and
        because the dev box runs `Asia/Yerevan` (+4) and `Asia/Tokyo` (+9), so a
        hardcoded zone is the only way this test does not depend on where the
        suite runs."""
        rows = ["2026-10-17 21:30:00+00", "2026-10-17 23:30:00+00"]

        # What the extracted expression actually does...
        key = _group_key_expression()
        groups = _synthetic_group(rows, session_tz="Europe/Berlin")
        assert list(groups) == ["2026-10-17"], \
            f"{key} must bucket both instants into the single UTC day, got {groups}"

        # ...and, as the contrast that makes the first assertion meaningful, the
        # OLD expression demonstrably puts them in different days under the same
        # session zone. If this ever stops holding, the test above has stopped
        # being able to detect the bug it exists for.
        legacy = _eval_synthetic_day_part(
            "SELECT DISTINCT DATE(closed_at)::text AS d "
            "FROM (VALUES (CAST(:c0 AS timestamptz)), (CAST(:c1 AS timestamptz)))"
            " AS v(closed_at) ORDER BY 1",
            rows, {f"c{i}": datetime.fromisoformat(r) for i, r in enumerate(rows)},
            tz="Europe/Berlin")
        assert [r[0] for r in legacy] == ["2026-10-17", "2026-10-18"], \
            "the bare DATE(closed_at) form should split the day; if not, the " \
            "assertion above can no longer tell the two expressions apart"

    def test_the_filter_cannot_truncate_a_boundary_day(self):
        """Bug B. `closed_at >= :cutoff` is a TIMESTAMP comparison, so a sync at
        23:02 on Oct 17 uses a cutoff of Oct 15 23:02 — and Oct 15 is then
        recomputed from only the trades after 23:02. The recompute SETs absolute
        aggregates, so this is not "a day that stays stale", it is a COMPLETE day
        overwritten with a PARTIAL one: trades silently drop out of the very
        total the daily-loss breaker reads.

        The window must therefore be filtered on the DATE, which widens it to
        up to ~72h of days. That is the correct trade — understating a losing day
        is the failure mode that matters.
        """
        oct15 = ["2026-10-15 00:00:00+00", "2026-10-15 01:00:00+00",
                 "2026-10-15 12:00:00+00", "2026-10-15 20:00:00+00",
                 "2026-10-15 23:30:00+00"]
        rows = oct15 + ["2026-10-16 10:00:00+00", "2026-10-17 23:59:00+00"]

        groups = _synthetic_group(rows, session_tz="UTC")

        assert sorted(groups) == ["2026-10-15", "2026-10-16", "2026-10-17"], \
            f"every day from :first_day must be recomputed, got {sorted(groups)}"

        # The load-bearing assertion: Oct 15's group exists at all AND covers the
        # whole day. Against a `closed_at >= :cutoff` predicate with cutoff
        # Oct 15 23:02 the group holds ONE row (23:30), not five.
        oct15_group = groups["2026-10-15"]
        assert oct15_group is not None, \
            f"the boundary day was truncated away entirely: {groups}"

        counts = _eval_synthetic_day_part(
            f"SELECT {_group_key_expression()}::text AS d, COUNT(*) "
            f"FROM (VALUES "
            + ", ".join(f"(CAST(:c{i} AS timestamptz))" for i in range(len(rows)))
            + f") AS v(closed_at) WHERE {_where_predicate()} GROUP BY 1 ORDER BY 1",
            rows, {f"c{i}": datetime.fromisoformat(r) for i, r in enumerate(rows)},
            tz="UTC")
        per_day = {d: int(n) for d, n in counts}
        assert per_day["2026-10-15"] == 5, \
            (f"Oct 15 must be recomputed from its COMPLETE day; got {per_day} "
             f"via `{_where_predicate()}` — a timestamp cutoff drops the rest")

    def test_the_predicate_compares_dates_not_timestamps(self):
        """The mechanism behind Bug B, asserted directly: the filter must be a
        date comparison, so the `:cutoff`-style timestamp parameter cannot be
        reintroduced without this failing."""
        predicate = _where_predicate()
        assert "::date >=" in predicate, \
            f"the filter must compare DATES, got: {predicate}"
        assert ":first_day" in predicate, predicate
        assert "AT TIME ZONE 'UTC'" in predicate, \
            f"the day boundary must be UTC, got: {predicate}"


class TestExchangeSyncSourceContract:
    def test_the_statement_carries_no_bare_date_closed_at(self):
        """Scoped to the extracted `FROM (SELECT ...)` subquery — the SET clause
        holds the prose that names the old expression in order to warn against
        it, so a text ban over the whole statement would fail on its own
        documentation."""
        sub = _sync_subquery()
        import re
        bare = re.findall(r"DATE\(closed_at\)", sub, re.IGNORECASE)
        assert bare == [], f"session-local day grouping is back in: {bare}"
        assert "AT TIME ZONE 'UTC'" in sub, \
            f"the day key must be resolved in UTC, got:\n{sub}"

    def test_the_group_key_and_the_group_by_clause_agree(self):
        """`SELECT <key> as d ... GROUP BY <expr>` — if these two ever diverge,
        Postgres groups one day and joins another. Cheap to pin, invisible in a
        read of the statement."""
        assert _group_by_expression().upper() == _group_key_expression().upper(), \
            (_group_key_expression(), _group_by_expression())

    def test_the_day_key_is_the_utc_expression_verbatim(self):
        assert "AT TIME ZONE 'UTC'" in _group_key_expression(), \
            _group_key_expression()

    def test_first_day_is_bound_from_python_and_cutoff_is_not(self):
        """`:first_day` must reach the statement, computed in Python as the DATE
        48h back — and the timestamp `:cutoff` must be gone from the statement's
        params (it stays bound for the per-trade income-API loop above it, which
        filters individual trades and so is not affected by either bug)."""
        import ast
        import inspect
        import textwrap
        import backend.routers.settings as s

        assert ":first_day" in _sync_sql()
        assert ":cutoff" not in _sync_sql(), \
            "the recompute must not filter on the 48h timestamp cutoff"

        tree = ast.parse(textwrap.dedent(inspect.getsource(s.exchange_sync)))
        dicts = [n for n in ast.walk(tree) if isinstance(n, ast.Dict)]
        bound = [n for n in dicts
                 if any(getattr(k, "value", None) == "first_day" for k in n.keys)]
        assert len(bound) == 1, \
            f"expected exactly one params dict binding first_day, found {len(bound)}"
        keys = [getattr(k, "value", None) for k in bound[0].keys]
        assert "cutoff" not in keys, \
            "cutoff must no longer be bound into the recompute statement"
        # The dict must bind the NAME, and the name must be assigned the
        # `(now(utc) - timedelta(hours=48)).date()` — so a dict that inlines a
        # timestamp, or binds a day derived some other way, fails.
        value = bound[0].values[keys.index("first_day")]
        assert isinstance(value, ast.Name) and value.id == "first_day", \
            f"first_day must bind the computed name, got {ast.unparse(value)!r}"

        assigns = [n for n in ast.walk(tree)
                   if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "first_day"
                           for t in n.targets)]
        assert len(assigns) == 1, f"first_day must be assigned once, found {assigns}"
        assert assigns[0].lineno < bound[0].lineno, \
            "first_day must be computed before the statement that binds it"
        computed = assigns[0].value
        # The assertion is on `.date()` over a `timedelta` subtraction, so neither
        # a bare 48h timestamp nor a re-derived day can pass.
        assert (isinstance(computed, ast.Call)
                and isinstance(computed.func, ast.Attribute)
                and computed.func.attr == "date"), \
            f"first_day must be a .date() call, got {ast.unparse(computed)!r}"
        inner = computed.func.value
        assert isinstance(inner, ast.BinOp) and isinstance(inner.op, ast.Sub), \
            ast.unparse(computed)
        window = inner.right
        assert (isinstance(window, ast.Call)
                and isinstance(window.func, ast.Name)
                and window.func.id == "timedelta"
                and any(getattr(k, "arg", None) == "hours" for k in window.keywords)), \
            (f"first_day must subtract a timedelta(hours=...) window: "
             f"{ast.unparse(computed)}")
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "now" for n in ast.walk(inner.left)), \
            f"first_day must be measured from datetime.now(...): {ast.unparse(computed)}"


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

    @pytest.mark.parametrize("raw", [-0.5, -3.0, -20.0, -0.0, 9999,
                                     float("nan"), None, "abc"])
    def test_the_limit_is_always_positive_for_every_rejected_input(self, raw):
        """A winning day is excluded from the latch because the limit is
        strictly positive, not because of a floor in the SQL. That only holds if
        EVERY input `limit_r_from` can be handed still yields a positive limit —
        including the ones it rejects. The previous version of this test asserted
        `3.0 > 0` over a hardcoded tuple, never calling the helper, so it stayed
        green through a rewrite that returned 0 or -1 for any input."""
        from backend.core.daily_reset import limit_r_from
        assert limit_r_from({"daily_loss_limit_r": raw}) > 0, raw

    def test_the_sync_expression_carries_no_floor_operator(self):
        """The test name says "not a floor". Assert that too: the COALESCE guards
        a NULL `total_r`; a winning day is excluded because `limit_r_from`
        guarantees a strictly positive limit. Do NOT let anyone 'fix' this into
        GREATEST(0, -total_r) for symmetry with used_r_from — the source comment
        at backend/routers/settings.py explains why it is not spelled that way."""
        expr = _latch_expression()
        assert "GREATEST" not in expr.upper()
        assert "COALESCE" in expr.upper()

    def test_the_expression_excludes_winning_days_and_honours_the_bound_limit(self):
        """Behavioural, against the real DB — but it WRITES nothing.

        The extracted expression is evaluated over synthetic day totals instead
        of by seeding `trades` (a Trade row needs a closed position, a strategy
        type and a symbol, and a leaked one shows up on the Performance page).
        This proves the two properties the source-contract tests above can only
        assert textually: that the configured limit is the bound parameter rather
        than a constant, and that a winning day never latches — via the
        POSITIVITY of the limit, not via a floor in the expression (see
        test_the_limit_is_always_positive_for_every_rejected_input).
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

        # A NULL total_r must not leave the latch NULL/indeterminate. Note this
        # case is UNREACHABLE through the real statement — the subquery already
        # emits `COALESCE(SUM(pnl_r),0) as total_r` — so the COALESCE in the
        # comparison is a defensive guard, not a fix. The case is asserted on the
        # extracted expression because that is what the expression accepts.
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

def _used_r_clamp_sites():
    """Every `max(0, -x)` call site under `backend/`, as `file:lineno`, plus the
    `used_r_from` definitions it may live in.

    Matched on the SHAPE — a two-argument `max()` whose first argument is the
    constant zero and whose second is a unary negation — and deliberately NOT on
    the identifiers in the arguments.

    That identifier assumption has now failed twice, both times silently, and
    both times the guard went green over ZERO matches while reading as coverage:

      1. the original literal substring `max(0.0, -float(` stopped existing the
         day `used_r_from` hoisted the `float()` into a local;
      2. the AST rewrite swapped the substring for
         `"total_pnl_r" in <args> or "total_r" in <args>`, which hoisting the
         float() defeats the same way — `ast.unparse` of the real call's
         arguments is `'0.0'` and `'-value'`, neither of which mentions the
         column. It matched zero sites.

    A hoisted local is the NORMAL state of this function (it grew one to hold
    the non-finite check), so an identifier-based guard is guaranteed to miss.
    The negation shape survives hoisting: `-value` is still a `UnaryOp`, which
    is exactly what the rewrite keys on.

    As a bonus it removes a latent false positive: `"total_r"` is a substring of
    `total_risk_distance`, so the old identifier test would have flagged an
    unrelated `max(0.0, -total_risk_distance)`.
    """
    import ast
    sites = []
    homes = []
    for p in sorted((REPO_ROOT / "backend").rglob("*.py")):
        tree = ast.parse(p.read_text())
        homes_here = [n for n in ast.walk(tree)
                      if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                      and n.name == "used_r_from"]
        for n in homes_here:
            homes.append(f"{p.name}:{n.lineno}")
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "max"
                    and len(node.args) == 2):
                continue
            zero, negated = node.args
            if not (isinstance(zero, ast.Constant) and zero.value == 0
                    and isinstance(negated, ast.UnaryOp)
                    and isinstance(negated.op, ast.USub)):
                continue
            owner = next((f for f in homes_here
                          if f.lineno <= node.lineno <= (f.end_lineno or 0)),
                         None)
            sites.append((f"{p.name}:{node.lineno}", owner))
    return sites, homes


class TestTheRulesExistInExactlyOnePlace:
    def test_the_guard_finds_the_real_clamp_it_exists_to_protect(self):
        """A guard that matches ZERO sites is worse than no guard, because it
        reads as coverage. Both previous versions of this rule did exactly
        that, unnoticed, and the docstring went on claiming otherwise — so the
        positive case is asserted first and explicitly."""
        sites, homes = _used_r_clamp_sites()
        assert len(sites) == 1, f"expected exactly one max(0, -x) site, found {sites}"
        assert sites[0][0].startswith("daily_reset.py"), sites
        assert sites[0][1] is not None, \
            f"the one clamp site {sites[0][0]} is not inside a used_r_from — " \
            "the owner resolution is dead"
        assert len(homes) == 1 and homes[0].startswith("daily_reset.py"), \
            f"exactly one used_r_from must own the formula, found {homes}"

    def test_the_used_r_formula_is_written_once_in_the_repo(self):
        """The whole point of the change. A second inline copy is free to drift
        back out of sync with the breaker, which is the defect this removes.

        Structural, and keyed on the negation shape rather than on the column
        names in the arguments — see `_used_r_clamp_sites` for why the name in
        the argument is not a property of the formula (both prior guards
        matched zero real sites once the float() was hoisted). It is verified
        by mutation in both directions, including the hoisted form."""
        sites, _ = _used_r_clamp_sites()
        offenders = [site for site, owner in sites if owner is None]
        assert offenders == [], \
            f"the used_r formula is re-implemented outside used_r_from at {offenders}"

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


# --------------------------------------------- exchange_sync R-rewrite guard

class TestExchangeSyncWillNotFabricateR:
    """The sync recomputes `pnl_r = exchange_pnl / (initial_risk_distance * quantity)`.

    On 2026-10-18 that division, run unguarded, rewrote four trades past
    |R| > 10 and pushed the daily summary to +134.6R — which is the breaker's
    own input. A $1.36 trade became +92.69R because its frozen 1R was $0.0147.
    """

    def _guard(self):
        from backend.routers.settings import MAX_PLAUSIBLE_R_MULTIPLE
        return MAX_PLAUSIBLE_R_MULTIPLE

    def test_a_credible_r_is_still_writable(self):
        """The guard must not block ordinary trades. 1R was 1% of ~$221 equity."""
        cap = self._guard()
        assert 0.61 / 2.21 < cap, "a normal trade must remain writable"

    @pytest.mark.parametrize("usd,risk_amt,label", [
        (1.36, 0.014709, "376 ETHUSDT -> +92.69R"),
        (1.39, 0.025688, "364 SOLUSDT -> +54.01R"),
        (-8.34, 0.456700, "354 AVAXUSDT -> -18.26R"),
    ])
    def test_an_implausible_r_from_an_implausible_risk_basis_is_refused(self, usd, risk_amt, label):
        """Real numbers from the incident. `risk_amt` far below the intended 1R
        means the division is meaningless, not that the trade was exceptional."""
        cap = self._guard()
        assert abs(usd / risk_amt) >= cap, f"{label} must trip the guard"

    def test_the_sync_actually_gates_the_rewrite_on_the_cap(self):
        """Source contract: the cap must be *used*, not merely defined — a
        constant nobody references is exactly how the unguarded version looked
        like it was protected."""
        import inspect
        import backend.routers.settings as s
        src = inspect.getsource(s.exchange_sync)
        assert "MAX_PLAUSIBLE_R_MULTIPLE" in src
        # and the skip path must leave pnl_r out of the UPDATE it issues
        skip = src.split("MAX_PLAUSIBLE_R_MULTIPLE", 1)[1]
        assert ".values(pnl_usdt=" in skip, \
            "the skip path must write USD only and leave R untouched"
        assert "pnl_r=" not in skip.split("pnl_r = candidate_r")[0].split(
            "await db.execute", 1)[-1], \
            "the skip path must not write pnl_r"
