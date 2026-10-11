"""API smoke tests — full request cycle against the real app and dev database.

Covers the router-prefix fix (PHASE1), auth guard, and empty-state responses
for every resource endpoint. Requires the local PostgreSQL `fubot` database
(see alembic/AGENTS.md provisioning).
"""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.models.position import Position as PositionModel
from tests.backend.conftest import TEST_PASSWORD, run_async  # shared constants

def _login(client, user):
    res = client.post("/api/auth/login", json={"username": user.username, "password": TEST_PASSWORD})
    assert res.status_code == 200
    # The backend's cookie is set with secure=True, which httpx won't replay over
    # plain http://testserver — same reason dev relies on the frontend's JS cookie.
    # Mirror that behavior: carry the token from the response body.
    client.cookies.set("fubot_token", res.json()["token"])
    return res


def test_login_sets_cookie_and_returns_token(client, test_user):
    res = _login(client, test_user)
    body = res.json()
    assert body["token"]
    assert body["user"]["username"] == test_user.username
    assert "fubot_token" in res.cookies


def test_health_and_root(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert "FUBOT" in client.get("/").json()["message"]


def test_no_double_prefix(client, test_user):
    """Regression: routers used to be double-prefixed (/api/auth/auth/login)."""
    res = client.post("/api/auth/auth/login", json={"username": test_user.username, "password": TEST_PASSWORD})
    assert res.status_code == 404


def test_me_requires_auth(client):
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_me_with_cookie(client, test_user):
    _login(client, test_user)
    res = client.get("/api/auth/me")
    assert res.status_code == 200
    body = res.json()
    assert body["username"] == test_user.username
    # /me echoes the token so a cookie-only session can re-seed localStorage
    assert "token" in body
    assert body["token"]


def test_me_and_endpoints_accept_bearer_only(client, test_user):
    """Regression: endpoints used to read ONLY the httpOnly cookie, so a valid
    localStorage token (sent as Bearer) still got 401 → checkAuth cleared the
    session and the user was signed out on refresh. All endpoints must accept
    the Bearer header with NO cookie present."""
    _login(client, test_user)
    token = client.get("/api/auth/me").json()["token"]
    client.cookies.clear()  # cookie gone — only the Bearer header remains

    res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["username"] == test_user.username

    # A protected data endpoint must also accept Bearer-only auth
    res = client.get("/api/dashboard/overview", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200


def test_wrong_password_401(client, test_user):
    res = client.post("/api/auth/login", json={"username": test_user.username, "password": "nope"})
    assert res.status_code == 401


EMPTY_OK = [
    "/api/positions/",
    "/api/trades/",
    "/api/trades/stats",
    "/api/signals/",
    "/api/signals/active",
    "/api/signals/missed",
    "/api/orders/",
    "/api/performance/equity",
    "/api/performance/monthly",
    "/api/performance/strategies",
]


@pytest.mark.parametrize("path", EMPTY_OK)
def test_resource_endpoints_empty_state(client, test_user, path):
    _login(client, test_user)
    res = client.get(path)
    assert res.status_code == 200
    assert res.json() is not None


def test_settings_trading_roundtrip(client, test_user):
    _login(client, test_user)
    res = client.get("/api/settings/trading")
    assert res.status_code == 200
    original = res.json()

    res = client.put("/api/settings/trading", json={
        "risk_percent": original["risk_percent"],
        "max_positions": original["max_positions"],
    })
    assert res.status_code == 200

    res = client.get("/api/settings/trading")
    assert res.json()["risk_percent"] == original["risk_percent"]
    assert res.json()["max_positions"] == original["max_positions"]


def test_bot_engine_wired(client, test_user, monkeypatch):
    """PHASE4_STEP1: main.py lifespan builds the BotEngine and registers it."""
    _login(client, test_user)

    res = client.get("/api/bot/status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "stopped"
    assert body["mode"] == "paper"
    assert body["daily_stats"] is not None  # engine.get_status() served real data

    # Keep the scan loop off the network: BinanceService returns empty candles
    async def empty_klines(self, symbol, interval="1h", limit=100):
        return []

    async def no_position(self, symbol):
        return None

    monkeypatch.setattr("backend.services.binance_service.BinanceService.get_klines", empty_klines)
    monkeypatch.setattr("backend.services.binance_service.BinanceService.get_position", no_position)

    res = client.post("/api/bot/start", json={"mode": "paper"})
    assert res.status_code == 200
    assert res.json()["status"] == "running"

    res = client.get("/api/bot/status")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running"
    assert body["scan_count"] >= 1  # the first scan runs immediately

    res = client.post("/api/bot/stop")
    assert res.status_code == 200
    assert client.get("/api/bot/status").json()["status"] == "stopped"


def test_missing_resources_404(client, test_user):
    _login(client, test_user)
    assert client.get("/api/trades/999999").status_code == 404
    assert client.get("/api/positions/999999").status_code == 404


def test_overview_payload_matches_frontend_contract(client, test_user):
    """Regression: overview bot_status must carry scan_count/last_scan (Header renders
    them) and today must carry the PnLCard fields (pnl_r/wins/losses/daily_limit_status).
    The old bot_status read nonexistent app.state attrs — scan_count was missing and
    the dashboard crashed after login."""
    _login(client, test_user)
    body = client.get("/api/dashboard/overview").json()
    bot = body["bot_status"]
    assert {"status", "mode", "scan_count", "last_scan"} <= set(bot)
    assert bot["status"] in ("running", "stopped")
    assert bot["scan_count"] >= 0

    today = client.get("/api/dashboard/today").json()
    assert {"pnl_r", "pnl_usdt", "wins", "losses", "trades_count", "daily_limit_status"} <= set(today)
    assert today["daily_limit_status"]["limit_r"] == 3.0


def test_overview_ok_after_scan_sets_last_scan(client, test_user, monkeypatch):
    """Regression: engine.get_status() returns last_scan as an ISO string; the
    overview endpoint used to call .isoformat() on it again and 500'd once the
    first scan had run."""
    _login(client, test_user)

    async def empty_klines(self, symbol, interval="1h", limit=100):
        return []

    async def no_position(self, symbol):
        return None

    monkeypatch.setattr("backend.services.binance_service.BinanceService.get_klines", empty_klines)
    monkeypatch.setattr("backend.services.binance_service.BinanceService.get_position", no_position)

    assert client.post("/api/bot/start", json={"mode": "paper"}).status_code == 200
    import time
    time.sleep(0.3)  # let the first scan complete → last_scan set
    res = client.get("/api/dashboard/overview")
    assert res.status_code == 200, res.text
    bot = res.json()["bot_status"]
    assert bot["scan_count"] >= 1
    assert bot["last_scan"] is not None  # an ISO string, not a crash
    client.post("/api/bot/stop")


def test_position_payload_has_live_mark_price(client, test_user):
    """Regression: PositionsTable renders pos.mark_price/unrealized_pnl via .toFixed —
    the payload used to omit them, crashing the Overview the moment a paper
    position existed."""
    import asyncio as _asyncio
    from datetime import datetime, timezone as _tz
    from sqlalchemy import select as _select

    async def _make_position():
        engine = create_async_engine(settings.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as db:
                db.add(PositionModel(
                    symbol="BTCUSDT", side="LONG", entry_price=100.0, quantity=1.0,
                    leverage=10, stop_loss=95.0, take_profit=110.0, status="OPEN"))
                await db.commit()
                row = (await db.execute(select(PositionModel).where(
                    PositionModel.symbol == "BTCUSDT",
                    PositionModel.status == "OPEN"))).scalars().first()
                return row.id if row else None
        finally:
            await engine.dispose()

    async def _remove(pos_id):
        engine = create_async_engine(settings.DATABASE_URL)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as db:
                row = await db.get(PositionModel, pos_id)
                if row:
                    await db.delete(row)
                    await db.commit()
        finally:
            await engine.dispose()

    _login(client, test_user)
    pos_id = run_async(_make_position())
    try:
        res = client.get("/api/positions/")
        assert res.status_code == 200
        found = [p for p in res.json()["positions"] if p["id"] == pos_id]
        assert found, "the open position must be returned"
        p = found[0]
        assert isinstance(p["mark_price"], (int, float)) and p["mark_price"] > 0
        # BTCUSDT LONG: unrealized pnl = (mark - 100) * 1 — signed, finite
        assert isinstance(p["unrealized_pnl"], (int, float))
        res2 = client.get("/api/dashboard/overview")
        overview_positions = [q for q in res2.json()["open_positions"] if q["id"] == pos_id]
        assert overview_positions and "mark_price" in overview_positions[0]
    finally:
        run_async(_remove(pos_id))


def test_strategies_endpoint_returns_r_buckets(client, test_user):
    """Performance page RDistributionChart reads {bucket, count} pairs — the old
    payload was per-strategy stats, so b.bucket.startsWith crashed the page and
    (with no error boundary) killed the whole app."""
    _login(client, test_user)
    res = client.get("/api/performance/strategies")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data, "bucket list must not be empty"
    for item in data:
        assert set(item) == {"bucket", "count"}
        assert item["count"] >= 0
    labels = [item["bucket"] for item in data]
    assert "3R+" in labels and "0 to 0.5R" in labels  # spec bucket labels
