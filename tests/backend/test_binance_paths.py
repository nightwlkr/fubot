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
        # exchangeInfo (for LOT_SIZE rounding) returns a minimal payload
        if "exchangeInfo" in url:
            return httpx.Response(200, json={"symbols": []})
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
