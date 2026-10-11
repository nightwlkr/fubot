"""API smoke tests — full request cycle against the real app and dev database.

Covers the router-prefix fix (PHASE1), auth guard, and empty-state responses
for every resource endpoint. Requires the local PostgreSQL `fubot` database
(see alembic/AGENTS.md provisioning).
"""
import asyncio
import secrets

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import settings
from backend.database import AsyncSessionLocal
from backend.main import app
from backend.models.user import User
from backend.services import auth_service

TEST_PASSWORD = "test-pass-1234"


def _run(coro):
    """Run an async DB operation on its own engine so the pool never crosses
    event loops with the TestClient portal (which owns backend.database.engine)."""
    return asyncio.run(coro)


async def _create_user():
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            username = f"api_test_{secrets.token_hex(4)}"
            db.add(User(username=username,
                        password_hash=auth_service.hash_password(TEST_PASSWORD),
                        role="admin"))
            await db.commit()
            result = await db.execute(select(User).where(User.username == username))
            return result.scalar_one()
    finally:
        await engine.dispose()


async def _delete_user(user_id):
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            result = await db.execute(select(User).where(User.id == user_id))
            u = result.scalar_one_or_none()
            if u:
                await db.delete(u)
                await db.commit()
    finally:
        await engine.dispose()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def test_user():
    user = _run(_create_user())
    yield user
    _run(_delete_user(user.id))


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


def test_bot_status_without_engine(client, test_user):
    _login(client, test_user)
    res = client.get("/api/bot/status")
    assert res.status_code == 200
    assert res.json()["status"] == "stopped"

    res = client.post("/api/bot/start", json={"mode": "paper"})
    assert res.status_code == 503  # engine not wired into main.py yet


def test_missing_resources_404(client, test_user):
    _login(client, test_user)
    assert client.get("/api/trades/999999").status_code == 404
    assert client.get("/api/positions/999999").status_code == 404
