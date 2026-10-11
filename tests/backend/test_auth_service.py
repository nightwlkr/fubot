"""Auth service tests — JWT roundtrip, password hashing, login rate limiting."""
from types import SimpleNamespace

import pytest

from backend.services import auth_service


def make_user():
    return SimpleNamespace(id=1, username="tester", role="admin",
                           password_hash=auth_service.hash_password("s3cret-pass"))


def test_jwt_roundtrip():
    token = auth_service.create_access_token(make_user())
    payload = auth_service.decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "1"
    assert payload["username"] == "tester"
    assert payload["role"] == "admin"


def test_jwt_rejects_garbage():
    assert auth_service.decode_access_token("not-a-token") is None


def test_password_hash_verify():
    h = auth_service.hash_password("s3cret-pass")
    assert h != "s3cret-pass"
    assert auth_service.verify_password("s3cret-pass", h) is True
    assert auth_service.verify_password("wrong", h) is False


@pytest.fixture(autouse=True)
def clear_rate_limit():
    auth_service._failed_attempts.clear()
    yield
    auth_service._failed_attempts.clear()


class FakeDB:
    pass


async def fake_get_user(db, username):
    if username == "tester":
        return make_user()
    return None


async def test_authenticate_success_and_failure(monkeypatch):
    monkeypatch.setattr(auth_service, "get_user_by_username", fake_get_user)
    user = await auth_service.authenticate(FakeDB(), "tester", "s3cret-pass", "1.2.3.4")
    assert user is not None and user.username == "tester"

    user = await auth_service.authenticate(FakeDB(), "tester", "wrong", "1.2.3.4")
    assert user is None


async def test_rate_limit_after_five_failures(monkeypatch):
    monkeypatch.setattr(auth_service, "get_user_by_username", fake_get_user)
    for _ in range(5):
        u = await auth_service.authenticate(FakeDB(), "tester", "bad", "9.9.9.9")
        assert u is None
    with pytest.raises(auth_service.RateLimitExceeded):
        await auth_service.authenticate(FakeDB(), "tester", "s3cret-pass", "9.9.9.9")


async def test_rate_limit_is_per_ip(monkeypatch):
    monkeypatch.setattr(auth_service, "get_user_by_username", fake_get_user)
    for _ in range(5):
        await auth_service.authenticate(FakeDB(), "tester", "bad", "8.8.8.8")
    # different IP is unaffected
    user = await auth_service.authenticate(FakeDB(), "tester", "s3cret-pass", "7.7.7.7")
    assert user is not None
