"""SignalService pipeline tests — mocked Binance candles, real database persistence."""
import asyncio
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models.signal import Signal as SignalModel
from backend.services.signal_service import SignalService

SYMBOL = "TESTUSDT"  # never a real seed symbol — cleanup is trivial


def flat_buy_candle(i):
    """Neutral-trend candle whose close sits near the high → BUY-dominant volume."""
    return _candle(100, 101, 99, 100.9, i)


def flat_doji_candle(i):
    """Tiny body range → ~50/50 volume → volume filter fails."""
    return _candle(100, 100.5, 99.5, 100, i)


def _candle(o, h, l, c, i):
    return {"open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": 100.0, "close_time": datetime(2026, 1, 1, 0, i % 60)}


def fvg_tail(candles, final_close=None):
    """Insert a bullish FVG pattern (c1.high=106, c3.low=110 gap) a few candles
    from the end, and optionally force the FINAL close to set the trend bias.

    detect_fvg runs over the whole 1h series (FVG moved off 5m where gaps never
    form), but analyze_trend_bias reads closes[-1] vs sma_50. Placing the FVG at
    [n-6..n-4] keeps it detectable without corrupting the trend; final_close
    (> sma_50 ≈ 101) forces BULLISH, (< 100.5) forces BEARISH.

    The FVG candidate is LONG, so tests that need the signal to PASS the trend
    gate set final_close high (BULLISH); the counter-trend test sets it low.
    """
    n = len(candles)
    # place the 3-candle FVG at positions [n-6, n-5, n-4]
    candles[n - 6] = _candle(105, 106, 100, 101, n - 6)
    candles[n - 5] = _candle(107, 112, 107, 111, n - 5)
    candles[n - 4] = _candle(111, 115, 110, 114, n - 4)
    if final_close is not None:
        # final candle closes clearly above sma_50 → BULLISH (LONG aligned)
        candles[-1] = _candle(100, final_close + 1, 99, final_close, n - 1)
    return candles


def make_klines(kind="flat"):
    if kind == "flat":
        # FVG on 1h; final close high → BULLISH so the LONG FVG passes the
        # trend gate (the neutral-gate case is a separate fixture/test).
        one_h = fvg_tail([flat_buy_candle(i) for i in range(200)], final_close=115)
        five_m = [flat_buy_candle(i) for i in range(200)]
        fifteen_m = [flat_buy_candle(i) for i in range(200)]
    elif kind == "bearish_1h":
        # final close below sma_50 → BEARISH, so the LONG FVG is counter-trend
        # skipped (the bearish series ends near 1, sma_50 ≈ 25)
        one_h = fvg_tail(
            [_candle(200 - i, 200 - i + 1, 200 - i - 1, 200 - i, i) for i in range(200)],
            final_close=0.5)
        five_m = [flat_buy_candle(i) for i in range(200)]
        fifteen_m = [flat_buy_candle(i) for i in range(200)]
    elif kind == "doji_volume":
        # BULLISH trend (so LONG isn't counter-trend), but doji 1h volume →
        # the volume filter must reject it.
        one_h = fvg_tail([flat_doji_candle(i) for i in range(200)], final_close=115)
        five_m = [flat_doji_candle(i) for i in range(200)]
        fifteen_m = [flat_buy_candle(i) for i in range(200)]
    elif kind == "neutral_1h":
        # final close ≈ sma_50 (101.3, within 0.1%) → NEUTRAL (neutral gate test)
        one_h = fvg_tail([flat_buy_candle(i) for i in range(200)], final_close=101.3)
        five_m = [flat_buy_candle(i) for i in range(200)]
        fifteen_m = [flat_buy_candle(i) for i in range(200)]
    else:
        raise ValueError(kind)
    return {"1h": one_h, "5m": five_m, "15m": fifteen_m}


class FakeBinance:
    def __init__(self, data):
        self.data = data

    async def get_klines(self, symbol, interval, limit=200):
        return self.data[interval][:limit]


async def _scan_and_fetch(kind, rr_min=3.0, svc_settings=None):
    """Run one scan on a dedicated engine, fetch persisted rows, dispose.

    Returns (in_memory_signals, persisted_rows_as_dicts).
    """
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        # clean slate — leftover rows (e.g. from a crashed run) would break counts
        async with Session() as db:
            await db.execute(delete(SignalModel).where(SignalModel.symbol == SYMBOL))
            await db.commit()
        svc = SignalService(session_factory=Session, binance=FakeBinance(make_klines(kind)),
                            settings=svc_settings or {"rr_min": rr_min})
        signals = await svc.scan_symbol(SYMBOL)
        async with Session() as db:
            rows = (await db.execute(
                select(SignalModel).where(SignalModel.symbol == SYMBOL))).scalars().all()
            persisted = [
                {"strategy_type": r.strategy_type, "direction": r.direction,
                 "status": r.status, "skip_reason": r.skip_reason,
                 "volume_confirmed": r.volume_confirmed,
                 "stop_loss": float(r.stop_loss), "take_profit": float(r.take_profit),
                 "rr_ratio": float(r.rr_ratio)}
                for r in rows
            ]
            return signals, persisted
    finally:
        await engine.dispose()


async def _cleanup():
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            await db.execute(delete(SignalModel).where(SignalModel.symbol == SYMBOL))
            await db.commit()
    finally:
        await engine.dispose()


def _run(coro):
    """Run a coroutine on a fresh loop, preserving the thread's current loop.

    asyncio.run() leaves set_event_loop(None) behind, which breaks the next
    pytest-asyncio test depending on collection order — restore it instead.
    """
    policy = asyncio.get_event_loop_policy()
    try:
        prev = policy.get_event_loop()
    except RuntimeError:
        prev = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(prev)
        loop.close()


def test_strategy_toggle_gates_the_scan():
    """The strategy_*_enabled settings existed in the Settings API but were
    never consumed — a 'disabled' strategy kept firing (DECISION.md D1:
    FVG produced 15.6% targets and -0.397R/trade live). Disabling a strategy
    must remove its candidates entirely."""
    async def scenario():
        # FVG off: the flat fixture's FVG tail must NOT yield a candidate
        signals, persisted = await _scan_and_fetch(
            "flat", svc_settings={"rr_min": 3.0, "trade_neutral_markets": True,
                                  "trade_min_trend_strength": "WEAK",
                                  "strategy_fvg_enabled": False})
        try:
            assert not [s for s in signals if s.strategy_type == "FVG"], \
                "FVG fired while strategy_fvg_enabled=false"
            assert not [r for r in persisted if r["strategy_type"] == "FVG"]
        finally:
            await _cleanup()
    _run(scenario())

    async def scenario_all_off():
        # all three off: no candidates at all
        signals, persisted = await _scan_and_fetch(
            "flat", svc_settings={"rr_min": 3.0, "trade_neutral_markets": True,
                                  "trade_min_trend_strength": "WEAK",
                                  "strategy_fvg_enabled": False,
                                  "strategy_3candle_enabled": False,
                                  "strategy_choch_enabled": False})
        try:
            assert signals == [] and persisted == [], \
                f"strategies disabled but candidates still generated: {[s.strategy_type for s in signals]}"
        finally:
            await _cleanup()
    _run(scenario_all_off())


def test_scan_persists_pending_fvg_signal():
    async def scenario():
        # The fvg_tail pattern on 1h tips the SMA → BULLISH/WEAK; allow neutral
        # and a WEAK min-strength so this test validates the FVG signal flow
        # (the trend gate itself is covered by test_scan_skips_neutral_market).
        signals, persisted = await _scan_and_fetch(
            "flat", svc_settings={"rr_min": 3.0, "trade_neutral_markets": True,
                                  "trade_min_trend_strength": "WEAK",
                                  "strategy_fvg_enabled": True})
        try:
            fvg = [s for s in signals if s.strategy_type == "FVG"]
            assert fvg, "the trailing FVG pattern must yield a candidate"
            sig = fvg[0]
            # FVG now detected on 1h; zone = [c1.high, c3.low] = [106, 110]
            assert sig.status == "PENDING" and sig.skip_reason is None
            assert sig.direction == "LONG" and sig.volume_confirmed is True
            assert float(sig.entry_zone_min) == 106.0 and float(sig.entry_zone_max) == 110.0
            # stop = zone_min - ATR (entry 108 - stop 106 = risk 2 → TP 108+6=114)
            assert float(sig.rr_ratio) == 3.0
            assert len(persisted) == 1 and persisted[0]["status"] == "PENDING"
        finally:
            await _cleanup()
    _run(scenario())


def test_scan_skips_on_bearish_trend():
    async def scenario():
        signals, persisted = await _scan_and_fetch(
            "bearish_1h", svc_settings={"rr_min": 3.0, "trade_neutral_markets": True,
                                       "strategy_fvg_enabled": True})
        try:
            assert signals, "candidate is still generated, but filtered"
            assert signals[0].status == "SKIPPED"
            assert signals[0].skip_reason == "Trend filter: BEARISH"
            assert len(persisted) == 1
            assert persisted[0]["status"] == "SKIPPED"
            assert persisted[0]["skip_reason"] == "Trend filter: BEARISH"
        finally:
            await _cleanup()
    _run(scenario())


def test_scan_skips_when_volume_not_confirmed():
    async def scenario():
        # volume filter is OFF by default (backtest showed it blocked profitable
        # trades) — opt in here to exercise the skip path
        signals, persisted = await _scan_and_fetch(
            "doji_volume", svc_settings={"rr_min": 3.0, "trade_neutral_markets": True,
                                         "volume_filter_enabled": True,
                                         "strategy_fvg_enabled": True})
        try:
            assert signals[0].status == "SKIPPED"
            assert signals[0].skip_reason == "Volume filter: no dominant side confirmation"
            assert signals[0].volume_confirmed is False
            assert persisted[0]["skip_reason"] == "Volume filter: no dominant side confirmation"
        finally:
            await _cleanup()
    _run(scenario())


def test_volume_filter_disabled_by_default():
    """The doji-volume fixture would be volume-skipped, but with the filter off
    (default) the signal passes to PENDING."""
    async def scenario():
        signals, persisted = await _scan_and_fetch(
            "doji_volume", svc_settings={"rr_min": 3.0, "trade_neutral_markets": True,
                                        "strategy_fvg_enabled": True})
        try:
            assert signals[0].status == "PENDING", (
                f"expected PENDING with volume filter off, got {signals[0].status}: "
                f"{signals[0].skip_reason}")
        finally:
            await _cleanup()
    _run(scenario())


def test_scan_dedups_repeated_scans():
    async def scenario():
        svc_settings = {"rr_min": 3.0, "trade_neutral_markets": True,
                        "strategy_fvg_enabled": True}
        signals1, persisted1 = await _scan_and_fetch("flat", svc_settings=svc_settings)
        signals2, persisted2 = await _scan_and_fetch("flat", svc_settings=svc_settings)
        try:
            assert len(signals1) == len(signals2)
            assert len(persisted1) == 1, "first scan persists one row"
            assert len(persisted2) == 1, "second scan must dedup, not flood the table"
        finally:
            await _cleanup()
    _run(scenario())


def test_skipped_signal_does_not_block_fresh():
    """Regression: a SKIPPED signal must NOT reserve the (symbol, strategy,
    direction) slot — a rejected signal (counter-trend etc.) shouldn't block
    future attempts once the market changes. Observed live: 43 stale SKIPPED
    rows blocked all new signals for 43 minutes of scanning."""
    async def scenario():
        from sqlalchemy import select, update
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.models.signal import Signal as SignalModel
        from backend.services.signal_service import SignalService
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as db:
                await db.execute(__import__("sqlalchemy").delete(SignalModel).where(
                    SignalModel.symbol == SYMBOL))
                await db.commit()

            svc = SignalService(session_factory=Session,
                                binance=FakeBinance(make_klines("flat")),
                                settings={"rr_min": 3.0, "trade_neutral_markets": True,
                                          "strategy_fvg_enabled": True})

            # scan #1: creates a PENDING signal
            await svc.scan_symbol(SYMBOL)
            async with Session() as db:
                row = (await db.execute(select(SignalModel).where(
                    SignalModel.symbol == SYMBOL))).scalars().first()
            assert row.status == "PENDING"

            # mark it SKIPPED (as if a filter rejected it)
            async with Session() as db:
                await db.execute(update(SignalModel).where(
                    SignalModel.id == row.id).values(status="SKIPPED"))
                await db.commit()

            # scan #2: the SKIPPED row must NOT block a fresh signal
            await svc.scan_symbol(SYMBOL)
            async with Session() as db:
                rows = (await db.execute(select(SignalModel).where(
                    SignalModel.symbol == SYMBOL))).scalars().all()
            statuses = [r.status for r in rows]
            assert "PENDING" in statuses, (
                f"a fresh PENDING should be created despite the SKIPPED row, got {statuses}")
        finally:
            async with Session() as db:
                await db.execute(__import__("sqlalchemy").delete(SignalModel).where(
                    SignalModel.symbol == SYMBOL))
                await db.commit()
            await engine.dispose()
    _run(scenario())


def test_stale_pending_signals_are_expired():
    """Regression: a PENDING signal older than signal_max_age_minutes must be
    EXPIRED on the next scan, so it stops blocking fresh signals for the same
    symbol+strategy+direction (the stale-PENDING deadlock)."""
    async def scenario():
        from datetime import datetime, timedelta, timezone
        from sqlalchemy import select, update, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.models.signal import Signal as SignalModel
        from backend.services.signal_service import SignalService
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        svc_settings = {"rr_min": 3.0, "trade_neutral_markets": True,
                        "strategy_fvg_enabled": True}
        try:
            # clean slate
            async with Session() as db:
                await db.execute(delete(SignalModel).where(SignalModel.symbol == SYMBOL))
                await db.commit()

            svc = SignalService(session_factory=Session,
                                binance=FakeBinance(make_klines("flat")),
                                settings=svc_settings)

            # scan #1: creates a fresh PENDING signal
            await svc.scan_symbol(SYMBOL)
            async with Session() as db:
                rows = (await db.execute(select(SignalModel).where(
                    SignalModel.symbol == SYMBOL))).scalars().all()
            assert any(r.status == "PENDING" for r in rows), "scan #1 makes a PENDING"

            # backdate all PENDING rows 2 hours (stale)
            async with Session() as db:
                await db.execute(
                    update(SignalModel)
                    .where(SignalModel.status == "PENDING")
                    .values(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
                )
                await db.commit()

            # scan #2: must expire the stale row and allow a fresh one
            await svc.scan_symbol(SYMBOL)
            async with Session() as db:
                rows2 = (await db.execute(select(SignalModel).where(
                    SignalModel.symbol == SYMBOL))).scalars().all()
            statuses = [r.status for r in rows2]
            # the stale PENDING is now EXPIRED (not left blocking)
            assert "EXPIRED" in statuses, f"expected an EXPIRED row, got {statuses}"
            # and a fresh PENDING was created (the slot is free again)
            assert "PENDING" in statuses, f"expected a fresh PENDING, got {statuses}"
        finally:
            async with Session() as db:
                await db.execute(delete(SignalModel).where(SignalModel.symbol == SYMBOL))
                await db.commit()
            await engine.dispose()
    _run(scenario())


def test_scan_skips_neutral_market():
    """With trade_neutral_markets=false, a NEUTRAL trend bias skips the signal
    (default is now TRUE — neutral allowed — per the 2026-10-13 backtest)."""
    async def scenario():
        signals, persisted = await _scan_and_fetch(
            "neutral_1h", svc_settings={"rr_min": 3.0, "trade_neutral_markets": False,
                                       "strategy_fvg_enabled": True})
        try:
            assert signals, "candidate generated but gated"
            assert signals[0].status == "SKIPPED"
            assert signals[0].skip_reason == "Trend filter: neutral market"
            assert persisted[0]["status"] == "SKIPPED"
        finally:
            await _cleanup()
    _run(scenario())
