"""WebSocket manager for Binance Futures market data streams."""
import asyncio
import json
import logging
from typing import Callable

import aiohttp

from backend.config import settings

logger = logging.getLogger(__name__)

PROD_WS = "wss://fstream.binance.com"
TESTNET_WS = "wss://testnet.binancefuture.com"


class BinanceWebSocketManager:
    """Manages Binance Futures WebSocket connections with auto-reconnect."""

    def __init__(self, testnet: bool | None = None):
        self._testnet = testnet if testnet is not None else settings.BINANCE_TESTNET
        self._base_url = TESTNET_WS if self._testnet else PROD_WS
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._subscriptions: dict[str, Callable] = {}
        self._listen_task: asyncio.Task | None = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 30.0
        self._running = False



    async def connect(self):
        """Establish WebSocket connection."""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._base_url, heartbeat=30)
        self._running = True
        self._listen_task = asyncio.create_task(self._listen())
        logger.info(f"WebSocket connected to {'testnet' if self._testnet else 'prod'}")


    async def subscribe(self, stream: str, callback: Callable):
        """Subscribe to a stream. stream format: '<symbol>@<stream_type>'."""
        self._subscriptions[stream] = callback
        if self._ws and self._ws.closed:
            await self.connect()
        elif self._ws and not self._ws.closed:
            msg = {"method": "SUBSCRIBE", "params": [stream], "id": len(self._subscriptions)}
            await self._ws.send_str(json.dumps(msg))
            logger.info(f"Subscribed to {stream}")

    async def unsubscribe(self, stream: str):
        """Unsubscribe from a stream."""
        self._subscriptions.pop(stream, None)
        if self._ws and not self._ws.closed:
            msg = {"method": "UNSUBSCRIBE", "params": [stream], "id": len(self._subscriptions)}
            await self._ws.send_str(json.dumps(msg))

    async def close(self):
        """Close WebSocket gracefully."""
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session:
            await self._session.close()
        logger.info("WebSocket closed")




    async def _listen(self):
        """Main listen loop with auto-reconnect."""
        while self._running:
            try:
                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        try:
                            data = json.loads(msg.data)
                            await self._handle_message(data)
                        except json.JSONDecodeError:
                            pass
                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        raise
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE):
                        break
            except (asyncio.CancelledError, aiohttp.ClientError):
                pass
            except Exception as e:
                logger.warning(f"WebSocket error: {e}, reconnecting in {self._reconnect_delay}s")

            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self._max_reconnect_delay)
                try:
                    await self.connect()
                    # Re-subscribe all
                    for stream, cb in list(self._subscriptions.items()):
                        msg = {"method": "SUBSCRIBE", "params": [stream], "id": 1}
                        await self._ws.send_str(json.dumps(msg))
                except Exception as e:
                    logger.error(f"Reconnect failed: {e}")

    async def _handle_message(self, data: dict):
        """Route incoming messages to subscribers."""
        stream = data.get("stream", "")
        if stream in self._subscriptions:
            try:
                payload = data.get("data", {})
                await self._subscriptions[stream](payload)
            except Exception as e:
                logger.error(f"Error in callback for {stream}: {e}")

    # --- Convenience methods ---

    async def watch_aggtrades(self, symbol: str, callback: Callable):
        """Stream: <symbol>@aggTrade"""
        stream = f"{symbol.lower()}@aggTrade"
        async def wrapper(data):
            cb_data = {
                "symbol": data.get("s", symbol),
                "price": float(data.get("p", 0)),
                "quantity": float(data.get("q", 0)),
                "is_buyer_maker": data.get("m", False),
                "trade_time": data.get("T", 0),
            }
            await callback(cb_data)
        await self.subscribe(stream, wrapper)

    async def watch_klines(self, symbol: str, interval: str, callback: Callable):
        """Stream: <symbol>@kline_<interval>"""
        stream = f"{symbol.lower()}@kline_{interval}"
        async def wrapper(data):
            k = data.get("k", {})
            cb_data = {
                "symbol": symbol.upper(),
                "interval": interval,
                "open": float(k.get("o", 0)),
                "high": float(k.get("h", 0)),
                "low": float(k.get("l", 0)),
                "close": float(k.get("c", 0)),
                "volume": float(k.get("v", 0)),
                "is_closed": k.get("x", False),
                "open_time": k.get("t", 0),
                "close_time": k.get("T", 0),
            }
            await callback(cb_data)
        await self.subscribe(stream, wrapper)

    async def watch_book_ticker(self, symbol: str, callback: Callable):
        """Stream: <symbol>@bookTicker"""
        stream = f"{symbol.lower()}@bookTicker"
        async def wrapper(data):
            cb_data = {
                "symbol": data.get("s", symbol.upper()),
                "bid_price": float(data.get("b", 0)),
                "bid_qty": float(data.get("B", 0)),
                "ask_price": float(data.get("a", 0)),
                "ask_qty": float(data.get("A", 0)),
            }
            await callback(cb_data)
        await self.subscribe(stream, wrapper)
