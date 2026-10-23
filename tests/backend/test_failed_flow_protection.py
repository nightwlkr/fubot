"""Regression tests for verifying exchange stop protection after entry failure."""
import asyncio

import pytest


class _Exchange:
    def __init__(self, position, algos):
        self.position = position
        self.algos = algos
        self.market_closes = []

    async def get_position(self, symbol):
        return self.position

    async def get_open_algo_orders(self, symbol):
        return self.algos

    async def place_market_order(self, symbol, side, quantity, reduce_only=False):
        self.market_closes.append((symbol, side, quantity, reduce_only))
        return {"status": "FILLED"}


def _service(exchange):
    from backend.services.strategy_service import StrategyService

    service = StrategyService.__new__(StrategyService)
    service.binance = exchange
    return service


@pytest.mark.parametrize(
    "stop,expected_protected",
    [
        ({"side": "SELL", "positionSide": "BOTH", "workingType": "MARK_PRICE",
          "quantity": "1", "triggerPrice": "95", "reduceOnly": "true"}, True),
        ({"side": "BUY", "positionSide": "BOTH", "workingType": "MARK_PRICE",
          "quantity": "1", "triggerPrice": "95", "reduceOnly": "true"}, False),
        ({"side": "SELL", "positionSide": "BOTH", "workingType": "MARK_PRICE",
          "quantity": "0.5", "triggerPrice": "95", "reduceOnly": "true"}, False),
        ({"side": "SELL", "positionSide": "BOTH", "workingType": "MARK_PRICE",
          "quantity": "1", "triggerPrice": "105", "reduceOnly": "true"}, False),
        ({"side": "SELL", "positionSide": "BOTH", "workingType": "MARK_PRICE",
          "quantity": "1", "triggerPrice": "95", "reduceOnly": "false"}, False),
        ({"side": "SELL", "positionSide": "BOTH", "workingType": "MARK_PRICE",
          "quantity": "1", "triggerPrice": "95", "closePosition": "true"}, True),
        ({"side": "SELL", "positionSide": "LONG", "workingType": "MARK_PRICE",
          "quantity": "1", "triggerPrice": "95", "reduceOnly": "true"}, True),
        ({"side": "SELL", "positionSide": "SHORT", "workingType": "MARK_PRICE",
          "quantity": "1", "triggerPrice": "95", "reduceOnly": "true"}, False),
        ({"side": "SELL", "positionSide": "BOTH", "workingType": "CONTRACT_PRICE",
          "quantity": "1", "triggerPrice": "95", "reduceOnly": "true"}, False),
    ],
)
def test_stop_protection_requires_correct_side_trigger_and_full_quantity(
    stop, expected_protected
):
    from backend.services.strategy_service import StrategyService

    position = {"side": "LONG", "quantity": 1.0, "markPrice": 100.0}
    algo = {"orderType": "STOP_MARKET", **stop}
    assert StrategyService._has_covering_protective_stop(position, [algo]) is expected_protected


def test_failed_flow_does_not_trust_an_undersized_resting_stop():
    async def scenario():
        exchange = _Exchange(
            {"side": "LONG", "quantity": 1.0, "markPrice": 100.0},
            [{"orderType": "STOP_MARKET", "side": "SELL", "positionSide": "BOTH",
              "workingType": "MARK_PRICE", "quantity": "0.5",
              "triggerPrice": "95", "reduceOnly": "true"}],
        )
        result = await _service(exchange)._flatten_if_unprotected("BTCUSDT")
        assert result.flat is True
        assert exchange.market_closes == [("BTCUSDT", "SELL", 1.0, True)]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "position",
    [
        {"side": "LONG", "quantity": "NaN", "markPrice": 100.0},
        {"side": "LONG", "quantity": "Infinity", "markPrice": 100.0},
        {"side": "LONG", "quantity": -1.0, "markPrice": 100.0},
        {"side": "LONG", "quantity": "not-a-number", "markPrice": 100.0},
        {"side": "UNKNOWN", "quantity": 1.0, "markPrice": 100.0},
    ],
)
def test_failed_flow_does_not_trade_on_invalid_position_snapshot(position):
    async def scenario():
        exchange = _Exchange(position, [])
        result = await _service(exchange)._flatten_if_unprotected("BTCUSDT")
        assert result.flat is False
        assert result.exposure_unknown is True
        assert exchange.market_closes == []

    asyncio.run(scenario())


def test_failed_flow_keeps_position_when_valid_covering_stop_rests():
    async def scenario():
        exchange = _Exchange(
            {"side": "SHORT", "quantity": 2.0, "markPrice": 100.0},
            [{"orderType": "STOP_MARKET", "side": "BUY", "positionSide": "BOTH",
              "workingType": "MARK_PRICE", "quantity": "2",
              "triggerPrice": "105", "reduceOnly": True}],
        )
        result = await _service(exchange)._flatten_if_unprotected("ETHUSDT")
        assert result.flat is False
        assert result.position == exchange.position
        assert not result.exposure_unknown
        assert exchange.market_closes == []

    asyncio.run(scenario())
