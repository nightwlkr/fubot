# Phase 4 — Step 2: WebSocket Server + Real-time Push
## FUBOT: Live Dashboard Updates

---

## Objective
Build `WebSocketManager` and wire it into the FastAPI app so the React dashboard receives live updates on positions, P&L, orders, signals, and alerts without polling.

---

## Context
- **Protocol**: WebSocket over WSS (`wss://fubot.bedikian.cc/ws`)
- **Auth**: JWT token passed in first message after connection
- **Broadcast**: Server → Client only (client never sends market data — only acks)
- **Message format**: JSON `{ type: string, data: object }`
- **Depends on**: Phase 1 Step 4 (JWT auth)

---

## WebSocket Message Protocol

### Client → Server
```json
{ "type": "auth", "token": "eyJ..." }
```
Response: `{ "type": "auth_ok" }` or `{ "type": "auth_error", "message": "Invalid token" }`

### Server → Client (broadcast events)

| Event Type | Trigger | Data |
|---|---|---|
| `position_update` | New position opened | `{ position: {...} }` |
| `position_closed` | Trade completed | `{ position_id, symbol, pnl_r, exit_reason }` |
| `order_update` | Order filled/cancelled | `{ order: {...} }` |
| `signal_update` | New signal detected | `{ signal: {...} }` |
| `pnl_update` | P&L changes (every 30s) | `{ pnl_r, pnl_usdt, open_count }` |
| `bot_status` | Bot started/stopped/status change | `{ status, mode }` |
| `alert` | Any alert | `{ level, message, symbol }` |
| `equity_snapshot` | Equity log (every 5 min) | `{ balance_usdt, timestamp }` |

---

## WebSocketManager: `backend/core/websocket_manager.py`

```python
from fastapi import WebSocket, WebSocketDisconnect
from typing import Callable, Any

class ConnectionManager:
    """Manages all connected WebSocket clients."""

    def __init__(self):
        self.active_connections: dict[int, WebSocket] = {}  # user_id → websocket
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
    """
    FastAPI WebSocket endpoint handler.
    Handles auth and routes messages.
    """

    async def websocket_endpoint(self, websocket: WebSocket):
        """
        WS /ws
        1. Accept connection
        2. Wait for auth message
        3. Add to manager
        4. Keep alive, handle disconnect
        """
        # Accept
        await websocket.accept()

        # Wait for auth (max 10 seconds)
        try:
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        except asyncio.TimeoutError:
            await websocket.close(code=4001, reason="Auth timeout")
            return

        if auth_msg.get('type') != 'auth' or not auth_msg.get('token'):
            await websocket.close(code=4002, reason="Missing auth token")
            return

        # Validate JWT
        try:
            payload = jwt.decode(
                auth_msg['token'],
                settings.FUBOT_SECRET_KEY,
                algorithms=['HS256']
            )
            user_id = payload['sub']
        except jwt.InvalidTokenError:
            await websocket.close(code=4003, reason="Invalid token")
            return

        # Connected
        await ws_manager.connect(websocket, user_id)

        try:
            # Keep-alive loop (pings/pongs handled by framework)
            while True:
                # We don't receive meaningful messages from client in this design
                # Just wait for disconnect
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            await ws_manager.disconnect(user_id)
```

---

## FastAPI Route

In `backend/main.py`:
```python
from backend.core.websocket_manager import ws_manager, WebSocketRouter

ws_router = WebSocketRouter()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await ws_router.websocket_endpoint(websocket)
```

---

## Broadcast Examples (From Other Services)

```python
# From PositionManager when trade closes:
await ws_manager.broadcast({
    'type': 'position_closed',
    'data': {
        'position_id': position.id,
        'symbol': position.symbol,
        'pnl_r': pnl_r,
        'exit_reason': reason,
        'closed_at': datetime.utcnow().isoformat()
    }
})

# From BotEngine every 30s:
await ws_manager.broadcast({
    'type': 'pnl_update',
    'data': {
        'pnl_r': current_pnl_r,
        'pnl_usdt': current_pnl_usdt,
        'open_count': open_position_count,
        'timestamp': datetime.utcnow().isoformat()
    }
})
```

---

## React Dashboard Client Hook

In the frontend (`frontend/src/hooks/useWebSocket.ts`):
```typescript
export function useWebSocket(onMessage: (msg: WsMessage) => void) {
  const [ws, setWs] = useState<WebSocket | null>(null);

  useEffect(() => {
    const token = getAuthToken(); // from cookie
    const socket = new WebSocket(`wss://fubot.bedikian.cc/ws`);

    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'auth', token }));
    };

    socket.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      onMessage(msg);
    };

    socket.onclose = () => {
      // Reconnect after 5 seconds
      setTimeout(() => setWs(new WebSocket(...)), 5000);
    };

    setWs(socket);
    return () => socket.close();
  }, []);

  return ws;
}
```

---

## Deliverables

### 1. `backend/core/websocket_manager.py`
- `ConnectionManager` class
- `ws_manager` singleton
- `WebSocketRouter` class

### 2. Update `backend/main.py`
Add WebSocket route.

### 3. `frontend/src/hooks/useWebSocket.ts`
React hook for dashboard WebSocket connection with auto-reconnect.

### 4. Validation
```bash
# Test with websocat
wscat -H "Upgrade: websocket" -H "Connection: Upgrade" \
  wss://fubot.bedikian.cc/ws

# In another terminal, trigger an event from the bot
# The message should appear in wscat within 1 second
```

---

## Dependencies
- **Requires**: Phase 1 Step 4 (JWT auth), Phase 4 Step 1 (BotEngine)

## Files Modified/Created
- `backend/core/websocket_manager.py`
- `backend/main.py` (updated)
- `frontend/src/hooks/useWebSocket.ts`

## Next Step
Phase 4 Step 3: REST API Completion

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
