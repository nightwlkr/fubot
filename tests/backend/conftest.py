"""
Async pytest fixtures for backend tests.

Run: PYTHONPATH=. pytest tests/backend/ -q

NOTE: no custom event_loop fixture — pytest-asyncio 0.24 deprecates redefining
it, and a session-scoped loop couples every async test (and the TestClient
portal lifecycles) to one loop. The default function-scoped loop keeps tests
isolated.
"""
import sys
import os
from typing import AsyncGenerator

import pytest_asyncio

# Ensure the project root is on sys.path so 'backend' is importable.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.database import AsyncSessionLocal  # noqa: E402


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSessionLocal, None]:
    """Provide a rolled-back async session for each test."""
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
