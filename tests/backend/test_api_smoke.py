"""API smoke tests — full request cycle against the real app and dev database.

Covers the router-prefix fix (PHASE1), auth guard, and empty-state responses
for every resource endpoint. Requires the local PostgreSQL `fubot` database
(see alembic/AGENTS.md provisioning).
"""

import pytest

from tests.backend.conftest import TEST_PASSWORD  # shared constant

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
    assert res.json()["username"] == test_user.username


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
