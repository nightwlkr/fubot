# Phase 6 — Step 4: WebSocket Load Test
## FUBOT: Verify Real-time Performance Under Concurrent Connections

---

## Objective
Test that the WebSocket server handles multiple simultaneous dashboard connections without lag or dropped messages.

---

## Context
- **Depends on**: Phase 6 Step 1 (bot running)
- **Target**: 5+ simultaneous WebSocket connections
- **Acceptable latency**: Message delivery < 1 second after server-side event
- **Test tool**: `websocat` or Python `websocket-client`

---

## Test Setup

Install test tool on the VPS or locally:

```bash
# On VPS or local machine
pip install websocket-client
```

---

## Test: Single Connection Latency

```bash
# Connect to WebSocket and watch for position_update messages
TOKEN="your_jwt_token_here"
wscat -c "wss://fubot.bedikian.cc/ws" \
  --header "Authorization: Bearer $TOKEN" \
  --execute "sleep 30"
```

Or with Python:

```python
import json
import websocket
import time
import threading

TOKEN = "your_token_here"
received = []
start = None

def on_message(ws, message):
    global received, start
    data = json.loads(message)
    received.append((time.time(), data))
    if start and len(received) == 1:
        latency = received[0][0] - start
        print(f"First message latency: {latency:.3f}s")

def on_open(ws):
    global start
    ws.send(json.dumps({"type": "auth", "data": {"token": TOKEN}}))
    start = time.time()
    print("Connected and authenticated")

ws = websocket.WebSocketApp(
    "wss://fubot.bedikian.cc/ws",
    on_message=on_message,
    on_open=on_open,
)
ws.run_forever(ping_interval=15)
```

Expected: First `position_update` or `bot_status` message arrives within 1 second.

---

## Test: 5 Simultaneous Connections

```python
import json
import websocket
import time
import threading

TOKEN = "your_token_here"

results = []
lock = threading.Lock()

def make_connection(conn_id):
    received = []
    connect_time = time.time()
    connected = [False]

    def on_message(ws, message):
        data = json.loads(message)
        if data.get("type") == "bot_status":
            latency = time.time() - connect_time
            with lock:
                results.append({"id": conn_id, "latency": latency, "status": "ok"})
            ws.close()

    def on_open(ws):
        connected[0] = True
        ws.send(json.dumps({"type": "auth", "data": {"token": TOKEN}}))

    ws = websocket.WebSocketApp(
        "wss://fubot.bedikian.cc/ws",
        on_message=on_message,
        on_open=on_open,
    )
    t = threading.Thread(target=lambda: ws.run_forever(ping_interval=15))
    t.daemon = True
    t.start()
    return t

# Open 5 connections simultaneously
threads = []
for i in range(5):
    t = make_connection(i)
    threads.append(t)
    time.sleep(0.1)  # Stagger slightly

# Wait for all to receive first message
for t in threads:
    t.join(timeout=10)

print("\n=== Load Test Results ===")
for r in sorted(results, key=lambda x: x["id"]):
    print(f"Connection {r['id']}: latency={r['latency']:.3f}s status={r['status']}")
print(f"Total connections: {len(results)}/5")
print(f"Average latency: {sum(r['latency'] for r in results)/len(results):.3f}s")
```

Expected:
- All 5 connections establish and receive messages
- Average latency < 1 second
- No connection errors

---

## Test: Connection Drop and Reconnect

```python
import json, websocket, time

TOKEN = "your_token_here"

# Connect, wait for message, disconnect, reconnect
ws1 = websocket.WebSocketApp("wss://fubot.bedikian.cc/ws")
messages = []

def on_message(ws, message):
    messages.append(json.loads(message))

ws1 = websocket.WebSocketApp(
    "wss://fubot.bedikian.cc/ws",
    on_message=on_message,
)
ws1.on_open = lambda ws: ws.send(json.dumps({"type": "auth", "data": {"token": TOKEN}}))

t = threading.Thread(target=lambda: ws1.run_forever())
t.daemon = True
t.start()
time.sleep(2)
ws1.close()
print(f"After disconnect: received {len(messages)} messages before close")

# Reconnect immediately
ws2 = websocket.WebSocketApp("wss://fubot.bedikian.cc/ws", on_message=on_message)
ws2.on_open = lambda ws: ws.send(json.dumps({"type": "auth", "data": {"token": TOKEN}}))
t2 = threading.Thread(target=lambda: ws2.run_forever())
t2.daemon = True
t2.start()
time.sleep(2)
print(f"After reconnect: received {len(messages)} total messages")
ws2.close()
```

Expected: Reconnect succeeds, messages continue arriving.

---

## Deliverables

1. Single connection latency < 1 second
2. All 5 simultaneous connections succeed
3. Average latency acceptable under load
4. Reconnect logic works correctly

---

## Next Step
Phase 6 Step 5: Production Deploy — Nginx SSL + DNS + certbot

---
*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
