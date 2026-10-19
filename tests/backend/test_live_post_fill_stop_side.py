"""
Regression tests: the SL/TP must be re-validated against the LIVE market AFTER
the entry fills.

Production evidence (VPS contabo01, 2026-10-17, signal 244489 APEUSDT LONG):

    14:31:36  LIMIT BUY filled at 0.14860
    14:31:37  SELL STOP triggerPrice=0.1484 rejected — 400 Bad Request
    14:31:37  naked 1592 APE on the exchange -> flattened at market

The plan-level side check (gate 4b) compares `entry` with `signal.stop_loss`,
so it says nothing about where the market has moved to in the ~1 second between
the limit fill and the SL placement. Gate 4b passed at 0.15030/0.14839860; by
the time the SL was posted the mark had already crossed it.

Approved fix ("Option A"): after a live fill, re-validate the stop AND target
against the current market price. If either is no longer on the correct side,
do NOT place it — flatten at market, mark the signal SKIPPED, return SKIP.
Re-anchoring the stop (B) and cancelling/refilling at market (C) were rejected.

Deliberately NOT fixed here (tracked separately): `_signed_request` re-raises
`httpx.HTTPStatusError` without reading the body, so Binance's real
{"code": -2021, "msg": ...} is discarded and the 400 message is unhelpful. No
test below depends on Binance's error text.

Mixes behavioural tests (the flatten, the SKIP, the recorded Trade) with
source-contract tests (the check cannot be moved back above the fill, must read
the MARK price, must be live-only) — the defect is structural, so it can be
silently reintroduced by a refactor that keeps the tests' observable behaviour.
"""
import inspect
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from backend.services.risk_service import RiskService
from backend.services.strategy_service import StrategyService
from backend.services.order_service import OrderService
from backend.services.signal_service import Signal


# A symbol no real setup uses; every DB-touching test deletes its own rows.
TEST_SYMBOL = "ZZSIDEUSDT"


class FakeOrderService(OrderService):
    """OrderService without DB writes, with a controllable open-position answer."""

    def __init__(self, open_position=None):
        super().__init__(binance=None, session_factory=None)
        self._open_position = open_position

    async def get_current_position(self, symbol):
        return self._open_position


class FakeBinance:
    """Records every order call and serves controllable market data.

    `last_price` is the LAST-TRADE price (what gate 4/4b read); `mark` is the
    MARK price (what Binance triggers conditional orders on). They are separate
    so a test can prove which one the post-fill check consults.
    """

    def __init__(self, last_price=100.5, mark=None, fill_price=None,
                 open_qty=0.0, side="LONG", total_pnl=-0.5):
        self.last_price = last_price
        self.mark = last_price if mark is None else mark
        self.fill_price = last_price if fill_price is None else fill_price
        self.open_qty = open_qty
        self.side = side
        self.total_pnl = total_pnl
        self.calls = []
        self.mark_calls = 0

    async def get_ticker(self, symbol):
        return {"symbol": symbol, "price": self.last_price}

    async def get_mark_price(self, symbol):
        self.mark_calls += 1
        return {"symbol": symbol, "markPrice": self.mark}

    async def get_account_balance(self):
        return [{"asset": "USDT", "availableBalance": "10000"}]

    async def place_limit_order(self, symbol, side, price, quantity):
        self.calls.append(("LIMIT", side, price))
        return {"orderId": 101, "status": "NEW", "price": price, "quantity": quantity}

    async def get_order(self, symbol, order_id):
        return {"orderId": order_id, "status": "FILLED",
                "executedQty": "10", "avgPrice": str(self.fill_price)}

    async def cancel_order(self, symbol, order_id):
        self.calls.append(("CANCEL", order_id))
        return {"orderId": order_id, "status": "CANCELED"}

    async def modify_position_stop(self, symbol, side, stop_price, quantity):
        self.calls.append(("STOP_MARKET", stop_price, quantity))
        return {"orderId": 102, "status": "NEW"}

    async def place_take_profit_order(self, symbol, side, tp_price, quantity):
        self.calls.append(("TAKE_PROFIT_MARKET", tp_price, quantity))
        return {"orderId": 103, "status": "NEW"}

    async def get_position(self, symbol):
        if not self.open_qty:
            return {}
        return {"symbol": symbol, "side": self.side, "quantity": self.open_qty}

    async def get_open_algo_orders(self, symbol):
        return []

    async def place_market_order(self, symbol, side, quantity, reduce_only=False):
        self.calls.append(("MARKET", side, quantity))
        self.open_qty = 0.0
        return {"orderId": 7, "status": "FILLED"}

    async def get_position_income(self, symbol, income_type=None, start_time=None):
        return {"totalPnl": str(self.total_pnl)}


# LONG plan: zone 100-101 (mid 100.5), stop 95 (5.5 away), target 118 (17.5
# away) => 3.18:1, so the R:R and min-risk gates pass at BOTH the midpoint and
# the live price. The SHORT plan mirrors it (stop 105.5, target 84.5).
LONG = dict(direction="LONG", stop_loss=Decimal("95"), take_profit=Decimal("118"))
SHORT = dict(direction="SHORT", stop_loss=Decimal("105.5"), take_profit=Decimal("84.5"))


def make_signal(**over):
    args = dict(
        symbol=TEST_SYMBOL, strategy_type="3CANDLE",
        entry_zone_min=Decimal("100"), entry_zone_max=Decimal("101"),
        rr_ratio=Decimal("3"), volume_confirmed=True,
        created_at=_now(), status="PENDING", db_id=None, **LONG,
    )
    args.update(over)
    return Signal(**args)


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def make_strategy(binance, order_svc=None, settings=None):
    return StrategyService(
        RiskService(), order_svc or FakeOrderService(),
        binance=binance, session_factory=None,
        settings=settings or {"rr_min": 3.0, "max_positions": 3, "risk_percent": 1.0},
    )


def kinds(binance):
    return [c[0] for c in binance.calls]


# ---------------------------------------------------------------------------
# 1. Behaviour — the post-fill side check
# ---------------------------------------------------------------------------

class TestPostFillSideCheck:
    """The gate-4b plan check compares `entry` with the stop. Between the limit
    fill and the SL placement (~1s live) the market can cross the stop, and
    Binance rejects the already-in-the-money conditional order with 400 —
    leaving the fill naked."""

    async def test_market_crossed_the_stop_after_the_fill_is_flattened(self):
        """The APEUSDT case: gate 4b passes on the plan (last price 100.5 >
        stop 95), the limit fills, and by SL-placement time the MARK has
        dropped to 94.9 — below the stop."""
        binance = FakeBinance(last_price=100.5, mark=94.9,
                              fill_price=100.5, open_qty=10.0)
        decision = await make_strategy(binance).evaluate_signal(
            make_signal(), paper_mode=False)

        assert decision.decision == "SKIP", decision.skip_reason
        assert "stop" in decision.skip_reason, decision.skip_reason
        # The SL that Binance would have rejected is never attempted...
        assert "STOP_MARKET" not in kinds(binance), kinds(binance)
        # ...and the exchange position is closed at market instead.
        assert ("MARKET", "SELL", 10.0) in binance.calls, binance.calls

    async def test_target_reached_after_the_fill_is_flattened(self):
        """The mirror image: the move ran to the target before the TP could
        rest, so a TAKE_PROFIT_MARKET at that level would also be rejected."""
        binance = FakeBinance(last_price=100.5, mark=118.2,
                              fill_price=100.5, open_qty=10.0)
        decision = await make_strategy(binance).evaluate_signal(
            make_signal(), paper_mode=False)

        assert decision.decision == "SKIP"
        assert "target" in decision.skip_reason, decision.skip_reason
        assert "TAKE_PROFIT_MARKET" not in kinds(binance), kinds(binance)
        assert ("MARKET", "SELL", 10.0) in binance.calls, binance.calls

    async def test_valid_sides_still_execute_and_place_both_orders(self):
        """The check must not fire on the normal case — a LONG with the mark
        between the stop and the target places the SL and the TP as before."""
        binance = FakeBinance(last_price=100.5, mark=100.4, fill_price=100.5)
        decision = await make_strategy(binance).evaluate_signal(
            make_signal(), paper_mode=False)

        assert decision.decision == "EXECUTE", decision.skip_reason
        assert kinds(binance) == ["LIMIT", "STOP_MARKET", "TAKE_PROFIT_MARKET"], kinds(binance)

    async def test_equality_with_the_mark_price_is_invalid(self):
        """A stop AT the mark price satisfies the trigger condition
        (`mark >= trigger` for a SELL stop), so Binance rejects the order
        immediately rather than resting it. Equality is therefore treated as
        INVALID, not as a valid-but-tight stop."""
        assert (await make_strategy(
            FakeBinance(last_price=100.5, mark=95.0, open_qty=10.0)
        ).evaluate_signal(make_signal(), paper_mode=False)).decision == "SKIP"

        assert (await make_strategy(
            FakeBinance(last_price=100.5, mark=118.0, open_qty=10.0)
        ).evaluate_signal(make_signal(), paper_mode=False)).decision == "SKIP"

    async def test_short_mirrors_the_side_check(self):
        """For a SHORT the stop must sit strictly ABOVE the mark and the target
        strictly BELOW it."""
        # valid short: mark inside (target, stop)
        ok = FakeBinance(last_price=100.5, mark=100.4, fill_price=100.5)
        decision = await make_strategy(ok).evaluate_signal(
            make_signal(**SHORT), paper_mode=False)
        assert decision.decision == "EXECUTE", decision.skip_reason

        # the MARK rose above the short's stop (105.5) while the last trade
        # price still passes gate 4b — the BUY stop is already in the money
        bad = FakeBinance(last_price=100.5, mark=106.0, fill_price=100.5,
                          open_qty=10.0, side="SHORT")
        decision = await make_strategy(bad).evaluate_signal(
            make_signal(**SHORT), paper_mode=False)
        assert decision.decision == "SKIP", decision.skip_reason
        assert "stop" in decision.skip_reason, decision.skip_reason
        assert "STOP_MARKET" not in kinds(bad), kinds(bad)
        assert ("MARKET", "BUY", 10.0) in bad.calls, bad.calls

    async def test_paper_mode_is_never_re_validated(self):
        """Paper fills are SIMULATED at the plan price, so the plan is always
        internally consistent and no post-fill check applies."""
        binance = FakeBinance(last_price=100.5, mark=94.9)
        decision = await make_strategy(binance).evaluate_signal(
            make_signal(), paper_mode=True)
        assert decision.decision == "EXECUTE" and decision.paper is True
        assert binance.mark_calls == 0, "paper mode must not read the mark price"

    async def test_unreadable_mark_price_fails_closed(self):
        """The mark price is a PUBLIC read but it can still fail. It must NOT
        fall back to the last price: a last price below the stop with a mark
        above it authorises exactly the SL that Binance rejects. Unknown price
        => do not place protection, flatten."""
        binance = FakeBinance(last_price=100.5, mark=94.9, open_qty=10.0)

        async def broken(symbol):
            raise RuntimeError("premiumIndex 502")

        binance.get_mark_price = broken
        decision = await make_strategy(binance).evaluate_signal(
            make_signal(), paper_mode=False)

        assert decision.decision == "SKIP"
        assert "market price" in decision.skip_reason, decision.skip_reason
        assert "STOP_MARKET" not in kinds(binance), kinds(binance)
        assert ("MARKET", "SELL", 10.0) in binance.calls, binance.calls

    async def test_the_signal_is_skipped_not_left_pending(self):
        """A PENDING signal is re-evaluated on the next scan and could place a
        SECOND entry on a symbol the exchange may still hold."""
        binance = FakeBinance(last_price=100.5, mark=94.9, open_qty=10.0)
        signal = make_signal()
        decision = await make_strategy(binance).evaluate_signal(signal, paper_mode=False)
        assert decision.decision == "SKIP"
        assert signal.status == "SKIPPED", signal.status

    async def test_skipped_when_the_flatten_cannot_be_verified(self):
        """If the exchange position cannot be read we must NOT assume flat —
        the row stays OPEN for the reconcile, exactly as the failed-flow
        handler does."""
        binance = FakeBinance(last_price=100.5, mark=94.9)

        async def unverifiable(symbol):
            raise RuntimeError("positionRisk -1003")

        binance.get_position = unverifiable
        decision = await make_strategy(binance).evaluate_signal(
            make_signal(), paper_mode=False)
        assert decision.decision == "SKIP"
        assert "MARKET" not in kinds(binance), \
            "must not market-close on an unverifiable position state"


# ---------------------------------------------------------------------------
# 2. Behaviour — the flatten records a Trade (real DB, self-cleaning)
# ---------------------------------------------------------------------------

class TestFlattenRecordsARealisedTrade:
    """A ~1-second flatten is still a real loss: it must hit `trades` and the
    daily R accumulator. Commit 130f16e added the Trade row, but the path was
    broken in two ways that made the insert fail and the row never appear:

      * `StrategyService._mode` was never initialised or assigned anywhere, so
        `self._mode` raised AttributeError inside `_close_position_row`;
      * `Trade.strategy_type` is NOT NULL with no server default and the row
        was built without it.

    Both are swallowed by the broad `except Exception`, so the Position row was
    also left OPEN for a position that no longer existed.

    Everything written here is deleted in the `finally` (tests/AGENTS.md: a
    test that inserts without cleaning up permanently pollutes the dev DB).
    """

    async def test_flatten_closes_the_position_and_records_the_trade(self):
        from backend.database import AsyncSessionLocal
        from backend.models.position import Position
        from backend.models.trade import Trade
        from backend.models.signal import Signal as SignalModel

        signal_id = position_id = None
        try:
            async with AsyncSessionLocal() as session:
                row = SignalModel(
                    symbol=TEST_SYMBOL, strategy_type="3CANDLE", direction="LONG",
                    entry_zone_min=Decimal("100"), entry_zone_max=Decimal("101"),
                    stop_loss=Decimal("95"), take_profit=Decimal("118"),
                    rr_ratio=Decimal("3"), volume_confirmed=True, status="PENDING",
                    created_at=_now(),
                )
                session.add(row)
                await session.commit()
                signal_id = row.id

            binance = FakeBinance(last_price=100.5, mark=94.9, fill_price=100.5,
                                  open_qty=10.0, total_pnl=-0.5)
            strategy = StrategyService(
                RiskService(), FakeOrderService(),
                binance=binance, session_factory=AsyncSessionLocal,
                settings={"rr_min": 3.0, "max_positions": 99, "risk_percent": 1.0},
            )
            decision = await strategy.evaluate_signal(
                make_signal(db_id=signal_id), paper_mode=False)
            assert decision.decision == "SKIP", decision.skip_reason

            async with AsyncSessionLocal() as session:
                pos = (await session.execute(
                    select(Position).where(Position.symbol == TEST_SYMBOL)
                )).scalars().all()
                trades = (await session.execute(
                    select(Trade).where(Trade.symbol == TEST_SYMBOL)
                )).scalars().all()
                sig = await session.get(SignalModel, signal_id)

                assert len(pos) == 1, [p.id for p in pos]
                position_id = pos[0].id
                assert pos[0].status == "CLOSED", pos[0].status
                assert len(trades) == 1, "the flatten must record a Trade row"
                assert trades[0].exit_reason == "FLATTENED", trades[0].exit_reason
                assert float(trades[0].pnl_usdt) == pytest.approx(-0.5)
                assert trades[0].position_id == pos[0].id
                assert trades[0].strategy_type == "3CANDLE", trades[0].strategy_type
                assert sig.status == "SKIPPED", sig.status
        finally:
            async with AsyncSessionLocal() as session:
                await session.execute(delete(Trade).where(Trade.symbol == TEST_SYMBOL))
                await session.execute(delete(Position).where(Position.symbol == TEST_SYMBOL))
                await session.execute(
                    delete(SignalModel).where(SignalModel.symbol == TEST_SYMBOL))
                await session.commit()


# ---------------------------------------------------------------------------
# 3. Source contract — the check cannot be silently reintroduced
# ---------------------------------------------------------------------------

class TestPostFillCheckIsStructural:
    def test_the_check_sits_between_the_fill_and_the_sl(self):
        """Ordering is the whole fix. It must run AFTER the fill (there is
        nothing to validate before) and AFTER the position row exists (so the
        flatten can record a Trade against it), but BEFORE `modify_position_stop`
        (the call that gets a 400)."""
        src = inspect.getsource(StrategyService.evaluate_signal)
        assert src.count("_side_violation(") == 1, \
            "the post-fill side check must be called exactly once"
        i = src.index("_side_violation(")
        assert src.index("_update_position_fill(position_id") < i < src.index("modify_position_stop("), \
            "check must run after _update_position_fill and before modify_position_stop"

    def test_the_check_is_live_only(self):
        """Paper fills at the plan price, so the plan is always consistent.
        The call must live in the `else:` (live) branch."""
        src = inspect.getsource(StrategyService.evaluate_signal)
        assert src.count("_side_violation(") == 1
        assert src.index("if paper_mode:") < src.index("_side_violation("), \
            "the post-fill check must sit in the live (else) branch"

    def test_the_side_check_reads_the_mark_price_not_the_last_price(self):
        """Binance triggers conditional orders against the MARK price. Reading
        the last price could authorise an SL the exchange still rejects."""
        src = inspect.getsource(StrategyService._mark_price)
        assert "get_mark_price" in src, "the post-fill check must read the MARK price"
        assert "get_ticker" not in src, \
            "a last-trade price fallback re-authorises the rejected SL"

    def test_sides_are_compared_strictly(self):
        """A stop AT the mark already satisfies the trigger condition, so the
        comparisons must be >= / <=, not > / <."""
        src = inspect.getsource(StrategyService._side_violation)
        assert "stop_px >= market_px" in src
        assert "stop_px <= market_px" in src
        assert "tp_px <= market_px" in src
        assert "tp_px >= market_px" in src

    def test_an_unreadable_price_is_invalid(self):
        """`None`/0 must not fall through as a valid setup."""
        src = inspect.getsource(StrategyService._side_violation)
        assert "if not market_px or market_px <= 0:" in src

    def test_the_violation_branch_flattens_and_records(self):
        """No naked exchange position, no orphan row, no lost P&L."""
        src = inspect.getsource(StrategyService.evaluate_signal)
        idx = src.index("_side_violation(")
        branch = src[idx:src.index("return TradeDecision", idx)]
        assert "_flatten_if_unprotected(symbol)" in branch
        assert "_close_position_row(position_id)" in branch, \
            "the flatten must close the row through the path that records a Trade"
        assert '"SKIPPED"' in branch
        assert "signal.status = \"SKIPPED\"" in branch

    def test_the_violation_is_logged_with_the_whole_picture(self):
        """An operator reading journalctl must see symbol, direction, fill,
        market, stop and target."""
        src = inspect.getsource(StrategyService.evaluate_signal)
        idx = src.index("_side_violation(")
        log_call = src[src.index("logger.error", idx):src.index("_flatten_if_unprotected", idx)]
        for token in ("[%s]", "fill_price", "mark", "stop_px"):
            assert token in log_call, f"the WARNING must include {token}"

    def test_mode_is_initialised_so_the_trade_row_is_written(self):
        """`self._mode` raised AttributeError inside `_close_position_row`, which
        the broad except swallowed — so no Trade row was ever recorded and the
        Position row stayed OPEN."""
        src = inspect.getsource(StrategyService.__init__)
        assert "_mode" in src, "_mode must be initialised (mirrors PositionManager)"