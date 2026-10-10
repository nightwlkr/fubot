#!/usr/bin/env python3
"""
Initialize the database (create if not exists).
Run: python scripts/init_db.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.config import settings


async def init_db():
    # Connect to postgres default db to create fubot db if not exists
    default_url = settings.DATABASE_URL.rsplit("/", 1)[0] + "/postgres"

    engine = create_async_engine(default_url, isolation_level="AUTOCOMMIT")

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'fubot'")
        )
        exists = result.scalar()

        if not exists:
            await conn.execute(text("CREATE DATABASE fubot"))
            print("Database 'fubot' created.")
        else:
            print("Database 'fubot' already exists.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_db())
