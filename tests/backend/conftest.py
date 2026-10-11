"""
Async pytest fixtures for backend tests.

Usage: tests/ ├── conftest.py (this file) └── test_indicators.py

Run: PYTHONPATH=. pytest tests/backend/ -v

"""
import asyncio
import sys
import os
from datetime import datetime
from typing import AsyncGenerator, Generator

import pytest
import pytest_asyncio
import pytest_asyncio

# Ensure the project root is on sys.path so 'backend' is importable.
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.database import Base, AsyncSessionLocal  # noqa: E402


@pytest.fixture(scope="session")
def event_loop() -> Generator[asyncio.AbstractEventLoop, None, None]:
    """Create a session-scoped event loop for async tests."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSessionLocal, None]:
    """Provide a rolled-back async session for each test."""
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()
