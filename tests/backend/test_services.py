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


class TestRiskService:
    def test_position_size_math(self):
        """risk$ = balance*1% ; qty = risk$/(entry-SL)*entry."""
        svc = RiskService()
        qty = svc.calc_position_size(
            balance_usdt=Decimal("10000"), risk_percent=Decimal("1"),
            entry_price=Decimal("100"), stop_loss_price=Decimal("90"),
        )
        # 100 USD risk / 10 diff * 100 = 1000
        assert qty == Decimal("1000.000000")

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
        return {"orderId": 101, "status": "FILLED"}

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
        assert plan["size"] == Decimal("1000.000000")
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
