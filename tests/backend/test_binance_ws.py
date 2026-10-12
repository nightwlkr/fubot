"""Regression tests for BinanceWebSocketManager — the reconnect task leak.

Bug: _listen()'s reconnect path called connect(), which spawned a SECOND
_listen() task while the old loop kept running → concurrent receive() on one
socket ('Concurrent call to receive() is not allowed') and an exponential
task/memory leak (1.5GB, starved event loop, auth requests hung).
"""
import asyncio

import pytest

from backend.services.binance_ws import BinanceWebSocketManager


class FakeClosedWS:
    """Minimal stand-in for an aiohttp WS that immediately ends iteration."""
    closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self):
        pass

    async def send_str(self, s):
        pass


class FakeSession:
    closed = True

    async def close(self):
        pass


async def test_reconnect_does_not_spawn_listener_tasks():
    """_listen must reconnect inline (no new task). After several reconnect
    cycles there must be exactly ONE listener task."""
    mgr = BinanceWebSocketManager(testnet=False)
    mgr._session = FakeSession()

    connect_calls = []

    async def fake_open_socket():
        # simulate a fresh socket each reconnect
        ws = FakeClosedWS()
        mgr._ws = ws
        connect_calls.append(ws)

    mgr._open_socket = fake_open_socket

    # first connect() spawns the single listener
    await mgr._open_socket()
    mgr._running = True
    mgr._listen_task = asyncio.create_task(mgr._listen())

    # let the listener run through a few reconnect cycles
    await asyncio.sleep(0.05)

    # there must be exactly one listener task (not one per reconnect)
    tasks = [t for t in asyncio.all_tasks()
             if t is not asyncio.current_task() and not t.done()]
    listen_tasks = [t for t in tasks if t.get_coro().__qualname__.startswith(
        "BinanceWebSocketManager._listen")]
    assert len(listen_tasks) == 1, (
        f"expected 1 listener task, found {len(listen_tasks)} — reconnect "
        f"spawned extra listeners (task leak)")
    assert len(connect_calls) >= 1, "reconnect should have opened sockets"

    mgr._running = False
    mgr._listen_task.cancel()
    try:
        await mgr._listen_task
    except asyncio.CancelledError:
        pass


async def test_close_cancels_listener():
    mgr = BinanceWebSocketManager(testnet=False)
    mgr._session = FakeSession()
    mgr._ws = FakeClosedWS()
    mgr._running = True
    mgr._listen_task = asyncio.create_task(mgr._listen())
    await mgr.close()
    assert mgr._listen_task.done(), "close() must cancel the listener task"
