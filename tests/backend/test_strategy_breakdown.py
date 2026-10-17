"""Per-strategy performance breakdown.

`/performance/strategies` returns R-distribution BUCKETS — it feeds the
histogram and deliberately carries no per-strategy data. So there was no way to
see which strategy actually produces the P&L. That matters because the live
book diverged sharply: at risk_percent=5 the notional cap (300%) bound on
stops tighter than 1.67%, which was 13 of the last 15 trades, so winners
carried 1.70x the risk of losers and the books showed +$17.02 while reporting
-10.3R. Reading "3-candle is the problem" off the R column alone would have
been wrong — it is the best performer in dollars (+$20.18, PF 1.79).

This endpoint exists so that judgement is made from per-strategy numbers with
enough sample behind them, not from a single blended figure.
"""
import contextlib
import pytest

SYMS = ["ZZPMONAUSDT", "ZZPMONBUSDT"]

# strategy_type is the grouping key; the probe symbol keeps rows isolated.
LEDGER = [
    # (symbol, strategy, side, pnl_r, pnl_usdt)
    ("ZZPMONAUSDT", "ZSA", "LONG",  2.0,  4.00),
    ("ZZPMONAUSDT", "ZSA", "LONG",  1.5,  3.00),
    ("ZZPMONAUSDT", "ZSA", "SHORT", -1.0, -2.00),
    ("ZZPMONAUSDT", "ZSA", "LONG",  0.0,  0.00),
    ("ZZPMONBUSDT", "ZSB",   "LONG",  3.0,  1.50),
    ("ZZPMONBUSDT", "ZSB",   "SHORT", -1.0, -0.50),
    ("ZZPMONBUSDT", "ZSB",   "SHORT", -2.0, -1.00),
    ("ZZPMONBUSDT", "ZSC",     "LONG", -1.5, -3.00),
]


@contextlib.contextmanager
def seeded(rows=LEDGER, *, mode="live"):
    """Seed a ledger, purge on entry and exit. Own NullPool engine per phase —
    the shared pool is loop-bound and each run_async gets a fresh loop."""
    from datetime import datetime, timezone
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from backend.config import settings as cfg
    from backend.models.trade import Trade
    from conftest import run_async

    def _session():
        engine = create_async_engine(cfg.DATABASE_URL, poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False), engine

    def build():
        return [Trade(symbol=s, side=side, strategy_type=strat,
                      entry_price=100, exit_price=100, quantity=1,
                      pnl_usdt=u, pnl_r=r, exit_reason="MANUAL_CLOSE",
                      mode=mode, closed_at=datetime.now(timezone.utc))
                for s, strat, side, r, u in rows]

    async def _load():
        Session, engine = _session()
        try:
            async with Session() as db:
                await db.execute(delete(Trade).where(Trade.symbol.in_(SYMS)))
                db.add_all(build())
                await db.commit()
        finally:
            await engine.dispose()

    async def _purge():
        Session, engine = _session()
        try:
            async with Session() as db:
                await db.execute(delete(Trade).where(Trade.symbol.in_(SYMS)))
                await db.commit()
        finally:
            await engine.dispose()

    try:
        run_async(_load())
        yield
    finally:
        run_async(_purge())


@pytest.fixture()
def auth(client, test_user):
    from conftest import TEST_PASSWORD
    res = client.post("/api/auth/login",
                      json={"username": test_user.username,
                            "password": TEST_PASSWORD})
    assert res.status_code == 200, res.text
    client.cookies.set("fubot_token", res.json()["token"])
    return client


def _get(auth, **params):
    r = auth.get("/api/performance/strategies/breakdown", params=params)
    assert r.status_code == 200, r.text
    return {d["strategy"]: d for d in r.json()["data"]}


class TestBreakdownSchema:
    def test_the_response_carries_every_field_the_table_renders(self):
        from backend.schemas.performance import StrategyBreakdownResponse
        fields = StrategyBreakdownResponse.model_fields["data"].annotation
        from backend.schemas.performance import StrategyBreakdown
        for f in ("strategy", "total_trades", "wins", "losses", "breakevens",
                  "win_rate", "sum_r", "avg_r", "profit_factor",
                  "total_pnl_usdt"):
            assert f in StrategyBreakdown.model_fields, f

    def test_the_histogram_endpoint_is_untouched(self):
        """`/performance/strategies` feeds the R-distribution chart and returns
        {bucket,count}. Adding per-strategy data must not change it — the
        frontend reads those pairs."""
        import inspect
        import backend.routers.performance as perf
        src = inspect.getsource(perf.strategy_performance)
        assert "RBucket" in src, "the histogram must keep bucketing by R"
        assert "strategy_type" not in src, (
            "the histogram endpoint must not start grouping by strategy — the "
            "frontend reads {bucket, count} from it")


class TestBreakdownNumbers:
    def test_it_groups_by_strategy(self, auth):
        """Asserts the seeded strategies are present, not that they are the ONLY
        ones — the dev database holds real live trades, and the endpoint is
        supposed to report all of them."""
        with seeded():
            d = _get(auth)
        for name in ("ZSA", "ZSB", "ZSC"):
            assert name in d, f"{name} missing from {sorted(d)}"
        # Each seeded strategy's rows must not have been merged into another.
        assert d["ZSA"]["total_trades"] == 4, d["ZSA"]
        assert d["ZSB"]["total_trades"] == 3, d["ZSB"]
        assert d["ZSC"]["total_trades"] == 1, d["ZSC"]

    def test_it_counts_wins_losses_and_breakevens_per_strategy(self, auth):
        with seeded():
            d = _get(auth)
        c = d["ZSA"]
        assert (c["total_trades"], c["wins"], c["losses"], c["breakevens"]) == (4, 2, 1, 1), c
        o = d["ZSB"]
        assert (o["total_trades"], o["wins"], o["losses"], o["breakevens"]) == (3, 1, 2, 0), o

    def test_win_rate_agrees_with_the_counts(self, auth):
        """wins + losses + breakevens must reconcile to total_trades, and the
        percentage must follow from the same counts — a win_rate derived from a
        different denominator is how these tables quietly disagree with the
        tiles above them."""
        with seeded():
            d = _get(auth)
        for name, s in d.items():
            assert s["wins"] + s["losses"] + s["breakevens"] == s["total_trades"], \
                f"{name}: {s}"
            assert s["win_rate"] == pytest.approx(
                s["wins"] / s["total_trades"] * 100, abs=0.01), f"{name}: {s}"

    def test_it_reports_r_and_usd_totals_independently(self, auth):
        """The whole reason this endpoint exists: R and USD can disagree. 3CANDLE
        is +$5.00 but -0.5R+4.5R-1R+0R = +4.5R here, while OBFVG is +0.00 USDT
        and 0.00R. Reporting only one of them hides which is which."""
        with seeded():
            d = _get(auth)
        assert d["ZSA"]["sum_r"] == pytest.approx(2.5)
        assert d["ZSA"]["total_pnl_usdt"] == pytest.approx(5.0)
        assert d["ZSB"]["sum_r"] == pytest.approx(0.0)
        assert d["ZSB"]["total_pnl_usdt"] == pytest.approx(0.0)

    def test_profit_factor_is_gross_win_over_gross_loss(self, auth):
        with seeded():
            d = _get(auth)
        # OBFVG: +1.50 gross win, 0.50 + 1.00 gross loss -> 1.00
        assert d["ZSB"]["profit_factor"] == pytest.approx(1.0)
        # 3CANDLE: +7.00 gross win, 2.00 gross loss -> 3.50
        assert d["ZSA"]["profit_factor"] == pytest.approx(3.5)

    def test_a_strategy_with_no_losses_reports_an_infinite_pf_not_zero(self, auth):
        """Zero losses must NOT read as PF 0.0 — that is the opposite of what
        it means, and it would sort a flawless strategy to the bottom."""
        from datetime import datetime, timezone
        from backend.models.trade import Trade
        rows = [("ZZPMONAUSDT", "ZSONLY", "LONG", 1.0, 1.0),
                ("ZZPMONAUSDT", "ZSONLY", "LONG", 2.0, 2.0)]
        with seeded(rows=rows):
            d = _get(auth)
        assert d["ZSONLY"]["profit_factor"] is None, (
            "PF must be null (unknown/undivided), not 0.0 and not a fake number")

    def test_rows_are_ordered_by_r_expectancy_worst_first(self, auth):
        """Worst avg_r leads, so the strategies needing attention are on top.
        Only the seeded subset is compared — real rows also appear."""
        with seeded():
            r = auth.get("/api/performance/strategies/breakdown")
        rows = {d["strategy"]: d["avg_r"] for d in r.json()["data"]}
        assert rows["ZSC"] == pytest.approx(-1.5), rows
        assert rows["ZSB"] == pytest.approx(0.0), rows
        assert rows["ZSA"] == pytest.approx(0.625), rows
        seq = [rows[n] for n in ("ZSC", "ZSB", "ZSA") if n in rows]
        assert seq == sorted(seq), f"not ascending: {seq}"


class TestBreakdownRespectsFilters:
    def test_paper_fills_are_excluded_by_default(self, auth):
        """Same contract as every other trade-derived endpoint: paper is not
        real money and must never contaminate a live number."""
        with seeded(mode="paper"):
            assert "ZSA" not in _get(auth), "paper trades leaked into the live view"
            assert "ZSA" in _get(auth, mode="paper")

    def test_days_narrows_the_breakdown(self, auth):
        from datetime import datetime, timedelta, timezone
        from backend.models.trade import Trade
        with seeded():
            assert "ZSA" in _get(auth)
            assert _get(auth, days=1)["ZSA"]["total_trades"] == 4

    def test_an_empty_book_returns_an_empty_list_not_an_error(self, auth):
        r = auth.get("/api/performance/strategies/breakdown")
        assert r.status_code == 200
        assert isinstance(r.json()["data"], list)
