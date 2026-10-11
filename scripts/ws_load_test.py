#!/usr/bin/env python3
"""PHASE6_STEP4 — WebSocket load test.

ConnectionManager keys sockets by user_id (one WS per user, per PHASE4_STEP2),
so a meaningful fan-out test needs DISTINCT users. This script:

  1. creates N users directly in the database (idempotent)
  2. logs each in via the API
  3. connects N concurrent authenticated WebSocket clients
  4. triggers real server broadcasts (bot start/stop alerts)
  5. verifies every client receives them and measures latency

Usage:
    python scripts/ws_load_test.py --clients 50 --password <admin-password>
"""
import argparse
import asyncio
import json
import os
import time

import asyncpg
import httpx
import websockets
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def database_dsn(explicit: str | None) -> str:
    dsn = explicit or os.environ.get("DATABASE_URL")
    if not dsn:
        repo_env = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(repo_env):
            for line in open(repo_env):
                if line.startswith("DATABASE_URL="):
                    dsn = line.split("=", 1)[1].strip()
                    break
    assert dsn, "DATABASE_URL not found (pass --database-url or set the env var)"
    return dsn.replace("+asyncpg", "")


async def create_users(dsn: str, n: int):
    conn = await asyncpg.connect(dsn)
    try:
        for i in range(n):
            username = f"ws_load_{i}"
            await conn.execute(
                "INSERT INTO users (username, password_hash, role) VALUES ($1, $2, 'admin') "
                "ON CONFLICT (username) DO UPDATE SET password_hash = EXCLUDED.password_hash",
                username, pwd_context.hash("ws-load-test-pass"),
            )
    finally:
        await conn.close()


async def login_all(api: str, n: int) -> list[str]:
    async with httpx.AsyncClient() as client:
        tokens = []
        for i in range(n):
            res = await client.post(f"{api}/api/auth/login",
                                    json={"username": f"ws_load_{i}",
                                          "password": "ws-load-test-pass"})
            res.raise_for_status()
            tokens.append(res.json()["token"])
        return tokens


async def client_task(url: str, token: str, idx: int, results: dict, ready: asyncio.Event):
    latencies = []
    results[f"alerts_{idx}"] = 0
    results[f"max_latency_{idx}"] = 0.0
    t0 = time.perf_counter()
    async with websockets.connect(url, open_timeout=10) as ws:
        results[f"connect_{idx}"] = time.perf_counter() - t0
        await ws.send(json.dumps({"type": "auth", "data": {"token": token}}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        results[f"auth_{idx}"] = reply.get("type") == "auth_ok"
        ready.set()
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if msg.get("type") == "alert":
                    lat = time.perf_counter() - results["broadcast_t0"]
                    latencies.append(lat)
                    # update counters live — main() cancels this task before the
                    # recv loop ever exits on its own
                    results[f"alerts_{idx}"] = len(latencies)
                    results[f"max_latency_{idx}"] = max(results[f"max_latency_{idx}"], lat)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            pass


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="ws://127.0.0.1:8001/ws")
    parser.add_argument("--api", default="http://127.0.0.1:8001")
    parser.add_argument("--clients", type=int, default=5)
    parser.add_argument("--admin-user", default="admin")
    parser.add_argument("--password", required=True,
                        help="admin password (to trigger bot start/stop broadcasts)")
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    dsn = database_dsn(args.database_url)
    await create_users(dsn, args.clients)
    tokens = await login_all(args.api, args.clients)

    results: dict = {}
    ready = asyncio.Event()
    tasks = [asyncio.create_task(client_task(args.url, tok, i, results, ready))
             for i, tok in enumerate(tokens)]

    deadline = time.time() + 20
    while time.time() < deadline:
        if sum(1 for k in results if k.startswith("auth_")) == args.clients:
            break
        await asyncio.sleep(0.1)
    authed = sum(1 for k in results if k.startswith("auth_") and results[k])
    print(f"authenticated: {authed}/{args.clients}")

    # trigger real broadcasts (admin starts/stops the bot → alerts to all users)
    async with httpx.AsyncClient() as client:
        admin_token = (await client.post(
            f"{args.api}/api/auth/login",
            json={"username": args.admin_user, "password": args.password})).json()["token"]
        for path, payload in (("/api/bot/start", {"mode": "paper"}),
                              ("/api/bot/stop", None)):
            results["broadcast_t0"] = time.perf_counter()
            res = await client.post(f"{args.api}{path}", cookies={"fubot_token": admin_token},
                                    json=payload)
            print(f"{path} → {res.status_code}")
            await asyncio.sleep(1.5)

    await asyncio.sleep(3)
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    alert_counts = [v for k, v in results.items() if k.startswith("alerts_")]
    connects = [v for k, v in results.items() if k.startswith("connect_")]
    latencies = [v for k, v in results.items() if k.startswith("max_latency_") and v > 0]
    got_both = sum(1 for c in alert_counts if c >= 2)
    print(f"\nclients receiving both broadcasts: {got_both}/{args.clients}")
    if alert_counts:
        print(f"alert delivery: min={min(alert_counts)} max={max(alert_counts)}")
    if latencies:
        print(f"broadcast latency (worst client): {max(latencies)*1000:.0f}ms")
    if connects:
        print(f"connect+auth handshake: max={max(connects)*1000:.0f}ms "
              f"avg={sum(connects)/len(connects)*1000:.0f}ms")
    ok = authed == args.clients and got_both >= args.clients * 0.9
    print(f"\nVERDICT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
