"""
Async pytest fixtures for backend tests.

Run: PYTHONPATH=. pytest tests/backend/ -q

NOTE: no custom event_loop fixture — pytest-asyncio 0.24 deprecates redefining
it, and a session-scoped loop couples every async test (and the TestClient
portal lifecycles) to one loop. The default function-scoped loop keeps tests
isolated.
"""
import asyncio
import secrets
import sys
import os
from typing import AsyncGenerator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import pytest_asyncio

# Ensure the project root is on sys.path so 'backend' is importable.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.database import AsyncSessionLocal  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models.user import User  # noqa: E402
from backend.services import auth_service  # noqa: E402

TEST_PASSWORD = "test-pass-1234"


def run_async(coro):
    """Run a coroutine on a fresh loop, preserving the thread's current loop.

    asyncio.run() leaves set_event_loop(None) behind, which breaks the next
    pytest-asyncio test depending on collection order — restore it instead.
    """
    policy = asyncio.get_event_loop_policy()
    try:
        prev = policy.get_event_loop()
    except RuntimeError:
        prev = None
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        asyncio.set_event_loop(prev)
        loop.close()


async def _create_user():
    engine = create_async_engine(__import__('backend.config', fromlist=['settings']).settings.DATABASE_URL)
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
    engine = create_async_engine(__import__('backend.config', fromlist=['settings']).settings.DATABASE_URL)
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
def test_user():
    """Admin test user in the dev DB, created per module and removed after."""
    user = run_async(_create_user())
    yield user
    run_async(_delete_user(user.id))


@pytest.fixture()
def client():
    """Fresh app lifespan per test — the bot engine's DB session must never
    span TestClient portal lifecycles (PendingRollbackError otherwise)."""
    from backend.database import engine as shared_engine
    run_async(shared_engine.dispose())  # drop pooled connections from other loops
    with TestClient(app) as c:
        yield c
    run_async(shared_engine.dispose())


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSessionLocal, None]:
    """Provide a rolled-back async session for each test."""
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
