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


def fvg_tail(candles):
    """Append a clean bullish FVG: c1 bearish (high 106), c2 gap-up (low 107), c3 confirms."""
    n = len(candles)
    candles[-3] = _candle(105, 106, 100, 101, n - 3)
    candles[-2] = _candle(107, 112, 107, 111, n - 2)
    candles[-1] = _candle(111, 115, 110, 114, n - 1)
    return candles


def make_klines(kind="flat"):
    if kind == "flat":
        one_h = [flat_buy_candle(i) for i in range(200)]
        five_m = fvg_tail([flat_buy_candle(i) for i in range(200)])
        fifteen_m = [flat_buy_candle(i) for i in range(200)]
    elif kind == "bearish_1h":
        one_h = [_candle(200 - i, 200 - i + 1, 200 - i - 1, 200 - i, i) for i in range(200)]
        five_m = fvg_tail([flat_buy_candle(i) for i in range(200)])
        fifteen_m = [flat_buy_candle(i) for i in range(200)]
    elif kind == "doji_volume":
        one_h = [flat_buy_candle(i) for i in range(200)]
        five_m = fvg_tail([flat_doji_candle(i) for i in range(200)])
        fifteen_m = [flat_buy_candle(i) for i in range(200)]
    else:
        raise ValueError(kind)
    return {"1h": one_h, "5m": five_m, "15m": fifteen_m}


class FakeBinance:
    def __init__(self, data):
        self.data = data

    async def get_klines(self, symbol, interval, limit=200):
        return self.data[interval][:limit]


async def _scan_and_fetch(kind, rr_min=3.0):
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
                            settings={"rr_min": rr_min})
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


def test_scan_persists_pending_fvg_signal():
    async def scenario():
        signals, persisted = await _scan_and_fetch("flat")
        try:
            fvg = [s for s in signals if s.strategy_type == "FVG"]
            assert fvg, "the trailing FVG pattern must yield a candidate"
            sig = fvg[0]
            # 1h flat (closes 100.9, ATR=2) → NEUTRAL bias; zone candles BUY-dominant
            assert sig.status == "PENDING" and sig.skip_reason is None
            assert sig.direction == "LONG" and sig.volume_confirmed is True
            assert float(sig.entry_zone_min) == 106.0 and float(sig.entry_zone_max) == 107.0
            assert float(sig.stop_loss) == 104.0        # zone_min 106 - ATR 2
            assert float(sig.take_profit) == 114.0      # entry 106.5 + risk 2.5 * 3
            assert float(sig.rr_ratio) == 3.0
            assert len(persisted) == 1 and persisted[0]["status"] == "PENDING"
        finally:
            await _cleanup()
    _run(scenario())


def test_scan_skips_on_bearish_trend():
    async def scenario():
        signals, persisted = await _scan_and_fetch("bearish_1h")
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
        signals, persisted = await _scan_and_fetch("doji_volume")
        try:
            assert signals[0].status == "SKIPPED"
            assert signals[0].skip_reason == "Volume filter: no dominant side confirmation"
            assert signals[0].volume_confirmed is False
            assert persisted[0]["skip_reason"] == "Volume filter: no dominant side confirmation"
        finally:
            await _cleanup()
    _run(scenario())


def test_scan_dedups_repeated_scans():
    async def scenario():
        signals1, persisted1 = await _scan_and_fetch("flat")
        signals2, persisted2 = await _scan_and_fetch("flat")
        try:
            assert len(signals1) == len(signals2)
            assert len(persisted1) == 1, "first scan persists one row"
            assert len(persisted2) == 1, "second scan must dedup, not flood the table"
        finally:
            await _cleanup()
    _run(scenario())
