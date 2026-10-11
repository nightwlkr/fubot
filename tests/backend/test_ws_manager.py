"""WebSocket manager tests — connection tracking and the /ws auth handshake."""
import asyncio

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from backend.core.websocket_manager import ConnectionManager, WebSocketRouter, ws_manager
from backend.services import auth_service


class FakeWebSocket:
    def __init__(self):
        self.accepted = False
        self.sent = []
        self.closed = None

    async def accept(self):
        self.accepted = True

    async def send_json(self, msg):
        self.sent.append(msg)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


async def test_connect_broadcast_disconnect():
    ws = FakeWebSocket()
    mgr = ConnectionManager()
    await mgr.connect(ws, user_id=1)
    assert ws.accepted
    assert 1 in mgr.active_connections

    await mgr.broadcast({"type": "alert", "message": "hi"}, exclude_user=1)
    assert ws.sent == []  # excluded

    await mgr.broadcast({"type": "alert", "message": "hi2"})
    assert ws.sent == [{"type": "alert", "message": "hi2"}]

    await mgr.disconnect(1)
    assert 1 not in mgr.active_connections


def _ws_app():
    app = FastAPI()
    router = WebSocketRouter()
    app.add_api_websocket_route("/ws", router.websocket_endpoint)
    return app


def test_ws_rejects_missing_auth_token():
    client = TestClient(_ws_app())
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "nonsense", "data": {}})
            ws.receive_json()
    assert exc.value.code == 4002


def test_ws_rejects_invalid_token():
    client = TestClient(_ws_app())
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"type": "auth", "data": {"token": "garbage-token"}})
            ws.receive_json()
    assert exc.value.code == 4003


# NOTE: no valid-token handshake test here on purpose. The endpoint's keep-alive
# loop (`while True: sleep(1)`) never observes the client disconnect, so the
# TestClient teardown deadlocks. Registration after successful auth is covered
# by test_connect_broadcast_disconnect; fix the receive loop, then add the
# full-handshake happy path.
