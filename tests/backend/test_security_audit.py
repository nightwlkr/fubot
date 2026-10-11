"""PHASE6_STEP3 — security control checks (runnable subset of the audit).

Covers: JWT cookie flags, encrypted-at-rest API keys (never plaintext in the
DB), no secrets in logs, auth-bypass resistance (SQL-injection-style payloads),
and unauthenticated access guards.
"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.encryption import decrypt, encrypt, mask_key
from backend.models.account import Account

from tests.backend.conftest import TEST_PASSWORD, run_async as _run


def _login(client, user):
    res = client.post("/api/auth/login", json={"username": user.username,
                                               "password": TEST_PASSWORD})
    assert res.status_code == 200
    client.cookies.set("fubot_token", res.json()["token"])
    return res


async def _fetch_stored_api_keys():
    from backend.config import settings
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            result = await db.execute(select(Account))
            return [(a.id, a.api_key_encrypted, a.api_secret_encrypted) for a in result.scalars().all()]
    finally:
        await engine.dispose()


async def _delete_accounts():
    from backend.config import settings
    engine = create_async_engine(settings.DATABASE_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with Session() as db:
            for a in (await db.execute(select(Account))).scalars().all():
                await db.delete(a)
            await db.commit()
    finally:
        await engine.dispose()

def test_jwt_cookie_flags(client, test_user):
    """PHASE6_STEP3 §3: JWT cookie must be httpOnly + Secure + SameSite=Lax."""
    res = _login(client, test_user)
    set_cookie = res.headers.get("set-cookie", "")
    assert "fubot_token=" in set_cookie
    assert "HttpOnly" in set_cookie or "httponly" in set_cookie.lower()
    assert "Secure" in set_cookie or "secure" in set_cookie.lower()
    assert "SameSite=lax" in set_cookie or "samesite=lax" in set_cookie.lower()


def test_api_keys_stored_encrypted_never_plaintext(client, test_user):
    """PHASE6_STEP3 §1: stored credentials must not be plaintext."""
    _login(client, test_user)
    res = client.post("/api/settings/apikey",
                      json={"api_key": "PLAINTEXT_AUDIT_KEY", "api_secret": "PLAINTEXT_AUDIT_SECRET"})
    assert res.status_code == 200
    try:
        rows = _run(_fetch_stored_api_keys())
        assert rows, "an account row must exist after saving keys"
        for _id, key_enc, secret_enc in rows:
            assert "PLAINTEXT_AUDIT_KEY" not in (key_enc or "")
            assert "PLAINTEXT_AUDIT_SECRET" not in (secret_enc or "")
            # and they must decrypt back to the original values
            assert decrypt(key_enc) == "PLAINTEXT_AUDIT_KEY"
            assert decrypt(secret_enc) == "PLAINTEXT_AUDIT_SECRET"
    finally:
        _run(_delete_accounts())


def test_no_secrets_logged(caplog, client, test_user):
    """PHASE6_STEP3 §2: the plaintext API key must never appear in logs."""
    _login(client, test_user)
    with caplog.at_level("DEBUG"):
        client.post("/api/settings/apikey",
                    json={"api_key": "SECRETLOGCHECK123", "api_secret": "SECRETLOGCHECK456"})
        client.post("/api/auth/login", json={"username": test_user.username,
                                             "password": "SECRETLOGCHECK789"})
    assert "SECRETLOGCHECK123" not in caplog.text
    assert "SECRETLOGCHECK456" not in caplog.text
    assert "SECRETLOGCHECK789" not in caplog.text
    _run(_delete_accounts())


def test_sql_injection_payloads_rejected_safely(client, test_user):
    """ORM-bound params everywhere — injection payloads get 401/404/422, never data."""
    _login(client, test_user)
    payloads = ["' OR 1=1 --", "'; DROP TABLE users; --", "admin'--"]
    for p in payloads:
        res = client.post("/api/auth/login", json={"username": p, "password": p})
        assert res.status_code in (401, 422), f"unexpected status for payload {p!r}"
        res = client.get(f"/api/trades/{p}")
        assert res.status_code in (404, 422), f"unexpected status for payload {p!r}"


def test_unauthenticated_access_blocked(client, test_user):
    client.cookies.clear()
    for path in ("/api/positions/", "/api/trades/", "/api/settings/trading",
                 "/api/bot/status", "/api/orders/", "/api/performance/equity"):
        assert client.get(path).status_code == 401, path


def test_encryption_is_aes_gcm_with_random_nonce():
    ct1, ct2 = encrypt("same-input"), encrypt("same-input")
    assert ct1 != ct2, "nonce reuse detected"
    assert mask_key("ABCD1234") == "****1234"
