"""Service layer tests — risk sizing, strategy gating, order tracking, signal stub, alerts."""
from decimal import Decimal

import pytest

from backend.services.risk_service import RiskService
from backend.services.strategy_service import StrategyService
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


class TestStrategyService:
    def make(self):
        return StrategyService(RiskService(), OrderService())

    def valid_args(self, **over):
        args = dict(
            symbol="BTCUSDT", side="LONG",
            entry_zone_min=Decimal("100"), entry_zone_max=Decimal("101"),
            stop_loss=Decimal("90"), take_profit=Decimal("130"),
            rr_ratio=Decimal("3"), volume_confirmed=True,
            balance_usdt=Decimal("10000"), risk_percent=Decimal("1"), min_rr=Decimal("3"),
        )
        args.update(over)
        return args

    def test_rejects_unconfirmed_volume(self):
        plan = self.make().evaluate(**self.valid_args(volume_confirmed=False))
        assert plan is None

    def test_rejects_low_rr(self):
        plan = self.make().evaluate(**self.valid_args(
            stop_loss=Decimal("90"), take_profit=Decimal("110"), rr_ratio=Decimal("2")))
        assert plan is None

    def test_accepts_valid_setup_and_sizes_position(self):
        plan = self.make().evaluate(**self.valid_args())
        assert plan is not None
        assert plan["size"] == Decimal("1000.000000")
        assert plan["stop_loss"] == Decimal("90")
        assert plan["take_profit"] == Decimal("130")


class TestOrderService:
    async def test_paper_order_fills_immediately(self):
        svc = OrderService()
        order = await svc.place_order("BTCUSDT", "BUY", "MARKET", Decimal("1.5"), simulate=True)
        assert order["status"] == "FILLED"
        assert order["filled_quantity"] == Decimal("1.5")
        fetched = await svc.get_order(id(order))
        assert fetched is order

    async def test_live_order_stays_new(self):
        """FIXME: OrderService never talks to Binance — even simulate=False just records a dict."""
        svc = OrderService()
        order = await svc.place_order("BTCUSDT", "BUY", "LIMIT", Decimal("1"), price=Decimal("100"), simulate=False)
        assert order["status"] == "NEW"
        assert order["filled_quantity"] == 0


async def test_signal_service_scan_is_stub():
    """PHASE4 orchestration gap: scan_symbol never consults indicators and always returns None."""
    svc = SignalService()
    assert await svc.scan_symbol("BTCUSDT") is None


async def test_alert_service_without_ws_manager():
    svc = AlertService()
    svc.signal("test message")  # must not raise
    svc.trade("BTCUSDT", "LONG", r=2.5, pnl_usdt=150.0)


async def test_alert_service_broadcasts_via_ws_manager():
    class FakeWS:
        def __init__(self):
            self.sent = []

        def broadcast(self, msg):
            self.sent.append(msg)

    ws = FakeWS()
    svc = AlertService()
    svc.register_ws_manager(ws)
    svc.signal("hello", level="ERROR")
    assert ws.sent == [{"type": "alert", "level": "ERROR", "message": "hello"}]
    # NOTE: the real ws_manager.broadcast is async — AlertService calls it without await,
    # so real broadcasts are never delivered (documented defect, see audit report).
