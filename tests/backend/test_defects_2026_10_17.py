"""Regression tests for three small defects fixed on 2026-10-17 (post-57f5187).

Each class maps to one defect and mixes BEHAVIOURAL tests (the observable
outcome) with SOURCE-CONTRACT tests (structural guards — these defects are
structural, so a behavioural test alone can be satisfied by a re-broken
implementation).

  1. `_signed_request` discarded Binance's error body and leaked the request
     signature into the journal.
  2. A FLATTENED trade never reached `daily_summary` (the -3R circuit breaker
     input) until the 48h exchange-sync recompute.
  3. `/bot/status` hardcoded `open_positions: []`.

No test here touches the network (httpx.MockTransport) and every test that
writes a row deletes it in a `finally` — see tests/AGENTS.md.
"""
import inspect
import logging

import httpx
import pytest

from backend.services.binance_service import BinanceAPIError, BinanceService


def _signed_service(handler) -> BinanceService:
    svc = BinanceService(api_key="SUPERSECRETKEY", api_secret="s", testnet=True)
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=svc._base_url
    )
    return svc


# ---------------------------------------------------------------------------
# 1. _signed_request destroys the error and leaks the signature
# ---------------------------------------------------------------------------

class TestBinanceErrorBodyIsSurfaced:
    """`_signed_request` called `resp.raise_for_status()`, so a 400 surfaced as
    `Client error '400 Bad Request' for url '...'` and Binance's own body —
    `{"code":-2021,"msg":"ReduceOnly Order is rejected..."}` — was never read.
    Diagnosing a live order rejection took a journal dump plus a code read.
    """

    async def test_400_with_a_binance_json_body_surfaces_code_and_msg(self):
        def handler(request):
            return httpx.Response(400, json={
                "code": -2021,
                "msg": "ReduceOnly Order is rejected. Please check your existing "
                       "position and open order.",
            })

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("POST", "fapi/v1/order")
        finally:
            await svc.close()

        assert exc.value.code == -2021, \
            "Binance's own error code must survive; the HTTP status alone is useless"
        assert "ReduceOnly Order is rejected" in str(exc.value), \
            "Binance's message must be in the exception the caller logs"

    async def test_404_with_an_html_body_still_raises_with_the_http_status(self):
        """Binance serves an HTML error page for some 404s — there is no JSON to
        read, so the error must still be raised (never swallowed into `{}`) and
        must fall back to the HTTP status."""
        def handler(request):
            return httpx.Response(404, text="<html><body>404 Not Found</body></html>",
                                  headers={"Content-Type": "text/html"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("GET", "fapi/v1/nonexistent")
        finally:
            await svc.close()

        assert exc.value.code == 404
        assert "404" in str(exc.value)

    async def test_public_request_surfaces_the_binance_body_too(self):
        """The public path had the identical `raise_for_status()` blind spot."""
        def handler(request):
            return httpx.Response(418, json={"code": -1001, "msg": "Internal error."})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._public_request("fapi/v1/klines", {"symbol": "BTCUSDT"})
        finally:
            await svc.close()

        assert exc.value.code == -1001
        assert "Internal error." in str(exc.value)


class TestRequestSignatureNeverLeaks:
    """The signed URL embeds `signature=<hmac>`. httpx builds the
    `HTTPStatusError` message from the URL, so every 4xx wrote credential
    material into `journalctl` (observed live:
    `...&timestamp=1792240296815&signature=48e771c4153b5b92cfdfdee...`)."""

    async def test_no_raised_string_contains_a_signature(self):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            return httpx.Response(400, text="<html>nope</html>",
                                  headers={"Content-Type": "text/html"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(Exception) as exc:
                await svc._signed_request("GET", "fapi/v2/positionRisk")
            rendered = f"{exc.value!r} {exc.value}"
        finally:
            await svc.close()

        # The signature IS sent (that is what signs the request)...
        assert "signature=" in seen["url"]
        # ...but it must not be in anything an operator reads.
        assert "signature=" not in rendered, \
            f"the HMAC signature leaked into the exception: {rendered}"
        assert svc._sign("timestamp=1") not in rendered

    async def test_no_logged_string_contains_a_signature(self, caplog):
        """`logger.error(f"Request failed after 3 attempts: {e}")` writes the
        exception text to the journal — it must be redacted too."""

        def handler(request):
            raise httpx.ConnectError("boom", request=request)

        svc = _signed_service(handler)
        with caplog.at_level(logging.DEBUG, logger="backend.services.binance_service"):
            try:
                with pytest.raises(Exception):
                    await svc._signed_request("GET", "fapi/v2/positionRisk")
            finally:
                await svc.close()

        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert blob, "expected the retry loop to log something"
        assert "signature=" not in blob, \
            f"the HMAC signature leaked into the log: {blob}"
        assert svc._sign("timestamp=1") not in blob

    def test_redaction_is_a_named_helper_that_covers_the_credential_keys(self):
        """Structural guard: redaction must be a reusable helper, not an ad-hoc
        `str.replace` at one call site, and it must cover `apiKey` too."""
        import backend.services.binance_service as bs
        assert hasattr(bs, "redact_url"), "a reusable redaction helper must exist"
        for key in ("signature", "apiKey"):
            assert key in bs._SENSITIVE_QUERY_KEYS, f"redact_url must cover {key}"

        blob = ("https://fapi.binance.com/fapi/v1/order?symbol=BTCUSDT"
                "&apiKey=KEY123&signature=deadbeefcafe&timestamp=1 "
                "X-MBX-APIKEY: KEY123")
        out = bs.redact_url(blob)
        assert "deadbeefcafe" not in out
        assert "KEY123" not in out
        assert "signature=" not in out and "apiKey=" not in out
        assert "symbol=BTCUSDT" in out, "non-secret context must survive redaction"

    def test_both_request_paths_build_their_error_from_the_parsed_body(self):
        """Structural guard against reintroducing `raise_for_status()`, which
        discards Binance's body AND renders the signed URL into the exception."""
        import backend.services.binance_service as bs
        for name in ("_signed_request", "_public_request"):
            body = inspect.getsource(getattr(bs.BinanceService, name))
            assert "raise_for_status()" not in body, (
                f"{name} must not call raise_for_status()")
            assert "_error_from_response(" in body, (
                f"{name} must build its error from the parsed body")
        src = inspect.getsource(bs.BinanceService._error_from_response)
        assert "redact_url(" in src, \
            "the fallback URL embedded in the error must be redacted at the source"

    def test_the_url_logged_is_the_redacted_one_not_the_signed_one(self):
        """Prefer constructing the error with a sanitised URL over post-hoc
        string surgery on the exception message."""
        import backend.services.binance_service as bs
        src = inspect.getsource(bs)
        assert "redact_url(" in src
        assert "str(resp.request.url)" not in src or "redact_url(str(resp.request.url))" in src, \
            "the request URL must be redacted at the point it is read"


class TestBinanceErrorSemanticsUnchanged:
    """The 200-with-code path and the 3x429 exhaustion path have dedicated
    behaviour pinned by `test_review_2026_10_17.py`. Re-asserted here because
    this fix touches the exact lines that implement them."""

    async def test_200_with_string_code_200_is_success(self):
        def handler(request):
            return httpx.Response(200, json={"code": "200", "msg": "success"})

        svc = _signed_service(handler)
        try:
            data = await svc._signed_request("DELETE", "fapi/v1/algoOrder",
                                            {"algoId": 1})
        finally:
            await svc.close()
        assert data["code"] == "200"

    async def test_200_with_a_negative_code_still_raises(self):
        def handler(request):
            return httpx.Response(200, json={"code": -2011, "msg": "Unknown order"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("GET", "fapi/v1/algoOrder")
        finally:
            await svc.close()
        assert exc.value.code == -2011

    async def test_200_with_an_unparseable_code_is_treated_as_success(self):
        def handler(request):
            return httpx.Response(200, json={"code": None, "msg": "success"})

        svc = _signed_service(handler)
        try:
            data = await svc._signed_request("DELETE", "fapi/v1/algoOrder")
        finally:
            await svc.close()
        assert data["msg"] == "success"

    async def test_three_429s_still_raise_rate_limit_not_empty_dict(self):
        calls = {"n": 0}

        def handler(request):
            calls["n"] += 1
            return httpx.Response(429, headers={"Retry-After": "0"})

        svc = _signed_service(handler)
        try:
            with pytest.raises(BinanceAPIError) as exc:
                await svc._signed_request("GET", "fapi/v2/positionRisk")
        finally:
            await svc.close()

        assert calls["n"] == 3
        assert exc.value.code == -1003


# ---------------------------------------------------------------------------
# 2. A flattened trade never reached daily_summary
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402
from decimal import Decimal  # noqa: E402

from sqlalchemy import delete, select, text  # noqa: E402

from backend.models.position import Position as PositionModel  # noqa: E402

DAILY_SYMBOL = "ZZDAILYUSDT"


def _exchange_sync_recompute_sql():
    """The 48h recompute statement, EXTRACTED from `exchange_sync` at test time.

    This used to be a hand-copy of the statement. It drifted: when the latch was
    rewritten to bind the configured limit, the copy kept `total_r <= -3.0` and
    still passed, because this test only pins the SET-vs-accumulate property. The
    next reader would have taken it for the current SQL. Extracting it makes that
    class of drift impossible — a statement that stops parsing or stops binding
    fails here.
    """
    import backend.routers.settings as settings_router
    src = inspect.getsource(settings_router.exchange_sync)
    start = src.index('text("""') + len('text("""')
    return src[start:src.index('"""', start)]


class RecordingDailyReset:
    """Stands in for DailyResetService and records update_daily_summary calls."""

    def __init__(self):
        self.calls = []

    async def update_daily_summary(self, pnl_r, pnl_usdt):
        self.calls.append((float(pnl_r), float(pnl_usdt)))


class _IncomeBinance:
    """Minimal Binance stand-in: only the income lookup `_close_position_row`
    makes before writing the Trade row."""

    def __init__(self, total_pnl=-2.5):
        self.total_pnl = total_pnl

    async def get_position_income(self, symbol, income_type, start_time=None):
        return {"totalPnl": str(self.total_pnl)}



def _own_session_factory():
    """A private engine for this test's DB work.

    The shared `AsyncSessionLocal` pool is bound to whichever event loop last
    used it and every pytest-asyncio test gets a fresh loop, so a pooled
    connection reused across two of these tests raises "attached to a different
    loop" (see the same reasoning in test_trade_log_totals.py). A per-test engine
    keeps every connection inside the one loop that created it.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from backend.config import settings as cfg
    engine = create_async_engine(cfg.DATABASE_URL)
    return async_sessionmaker(engine, expire_on_commit=False), engine


def _open_position():
    from backend.models.position import Position
    return Position(
        symbol=DAILY_SYMBOL, side="LONG", entry_price=Decimal("100"),
        quantity=Decimal("10"), leverage=10,
        stop_loss=Decimal("95"), take_profit=Decimal("118"),
        initial_risk_distance=Decimal("5"),
        strategy_type="3CANDLE", status="OPEN", mode="live",
    )


class TestFlattenNotifiesTheDailyAccumulator:
    """`_close_position_row` writes a `trades` row (every performance endpoint
    reads `trades` since the 2026-10-17 mode-filter fix) but never notified
    `DailyResetService`, so the R only reached the daily accumulator via the 48h
    exchange-sync recompute. Between the flatten and that recompute the day's
    loss total — the input to the -3R circuit breaker — understated reality.

    WHY THE CALL IS SAFE (the trap, resolved by reading the code): 
    `update_daily_summary` INCREMENTS — `total_trades + 1`,
    `total_pnl_r + pnl_r` — it never reads `trades`. Its only other caller is
    `PositionManager._close_position`, a different close path that cannot also
    flatten this row. The 48h recompute lives in `routers/settings.exchange_sync`
    and does NOT call it: it runs one raw-SQL `UPDATE daily_summary SET ... FROM
    (SELECT ... FROM trades GROUP BY DATE(closed_at))`, which SETS absolute
    aggregates and is therefore idempotent — it overwrites, it never adds. The
    incremental call and the recompute are compatible by construction.
    """

    async def test_flatten_notifies_the_daily_service_exactly_once_with_the_right_r(self):
        from backend.models.trade import Trade
        from backend.services.strategy_service import StrategyService

        daily = RecordingDailyReset()
        Session, engine = _own_session_factory()
        try:
            async with Session() as session:
                row = _open_position()
                session.add(row)
                await session.commit()
                position_id = row.id

            strategy = StrategyService(
                risk_svc=None, order_svc=None,
                binance=_IncomeBinance(total_pnl=-2.5),
                session_factory=Session, settings={}, daily_reset=daily,
            )
            strategy._mode = "live"
            await strategy._close_position_row(position_id)

            assert len(daily.calls) == 1, \
                f"the flatten must notify DailyResetService exactly once, got {daily.calls}"
            pnl_r, pnl_usdt = daily.calls[0]
            # -2.5 USDT against a frozen risk of 5 * 10 = 50 USDT => -0.05R
            assert pnl_usdt == pytest.approx(-2.5)
            assert pnl_r == pytest.approx(-0.05)
        finally:
            async with Session() as session:
                await session.execute(delete(Trade).where(Trade.symbol == DAILY_SYMBOL))
                await session.execute(
                    delete(PositionModel).where(PositionModel.symbol == DAILY_SYMBOL))
                await session.commit()
            await engine.dispose()

    async def test_a_second_close_of_the_same_row_does_not_notify_again(self):
        """`_close_position_row` is idempotent (it bails unless the row is still
        OPEN), so the accumulator must not be incremented twice for one loss."""
        from backend.models.trade import Trade
        from backend.services.strategy_service import StrategyService

        daily = RecordingDailyReset()
        Session, engine = _own_session_factory()
        try:
            async with Session() as session:
                row = _open_position()
                session.add(row)
                await session.commit()
                position_id = row.id

            strategy = StrategyService(
                risk_svc=None, order_svc=None, binance=_IncomeBinance(),
                session_factory=Session, settings={}, daily_reset=daily,
            )
            strategy._mode = "live"
            await strategy._close_position_row(position_id)
            await strategy._close_position_row(position_id)

            assert len(daily.calls) == 1, daily.calls
        finally:
            async with Session() as session:
                await session.execute(delete(Trade).where(Trade.symbol == DAILY_SYMBOL))
                await session.execute(
                    delete(PositionModel).where(PositionModel.symbol == DAILY_SYMBOL))
                await session.commit()
            await engine.dispose()

    async def test_flatten_then_48h_recompute_does_not_double_count(self):
        """Real DB, real DailyResetService, then the VERBATIM recompute statement
        `routers/settings.exchange_sync` runs. Were both additive, this would
        read 2x the flattened R — the trap this defect invites."""
        from backend.core.daily_reset import DailyResetService
        from backend.models.daily_summary import DailySummary
        from backend.models.trade import Trade
        from backend.services.strategy_service import StrategyService

        today = datetime.now(timezone.utc).date()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
        Session, engine = _own_session_factory()
        try:
            # Today's summary is shared state. test_daily_reset.py already wipes
            # it in the same way, so a clean slate is the established convention.
            async with Session() as session:
                await session.execute(
                    delete(DailySummary).where(DailySummary.date == today))
                await session.commit()

            async with Session() as session:
                row = _open_position()
                session.add(row)
                await session.commit()
                position_id = row.id

            daily = DailyResetService(session_factory=Session,
                                      settings={"daily_loss_limit_r": -3.0})
            strategy = StrategyService(
                risk_svc=None, order_svc=None, binance=_IncomeBinance(total_pnl=-2.5),
                session_factory=Session, settings={}, daily_reset=daily,
            )
            strategy._mode = "live"
            await strategy._close_position_row(position_id)

            after_increment = await daily.get_daily_stats()
            assert after_increment["total_trades"] == 1
            assert after_increment["total_pnl_r"] == pytest.approx(-0.05)

            async with Session() as session:
                await session.execute(text(_exchange_sync_recompute_sql()),
                                      {"cutoff": cutoff, "limit_r": 3.0})
                await session.commit()

            after_recompute = await daily.get_daily_stats()
            assert after_recompute["total_pnl_r"] == pytest.approx(-0.05), \
                "the 48h recompute must overwrite, never add"
            assert after_recompute["total_trades"] == 1
            assert after_recompute["used_r"] == pytest.approx(0.05)
        finally:
            async with Session() as session:
                await session.execute(delete(Trade).where(Trade.symbol == DAILY_SYMBOL))
                await session.execute(
                    delete(PositionModel).where(PositionModel.symbol == DAILY_SYMBOL))
                await session.execute(
                    delete(DailySummary).where(DailySummary.date == today))
                await session.commit()
            await engine.dispose()

    def test_update_daily_summary_is_an_increment_not_a_recompute(self):
        """The trap, pinned structurally: if this ever becomes a recompute — or
        ever starts being called from a recompute path — the wiring above must be
        re-examined for double counting."""
        from backend.core.daily_reset import DailyResetService
        src = inspect.getsource(DailyResetService.update_daily_summary)
        assert "+ pnl_r" in src and "+ pnl_usdt" in src, \
            "update_daily_summary is an ACCUMULATOR (+=), not a recompute"
        assert "select(Trade" not in src and "models.trade" not in src, \
            "it must not read the `trades` table; recomputation lives in exchange_sync"
        assert "DailySummary.date == today" in src

    def test_the_exchange_sync_recompute_sets_absolute_aggregates(self):
        """exchange_sync must keep SETting daily_summary from a `trades`
        aggregate — that is exactly what makes the incremental call safe."""
        import backend.routers.settings as settings_router
        src = inspect.getsource(settings_router.exchange_sync)
        assert "UPDATE daily_summary SET" in src
        assert "FROM (SELECT DATE(closed_at) as d, COUNT(*)" in src
        assert "update_daily_summary" not in src, \
            "exchange_sync must never call the accumulator — that would double count"

    def test_close_position_row_notifies_the_daily_service(self):
        """Source-contract: the notification lives in `_close_position_row`,
        AFTER the Trade commit, and a failure there must not undo the Trade."""
        from backend.services.strategy_service import StrategyService
        src = inspect.getsource(StrategyService._close_position_row)
        assert "update_daily_summary(" in src, \
            "a recorded FLATTENED trade must reach daily_summary immediately"
        assert src.index("await session.commit()") < src.index("update_daily_summary("), \
            "notify only after the Trade row is durably committed"
        # The notification must be defensively wrapped: it runs inside a method
        # whose whole contract is "never undo the recorded Trade".
        tail = src[src.index("update_daily_summary("):]
        assert "except Exception" in tail, \
            "a failed daily notification must not propagate into the close path"

    def test_strategy_service_accepts_an_injected_daily_reset(self):
        """Minimal wiring: constructor injection, matching how PositionManager
        receives it — no global, no module-level singleton."""
        from backend.services.strategy_service import StrategyService
        sig = inspect.signature(StrategyService.__init__)
        assert "daily_reset" in sig.parameters, \
            "DailyResetService must be injectable (PositionManager takes it the same way)"
        assert "daily_reset" in inspect.getsource(StrategyService.__init__)

    def test_main_wires_the_same_daily_reset_into_both_components(self):
        """One instance, injected into PositionManager AND StrategyService —
        two separate accumulators would report different daily R totals."""
        import backend.main as main_mod
        src = inspect.getsource(main_mod.init_bot)
        assert src.count("DailyResetService(") == 1, \
            "DailyResetService must be built ONCE and shared"
        strategy_block = src.split("strategy_service = StrategyService(")[1].split(")")[0]
        assert "daily_reset=daily_reset" in strategy_block
        assert "daily_reset.bot = engine" in src, \
            "the -3R stop must still be able to stop the engine"


# ---------------------------------------------------------------------------
# 3. /bot/status hardcoded open_positions: []
# ---------------------------------------------------------------------------

BOT_SYMBOL_A = "ZZBOTAAAUSDT"
BOT_SYMBOL_B = "ZZBOTBBBUSDT"


def _fake_engine(session_factory):
    """A BotEngine with everything stubbed except what get_status touches."""
    from backend.core.bot_engine import BotEngine
    from backend.core.daily_reset import DailyResetService

    engine = BotEngine(
        settings={}, binance=None, signal_service=None, strategy_service=None,
        risk_service=None, order_service=None, alert_service=None,
        daily_reset=DailyResetService(
            session_factory=session_factory, settings={"daily_loss_limit_r": -3.0}),
        session_factory=session_factory,
    )
    return engine


class _StaticPM:
    """PositionManager stand-in exposing only the snapshot cache."""

    def __init__(self, cached):
        self._cached = cached
        self.db_reads = 0

    def _cached_open_positions(self):
        return self._cached


class TestBotStatusReportsRealOpenPositions:
    """`BotEngine.get_status` returned a literal `"open_positions": []`. Observed
    live: the endpoint reported zero while three positions were open, so every
    consumer that is not the dashboard was told the account was flat."""

    async def test_status_reports_the_real_open_positions(self):
        from backend.models.position import Position as Pos
        from sqlalchemy import delete, select

        Session, engine = _own_session_factory()
        try:
            async with Session() as session:
                await session.execute(delete(Pos).where(
                    Pos.symbol.in_([BOT_SYMBOL_A, BOT_SYMBOL_B])))
                session.add_all([
                    Pos(symbol=BOT_SYMBOL_A, side="LONG", entry_price=Decimal("100"),
                        quantity=Decimal("1"), leverage=10,
                        stop_loss=Decimal("95"), take_profit=Decimal("118"),
                        initial_risk_distance=Decimal("5"),
                        strategy_type="3CANDLE", status="OPEN", mode="live"),
                    Pos(symbol=BOT_SYMBOL_B, side="SHORT", entry_price=Decimal("200"),
                        quantity=Decimal("2"), leverage=10,
                        stop_loss=Decimal("210"), take_profit=Decimal("170"),
                        initial_risk_distance=Decimal("10"),
                        strategy_type="FVG", status="OPEN", mode="live"),
                    # CLOSED rows must not be reported.
                    Pos(symbol=BOT_SYMBOL_B, side="SHORT", entry_price=Decimal("210"),
                        quantity=Decimal("2"), leverage=10,
                        strategy_type="FVG", status="CLOSED", mode="live",
                        closed_at=datetime.now(timezone.utc)),
                ])
                await session.commit()

            bot = _fake_engine(Session)
            status = await bot.get_status()

            reported = status["open_positions"]
            assert {p["symbol"] for p in reported} == {BOT_SYMBOL_A, BOT_SYMBOL_B}, \
                f"status must report the real OPEN positions, got {reported}"
            assert len(reported) == 2, "a CLOSED row must not be reported"
            sides = {p["symbol"]: p["side"] for p in reported}
            assert sides == {BOT_SYMBOL_A: "LONG", BOT_SYMBOL_B: "SHORT"}

            # JSON-serialisable — FastAPI must not 500 on a Decimal or datetime.
            import json
            json.dumps(status)
        finally:
            async with Session() as session:
                await session.execute(delete(Pos).where(
                    Pos.symbol.in_([BOT_SYMBOL_A, BOT_SYMBOL_B])))
                await session.commit()
            await engine.dispose()

    async def test_status_is_empty_only_when_nothing_is_open(self):
        from backend.models.position import Position as Pos
        from sqlalchemy import delete

        Session, engine = _own_session_factory()
        try:
            async with Session() as session:
                await session.execute(delete(Pos).where(
                    Pos.symbol.in_([BOT_SYMBOL_A, BOT_SYMBOL_B])))
                await session.commit()
            status = await _fake_engine(Session).get_status()
            assert status["open_positions"] == []
        finally:
            await engine.dispose()

    async def test_status_prefers_the_snapshot_cache_over_a_db_read(self):
        """The tick path must read a snapshot, not the database — and
        `get_status` is reachable from a request handler, so it should not add
        a query per call when the cache already answers it."""
        from backend.models.position import Position as Pos
        from sqlalchemy import delete, func, select

        Session, engine = _own_session_factory()
        try:
            async with Session() as session:
                await session.execute(delete(Pos).where(
                    Pos.symbol.in_([BOT_SYMBOL_A, BOT_SYMBOL_B])))
                session.add(Pos(
                    symbol=BOT_SYMBOL_A, side="LONG", entry_price=Decimal("100"),
                    quantity=Decimal("1"), leverage=10,
                    strategy_type="3CANDLE", status="OPEN", mode="live"))
                await session.commit()

            bot = _fake_engine(Session)
            # A populated cache must be used verbatim — including for a symbol
            # the cache and the DB can disagree about.
            bot.position_manager = _StaticPM([
                {"id": 999, "symbol": BOT_SYMBOL_A, "side": "LONG",
                 "entry_price": 100.0, "quantity": 1.0,
                 "stop_loss": 95.0, "take_profit": 118.0, "signal_id": None,
                 "opened_at": None, "initial_risk_distance": 5.0},
            ])
            status = await bot.get_status()
            assert [p["symbol"] for p in status["open_positions"]] == [BOT_SYMBOL_A]
            assert status["open_positions"][0]["id"] == 999

            # An empty cache falls back to the DB (the cache is only populated
            # after the first 30s pass, and `_cached_open_positions` cannot tell
            # "never refreshed" from "genuinely flat").
            bot.position_manager = _StaticPM([])
            status = await bot.get_status()
            assert [p["symbol"] for p in status["open_positions"]] == [BOT_SYMBOL_A]
        finally:
            async with Session() as session:
                await session.execute(delete(Pos).where(
                    Pos.symbol.in_([BOT_SYMBOL_A, BOT_SYMBOL_B])))
                await session.commit()
            await engine.dispose()

    def test_get_status_no_longer_hardcodes_an_empty_list(self):
        """Source-contract: the literal that made the endpoint lie."""
        from backend.core.bot_engine import BotEngine
        src = inspect.getsource(BotEngine.get_status)
        assert '"open_positions": []' not in src, \
            "get_status must report the real open positions, not a literal []"
        assert "open_positions" in src

    def test_the_fallback_is_conditional_on_the_cache(self):
        """Structural: the DB read must be the FALLBACK, not the only path —
        otherwise `get_status` adds a query per request."""
        from backend.core.bot_engine import BotEngine
        src = inspect.getsource(BotEngine.get_status)
        assert "_open_position_summaries()" in src, \
            "get_status must build the list from the real position source"
        summaries = inspect.getsource(BotEngine._open_position_summaries)
        assert "_cached_open_positions()" in summaries, \
            "the PositionManager snapshot cache must be preferred (no query per call)"
        assert "_open_positions_from_db()" in summaries, \
            "an empty cache must fall back to a query (documented, not silent)"
        # The cache branch must come FIRST: only an empty/unavailable cache
        # justifies the query.
        assert summaries.index("_cached_open_positions()") < \
            summaries.index("_open_positions_from_db()")
        helper = inspect.getsource(BotEngine._open_positions_from_db)
        assert 'Position.status == "OPEN"' in helper, \
            "the fallback must filter on status OPEN"
        assert "float(" in helper, "rows must be coerced to JSON-serialisable floats"
