"""Binance service tests — REST path building via httpx.MockTransport (no network)."""
import httpx
import pytest

from backend.services.binance_service import BinanceService


KLINE_ROW = [1, "100", "110", "90", "105", "10", 2, "1000", 5, "500"]


def make_service(handler):
    svc = BinanceService(api_key="k", api_secret="s", testnet=True)
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url=svc._base_url
    )
    return svc


async def test_klines_url_carries_fapi_prefix():
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, json=[KLINE_ROW])

    svc = make_service(handler)
    try:
        rows = await svc.get_klines("BTCUSDT", "1h", 1)
    finally:
        await svc._client.aclose()
    assert seen[0].startswith("https://testnet.binancefuture.com/fapi/v1/klines")
    assert "symbol=BTCUSDT" in seen[0]
    assert rows[0]["close"] == 105.0


async def test_order_book_uses_depth_endpoint():
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"bids": [["100", "1"]], "asks": [["101", "2"]]})

    svc = make_service(handler)
    try:
        book = await svc.get_order_book("BTCUSDT", 5)
    finally:
        await svc._client.aclose()
    assert "/fapi/v1/depth" in seen[0]
    assert book["bids"] == [[100.0, 1.0]]


async def test_ticker_path():
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"price": "123.45"})

    svc = make_service(handler)
    try:
        ticker = await svc.get_ticker("BTCUSDT")
    finally:
        await svc._client.aclose()
    assert "/fapi/v1/ticker/price" in seen[0]
    assert ticker["price"] == 123.45


async def test_signed_balance_url_and_signature():
    seen = []

    def handler(request: httpx.Request):
        seen.append((str(request.url), request.headers))
        return httpx.Response(200, json=[{"asset": "USDT", "availableBalance": "1000", "initialMargin": "0"}])

    svc = make_service(handler)
    try:
        balance = await svc.get_account_balance()
    finally:
        await svc._client.aclose()
    url, headers = seen[0]
    assert url.startswith("https://testnet.binancefuture.com/fapi/v2/balance")
    assert "timestamp=" in url and "signature=" in url
    assert headers.get("x-mbx-apikey") == "k"  # httpx lowercases header names
    assert balance[0]["asset"] == "USDT"


async def test_bapi_error_raises_on_error_payload():
    def handler(request: httpx.Request):
        return httpx.Response(200, json={"code": -1121, "msg": "Invalid symbol."})

    svc = make_service(handler)
    try:
        with pytest.raises(Exception) as exc:
            await svc.get_ticker("BAD???")
        assert "Invalid symbol" in str(exc.value)
    finally:
        await svc._client.aclose()


async def test_closing_orders_are_reduce_only():
    seen = []

    def handler(request: httpx.Request):
        url = str(request.url)
        seen.append(url)
        # exchangeInfo (for LOT_SIZE rounding). get_lot_size RAISES when the
        # symbol is absent — an empty filter dict is not a real Binance state
        # and made the rounders return their input unrounded. So the stub must
        # return real filters for the symbol under test.
        if "exchangeInfo" in url:
            return httpx.Response(200, json={"symbols": [{
                "symbol": "BTCUSDT",
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.1", "minPrice": "0.1"},
                ],
            }]})
        return httpx.Response(200, json={"algoId": 55, "algoStatus": "NEW"})

    svc = make_service(handler)
    try:
        stop = await svc.modify_position_stop("BTCUSDT", "LONG", 90000.0, 0.01)
        tp = await svc.place_take_profit_order("BTCUSDT", "LONG", 110000.0, 0.01)
    finally:
        await svc._client.aclose()
    # find the algoOrder URLs (skip exchangeInfo)
    order_urls = [u for u in seen if "algoOrder" in u]
    assert stop["side"] == "SELL" and tp["side"] == "SELL"  # LONG closes with SELL
    assert "type=STOP_MARKET" in order_urls[0] and "reduceOnly=true" in order_urls[0]
    assert "type=TAKE_PROFIT_MARKET" in order_urls[1] and "reduceOnly=true" in order_urls[1]


async def test_open_algo_orders_preserve_fields_needed_to_validate_stop_coverage():
    def handler(request: httpx.Request):
        assert request.url.path.endswith("/fapi/v1/openAlgoOrders")
        return httpx.Response(200, json=[{
            "algoId": 55, "symbol": "BTCUSDT", "orderType": "STOP_MARKET",
            "side": "SELL", "positionSide": "BOTH", "triggerPrice": "90000",
            "quantity": "0.01", "reduceOnly": True, "closePosition": False,
            "workingType": "MARK_PRICE", "algoStatus": "NEW",
        }])

    svc = make_service(handler)
    try:
        orders = await svc.get_open_algo_orders("BTCUSDT")
    finally:
        await svc._client.aclose()
    assert orders == [{
        "algoId": 55, "symbol": "BTCUSDT", "orderType": "STOP_MARKET",
        "side": "SELL", "positionSide": "BOTH", "triggerPrice": "90000",
        "quantity": "0.01", "reduceOnly": True, "closePosition": False,
        "workingType": "MARK_PRICE", "algoStatus": "NEW",
    }]


async def test_klines_window_carries_start_end_and_the_bar_cap():
    """`get_klines_window` is the backtest's only seam onto ranged history.

    `get_klines` takes no time parameters at all, which is why the harness
    capped itself at 1000 bars (3.47 days of 5m). Assert the range really
    reaches the wire, and that `limit` defaults to Binance's 1500 klines cap —
    ask for more and the overflow is silently dropped, so a wrong default is a
    truncated dataset that raises no error.
    """
    seen = []

    def handler(request: httpx.Request):
        seen.append(str(request.url))
        return httpx.Response(200, json=[KLINE_ROW])

    svc = make_service(handler)
    try:
        rows = await svc.get_klines_window("BTCUSDT", "5m", 1_700_000_000_000,
                                           1_700_003_888_000_000)
    finally:
        await svc._client.aclose()
    url = seen[0]
    assert url.startswith("https://testnet.binancefuture.com/fapi/v1/klines")
    assert "startTime=1700000000000" in url
    assert "endTime=1700003888000000" in url
    assert "limit=1500" in url
    assert "interval=5m" in url
    # Same normalised row shape as get_klines, so downstream needs no branch.
    assert rows[0]["open_time"] == 1 and rows[0]["close"] == 105.0
    assert rows[0]["close_time"] == 2
