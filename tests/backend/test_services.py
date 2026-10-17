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

    async def place_limit_order(self, symbol, side, price, quantity):
        self.calls.append(("LIMIT", side, price))
        self._limit_order_id = 101
        return {"orderId": 101, "status": "NEW", "price": price, "quantity": quantity}

    async def get_order(self, symbol, order_id):
        return {"orderId": order_id, "status": "FILLED",
                "executedQty": "10", "avgPrice": str(self.price)}

    async def cancel_order(self, symbol, order_id):
        self.calls.append(("CANCEL", order_id))
        return {"orderId": order_id, "status": "CANCELED"}

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
        """Live entries are LIMIT orders at the entry-zone midpoint (user-approved
        2026-10-15): MARKET fills landed up to 1% away, degrading the planned
        R:R and — when the market had passed the stop — producing an invalid SL
        that Binance rejected, leaving the entry naked."""
        binance = FakeBinance(price=100.5)
        order_svc = FakeOrderService()
        decision = await make_strategy(binance, order_svc).evaluate_signal(make_signal(), paper_mode=False)
        assert decision.decision == "EXECUTE" and decision.paper is False
        kinds = [c[0] for c in binance.calls]
        assert kinds == ["LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"], kinds
        # the limit sits at the planned midpoint (100.5), not the live price
        assert binance.calls[0][2] == 100.5
        # live orders are recorded in-memory and marked non-simulated
        assert all(o["simulate"] is False for o in order_svc.pending.values())

    async def test_live_limit_entry_records_the_fill(self):
        """The position must carry the exchange's executed qty/price, fetched
        from the order detail after the limit fills (observed: entry 1.1515
        planned vs 1.1518 filled on DOTUSDT)."""
        binance = FakeBinance(price=100.5)
        seen = {}

        async def get_order(symbol, order_id):
            return {"orderId": order_id, "status": "FILLED",
                    "executedQty": "9.9", "avgPrice": "100.62"}

        binance.get_order = get_order
        strategy = make_strategy(binance, FakeOrderService())
        # capture what the flow would write to the position row
        async def capture(position_id, price, qty=None):
            seen.update(price=price, qty=qty)
        strategy._update_position_fill = capture

        decision = await strategy.evaluate_signal(make_signal(), paper_mode=False)
        assert decision.decision == "EXECUTE"
        assert seen.get("price") == 100.62 and seen.get("qty") == 9.9, seen

    async def test_live_limit_entry_cancels_and_skips_when_unfilled(self):
        """If the limit does not fill within the window the order is cancelled
        and the signal skipped — 'the planned price or no trade', with no
        resting order and no position row left behind."""
        binance = FakeBinance(price=100.5)

        async def get_order(symbol, order_id):
            return {"orderId": order_id, "status": "NEW", "executedQty": "0", "avgPrice": "0"}

        async def no_position(symbol):
            return {}

        cancelled = []

        async def cancel(symbol, order_id):
            cancelled.append(order_id)
            return {"orderId": order_id, "status": "CANCELED"}

        binance.get_order = get_order
        binance.cancel_order = cancel
        binance.get_position = no_position  # flatten check: nothing there

        strategy = make_strategy(binance, FakeOrderService())
        strategy.limit_fill_timeout_s = 0.05
        strategy.limit_poll_interval_s = 0.01  # keep the test fast
        decision = await strategy.evaluate_signal(make_signal(), paper_mode=False)
        assert decision.decision == "SKIP"
        assert "not filled" in decision.skip_reason, decision.skip_reason
        assert cancelled == [101], f"resting limit not cancelled: {cancelled}"
        kinds = [c[0] for c in binance.calls]
        assert "STOP_MARKET" not in kinds, "SL placed for an unfilled entry"

    async def test_live_skips_when_rr_collapsed_at_market_price(self):
        """LIVE fills at MARKET, so the realised R:R is set by the fill price —
        not the zone midpoint. XRP 2026-10-15 had a 3.25:1 plan at the midpoint
        but filled 1% away (~1.1:1). Market price 102 vs zone 100-101/stop 90/
        tp 132 => 2.5:1 at the live price, 3.0:1 at the midpoint."""
        binance = FakeBinance(price=102.0)
        decision = await make_strategy(binance, FakeOrderService()).evaluate_signal(
            make_signal(), paper_mode=False)
        assert decision.decision == "SKIP"
        assert "live market price" in decision.skip_reason, decision.skip_reason

    async def test_paper_mode_uses_midpoint_rr(self):
        """Paper fills at the zone midpoint, so the live-price gate must NOT
        apply — the same setup executes in paper mode."""
        binance = FakeBinance(price=102.0)
        decision = await make_strategy(binance, FakeOrderService()).evaluate_signal(
            make_signal(), paper_mode=True)
        assert decision.decision == "EXECUTE" and decision.paper is True
        assert decision.plan["entry"] == 100.5

    def test_rr_validator_is_side_aware(self):
        """An inverted stop must fail the R:R check.

        The abs()-based check scored a LONG with its stop ABOVE the entry at
        93:1 (risk 30.5 "on the wrong side", reward 2859) and the bot executed
        it — Binance then rejected the SL (400 would-immediately-trigger) and
        the filled entry was left naked (live 2026-10-15, BTCUSDT)."""
        svc = RiskService()
        # LONG with stop ABOVE entry / target below -> invalid, however far away
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("105"), Decimal("130"),
                                     Decimal("3"), direction="LONG") is False
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("95"), Decimal("80"),
                                     Decimal("3"), direction="LONG") is False
        # correct sides still pass/fail on the ratio
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("90"), Decimal("130"),
                                     Decimal("3"), direction="LONG") is True
        # SHORT mirror image
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("95"), Decimal("70"),
                                     Decimal("3"), direction="SHORT") is False
        assert svc.validate_rr_ratio(Decimal("100"), Decimal("110"), Decimal("70"),
                                     Decimal("3"), direction="SHORT") is True

    async def test_skips_when_stop_already_breached(self):
        """The market must still be on the correct side of the stop. Entries
        fill at market, so a setup whose stop has been passed is void — placing
        the SL would fail with 400 and leave the entry naked (BTCUSDT/XRPUSDT
        2026-10-15)."""
        # LONG zone 100-101 (mid 100.5) with stop 99.5: market 99.2 has passed
        # the stop but still passes the +/-1% zone tolerance (>= 99.0)
        signal = make_signal(stop_loss=Decimal("99.5"))
        decision = await make_strategy(FakeBinance(price=99.2), FakeOrderService()
                                       ).evaluate_signal(signal, paper_mode=False)
        assert decision.decision == "SKIP"
        assert "stop already breached" in decision.skip_reason, decision.skip_reason

        # SHORT zone 100-101 with stop 100.8: market 101.0 has passed the stop
        short = make_signal(direction="SHORT", entry_zone_min=Decimal("100"),
                            entry_zone_max=Decimal("101"), stop_loss=Decimal("100.8"),
                            take_profit=Decimal("70"), rr_ratio=Decimal("3"))
        decision = await make_strategy(FakeBinance(price=101.0), FakeOrderService()
                                       ).evaluate_signal(short, paper_mode=False)
        assert decision.decision == "SKIP"
        assert "stop already breached" in decision.skip_reason, decision.skip_reason

    async def test_failed_live_flow_flattens_naked_position(self):
        """A live-flow exception after the entry filled must not leave an
        unprotected position on the exchange — flatten it."""
        binance = FakeBinance(price=100.5)
        closed = []

        async def boom(symbol, side, stop_price, quantity):
            raise RuntimeError("Binance API Error 400: would immediately trigger")

        async def get_position(symbol):
            return {"symbol": symbol, "side": "LONG", "quantity": 1.0}

        async def no_stops(symbol):
            return []

        async def market_close(symbol, side, qty):
            closed.append((symbol, side, qty))
            return {"orderId": 7}

        binance.modify_position_stop = boom
        binance.get_position = get_position
        binance.get_open_algo_orders = no_stops
        binance.place_market_order = market_close

        decision = await make_strategy(binance, FakeOrderService()).evaluate_signal(
            make_signal(), paper_mode=False)
        assert decision.decision == "SKIP"
        # place_market_order is also the entry leg, so look for the close leg
        assert ("BTCUSDT", "SELL", 1.0) in closed, f"naked position not flattened: {closed}"

    async def test_failed_live_flow_keeps_position_when_stop_rests(self):
        """If a stop IS resting, the failure was elsewhere — keep the position
        (the reconcile adopts it with its SL/TP) instead of closing it."""
        binance = FakeBinance(price=100.5)
        closed = []

        async def tp_boom(symbol, side, tp_price, quantity):
            raise RuntimeError("Binance API Error 400")

        async def get_position(symbol):
            return {"symbol": symbol, "side": "LONG", "quantity": 1.0}

        async def has_stop(symbol):
            return [{"algoId": 1, "orderType": "STOP_MARKET"}]

        async def market_close(symbol, side, qty):
            closed.append((symbol, side, qty))

        binance.place_take_profit_order = tp_boom
        binance.get_position = get_position
        binance.get_open_algo_orders = has_stop
        binance.place_market_order = market_close

        decision = await make_strategy(binance, FakeOrderService()).evaluate_signal(
            make_signal(), paper_mode=False)
        assert decision.decision == "SKIP"
        assert not [c for c in closed if c[1] == "SELL"], \
            f"protected position was closed: {closed}"

    async def test_notional_cap_is_configurable(self):
        """The position-notional cap silently overrode risk_percent: with ~1%
        stops the 50%-of-balance cap turned a 5% risk target into ~$1-2 per
        trade (live 2026-10-15). It must now come from the
        `max_position_notional_pct` setting so Settings can drive it.

        Setup: $200 balance, 5% risk = $10, entry 100.5, stop 99 (1.5% away) =>
        risk-based qty 6.667, notional $670."""
        base = {"rr_min": 3.0, "max_positions": 3, "risk_percent": 5.0,
                "paper_use_real_balance": False, "paper_balance_usdt": 200.0}
        over = dict(stop_loss=Decimal("99"), take_profit=Decimal("110"),
                    rr_ratio=Decimal("3"))

        d_50 = await make_strategy(
            FakeBinance(price=100.5), FakeOrderService(),
            settings={**base, "max_position_notional_pct": 50.0},
        ).evaluate_signal(make_signal(**over), paper_mode=True)
        d_500 = await make_strategy(
            FakeBinance(price=100.5), FakeOrderService(),
            settings={**base, "max_position_notional_pct": 500.0},
        ).evaluate_signal(make_signal(**over), paper_mode=True)

        assert d_50.decision == "EXECUTE" and d_500.decision == "EXECUTE"
        # 50% of 200 = $100 notional -> 100 / 100.5 = 0.995 qty (risk ~$1.49)
        assert abs(float(d_50.plan["size"]) - 0.995) < 0.01, d_50.plan["size"]
        # 500% cap no longer binds -> risk-based 10 / 1.5 = 6.667 qty
        assert abs(float(d_500.plan["size"]) - 6.667) < 0.01, d_500.plan["size"]
        assert float(d_500.plan["size"]) > float(d_50.plan["size"])

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
        # Market must sit ABOVE the stop (100.45) or the newer side check fires.
        signal = make_signal(stop_loss=Decimal("100.45"), take_profit=Decimal("100.65"))
        decision = await make_strategy(FakeBinance(price=100.5)).evaluate_signal(
            signal, paper_mode=True)
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
        one — otherwise two active SLs remain (observed live on 3 positions).

        The cancel now sweeps the symbol's resting STOP_MARKET orders on the
        EXCHANGE (via get_open_algo_orders), not the `orders` DB table: the
        trail path never writes its new algoId back to the table, so the table
        only ever knew the original entry SL and every trail stacked. This test
        asserts the exchange sweep cancels the resting stop and keeps the one
        just placed."""
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "DUPSLTST"
        cancelled = []
        placed = []
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="SHORT", entry_price=100.0, quantity=1.0,
                             stop_loss=105.0, take_profit=90.0, status="OPEN")
                s.add(p); await s.flush()
                pos_id = p.id
                await s.commit()

            class RecBinance:
                async def cancel_algo_order(self, symbol, algo_id):
                    cancelled.append(algo_id)
                    return {}
                async def modify_position_stop(self, symbol, side, stop_price, qty):
                    placed.append((symbol, side, stop_price))
                    return {"orderId": 2}
                async def get_open_algo_orders(self, symbol):
                    # The exchange has the ORIGINAL stop (555) resting; the new
                    # stop (2) is placed by modify_position_stop and must be
                    # kept by the sweep.
                    return [
                        {"algoId": 555, "orderType": "STOP_MARKET", "triggerPrice": 105.0},
                        {"algoId": 2, "orderType": "STOP_MARKET", "triggerPrice": 100.0},
                    ]
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
            assert 2 not in cancelled, f"the just-placed stop (2) must not be cancelled: {cancelled}"
            assert placed == [(SYM, "SHORT", 100.0)], f"new SL not placed: {placed}"
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_be_trail_requires_trigger_r_of_profit(self):
        """BE-trailing is price-based: it waits for `be_trail_trigger_r` R of
        profit (default +0.5R) instead of a structural BOS.

        The 2026-10-15 audit showed that at the strategy's ~80% stop-out rate an
        un-trailed structural stop makes expectancy negative (0.8×(-1R) +
        0.2×3R = -0.2R) whereas the BE stop keeps the same entries profitable
        (~+0.6R/trade)."""
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "BETRGTST"
        replaced = []
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="SHORT", entry_price=100.0, quantity=1.0,
                             stop_loss=110.0, take_profit=70.0, status="OPEN",
                             initial_risk_distance=10.0)
                s.add(p); await s.flush()
                pos_id = p.id
                await s.commit()

            class RecBinance:
                async def modify_position_stop(self, symbol, side, stop_price, qty):
                    replaced.append(stop_price)
                    return {"orderId": 2}
            async def _noop(*a, **k):
                return None
            alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
            daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
            pm = PositionManager(binance=RecBinance(), order_service=None,
                                 daily_reset=daily, alert_service=alerts,
                                 settings={}, session_factory=Session)
            pm._mode = "live"
            pos = {"id": pos_id, "symbol": SYM, "side": "SHORT", "quantity": 1.0,
                   "entry_price": 100.0, "stop_loss": 110.0, "take_profit": 70.0,
                   "initial_risk_distance": 10.0}

            # +0.4R only (price 96 vs trigger 95) -> no trail
            await pm._check_be_trailing(pos, current_price=96.0)
            assert replaced == [], f"trailed before +0.5R: {replaced}"

            # +0.51R (price 94.9) -> trail to BE
            await pm._check_be_trailing(pos, current_price=94.9)
            assert replaced == [100.0], f"did not trail at +0.5R: {replaced}"

            async with Session() as s:
                row = await s.get(Position, pos_id)
                assert float(row.stop_loss) == 100.0

            # one-way: already at BE -> no further action
            replaced.clear()
            await pm._check_be_trailing({**pos, "stop_loss": 100.0}, current_price=90.0)
            assert replaced == [], f"trailed twice: {replaced}"
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_be_trail_trigger_is_configurable(self):
        """The trigger distance comes from the be_trail_trigger_r setting."""
        from backend.core.position_manager import PositionManager
        async def _noop(*a, **k):
            return None
        alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
        daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
        pm = PositionManager(binance=None, order_service=None, daily_reset=daily,
                             alert_service=alerts, settings={"be_trail_trigger_r": 1.0},
                             session_factory=None)
        pm._mode = "paper"
        calls = []
        async def fake_trail(pid, price):
            calls.append((pid, price))
        pm._trail_stop_to_be = fake_trail
        pos = {"id": 1, "symbol": "X", "side": "LONG", "quantity": 1.0,
               "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 130.0,
               "initial_risk_distance": 10.0}
        await pm._check_be_trailing(pos, current_price=109.0)   # +0.9R
        assert calls == [], "trailed below the configured 1.0R trigger"
        await pm._check_be_trailing(pos, current_price=111.0)   # +1.1R
        assert calls == [(1, 100.0)], f"did not trail at the configured trigger: {calls}"

    async def test_fill_update_recomputes_frozen_risk_distance(self):
        """The frozen risk distance must follow the REAL entry — pnl_r is
        measured against it at close (XRP read +1.26R off the planned midpoint
        distance instead of +0.64R off the actual fill)."""
        from sqlalchemy import delete
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.config import settings as cfg
        from backend.models.position import Position
        from backend.services.strategy_service import StrategyService
        from backend.services.risk_service import RiskService
        engine = create_async_engine(cfg.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        SYM = "FILLTST"
        try:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                p = Position(symbol=SYM, side="SHORT", entry_price=100.0, quantity=5.0,
                             stop_loss=105.0, take_profit=90.0, status="OPEN",
                             initial_risk_distance=5.0)
                s.add(p); await s.flush()
                pos_id = p.id
                await s.commit()

            svc = StrategyService(RiskService(), order_svc=None, binance=None,
                                  session_factory=Session, settings={})
            await svc._update_position_fill(pos_id, 102.0, 7.0)

            async with Session() as s:
                row = await s.get(Position, pos_id)
                assert float(row.entry_price) == 102.0
                assert float(row.quantity) == 7.0
                assert float(row.initial_risk_distance) == 3.0, \
                    f"risk distance not recomputed from the fill: {row.initial_risk_distance}"
        finally:
            async with Session() as s:
                await s.execute(delete(Position).where(Position.symbol == SYM))
                await s.commit()
            await engine.dispose()

    async def test_profit_lock_trails_stop_at_trigger(self):
        """Profit lock: at `profit_lock_trigger_r` R in profit, trail the stop
        at `profit_lock_trail_r` R behind price — locking in real gains instead
        of just break-even."""
        from backend.core.position_manager import PositionManager
        async def _noop(*a, **k):
            return None
        alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
        daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
        pm = PositionManager(binance=None, order_service=None, daily_reset=daily,
                             alert_service=alerts, settings={}, session_factory=None)
        pm._mode = "paper"
        calls = []
        async def fake_trail(pid, price):
            calls.append((pid, price))
        pm._trail_stop_to_be = fake_trail

        # LONG: entry 100, risk 10, trigger 1R, trail 0.5R
        pos = {"id": 1, "symbol": "X", "side": "LONG", "quantity": 1.0,
               "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 130.0,
               "initial_risk_distance": 10.0}

        # +0.8R (price 108) -> below trigger, no trail
        await pm._check_profit_trailing(pos, current_price=108.0)
        assert calls == [], f"trailed below +1R trigger: {calls}"

        # +1.2R (price 112) -> trail to 112 - 5 = 107 (locks +0.7R)
        await pm._check_profit_trailing(pos, current_price=112.0)
        assert calls == [(1, 107.0)], f"did not trail at +1R: {calls}"

        # One-way: stop already at 107, price drops to 106 -> no backward trail
        calls.clear()
        await pm._check_profit_trailing({**pos, "stop_loss": 107.0}, current_price=106.0)
        assert calls == [], f"trailed stop backward: {calls}"

    async def test_profit_lock_trails_short(self):
        """Profit lock works for SHORT positions too."""
        from backend.core.position_manager import PositionManager
        async def _noop(*a, **k):
            return None
        alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
        daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
        pm = PositionManager(binance=None, order_service=None, daily_reset=daily,
                             alert_service=alerts, settings={}, session_factory=None)
        pm._mode = "paper"
        calls = []
        async def fake_trail(pid, price):
            calls.append((pid, price))
        pm._trail_stop_to_be = fake_trail

        # SHORT: entry 100, risk 10
        pos = {"id": 2, "symbol": "X", "side": "SHORT", "quantity": 1.0,
               "entry_price": 100.0, "stop_loss": 110.0, "take_profit": 70.0,
               "initial_risk_distance": 10.0}

        # +1.2R (price 88) -> trail to 88 + 5 = 93 (locks +0.7R)
        await pm._check_profit_trailing(pos, current_price=88.0)
        assert calls == [(2, 93.0)], f"did not trail SHORT at +1R: {calls}"

        # One-way: stop already at 93, price rises to 94 -> no backward trail
        calls.clear()
        await pm._check_profit_trailing({**pos, "stop_loss": 93.0}, current_price=94.0)
        assert calls == [], f"trailed SHORT stop backward: {calls}"

    async def test_profit_lock_trigger_is_configurable(self):
        """The profit-lock trigger and trail distance come from settings."""
        from backend.core.position_manager import PositionManager
        async def _noop(*a, **k):
            return None
        alerts = SimpleNamespace(signal=_noop, be_trailing=_noop, tp_hit=_noop, sl_hit=_noop)
        daily = SimpleNamespace(update_daily_summary=_noop, get_daily_stats=_noop)
        pm = PositionManager(binance=None, order_service=None, daily_reset=daily,
                             alert_service=alerts,
                             settings={"profit_lock_trigger_r": 2.0, "profit_lock_trail_r": 1.0},
                             session_factory=None)
        pm._mode = "paper"
        calls = []
        async def fake_trail(pid, price):
            calls.append((pid, price))
        pm._trail_stop_to_be = fake_trail

        pos = {"id": 3, "symbol": "X", "side": "LONG", "quantity": 1.0,
               "entry_price": 100.0, "stop_loss": 90.0, "take_profit": 130.0,
               "initial_risk_distance": 10.0}

        # +1.5R (price 115) -> below 2R trigger, no trail
        await pm._check_profit_trailing(pos, current_price=115.0)
        assert calls == [], f"trailed below configured 2R trigger: {calls}"

        # +2.1R (price 121) -> trail to 121 - 10 = 111 (locks +1.1R)
        await pm._check_profit_trailing(pos, current_price=121.0)
        assert calls == [(3, 111.0)], f"did not trail at configured trigger: {calls}"


class TestScanRateLimitGuard:
    """Regression: the scan loop must fetch open positions ONCE per pass, not
    once per symbol. The per-symbol get_current_position call made ~24 signed
    positionRisk requests per scan and tripped Binance's IP rate limit
    (-1003 -> HTTP 400/429), which blanked every signed call for ~60s and made
    the bot look dead."""

    async def test_scan_uses_one_position_fetch_not_per_symbol(self, monkeypatch):
        from backend.core.bot_engine import BotEngine

        calls = {"get_all_positions": 0, "get_position": 0}
        symbols = [f"SYM{i}USDT" for i in range(24)]

        async def fake_get_all_positions(self):
            calls["get_all_positions"] += 1
            return {}  # flat

        async def fake_get_position(self, symbol):
            calls["get_position"] += 1
            return None

        monkeypatch.setattr(
            "backend.services.binance_service.BinanceService.get_all_positions",
            fake_get_all_positions)
        monkeypatch.setattr(
            "backend.services.binance_service.BinanceService.get_position",
            fake_get_position)

        engine = BotEngine.__new__(BotEngine)
        engine._running = True
        engine._mode = "paper"
        engine.settings = {}
        engine.orders = type("O", (), {"get_all_open_positions": fake_get_all_positions,
                                       "get_current_position": fake_get_position})()
        engine.signals = type("S", (), {"scan_symbol": lambda self, s: _empty_async()})()
        engine.strategy = type("T", (), {"evaluate_signal": lambda self, sig, paper_mode=False: _empty_async()})()
        engine.daily_reset = type("D", (), {
            "get_daily_stats": lambda self: _stats_async(),
            "check_and_enforce_limit": lambda self: _true_async(),
        })()
        engine.alerts = type("A", (), {})()
        engine.session_factory = None

        async def fake_enabled():
            return symbols
        engine._get_enabled_symbols = fake_enabled

        await engine._scan_and_evaluate()

        # ONE bulk fetch for 24 symbols, ZERO per-symbol positionRisk calls
        assert calls["get_all_positions"] == 1, (
            f"expected 1 bulk position fetch per pass, got {calls['get_all_positions']}")
        assert calls["get_position"] == 0, (
            f"expected 0 per-symbol position calls, got {calls['get_position']}")


async def _empty_async():
    return []


async def _stats_async():
    return {"total_trades": 0, "used_r": 0.0, "limit_r": 3.0}


async def _true_async():
    return True


class TestRateLimiter:
    """Regression: the Binance REST rate limiter must actually throttle.

    The old implementation `await sleep()` while holding the lock and refilled
    a flat 20 tokens/sec from an oversized 1200 bucket, so it never throttled —
    the bot consumed Binance's real 2400-weight/min budget many times faster
    than the limiter believed, tripping the IP limit (-1003 -> HTTP 400/429)
    and blanking every signed call for ~60s."""

    async def test_burst_then_sustained_rate(self):
        import time as _time
        from backend.services.binance_service import RateLimiter

        r = RateLimiter(max_tokens=200, rate_per_sec=50.0)

        # A burst of up to max_tokens is instant (bucket starts full)
        t0 = _time.monotonic()
        for _ in range(200):
            await r.acquire(1)
        assert _time.monotonic() - t0 < 0.5, "burst up to bucket size must not block"

        # Beyond the bucket, the rate is capped at rate_per_sec — 100 more
        # tokens at 50/s must take ~2s, not 0.
        t1 = _time.monotonic()
        for _ in range(100):
            await r.acquire(1)
        elapsed = _time.monotonic() - t1
        assert elapsed >= 1.5, (
            f"expected ~2s to drain 100 tokens at 50/s, got {elapsed:.2f}s — "
            "the limiter is not throttling")

    async def test_acquire_does_not_hold_lock_while_waiting(self):
        """A blocked acquire must not serialise other acquirers behind it.

        With the old sleep-under-lock, a task waiting for tokens held the lock
        for the whole sleep, so every other caller queued behind it."""
        import asyncio as _asyncio
        from backend.services.binance_service import RateLimiter

        r = RateLimiter(max_tokens=10, rate_per_sec=100.0)
        # Drain the bucket so the next acquire must wait
        for _ in range(10):
            await r.acquire(1)

        results = []

        async def worker(n):
            await r.acquire(1)
            results.append(n)

        # Launch 3 waiters concurrently; with sleep-under-lock they'd serialise
        # (total ~0.03s each). With the fix they interleave on the same wait.
        await _asyncio.gather(worker(1), worker(2), worker(3))
        assert sorted(results) == [1, 2, 3]


class TestTrailStopNoStacking:
    """Regression: BE/profit-trailing must not stack exchange STOP_MARKET orders.

    `_cancel_active_sl` used to look up the stop to cancel via the `orders` DB
    table, but the trail path (`modify_position_stop`) never writes its new
    algoId back — so the table only knew the ORIGINAL entry SL. Cancelling it
    cancelled a dead order and left every trailed stop armed, stacking stops
    until the widest (the original entry stop) closed the position before any
    profit-lock stop triggered. Now it sweeps the symbol's resting stops on the
    exchange and keeps only the one just placed."""

    async def test_cancel_active_sl_sweeps_exchange_stops(self, monkeypatch):
        from backend.core.position_manager import PositionManager

        cancelled = []

        class FakeBinance:
            async def get_open_algo_orders(self, symbol):
                return [
                    {"algoId": 1, "orderType": "STOP_MARKET", "triggerPrice": 100.0},
                    {"algoId": 2, "orderType": "STOP_MARKET", "triggerPrice": 101.0},
                    {"algoId": 3, "orderType": "TAKE_PROFIT_MARKET", "triggerPrice": 110.0},
                ]

            async def cancel_algo_order(self, symbol, algo_id):
                cancelled.append(algo_id)

        pm = PositionManager.__new__(PositionManager)
        pm.session_factory = object()  # non-None so the guard passes
        pm.binance = FakeBinance()

        # keep_algo_id=2 is the stop just placed; it must NOT be cancelled.
        # The TP (algoId 3) must NOT be cancelled either — only STOP_MARKET.
        await pm._cancel_active_sl(position_id=1, symbol="XUSDT", keep_algo_id=2)
        assert sorted(cancelled) == [1], (
            f"expected only the stale stop (1) cancelled, got {sorted(cancelled)}")

    async def test_trail_passes_new_algo_id_as_keep(self, monkeypatch):
        """_trail_stop_to_be must pass modify_position_stop's orderId as keep."""
        from backend.core.position_manager import PositionManager
        from backend.models.position import Position
        from decimal import Decimal

        kept = {}

        class FakeBinance:
            async def modify_position_stop(self, symbol, side, stop_price, quantity):
                return {"orderId": 999, "symbol": symbol, "side": "SELL", "status": "NEW"}

        calls = {}

        class FakeSession:
            async def get(self, model, pid):
                p = Position(id=pid, symbol="XUSDT", side="LONG", status="OPEN",
                             entry_price=Decimal("100"), stop_loss=Decimal("99"),
                             quantity=Decimal("1"))
                return p
            async def commit(self): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        class FakeFactory:
            def __call__(self): return FakeSession()

        async def fake_cancel(position_id, symbol, keep_algo_id=None):
            calls["keep"] = keep_algo_id

        pm = PositionManager.__new__(PositionManager)
        pm.session_factory = FakeFactory()
        pm.binance = FakeBinance()
        pm._mode = "live"
        pm.alerts = None
        pm._cancel_active_sl = fake_cancel

        await pm._trail_stop_to_be(position_id=1, new_stop=Decimal("101"))
        assert calls.get("keep") == 999, (
            f"expected keep_algo_id=999 (the new stop), got {calls.get('keep')}")
