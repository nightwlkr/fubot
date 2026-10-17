"""Trade Log summary tiles: total P&L, wins and losses.

The page showed only five tiles — total trades, win rate, avg R, best, worst —
all derived from `pnl_r`. Total P&L in dollars, the raw win count and the loss
count were absent, which is the minimum set an operator actually checks a
trade log against. `/trades/stats` already loads every filtered trade and
discarded everything but `pnl_r`; this asserts the totals it now derives.

The reconciliation invariant is the load-bearing one: wins + losses +
breakevens must equal total_trades for any filter combination. Breakevens are
neither wins nor losses, so without this the three tiles silently fail to add
up and the page reads as broken.
"""
import contextlib
import pytest

# Symbols are prefixed ZZT so they cannot collide with real or test-suite rows.
SYMS = ["ZZTALPHAUSDT", "ZZTBETAUSDT", "ZZTGAMMAUSDT"]


def _trade(symbol, pnl_r, pnl_usdt, *, side="LONG", mode="live"):
    from datetime import datetime, timezone
    from backend.models.trade import Trade
    return Trade(
        symbol=symbol, side=side,
        entry_price=100, exit_price=100, quantity=1,
        pnl_usdt=pnl_usdt, pnl_r=pnl_r,
        exit_reason="MANUAL_CLOSE", strategy_type="3CANDLE",
        mode=mode,
        closed_at=datetime.now(timezone.utc),
    )


@contextlib.contextmanager
def seeded(rows):
    """Seed a known ledger, then purge — always, including on failure.

    The suite must stay idempotent and must not leak rows into the dev
    database (see tests/AGENTS.md: four `_close_position` tests once
    accumulated 238 phantom trades this way and dominated the trades table).

    Uses its OWN engine with NullPool: the shared asyncpg pool is bound to
    whichever loop last used it, and each run_async call gets a fresh loop, so
    a pooled connection reused across them raises "attached to a different
    loop". NullPool opens and closes per use, so nothing survives the loop.
    """
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool
    from backend.config import settings as cfg
    from backend.models.trade import Trade
    from conftest import run_async

    def _session():
        engine = create_async_engine(cfg.DATABASE_URL, poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False), engine

    async def _load(rs):
        Session, engine = _session()
        try:
            async with Session() as db:
                await db.execute(delete(Trade).where(Trade.symbol.in_(SYMS)))
                if rs:
                    db.add_all(rs)
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
        run_async(_load(rows))   # wipes first, so a prior leak can't skew it
        yield
    finally:
        run_async(_purge())


def _rows(ledger=None, exclude_paper=True):
    ledger = LEDGER if ledger is None else ledger
    return [
        _trade(s, r, u, mode="paper" if (s == "ZZTGAMMAUSDT" and exclude_paper) else "live")
        for s, r, u in ledger
    ]


@pytest.fixture()
def auth(client, test_user):
    """Log in so the endpoint is reachable — /trades/stats is authenticated."""
    from conftest import TEST_PASSWORD
    res = client.post("/api/auth/login",
                      json={"username": test_user.username, "password": TEST_PASSWORD})
    assert res.status_code == 200, res.text
    # The login cookie is set with secure=True, which httpx will not replay over
    # plain http://testserver — mirror the token from the response body instead.
    client.cookies.set("fubot_token", res.json()["token"])
    return client


def _get(auth, **params):
    r = auth.get("/api/trades/stats", params=params)
    assert r.status_code == 200, r.text
    return r.json()


# ALPHA 2 wins + 1 loss + 1 breakeven, BETA 1 loss, GAMMA 1 win (paper only).
# GAMMA carries a huge pnl_usdt so any leak of paper fills into a live total is
# impossible to miss.
LEDGER = [
    ("ZZTALPHAUSDT", 2.0, 20.00),
    ("ZZTALPHAUSDT", 1.0, 10.00),
    ("ZZTALPHAUSDT", -1.0, -10.00),
    ("ZZTALPHAUSDT", 0.0, 0.00),
    ("ZZTBETAUSDT", -2.0, -5.00),
    ("ZZTGAMMAUSDT", 3.0, 999.00),
]
LIVE_LEDGER = [t for t in LEDGER if t[0] != "ZZTGAMMAUSDT"]


class TestStatsExposeTheNewTotals:
    def test_the_response_schema_carries_every_new_field(self):
        from backend.schemas.trade import TradeStatsResponse
        for field in ("wins", "losses", "breakevens", "total_pnl_usdt"):
            assert field in TradeStatsResponse.model_fields, (
                f"/trades/stats must report {field} — the Trade Log tile "
                f"renders it")

    def test_it_reports_wins_losses_breakevens_and_total_pnl(self, auth):
        with seeded(_rows()):
            d = _get(auth, symbol="ZZTALPHAUSDT")
        assert d["wins"] == 2, d
        assert d["losses"] == 1, d
        assert d["breakevens"] == 1, d
        assert float(d["total_pnl_usdt"]) == pytest.approx(20.0), d   # 20+10-10+0

    def test_the_three_tiles_reconcile_with_the_trade_count(self, auth):
        """The load-bearing invariant. Breakevens are neither a win nor a loss,
        so wins+losses alone will NOT equal total_trades whenever one exists —
        which is what makes the "+N BE" suffix on the Wins tile necessary
        rather than decorative."""
        with seeded(_rows(LIVE_LEDGER)):
            for symbol in (None, "ZZTALPHAUSDT", "ZZTBETAUSDT"):
                d = _get(auth, symbol=symbol)
                assert d["wins"] + d["losses"] + d["breakevens"] == d["total_trades"], (
                    f"symbol={symbol}: {d['wins']}W + {d['losses']}L + "
                    f"{d['breakevens']}BE != {d['total_trades']} trades")

    def test_a_breakeven_is_counted_as_neither_win_nor_loss(self, auth):
        with seeded([_trade("ZZTBETAUSDT", 0.0, 0.0)]):
            d = _get(auth, symbol="ZZTBETAUSDT")
        assert (d["wins"], d["losses"], d["breakevens"]) == (0, 0, 1), d
        assert float(d["total_pnl_usdt"]) == 0.0, d

    def test_a_negative_total_keeps_its_sign(self, auth):
        with seeded([_trade("ZZTBETAUSDT", -1.0, -7.50),
                     _trade("ZZTBETAUSDT", -2.0, -12.50)]):
            d = _get(auth, symbol="ZZTBETAUSDT")
        assert float(d["total_pnl_usdt"]) == pytest.approx(-20.0), d


class TestTotalsRespectEveryFilter:
    """The cards sit directly above the filtered table. A card that ignores a
    filter reads as a broken page — the same defect fixed twice here."""

    def test_the_symbol_filter_narrows_the_totals(self, auth):
        with seeded(_rows(LIVE_LEDGER)):
            assert _get(auth, symbol="ZZTALPHAUSDT")["total_trades"] == 4
            assert _get(auth, symbol="ZZTBETAUSDT")["total_trades"] == 1

    def test_the_outcome_filter_agrees_with_the_tiles(self, auth):
        """Outcome=Win showing a non-zero Losses tile is exactly the confusion
        the shared-filter work was meant to prevent."""
        with seeded(_rows(LIVE_LEDGER)):
            win = _get(auth, symbol="ZZTALPHAUSDT", outcome="win")
            assert (win["wins"], win["losses"]) == (2, 0), win
            loss = _get(auth, symbol="ZZTALPHAUSDT", outcome="loss")
            assert (loss["wins"], loss["losses"]) == (0, 1), loss

    def test_the_side_filter_narrows_the_totals(self, auth):
        with seeded([_trade("ZZTALPHAUSDT", 1.0, 10.0, side="LONG"),
                     _trade("ZZTALPHAUSDT", -1.0, -10.0, side="SHORT")]):
            d = _get(auth, symbol="ZZTALPHAUSDT", side="SHORT")
        assert d["total_trades"] == 1, d

    def test_paper_fills_never_leak_into_live_totals(self, auth):
        """`mode` defaults to live. A paper fill is not real money — the same
        reasoning the endpoint already applies to win rate."""
        with seeded([_trade("ZZTBETAUSDT", 1.0, 50.0, mode="paper")]):
            assert _get(auth, symbol="ZZTBETAUSDT")["total_trades"] == 0
            assert _get(auth, symbol="ZZTBETAUSDT", mode="paper")["total_trades"] == 1

    def test_the_days_range_narrows_the_totals(self, auth):
        from datetime import datetime, timedelta, timezone
        old = _trade("ZZTBETAUSDT", -1.0, -10.0)
        old.closed_at = datetime.now(timezone.utc) - timedelta(days=40)
        with seeded([old, _trade("ZZTBETAUSDT", 1.0, 10.0)]):
            assert _get(auth, symbol="ZZTBETAUSDT")["total_trades"] == 2
            assert _get(auth, symbol="ZZTBETAUSDT", days=7)["total_trades"] == 1


class TestTotalsSurviveDegenerateInput:
    def test_an_empty_result_set_is_all_zeroes_not_an_error(self, auth):
        d = _get(auth, symbol="ZZTNOSUCHUSDT")
        assert (d["total_trades"], d["wins"], d["losses"], d["breakevens"]) == (0, 0, 0, 0)
        assert float(d["total_pnl_usdt"]) == 0.0

    def test_the_usdt_total_is_a_json_number_not_a_decimal(self, auth):
        """pnl_r/pnl_usdt are Numeric and come back as Decimal. Serialising a
        Decimal (or leaving it un-coerced through sum()) is how this endpoint
        already had a TypeError hazard documented for pnl_r; the usdt sum must
        be guarded identically and must serialise as a float."""
        with seeded([_trade("ZZTBETAUSDT", 1.0, 10.0),
                     _trade("ZZTBETAUSDT", 0.0, 0.0)]):
            r = auth.get("/api/trades/stats", params={"symbol": "ZZTBETAUSDT"})
            assert r.status_code == 200, r.text
            assert '"total_pnl_usdt": 10.0' in r.text.replace(
                '"total_pnl_usdt":10.0', '"total_pnl_usdt": 10.0'), r.text
            assert float(r.json()["total_pnl_usdt"]) == pytest.approx(10.0)


class TestFrontendRendersTheNewTiles:
    def test_the_page_reads_every_new_field(self):
        import pathlib
        src = pathlib.Path("frontend/src/pages/TradeLog.tsx").read_text()
        for field in ("wins", "losses", "breakevens", "total_pnl_usdt"):
            assert f"stats.{field}" in src, f"TradeLog.tsx must render stats.{field}"

    def test_the_tile_interface_matches_the_backend_schema(self):
        """TradeStats is declared TWICE — in types/index.ts and again locally in
        TradeLog.tsx. A field added to one and not the other type-checks as
        `any` on the other, so the drift is silent."""
        import pathlib, re
        idx = pathlib.Path("frontend/src/types/index.ts").read_text()
        page = pathlib.Path("frontend/src/pages/TradeLog.tsx").read_text()

        def fields(src, marker):
            body = src.split(marker, 1)[1]
            body = body[:body.index("}")]
            return set(re.findall(r"^\s*(\w+)\??:", body, re.M))

        a = fields(idx, "export interface TradeStats")
        b = fields(page, "interface TradeStats")
        assert a == b, (
            f"TradeStats drift: types/index.ts {sorted(a)} vs "
            f"TradeLog.tsx {sorted(b)}")
        for field in ("wins", "losses", "breakevens", "total_pnl_usdt"):
            assert field in a and field in b, f"{field} missing from a TradeStats type"
