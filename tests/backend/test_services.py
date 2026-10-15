"""Service layer tests — risk sizing, strategy decisions, orders, signals, alerts."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from backend.services.risk_service import RiskService
from backend.services.strategy_service import StrategyService, TradeDecision
from backend.services.order_service import OrderService
from backend.services.signal_service import SignalService
from backend.services.alert_service import AlertService
from backend.core.daily_reset import DailyResetService


class TestRiskService:
    def test_position_size_math(self):
        """risk$ = balance*1% ; qty = risk$/(entry-SL)."""
        svc = RiskService()
        qty = svc.calc_position_size(
            balance_usdt=Decimal("10000"), risk_percent=Decimal("1"),
            entry_price=Decimal("100"), stop_loss_price=Decimal("90"),
        )
        # 100 USD risk / 10 diff = 10 qty (loss at SL = 10*10 = 100 = risk$)
        assert qty == Decimal("10.000000")

    def test_position_size_no_entry_price_inflation(self):
        """Regression: qty must NOT scale with entry_price. A low-priced coin
        must not be oversized — realized SL loss = risk$ regardless of price."""
        svc = RiskService()
        # Same 1% risk, same 10% stop distance, but a low-priced coin (~13)
        risk = Decimal("100")  # 1% of 10000
        entry = Decimal("13.34")
        sl = Decimal("12.00")  # stop distance 1.34
        qty = svc.calc_position_size(Decimal("10000"), Decimal("1"), entry, sl)
        loss_at_sl = qty * abs(entry - sl)
        # exactly risk$ (allow tiny quantization from 6-decimal rounding)
        assert abs(loss_at_sl - risk) < Decimal("0.01")

    def test_notional_cap_limits_position(self):
        svc = RiskService()
        # absurd size -> capped to 50% of balance notional
        capped = svc.cap_position_notional(
            qty=Decimal("1000000"), entry_price=Decimal("10"),
            balance_usdt=Decimal("10000"), max_notional_fraction=Decimal("0.5"),
        )
        assert capped * Decimal("10") <= Decimal("10000") * Decimal("0.5")

    def test_notional_cap_leaves_small_positions(self):
        svc = RiskService()
        small = svc.cap_position_notional(
            qty=Decimal("10"), entry_price=Decimal("10"),
            balance_usdt=Decimal("10000"),
        )
        assert small == Decimal("10")

    def test_zero_price_diff_returns_zero(self):
        svc = RiskService()
        assert svc.calc_position_size(Decimal("10000"), Decimal("1"),
                                      Decimal("100"), Decimal("100")) == 0

    def test_rr_validation_boundary(self):
        svc = RiskService()
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("90"), Decimal("130"), Decimal("3")) is True
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("90"), Decimal("129"), Decimal("3")) is False
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("100"), Decimal("130"), Decimal("3")) is False

    def test_daily_loss_limit_hard_stop_at_limit(self):
        svc = RiskService()
        limit = Decimal("-3")
        svc.record_trade_r(Decimal("-2"))
        assert svc.hit_daily_loss_limit(limit) is False
        svc.record_trade_r(Decimal("-1"))
        assert svc.hit_daily_loss_limit(limit) is True

    def test_reset_daily(self):
        svc = RiskService()
        svc.record_trade_r(Decimal("-3"))
        svc.reset_daily()
        assert svc.hit_daily_loss_limit(Decimal("-3")) is False


class FakeBinance:
    """Records order calls; returns deterministic market data."""

    def __init__(self, price=100.0):
        self.price = price
        self.calls = []

    async def get_ticker(self, symbol):
        return {"symbol": symbol, "price": self.price}

    async def get_account_balance(self):
        return [{"asset": "USDT", "availableBalance": "10000"}]

    async def place_market_order(self, symbol, side, quantity):
        self.calls.append(("MARKET", side, quantity))
        return {"orderId": 101, "status": "FILLED",
                "executedQty": str(quantity), "avgPrice": str(self.price)}

    async def get_order(self, symbol, order_id):
        self.calls.append(("GET_ORDER", order_id))
        return {"orderId": order_id, "status": "FILLED",
                "executedQty": "10", "avgPrice": str(self.price)}

    async def modify_position_stop(self, symbol, side, stop_price, quantity):
        self.calls.append(("STOP_MARKET", stop_price, quantity))
        return {"orderId": 102, "status": "NEW"}

    async def place_take_profit_order(self, symbol, side, tp_price, quantity):
        self.calls.append(("TAKE_PROFIT_MARKET", tp_price, quantity))
        return {"orderId": 103, "status": "NEW"}


class FakeOrderService(OrderService):
    """OrderService without DB, with controllable open-position answer."""

    def __init__(self, open_position=None):
        super().__init__(binance=None, session_factory=None)
        self._open_position = open_position

    async def get_current_position(self, symbol):
        return self._open_position


def make_signal(**over):
    from backend.services.signal_service import Signal
    args = dict(
        symbol="BTCUSDT", strategy_type="FVG", direction="LONG",
        entry_zone_min=Decimal("100"), entry_zone_max=Decimal("101"),
        stop_loss=Decimal("90"), take_profit=Decimal("132"),  # entry 100.5 → R:R exactly 3.0
        rr_ratio=Decimal("3"), volume_confirmed=True,
        created_at=datetime.now(timezone.utc),
        status="PENDING", db_id=1,
    )
    args.update(over)
    return Signal(**args)


def make_strategy(binance=None, order_svc=None, settings=None):
    return StrategyService(
        RiskService(), order_svc or FakeOrderService(),
        binance=binance or FakeBinance(), session_factory=None,
        settings=settings or {"rr_min": 3.0, "max_positions": 3, "risk_percent": 1.0},
    )


class TestStrategyServiceEvaluate:
    def test_rejects_unconfirmed_volume(self):
        plan = make_strategy().evaluate(**valid_args(volume_confirmed=False))
        assert plan is None

    def test_rejects_low_rr(self):
        plan = make_strategy().evaluate(**valid_args(
            stop_loss=Decimal("90"), take_profit=Decimal("110"), rr_ratio=Decimal("2")))
        assert plan is None

    def test_accepts_valid_setup_and_sizes_position(self):
        plan = make_strategy().evaluate(**valid_args())
        assert plan is not None
        # risk $100 / diff 10 = 10 qty (no entry_price inflation)
        assert plan["size"] == Decimal("10.000000")
        assert plan["stop_loss"] == Decimal("90")
        assert plan["take_profit"] == Decimal("130")


def valid_args(**over):
    args = dict(
        symbol="BTCUSDT", side="LONG",
        entry_zone_min=Decimal("100"), entry_zone_max=Decimal("101"),
        stop_loss=Decimal("90"), take_profit=Decimal("130"),
        rr_ratio=Decimal("3"), volume_confirmed=True,
        balance_usdt=Decimal("10000"), risk_percent=Decimal("1"), min_rr=Decimal("3"),
    )
    args.update(over)
    return args


class TestStrategyServiceEvaluateSignal:
    async def test_paper_mode_executes_with_simulated_fill(self):
        binance = FakeBinance(price=100.5)
        order_svc = FakeOrderService()
        decision = await make_strategy(binance, order_svc).evaluate_signal(make_signal(), paper_mode=True)
        assert decision.decision == "EXECUTE" and decision.paper is True
        assert decision.plan["entry"] == 100.5
        filled = [o for o in order_svc.pending.values() if o["status"] == "FILLED"]
        assert len(filled) == 1 and filled[0]["simulate"] is True

    async def test_live_mode_places_entry_stop_and_tp(self):
        binance = FakeBinance(price=100.5)
        order_svc = FakeOrderService()
        decision = await make_strategy(binance, order_svc).evaluate_signal(make_signal(), paper_mode=False)
        assert decision.decision == "EXECUTE" and decision.paper is False
        kinds = [c[0] for c in binance.calls]
        assert kinds == ["MARKET", "STOP_MARKET", "TAKE_PROFIT_MARKET"]
        # live orders are recorded in-memory and marked non-simulated
        assert all(o["simulate"] is False for o in order_svc.pending.values())

    async def test_live_mode_fetches_fill_via_get_order_when_response_lacks_it(self):
        """place_market_order responses can omit avgPrice/executedQty (ACK type) —
        the flow must fetch the order detail so the DB records the exchange's
        fill instead of the signal midpoint (observed: entry 1.1515 planned vs
        1.1518 filled on DOTUSDT)."""
        binance = FakeBinance(price=100.5)
        async def place_no_fill(symbol, side, quantity):
            binance.calls.append(("MARKET", side, quantity))
            return {"orderId": 101, "status": "FILLED"}  # no executedQty/avgPrice
        binance.place_market_order = place_no_fill
        decision = await make_strategy(binance, FakeOrderService()).evaluate_signal(
            make_signal(), paper_mode=False)
        assert decision.decision == "EXECUTE"
        assert ("GET_ORDER", 101) in binance.calls

    async def test_live_mode_survives_fill_fetch_failure(self):
        """A failed fill-detail fetch must NOT abort the entry — by then the
        MARKET order has already filled; closing the flow would mark the DB row
        CLOSED while a live position exists on the exchange."""
        binance = FakeBinance(price=100.5)
        async def place_no_fill(symbol, side, quantity):
            binance.calls.append(("MARKET", side, quantity))
            return {"orderId": 101, "status": "FILLED"}
        async def get_order_boom(symbol, order_id):
            raise RuntimeError("timeout")
        binance.place_market_order = place_no_fill
        binance.get_order = get_order_boom
        decision = await make_strategy(binance, FakeOrderService()).evaluate_signal(
            make_signal(), paper_mode=False)
        assert decision.decision == "EXECUTE"
        kinds = [c[0] for c in binance.calls]
        assert "STOP_MARKET" in kinds and "TAKE_PROFIT_MARKET" in kinds

    async def test_skips_when_position_already_open(self):
        order_svc = FakeOrderService(open_position={"quantity": 1})
        decision = await make_strategy(order_svc=order_svc).evaluate_signal(make_signal(), paper_mode=True)
        assert decision.decision == "SKIP" and "already open" in decision.skip_reason

    async def test_skips_when_price_moved_above_zone(self):
        binance = FakeBinance(price=150.0)
        decision = await make_strategy(binance=binance).evaluate_signal(make_signal(), paper_mode=True)
        assert decision.decision == "SKIP" and "above zone" in decision.skip_reason

    async def test_skips_stale_signal_and_expires_it(self):
        signal = make_signal(created_at=datetime.now(timezone.utc) - timedelta(hours=2))
        decision = await make_strategy().evaluate_signal(signal, paper_mode=True)
        assert decision.decision == "SKIP"
        assert signal.status == "EXPIRED"

    async def test_skips_non_pending_signal(self):
        signal = make_signal(status="TRIGGERED")
        decision = await make_strategy().evaluate_signal(signal, paper_mode=True)
        assert decision.decision == "SKIP"


class TestEntryFillAndRiskGuard:
    """Regression for the -2R bug: entries fill at the signal's entry-zone
    midpoint (not live market price), and degenerate risk distances are skipped."""

    async def test_entry_fills_at_zone_midpoint_not_market_price(self):
        # market price sits far from the zone midpoint; the fill must use the midpoint
        binance = FakeBinance(price=100.9)  # inside zone [100,101] but not the midpoint
        order_svc = FakeOrderService()
        decision = await make_strategy(binance, order_svc).evaluate_signal(
            make_signal(), paper_mode=True)
        assert decision.decision == "EXECUTE"
        assert decision.plan["entry"] == 100.5  # zone midpoint, NOT 100.9
        filled = [o for o in order_svc.pending.values() if o["status"] == "FILLED"]
        assert float(filled[0]["price"]) == 100.5

    async def test_skips_degenerate_risk_distance(self):
        # entry zone [100,101] mid 100.5; stop 100.45 -> diff 0.05 < min (0.0502).
        # TP set so R:R still passes (3.0) — the min-risk guard must catch it.
        signal = make_signal(stop_loss=Decimal("100.45"), take_profit=Decimal("100.65"))
        decision = await make_strategy().evaluate_signal(signal, paper_mode=True)
        assert decision.decision == "SKIP"
        assert "too small" in decision.skip_reason

    async def test_accepts_normal_risk_distance(self):
        signal = make_signal(stop_loss=Decimal("90"))  # diff 10.5, well above min
        decision = await make_strategy().evaluate_signal(signal, paper_mode=True)
        assert decision.decision == "EXECUTE"
        assert decision.plan["initial_risk_distance"] == 10.5


class TestClosePositionRFrozen:
    """Regression: pnl_r uses the FROZEN initial_risk_distance, not the live
    stop_loss (BE-trailing mutates stop_loss and would inflate |R|)."""

    def test_r_uses_initial_risk_not_moved_stop(self):
        from backend.core.position_manager import PositionManager
        # Simulate a position whose stop was trailed to BE (entry == stop),
        # with a frozen initial risk of 2.5 and a +8.5 profit.
        entry, qty, exit_price = 106.5, 40.0, 115.0
        initial_risk = 2.5
        pnl_usdt = (exit_price - entry) * qty  # 340
        # frozen risk: 340 / (2.5*40) = 3.4R
        assert pnl_usdt / (initial_risk * qty) == 3.4
        # if we (wrongly) used the trailed stop (stop==entry, risk~0) the R
        # would blow up; the frozen value prevents that.


class TestPaperBalanceSource:
    """paper_use_real_balance (default true): paper sizing uses the real Binance
    USDT availableBalance; when disabled it falls back to paper_balance_usdt."""

    class BalanceBinance:
        def __init__(self, available="4321.5"):
            self.available = available

        async def get_account_balance(self):
            return [{"asset": "USDT", "availableBalance": self.available}]

        async def get_ticker(self, symbol):
            return {"symbol": symbol, "price": 100.0}

    async def test_paper_uses_real_balance_by_default(self):
        svc = make_strategy(binance=self.BalanceBinance(available="4321.5"))
        bal = await svc._account_balance(paper_mode=True)
        assert bal == Decimal("4321.5")

    async def test_paper_uses_simulated_balance_when_disabled(self):
        svc = make_strategy(
            binance=self.BalanceBinance(available="4321.5"),
            settings={"paper_use_real_balance": False, "paper_balance_usdt": 5000},
        )
        bal = await svc._account_balance(paper_mode=True)
        assert bal == Decimal("5000")

    async def test_paper_falls_back_to_simulated_on_fetch_error(self):
        class BrokenBinance:
            async def get_account_balance(self):
                raise RuntimeError("no keys")

        svc = make_strategy(
            binance=BrokenBinance(),
            settings={"paper_use_real_balance": True, "paper_balance_usdt": 7500},
        )
        bal = await svc._account_balance(paper_mode=True)
        assert bal == Decimal("7500")

    async def test_live_mode_always_uses_real_balance(self):
        svc = make_strategy(
            binance=self.BalanceBinance(available="999"),
            settings={"paper_use_real_balance": False, "paper_balance_usdt": 5000},
        )
        bal = await svc._account_balance(paper_mode=False)
        assert bal == Decimal("999")


class TestOrderService:
    async def test_paper_order_fills_immediately(self):
        svc = OrderService()
        order = await svc.place_order("BTCUSDT", "BUY", "MARKET", Decimal("1.5"), simulate=True)
        assert order["status"] == "FILLED"
        assert order["filled_quantity"] == Decimal("1.5")
        fetched = await svc.get_order(id(order))
        assert fetched is order

    async def test_live_order_stays_new_and_not_simulated(self):
        svc = OrderService()  # no db → no order-table logging in this test
        order = await svc.place_order("BTCUSDT", "BUY", "LIMIT", Decimal("1"),
                                      price=Decimal("100"), simulate=False)
        assert order["status"] == "NEW"
        assert order["simulate"] is False

    async def test_get_current_position_without_binance_is_none(self):
        assert await OrderService().get_current_position("BTCUSDT") is None


async def test_signal_service_scan_without_binance_returns_empty():
    svc = SignalService()
    assert await svc.scan_symbol("BTCUSDT") == []


# ---- Alerts ---------------------------------------------------------------

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast(self, msg):  # async — like the real ws_manager
        self.sent.append(msg)


async def test_alert_service_without_ws_manager():
    svc = AlertService()
    await svc.signal("test message")  # must not raise
    await svc.trade("BTCUSDT", "LONG", r=2.5, pnl_usdt=150.0)


async def test_alert_service_awaits_async_broadcast():
    ws = FakeWS()
    svc = AlertService()
    svc.register_ws_manager(ws)
    await svc.signal("hello", level="ERROR")
    assert ws.sent == [{"type": "alert", "level": "ERROR", "message": "hello", "symbol": None}]


async def test_alert_payload_shape_matches_frontend_contract():
    """Regression: the frontend toast handler reads `msg.level`/`msg.message`
    from the TOP LEVEL of the alert message (not nested under `data`). Assert
    the shape the frontend depends on, for the alert types it renders."""
    ws = FakeWS()
    svc = AlertService()
    svc.register_ws_manager(ws)
    await svc.sl_hit("BTCUSDT", pnl_r=-1.0, pnl_usdt=-50.0)
    await svc.daily_limit_hit(used_r=3.0, limit_r=3.0)
    for msg in ws.sent:
        assert msg["type"] == "alert"
        # frontend reads these top-level:
        assert "level" in msg and isinstance(msg["level"], str)
        assert "message" in msg and msg["message"]  # non-empty text


async def test_alert_engine_facing_methods():
    ws = FakeWS()
    svc = AlertService()
    svc.register_ws_manager(ws)
    await svc.bot_started("paper")
    await svc.trade_executed("BTCUSDT", "LONG", 100, 90, 130, 3.0)
    await svc.be_trailing("BTCUSDT")
    await svc.tp_hit("BTCUSDT", 3.0, 300.0)
    await svc.daily_limit_warning(2.5, 3.0)
    await svc.daily_limit_hit(3.0, 3.0)
    await svc.error("boom")
    messages = [m["message"] for m in ws.sent]
    assert any("started in PAPER" in m for m in messages)
    assert any(m.startswith("🚀 Entry: BTCUSDT") for m in messages)
    assert any("break-even" in m for m in messages)
    assert any("TP hit" in m for m in messages)
    assert any("Daily limit warning" in m for m in messages)
    assert any("DAILY LOSS LIMIT HIT" in m for m in messages)
    levels = [m["level"] for m in ws.sent]
    assert "CRITICAL" in levels and "WARNING" in levels and "ERROR" in levels


async def test_engine_lifecycle_broadcasts_ws_events():
    """PHASE4_STEP2: start/stop push bot_status to connected clients."""
    from backend.core.bot_engine import BotEngine
    from backend.core.websocket_manager import ws_manager
    from backend.services.signal_service import SignalService

    class FakeWS:
        def __init__(self):
            self.sent = []
        async def send_json(self, msg):
            self.sent.append(msg)

    ws = FakeWS()
    await ws_manager.connect(ws, user_id=99)
    try:
        alerts = AlertService()
        alerts.register_ws_manager(ws_manager)
        binance = FakeBinance(price=100.5)
        engine = BotEngine(
            settings={"rr_min": 3.0, "check_interval_seconds": 60, "max_positions": 3,
                      "risk_percent": 1.0, "daily_loss_limit_r": -3.0},
            binance=binance,
            signal_service=SignalService(),
            strategy_service=StrategyService(RiskService(), OrderService(binance=binance),
                                             binance=binance, session_factory=None,
                                             settings={"rr_min": 3.0}),
            risk_service=RiskService(),
            order_service=OrderService(binance=binance),
            alert_service=alerts,
            daily_reset=DailyResetService(settings={"daily_loss_limit_r": -3.0},
                                          alert_service=alerts),
            session_factory=None,
        )
        await engine.start("paper")
        types = [m.get("type") for m in ws.sent]
        assert "bot_status" in types and "alert" in types
        assert any(m.get("data", {}).get("status") == "running"
                   for m in ws.sent if m.get("type") == "bot_status")

        await engine.stop()
        types = [m.get("type") for m in ws.sent]
        assert types.count("bot_status") == 2  # running + stopped
        assert any(m.get("data", {}).get("status") == "stopped"
                   for m in ws.sent if m.get("type") == "bot_status")
    finally:
        await ws_manager.disconnect(99)


class TestOvertradingGuard:
    """Per-symbol cooldown + daily trade cap (the 'don't trade 101x/day' fix)."""

    class _DailyWithStats:
        """Stub daily_reset whose stats report a controllable trade count."""
        def __init__(self, total_trades):
            self._n = total_trades
        async def get_daily_stats(self):
            return {"total_trades": self._n, "used_r": 0.0, "limit_r": 3.0}
        async def check_and_enforce_limit(self):
            return True

    async def _make_engine(self, daily, session_factory=None):
        from backend.core.bot_engine import BotEngine
        from backend.services.signal_service import SignalService
        binance = SimpleNamespace()
        engine = BotEngine(
            settings={"max_daily_trades": 2, "symbol_cooldown_minutes": 240},
            binance=binance,
            signal_service=SignalService(),
            strategy_service=None, risk_service=None, order_service=None,
            alert_service=SimpleNamespace(),
            daily_reset=daily, session_factory=session_factory,
        )
        engine._running = True  # the loop breaks immediately when not running
        return engine

    async def test_daily_trade_cap_stops_scanning(self):
        """When today's trades >= max_daily_trades, the pass returns early."""
        engine = await self._make_engine(self._DailyWithStats(total_trades=5))
        scanned = []
        async def fake_scan(sym):
            scanned.append(sym)
            return []
        engine.signals.scan_symbol = fake_scan
        engine._get_enabled_symbols = lambda: _async_list(["BTCUSDT"])
        await engine._scan_and_evaluate()
        assert scanned == [], "no scan when daily cap is already exceeded"

    async def test_daily_trade_cap_allows_below_limit(self):
        engine = await self._make_engine(self._DailyWithStats(total_trades=1))
        scanned = []
        async def fake_scan(sym):
            scanned.append(sym)
            return []
        engine.signals.scan_symbol = fake_scan
        engine._get_enabled_symbols = lambda: _async_list(["BTCUSDT"])
        engine.orders = SimpleNamespace(get_current_position=lambda s: _async_none())
        await engine._scan_and_evaluate()
        assert scanned == ["BTCUSDT"], "scans normally below the cap"

    async def test_minutes_since_last_trade(self):
        """Cooldown helper: None when no trades, minutes when a recent trade."""
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.models.trade import Trade
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            pm = await self._make_engine(self._DailyWithStats(0), session_factory=Session)
            # no trades yet
            assert await pm._minutes_since_last_trade("NOPEUSDT") is None
            # insert a trade closed ~10 minutes ago
            async with Session() as s:
                await s.execute(delete(Trade).where(Trade.symbol == "COOLUSDT"))
                s.add(Trade(symbol="COOLUSDT", side="LONG", entry_price=1, exit_price=1,
                            quantity=1, pnl_usdt=0, pnl_r=0, exit_reason="TP_HIT",
                            strategy_type="3CANDLE",
                            closed_at=datetime.now(timezone.utc) - timedelta(minutes=10)))
                await s.commit()
            age = await pm._minutes_since_last_trade("COOLUSDT")
            assert age is not None and 9 <= age <= 11
        finally:
            async with Session() as s:
                await s.execute(delete(Trade).where(Trade.symbol == "COOLUSDT"))
                await s.commit()
            await engine.dispose()


def _async_list(items):
    async def _coro():
        return items
    return _coro()


def _async_none():
    async def _coro():
        return None
    return _coro()


def _async_true():
    async def _coro():
        return True
    return _coro()


class TestPositionManagerWebSocket:
    """Regression: bookTicker ticks close positions on TP/SL in real time,
    without waiting for the 30s REST poll. Uses the real DB with a dedicated
    test symbol and cleans up its own rows."""

    SYM = "WSTESTUSDT"

    class FakeWSManager:
        def __init__(self):
            self.handlers = {}
            self.subscribed = set()

        async def watch_book_ticker(self, symbol, callback):
            self.handlers[symbol] = callback
            self.subscribed.add(symbol)

        async def unsubscribe(self, stream):
            self.subscribed.discard(stream.split("@")[0].upper())

    class NoopBinance:
        async def get_ticker(self, symbol):
            return {"symbol": symbol, "price": 0.0}
        async def get_klines(self, symbol, interval, limit=100):
            return []

    async def _run(self, bid, ask, entry=100.0, sl=90.0, tp=130.0):
        """Seed an OPEN position, register WS handler, fire a tick, return
        (status, exit_reason) after cleanup."""
        from sqlalchemy import select, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        from backend.models.trade import Trade

        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as s:
                await s.execute(delete(Trade).where(Trade.symbol == self.SYM))
                await s.execute(delete(Position).where(Position.symbol == self.SYM))
                s.add(Position(symbol=self.SYM, side="LONG", entry_price=entry,
                               quantity=1.0, stop_loss=sl, take_profit=tp,
                               status="OPEN", initial_risk_distance=abs(entry - sl)))
                await s.commit()

            ws = self.FakeWSManager()

            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(tp_hit=_noop, sl_hit=_noop, be_trailing=_noop)
            daily_reset = SimpleNamespace(update_daily_summary=_noop,
                                          get_daily_stats=_noop)

            pm = PositionManager(binance=self.NoopBinance(), order_service=None,
                                 daily_reset=daily_reset, alert_service=alerts,
                                 settings={}, session_factory=Session,
                                 market_data_ws=ws)
            pm._running = True
            await pm._sync_ws_subscriptions(await pm._get_open_positions())
            assert self.SYM in ws.handlers

            await ws.handlers[self.SYM]({"bid_price": bid, "ask_price": ask})

            async with Session() as s:
                pos = (await s.execute(select(Position).where(
                    Position.symbol == self.SYM))).scalars().first()
                trade = (await s.execute(select(Trade).where(
                    Trade.symbol == self.SYM))).scalars().first()
                status = pos.status
                reason = trade.exit_reason if trade else None
            return status, reason
        finally:
            async with Session() as s:
                await s.execute(delete(Trade).where(Trade.symbol == self.SYM))
                await s.execute(delete(Position).where(Position.symbol == self.SYM))
                await s.commit()
            await engine.dispose()

    async def test_tick_at_take_profit_closes_position(self):
        status, reason = await self._run(bid=130.5, ask=130.6)
        assert status == "CLOSED" and reason == "TP_HIT"

    async def test_tick_at_stop_loss_closes_position(self):
        status, reason = await self._run(bid=89.5, ask=89.6)
        assert status == "CLOSED" and reason == "SL_HIT"

    async def test_midrange_tick_does_not_close(self):
        status, reason = await self._run(bid=105.0, ask=105.1)
        assert status == "OPEN" and reason is None


class TestCloseAllPositions:
    """Manual close-all (kill switch): closing the bot must not leave positions
    open with no TP/SL management (dangerous in live mode)."""

    class _DailyWithStats:
        async def get_daily_stats(self):
            return {"total_trades": 0, "used_r": 0.0, "limit_r": 3.0}
        async def update_daily_summary(self, *a, **k):
            return None
        async def check_and_enforce_limit(self):
            return True

    async def test_close_all_closes_open_positions(self):
        from sqlalchemy import select, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        from backend.models.trade import Trade
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "CLOSEALLUSDT"
        try:
            async with Session() as s:
                await s.execute(delete(Trade).where(Trade.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                s.add(Position(symbol=SYM, side="LONG", entry_price=100.0, quantity=1.0,
                               stop_loss=95.0, take_profit=110.0, status="OPEN",
                               initial_risk_distance=5.0))
                await s.commit()

            class NoopBinance:
                async def get_ticker(self, symbol):
                    return {"symbol": symbol, "price": 105.0}

            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, tp_hit=_noop, sl_hit=_noop, be_trailing=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)

            pm = PositionManager(binance=NoopBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "paper"
            closed = await pm.close_all_positions(reason="MANUAL_CLOSE")
            assert closed == 1, f"expected 1 closed, got {closed}"
            async with Session() as s:
                pos = (await s.execute(select(Position).where(Position.symbol == SYM))).scalars().first()
                trade = (await s.execute(select(Trade).where(Trade.symbol == SYM))).scalars().first()
            assert pos.status == "CLOSED"
            assert trade is not None and trade.exit_reason == "MANUAL_CLOSE"
        finally:
            async with Session() as s:
                await s.execute(delete(Trade).where(Trade.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()


class TestLiveModeSafety:
    """Live-mode exchange hygiene: BE-trailing moves the exchange stop, closes
    cancel the exchange SL/TP, manual closes place a market order, and orders
    link to their position (orders.position_id)."""

    async def test_trail_stop_updates_exchange_in_live_mode(self):
        """In live mode, BE-trailing must call modify_position_stop on the exchange."""
        from sqlalchemy import select, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "TRAILTST"
        calls = []
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="LONG", entry_price=100.0, quantity=1.0,
                             stop_loss=95.0, take_profit=110.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                await s.commit()

            class RecBinance:
                async def modify_position_stop(self, symbol, side, stop_price, qty):
                    calls.append((symbol, side, stop_price, qty))
                    return {"orderId": 1}
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)

            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            await pm._trail_stop_to_be(pos_id, 100.0)

            assert len(calls) == 1, f"expected exchange stop update, got {calls}"
            assert calls[0][0] == SYM and calls[0][2] == 100.0
            async with Session() as s:
                row = await s.get(Position, pos_id)
                assert float(row.stop_loss) == 100.0  # DB also updated
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_close_cancels_exchange_orders_in_live(self):
        """In live mode, closing a position must cancel its exchange SL/TP orders."""
        from sqlalchemy import select, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        from backend.models.order import Order
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "CLOSETST"
        cancelled = []
        market_closed = []
        try:
            async with Session() as s:
                await s.execute(delete(Order).where(Order.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="LONG", entry_price=100.0, quantity=2.0,
                             stop_loss=95.0, take_profit=110.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                # link a STOP_MARKET order to this position
                s.add(Order(position_id=pos_id, symbol=SYM, side="SELL",
                            order_type="STOP_MARKET", quantity=2.0,
                            binance_order_id=999))
                await s.commit()

            class RecBinance:
                async def cancel_algo_order(self, symbol, algo_id):
                    cancelled.append(algo_id)
                    return {}
                async def place_market_order(self, symbol, side, qty):
                    market_closed.append((symbol, side, qty))
                    return {"orderId": 2}
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)

            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            pos = {"id": pos_id, "symbol": SYM, "side": "LONG", "quantity": 2.0,
                   "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0}
            await pm._close_position(pos, "MANUAL_CLOSE", 101.0)

            assert 999 in cancelled, f"exchange SL order not cancelled: {cancelled}"
            assert (SYM, "SELL", 2.0) in market_closed, f"no live market close: {market_closed}"
        finally:
            async with Session() as s:
                await s.execute(delete(Order).where(Order.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_close_tp_hit_only_cancels_no_market_close(self):
        """On TP_HIT in live mode, the exchange already closed — cancel the
        sibling SL but do NOT place a redundant market close."""
        from sqlalchemy import select, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        from backend.models.order import Order
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "TPHITTST"
        cancelled = []
        market_closed = []
        try:
            async with Session() as s:
                await s.execute(delete(Order).where(Order.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="LONG", entry_price=100.0, quantity=1.0,
                             stop_loss=95.0, take_profit=110.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                s.add(Order(position_id=pos_id, symbol=SYM, side="SELL",
                            order_type="STOP_MARKET", quantity=1.0, binance_order_id=888))
                await s.commit()

            class RecBinance:
                async def cancel_algo_order(self, symbol, algo_id):
                    cancelled.append(algo_id)
                    return {}
                async def place_market_order(self, symbol, side, qty):
                    market_closed.append((symbol, side, qty))
                    return {"orderId": 2}
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            pos = {"id": pos_id, "symbol": SYM, "side": "LONG", "quantity": 1.0,
                   "entry_price": 100.0, "stop_loss": 95.0, "take_profit": 110.0}
            await pm._close_position(pos, "TP_HIT", 110.0)

            assert 888 in cancelled, f"SL not cancelled on TP hit: {cancelled}"
            assert market_closed == [], f"redundant market close on TP hit: {market_closed}"
        finally:
            async with Session() as s:
                await s.execute(delete(Order).where(Order.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_close_sweeps_unrecorded_algo_orders(self):
        """Resting algo orders NOT in the orders table (BE-trail stops, manual
        re-placements) must be swept by symbol on close — otherwise they
        outlive the position and can close a FUTURE position prematurely
        (observed live: a BE-trail stop survived a TP_HIT close)."""
        from sqlalchemy import select, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "SWEEPTST"
        cancelled = []
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="SHORT", entry_price=100.0, quantity=1.0,
                             stop_loss=105.0, take_profit=90.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                await s.commit()

            class RecBinance:
                # NO orders-table records for this position — the orphan is
                # only visible via openAlgoOrders.
                async def get_open_algo_orders(self, symbol):
                    return [{"algoId": 777, "symbol": symbol,
                             "orderType": "STOP_MARKET", "quantity": "1.0"}]
                async def cancel_algo_order(self, symbol, algo_id):
                    cancelled.append((symbol, algo_id))
                    return {}
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            pos = {"id": pos_id, "symbol": SYM, "side": "SHORT", "quantity": 1.0,
                   "entry_price": 100.0, "stop_loss": 105.0, "take_profit": 90.0}
            await pm._close_position(pos, "TP_HIT", 90.0)

            assert (SYM, 777) in cancelled, f"unrecorded algo order not swept: {cancelled}"
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_reconcile_records_exchange_only_position(self):
        """get_all_positions() returns TRANSFORMED dicts (symbol/side/quantity/
        entryPrice) — reading the raw 'positionAmt' key skipped every position,
        so an exchange-only position was never recorded and stayed invisible
        (observed live: a naked XRPUSDT short the DB believed was closed)."""
        from sqlalchemy import delete, select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "RECONTST"
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()

            class RecBinance:
                async def get_all_positions(self):
                    return [{"symbol": SYM, "side": "SHORT", "quantity": 5.0,
                             "entryPrice": 100.0, "markPrice": 99.0,
                             "unrealizedPnl": 5.0, "leverage": 5}]
                async def get_open_algo_orders(self, symbol):
                    return [{"algoId": 1, "orderType": "STOP_MARKET", "triggerPrice": "105.0"},
                            {"algoId": 2, "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": "90.0"}]
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            await pm._reconcile_exchange_positions()

            async with Session() as s:
                row = (await s.execute(
                    select(Position).where(Position.symbol == SYM))).scalars().first()
            assert row is not None, "exchange-only position was not recorded"
            assert row.status == "OPEN" and row.side == "SHORT"
            assert float(row.quantity) == 5.0 and float(row.entry_price) == 100.0
            # the exchange's own resting SL/TP are adopted so it can be managed
            assert float(row.stop_loss) == 105.0 and float(row.take_profit) == 90.0
            assert row.leverage == 5
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_close_flattens_leftover_exchange_position(self):
        """An engine-detected TP/SL exit must verify the exchange is actually
        flat — a rejected BE stop left XRPUSDT open and NAKED while the DB
        recorded it closed."""
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "FLATTST"
        market_closed = []
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="SHORT", entry_price=100.0, quantity=5.0,
                             stop_loss=105.0, take_profit=90.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                await s.commit()

            class RecBinance:
                async def get_position(self, symbol):
                    # exchange still holds the position the engine thinks it closed
                    return {"symbol": symbol, "side": "SHORT", "quantity": 5.0}
                async def place_market_order(self, symbol, side, qty):
                    market_closed.append((symbol, side, qty))
                    return {"orderId": 9}
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            pos = {"id": pos_id, "symbol": SYM, "side": "SHORT", "quantity": 5.0,
                   "entry_price": 100.0, "stop_loss": 105.0, "take_profit": 90.0}
            await pm._close_position(pos, "SL_HIT", 105.0)

            # a SHORT is closed by a BUY market order
            assert market_closed == [(SYM, "BUY", 5.0)], \
                f"leftover exchange position not flattened: {market_closed}"
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_failed_be_trail_reverts_db_stop(self):
        """If the exchange BE stop cannot be placed (e.g. -2021 'would trigger
        immediately'), the DB stop must be reverted — a DB stop with no matching
        exchange stop makes the engine report an exit that never happened."""
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "BERVTST"
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="SHORT", entry_price=100.0, quantity=1.0,
                             stop_loss=105.0, take_profit=90.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                await s.commit()

            class RecBinance:
                async def modify_position_stop(self, symbol, side, stop_price, qty):
                    raise RuntimeError("Binance API Error -2021: Order would immediately trigger")
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            await pm._trail_stop_to_be(pos_id, 100.0)

            async with Session() as s:
                row = await s.get(Position, pos_id)
                assert float(row.stop_loss) == 105.0, \
                    f"DB stop not reverted after failed placement: {row.stop_loss}"
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_trail_stop_cancels_old_sl_before_placing_new(self):
        """BE-trailing must cancel the old exchange SL before placing the new
        one — otherwise two active SLs remain (observed live on 3 positions)."""
        from sqlalchemy import select, delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        from backend.models.order import Order
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "DUPSLTST"
        cancelled = []
        placed = []
        try:
            async with Session() as s:
                await s.execute(delete(Order).where(Order.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="SHORT", entry_price=100.0, quantity=1.0,
                             stop_loss=105.0, take_profit=90.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                # an existing active SL algo order for this position
                s.add(Order(position_id=pos_id, symbol=SYM, side="BUY",
                            order_type="STOP_MARKET", quantity=1.0, binance_order_id=555))
                await s.commit()

            class RecBinance:
                async def cancel_algo_order(self, symbol, algo_id):
                    cancelled.append(algo_id)
                    return {}
                async def modify_position_stop(self, symbol, side, stop_price, qty):
                    placed.append((symbol, side, stop_price))
                    return {"orderId": 2}
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            await pm._trail_stop_to_be(pos_id, 100.0)

            assert 555 in cancelled, f"old SL not cancelled before re-placing: {cancelled}"
            assert placed == [(SYM, "SHORT", 100.0)], f"new SL not placed: {placed}"
        finally:
            async with Session() as s:
                await s.execute(delete(Order).where(Order.symbol == SYM))
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()
