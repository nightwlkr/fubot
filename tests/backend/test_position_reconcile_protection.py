"""Regression tests for exchange-position reconciliation protection."""
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest


VALID_SHORT_STOP = {
    "orderType": "STOP_MARKET", "side": "BUY", "positionSide": "BOTH",
    "workingType": "MARK_PRICE", "triggerPrice": "105",
    "reduceOnly": "true", "quantity": "2",
}


class _Session:
    def __init__(self, captured):
        self.captured = captured

    def add(self, row):
        self.captured.append(row)

    async def commit(self):
        pass

    async def execute(self, statement):
        self.captured.append(statement)


def _manager(monkeypatch, exchange_position, algos=None, *, algo_error=None,
             stop_error=None, tracked=None, mark=100):
    from backend.core.position_manager import PositionManager

    captured = []
    @asynccontextmanager
    async def session_factory():
        yield _Session(captured)

    class Binance:
        def __init__(self):
            self.stop_calls = []
            self.close_calls = []
            self.algo_reads = 0
            self.placed = False

        async def get_all_positions(self):
            return [exchange_position]

        async def get_open_algo_orders(self, symbol):
            self.algo_reads += 1
            if algo_error:
                raise RuntimeError("algo read unavailable")
            if self.placed:
                return [{**VALID_SHORT_STOP, "triggerPrice": "101"}]
            return algos or []

        async def get_mark_price(self, symbol):
            return {"markPrice": str(mark)}

        async def modify_position_stop(self, symbol, side, price, quantity):
            self.stop_calls.append((symbol, side, price, quantity))
            if stop_error:
                raise RuntimeError("stop placement failed")
            self.placed = True
            return {"orderId": 7, "status": "NEW"}

        async def place_market_order(self, *args, **kwargs):
            self.close_calls.append((args, kwargs))

    binance = Binance()
    alerts = SimpleNamespace(error_calls=[])
    async def alert(message):
        alerts.error_calls.append(message)
    alerts.error = alert

    pm = PositionManager(binance, None, None, alerts, {}, session_factory)
    pm._mode = "live"
    async def get_open_positions():
        return tracked or []
    monkeypatch.setattr(pm, "_get_open_positions", get_open_positions)
    return pm, binance, captured, alerts


def _position(**updates):
    result = {"symbol": "BTCUSDT", "side": "SHORT", "quantity": 2,
              "entryPrice": 100, "markPrice": 100, "leverage": 2}
    result.update(updates)
    return result


@pytest.mark.asyncio
async def test_valid_exchange_stop_is_adopted(monkeypatch):
    pm, binance, captured, _ = _manager(monkeypatch, _position(), [VALID_SHORT_STOP])
    await pm._reconcile_exchange_positions()
    row = next(item for item in captured if hasattr(item, "stop_loss"))
    assert float(row.stop_loss) == 105
    assert row.initial_risk_distance is None
    assert pm.reconciliation_blocked
    assert not binance.stop_calls and not binance.close_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("algos", [[], [{**VALID_SHORT_STOP, "orderType": "TAKE_PROFIT_MARKET"}],
                                     [{**VALID_SHORT_STOP, "side": "SELL"}]])
async def test_missing_or_invalid_stop_gets_emergency_stop(monkeypatch, algos):
    pm, binance, captured, _ = _manager(monkeypatch, _position(), algos)
    await pm._reconcile_exchange_positions()
    assert binance.stop_calls == [("BTCUSDT", "SHORT", 101.0, 2.0)]
    assert not binance.close_calls
    assert pm.reconciliation_blocked
    row = next(item for item in captured if hasattr(item, "stop_loss"))
    assert float(row.stop_loss) == 101
    assert row.initial_risk_distance is None


@pytest.mark.asyncio
async def test_algo_read_failure_preserves_exposure_and_blocks_entries(monkeypatch):
    pm, binance, captured, alerts = _manager(monkeypatch, _position(), algo_error=True)
    await pm._reconcile_exchange_positions()
    assert pm.reconciliation_blocked
    assert binance.stop_calls == [] and binance.close_calls == []
    assert any(hasattr(item, "status") for item in captured)
    assert alerts.error_calls


@pytest.mark.asyncio
async def test_malformed_exchange_position_never_guesses_or_closes(monkeypatch):
    pm, binance, captured, alerts = _manager(
        monkeypatch, _position(side="SIDEWAYS", quantity="NaN"), [VALID_SHORT_STOP])
    await pm._reconcile_exchange_positions()
    assert pm.reconciliation_blocked
    assert binance.stop_calls == [] and binance.close_calls == []
    assert not any(hasattr(item, "status") for item in captured)
    assert alerts.error_calls


@pytest.mark.asyncio
async def test_missing_quantity_in_exchange_snapshot_is_not_treated_as_flat(monkeypatch):
    raw = {"symbol": "BTCUSDT", "side": "SHORT", "entryPrice": 100}
    tracked = [{"id": 4, "symbol": "BTCUSDT", "side": "SHORT", "quantity": 2,
                "entry_price": 100, "stop_loss": 105, "initial_risk_distance": 5}]
    tracked[0]["mode"] = "live"
    pm, binance, captured, alerts = _manager(
        monkeypatch, raw, [VALID_SHORT_STOP], tracked=tracked)
    closed = []

    async def close_position(position, reason, price):
        closed.append((position, reason, price))

    monkeypatch.setattr(pm, "_close_position", close_position)
    await pm._reconcile_exchange_positions()

    assert pm.reconciliation_blocked
    assert not closed, "missing quantity must not be interpreted as confirmed zero exposure"
    assert alerts.error_calls


@pytest.mark.asyncio
async def test_live_reconciliation_does_not_close_paper_positions(monkeypatch):
    paper_position = {"id": 4, "symbol": "BTCUSDT", "side": "LONG", "quantity": 2,
                      "entry_price": 100, "stop_loss": 95, "initial_risk_distance": 5,
                      "mode": "paper"}
    pm, binance, _, _ = _manager(monkeypatch, _position(quantity=0), tracked=[paper_position])
    closed = []

    async def close_position(position, reason, price):
        closed.append((position, reason, price))

    monkeypatch.setattr(pm, "_close_position", close_position)
    await pm._reconcile_exchange_positions()

    assert not closed
    assert not pm.reconciliation_blocked


@pytest.mark.asyncio
async def test_uncertain_reconciliation_blocks_live_scan_before_symbol_fetch(monkeypatch):
    from backend.core.bot_engine import BotEngine

    engine = BotEngine(
        settings={"bot_mode": "live"}, binance=None, signal_service=None,
        strategy_service=None, risk_service=None, order_service=None,
        alert_service=None, daily_reset=None,
    )
    engine._running = True
    engine._mode = "live"
    engine.position_manager = SimpleNamespace(reconciliation_blocked=True)

    async def unexpected_symbol_fetch():
        pytest.fail("scanner must stop before fetching signals when exposure is unresolved")

    monkeypatch.setattr(engine, "_get_enabled_symbols", unexpected_symbol_fetch)

    await engine._scan_and_evaluate()


@pytest.mark.asyncio
async def test_clean_reconciliation_clears_prior_unresolved_state(monkeypatch):
    raw = _position(side="SIDEWAYS")
    pm, binance, _, _ = _manager(monkeypatch, raw, [VALID_SHORT_STOP])
    await pm._reconcile_exchange_positions()
    assert pm.reconciliation_blocked
    raw.update(quantity=0)
    await pm._reconcile_exchange_positions()
    assert not pm.reconciliation_blocked


@pytest.mark.asyncio
async def test_failed_emergency_stop_preserves_and_blocks(monkeypatch):
    pm, binance, captured, alerts = _manager(monkeypatch, _position(), [], stop_error=True)
    await pm._reconcile_exchange_positions()
    assert pm.reconciliation_blocked
    assert binance.close_calls == []
    assert alerts.error_calls
    assert any(hasattr(item, "status") for item in captured)


@pytest.mark.asyncio
async def test_tracked_position_with_missing_risk_basis_is_revalidated(monkeypatch):
    tracked = [{"id": 4, "symbol": "BTCUSDT", "side": "SHORT", "quantity": 2,
                "entry_price": 100, "stop_loss": None, "initial_risk_distance": None,
                "mode": "live"}]
    pm, binance, captured, _ = _manager(monkeypatch, _position(), [], tracked=tracked)
    await pm._reconcile_exchange_positions()
    assert binance.stop_calls == [("BTCUSDT", "SHORT", 101.0, 2.0)]
    assert not binance.close_calls
    assert pm.reconciliation_blocked
    updates = [s for s in captured if hasattr(s, "_values")]
    assert updates, "verified current stop must be persisted"
    params = updates[-1].compile().params
    assert params["stop_loss"] == 101
    assert "initial_risk_distance" not in params


@pytest.mark.asyncio
async def test_tracked_position_syncs_stop_but_preserves_frozen_risk(monkeypatch):
    tracked = [{"id": 4, "symbol": "BTCUSDT", "side": "SHORT", "quantity": 2,
                "entry_price": 100, "stop_loss": 103, "initial_risk_distance": 3,
                "mode": "live"}]
    pm, _, captured, _ = _manager(monkeypatch, _position(), [VALID_SHORT_STOP], tracked=tracked)
    await pm._reconcile_exchange_positions()
    updates = [s for s in captured if hasattr(s, "_values")]
    assert updates, "verified exchange stop must replace the stale DB stop"
    assignments = updates[-1].compile().params
    assert assignments["stop_loss"] == 105
    assert "initial_risk_distance" not in assignments
    assert pm.reconciliation_blocked is False


@pytest.mark.asyncio
@pytest.mark.parametrize("tracked", [
    {"side": "LONG", "quantity": 2, "entry_price": 100},
    {"side": "SHORT", "quantity": 1, "entry_price": 100},
    {"side": "SHORT", "quantity": 2, "entry_price": 99},
])
async def test_tracked_position_mismatch_blocks_new_entries(monkeypatch, tracked):
    row = {"id": 4, "symbol": "BTCUSDT", "mode": "live", "stop_loss": 105,
           "initial_risk_distance": 5, **tracked}
    pm, binance, captured, alerts = _manager(
        monkeypatch, _position(), [VALID_SHORT_STOP], tracked=[row])

    await pm._reconcile_exchange_positions()

    assert pm.reconciliation_blocked
    assert not binance.close_calls
    assert not any(hasattr(item, "status") for item in captured)
    assert not any(hasattr(item, "_values") for item in captured)
    assert any("does not match the database position" in message
               for message in alerts.error_calls)


@pytest.mark.asyncio
async def test_wrong_side_take_profit_is_not_adopted(monkeypatch):
    wrong_side_tp = {"orderType": "TAKE_PROFIT_MARKET", "side": "BUY",
                     "positionSide": "BOTH", "workingType": "MARK_PRICE",
                     "triggerPrice": "105", "reduceOnly": "true", "quantity": "2"}
    pm, binance, captured, _ = _manager(monkeypatch, _position(),
                                       [VALID_SHORT_STOP, wrong_side_tp])
    await pm._reconcile_exchange_positions()
    row = next(item for item in captured if hasattr(item, "stop_loss"))
    assert row.take_profit is None
    assert not binance.close_calls
