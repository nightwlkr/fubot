"""WebSocket manager — connection management, auth, broadcast for dashboard."""
import asyncio
import json
import logging
from typing import Any, Callable, Dict

from fastapi import WebSocket, WebSocketDisconnect

from backend.config import settings

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages all connected WebSocket clients."""

    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {}  # user_id → websocket
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        async with self._lock:
            self.active_connections[user_id] = websocket
        logger.info(f"WebSocket: user {user_id} connected. Total: {len(self.active_connections)}")

    async def disconnect(self, user_id: int):
        async with self._lock:
            if user_id in self.active_connections:
                del self.active_connections[user_id]
        logger.info(f"WebSocket: user {user_id} disconnected. Total: {len(self.active_connections)}")

    async def send_personal(self, user_id: int, message: dict):
        """Send message to a specific user."""
        async with self._lock:
            ws = self.active_connections.get(user_id)
        if ws:
            try:
                await ws.send_json(message)
            except Exception as e:
                logger.warning(f"Failed to send to user {user_id}: {e}")

    async def broadcast(self, message: dict, exclude_user: int | None = None):
        """Broadcast message to all connected users."""
        async with self._lock:
            connections = list(self.active_connections.items())

        for user_id, ws in connections:
            if exclude_user and user_id == exclude_user:
                continue
            try:
                await ws.send_json(message)
            except Exception:
                pass  # Client disconnected, will be cleaned up


# Global singleton
ws_manager = ConnectionManager()


class WebSocketRouter:
    """FastAPI WebSocket endpoint handler — handles auth and routes messages."""

    async def websocket_endpoint(self, websocket: WebSocket):
        """WS /ws — authenticate, manage connection."""
        await websocket.accept()

        # Wait for auth message (max 10 seconds)
        try:
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except asyncio.TimeoutError:
            await websocket.close(code=4001, reason="Auth timeout")
            return

        if auth_msg.get("type") != "auth" or not auth_msg.get("data", {}).get("token"):
            await websocket.close(code=4002, reason="Missing auth token")
            return

        token = auth_msg["data"]["token"]

        # Validate JWT
        try:
            from jose import jwt, JWTError
            payload = jwt.decode(token, settings.FUBOT_SECRET_KEY, algorithms=["HS256"])
            user_id = int(payload.get("sub", 0))
        except (JWTError, ValueError):
            await websocket.close(code=4003, reason="Invalid token")
            return

        # Connected
        await ws_manager.connect(websocket, user_id)

        try:
            # Keep-alive loop
            while True:
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            await ws_manager.disconnect(user_id)
        except Exception as e:
            logger.warning(f"WebSocket error for user {user_id}: {e}")
            await ws_manager.disconnect(user_id)
