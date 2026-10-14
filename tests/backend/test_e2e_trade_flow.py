"""PHASE6_STEP1 — End-to-end trade flow validation.

Validates the complete pipeline with only Binance mocked (controlled price path):

    signal detection → filters → decision → order placement → position tracked
    → TP hit → trade closed → PnL logged (trade + daily summary)

All other code paths are the real production services against the real database
schema (dedicated engine, self-cleaning).
"""
import asyncio
import pytest
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.core.bot_engine import BotEngine
from backend.core.daily_reset import DailyResetService
from backend.core.position_manager import PositionManager
from backend.models.daily_summary import DailySummary
from backend.models.position import Position as PositionModel
from backend.models.signal import Signal as SignalModel
from backend.models.symbol import Symbol
from backend.models.trade import Trade as TradeModel
from backend.services.alert_service import AlertService
from backend.services.binance_service import BinanceService
from backend.services.order_service import OrderService
from backend.services.risk_service import RiskService
from backend.services.signal_service import SignalService
from backend.services.strategy_service import StrategyService

SYMBOL = "TESTUSDT"


def _candle(o, h, l, c, i):
    return {"open": float(o), "high": float(h), "low": float(l), "close": float(c),
            "volume": 100.0, "close_time": datetime(2026, 1, 1, 0, i % 60)}


def flat_buy_candle(i):
    return _candle(100, 101, 99, 100.9, i)


def make_klines():
    one_h = [flat_buy_candle(i) for i in range(200)]          # ATR = 2
    # trailing bullish FVG on 1h (FVG detection moved off 5m): gap [c1.high, c3.low]
    one_h[-3] = _candle(105, 106, 100, 101, 197)
    one_h[-2] = _candle(107, 112, 107, 111, 198)
    one_h[-1] = _candle(111, 115, 110, 114, 199)
    # final close 101.4 → BULLISH + MODERATE + RSI NEUTRAL (114 tips RSI
    # overbought, which the LONG filter rejects; 101.4 > sma_50 ≈ 101.3 → aligned)
    one_h[-1]["close"] = 101.4
    five_m = [flat_buy_candle(i) for i in range(200)]
    fifteen_m = [flat_buy_candle(i) for i in range(200)]
    return {"1h": one_h, "5m": five_m, "15m": fifteen_m}


class FakeBinance:
    """Klines + a controllable ticker price; no auth'd endpoints needed."""

    def __init__(self):
        self.data = make_klines()
        self.price = 106.5  # inside the FVG zone [106, 107] at scan time

    async def get_klines(self, symbol, interval, limit=200):
        return self.data[interval][:limit]

    async def get_ticker(self, symbol):
        return {"symbol": symbol, "price": self.price}

    async def get_position(self, symbol):
        return None  # flat

    async def get_account_balance(self):
        return [{"asset": "USDT", "availableBalance": "10000"}]


async def _scenario():
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    summary_existed_before = False
    binance = FakeBinance()

    alert_service = AlertService()
    risk_service = RiskService()
    order_service = OrderService(binance=binance, session_factory=Session)
    signal_service = SignalService(session_factory=Session, binance=binance,
                                   # trade_neutral_markets + WEAK min-strength: the
                                   # e2e fixture is a small sample; this test validates
                                   # the trade flow, not the trend gate.
                                   settings={"rr_min": 3.0, "risk_percent": 1.0,
                                             "trade_neutral_markets": True,
                                             "trade_min_trend_strength": "WEAK"})
    strategy_service = StrategyService(risk_service, order_service, binance=binance,
                                       session_factory=Session,
                                       settings={"rr_min": 3.0, "max_positions": 50,
                                                 "risk_percent": 1.0})
    daily_reset = DailyResetService(session_factory=Session,
                                    settings={"daily_loss_limit_r": -3.0, "rr_min": 3.0},
                                    alert_service=alert_service)
    bot = BotEngine(settings={"rr_min": 3.0, "check_interval_seconds": 60},
                    binance=binance, signal_service=signal_service,
                    strategy_service=strategy_service, risk_service=risk_service,
                    order_service=order_service, alert_service=alert_service,
                    daily_reset=daily_reset, session_factory=Session)
    position_manager = PositionManager(binance=binance, order_service=order_service,
                                       daily_reset=daily_reset, alert_service=alert_service,
                                       settings={}, session_factory=Session)

    result = {}
    async with Session() as db:
        row = (await db.execute(select(DailySummary).where(
            DailySummary.date == datetime.now(timezone.utc).date()))).scalar_one_or_none()
        summary_existed_before = row is not None
        summary_before = (row.total_trades, float(row.total_pnl_r), row.wins) if row else (0, 0.0, 0)
        # isolate: disable every other enabled symbol so the engine scan only
        # trades TESTUSDT (their pending signals would paper-execute too)
        enabled_before = (await db.execute(
            select(Symbol.symbol).where(Symbol.enabled == True))).scalars().all()
        await db.execute(update(Symbol).where(Symbol.symbol != SYMBOL).values(enabled=False))
        await db.commit()
        # clean slate: leftover rows from a failed run would re-trigger execution
        await db.execute(delete(TradeModel).where(TradeModel.symbol == SYMBOL))
        await db.execute(delete(PositionModel).where(PositionModel.symbol == SYMBOL))
        await db.execute(delete(SignalModel).where(SignalModel.symbol == SYMBOL))
        # the engine scans only enabled symbols from the DB — register the test symbol
        existing = (await db.execute(select(Symbol).where(Symbol.symbol == SYMBOL))).scalar_one_or_none()
        if not existing:
            db.add(Symbol(symbol=SYMBOL, enabled=True, max_leverage=20, position_limit_usdt=10000))
        await db.commit()

    # ── 1. Signal detection: one full engine scan ────────────────────
    bot._running = True
    await bot._scan_and_evaluate()

    async with Session() as db:
        signals = (await db.execute(select(SignalModel).where(
            SignalModel.symbol == SYMBOL))).scalars().all()
        result["signals"] = [(s.strategy_type, s.direction, s.status,
                              float(s.entry_zone_min), float(s.entry_zone_max),
                              float(s.stop_loss), float(s.take_profit),
                              float(s.rr_ratio)) for s in signals]
        positions = (await db.execute(select(PositionModel).where(
            PositionModel.symbol == SYMBOL))).scalars().all()
        result["positions"] = [(p.side, p.status, float(p.entry_price),
                                float(p.quantity), float(p.stop_loss),
                                float(p.take_profit)) for p in positions]
        orders = list(order_service.pending.values())
        result["orders"] = [(o["status"], o["simulate"]) for o in orders]
        signal_rows = signals

    assert len(result["signals"]) == 1, result["signals"]
    st, direction, status, zmin, zmax, sl, tp, rr = result["signals"][0]
    # the engine scan evaluates and executes in one pass — the signal is
    # already TRIGGERED with an OPEN paper position by the time we look
    assert (st, direction, status) == ("FVG", "LONG", "TRIGGERED")
    # FVG zone is the classic gap [c1.high, c3.low]; stop = zone_min - ATR(1h)
    assert (zmin, zmax) == (106.0, 110.0)
    assert round(sl, 4) == 102.9424 and round(tp, 4) == 123.1727 and rr == 3.0

    # ── 2. Decision + execution (paper): order + OPEN position ───────
    assert len(result["positions"]) == 1, result["positions"]
    side, p_status, entry, qty, p_sl, p_tp = result["positions"][0]
    assert side == "LONG" and p_status == "OPEN"
    assert entry == 108.0 and round(p_sl, 4) == 102.9424 and round(p_tp, 4) == 123.1727
    # risk $100 (1% of 10k) / risk-dist 5.05758 = 19.7723 qty
    assert round(qty, 4) == 19.7723
    assert result["orders"] == [("FILLED", True)], "paper order fills immediately"

    # signal persisted as TRIGGERED with a triggered_at stamp
    async with Session() as db:
        sig = (await db.execute(select(SignalModel).where(
            SignalModel.symbol == SYMBOL))).scalar_one()
        assert sig.status == "TRIGGERED" and sig.triggered_at is not None
        position_id = (await db.execute(select(PositionModel).where(
            PositionModel.symbol == SYMBOL))).scalar_one().id

    # ── 3. Price runs to TP: PositionManager closes the position ─────
    binance.price = 124.0  # ≥ take_profit 123.1727
    await position_manager._check_all_positions()

    async with Session() as db:
        pos = await db.get(PositionModel, position_id)
        assert pos.status == "CLOSED" and pos.closed_at is not None
        trades = (await db.execute(select(TradeModel).where(
            TradeModel.symbol == SYMBOL))).scalars().all()
        assert len(trades) == 1
        trade = trades[0]
        assert trade.exit_reason == "TP_HIT"
        assert trade.strategy_type == "FVG"
        # entry 108, exit 124, qty 19.7723 → 16 * 19.7723 = 316.36
        # paper mode now deducts estimated fees: (108*19.7723 + 124*19.7723) * 0.0005 = 2.30
        # so PnL = 316.36 - 2.30 = 314.06
        assert round(float(trade.pnl_usdt), 2) == 314.06
        # pnl_r = pnl / (initial_risk 5.05758 * qty) = 314.06 / 100 = 3.14
        assert round(float(trade.pnl_r), 2) == 3.14
        result["trade"] = {"pnl_usdt": float(trade.pnl_usdt), "pnl_r": float(trade.pnl_r),
                           "reason": trade.exit_reason}

        summary = (await db.execute(select(DailySummary).where(
            DailySummary.date == datetime.now(timezone.utc).date()))).scalar_one_or_none()
        assert summary is not None
        # deltas vs the day's starting state (other paper trades may share today)
        assert summary.total_trades == summary_before[0] + 1
        assert summary.wins == summary_before[2] + 1
        import pytest as _pytest
        assert float(summary.total_pnl_r) == _pytest.approx(summary_before[1] + 3.1406, abs=0.01)
        result["summary"] = {"trades_delta": 1, "pnl_r_delta": float(summary.total_pnl_r) - summary_before[1]}

    # ── cleanup ───────────────────────────────────────────────────────
    async with Session() as db:
        await db.execute(delete(TradeModel).where(TradeModel.symbol == SYMBOL))
        await db.execute(delete(PositionModel).where(PositionModel.symbol == SYMBOL))
        await db.execute(delete(SignalModel).where(SignalModel.symbol == SYMBOL))
        await db.execute(delete(Symbol).where(Symbol.symbol == SYMBOL))
        for sym in enabled_before:
            await db.execute(update(Symbol).where(Symbol.symbol == sym).values(enabled=True))
        if not summary_existed_before:
            await db.execute(delete(DailySummary).where(
                DailySummary.date == datetime.now(timezone.utc).date()))
        await db.commit()

    await engine.dispose()
    return result


def test_e2e_trade_flow_signal_to_pnl():
    from backend.services.signal_service import Signal  # noqa: F401  (import sanity)
    result = asyncio.run(_scenario())
    assert result["trade"]["reason"] == "TP_HIT"
    assert result["trade"]["pnl_r"] == pytest.approx(3.1406, abs=0.01)
    assert result["summary"]["trades_delta"] == 1
    assert result["summary"]["pnl_r_delta"] == pytest.approx(3.1406, abs=0.01)
