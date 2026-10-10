#!/usr/bin/env python3
"""
One-time script to create the admin user.
Run: python scripts/create_admin.py
Safe to run multiple times — skips if admin already exists.
"""
import asyncio
import os
import sys

# Add parent dir to path so we can import backend modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passlib.hash import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.models.user import User


async def create_admin():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as db:
        # Check if admin already exists
        result = await db.execute(
            select(User).where(User.username == settings.ADMIN_USERNAME)
        )
        existing = result.scalar_one_or_none()

        if existing:
            print(f"Admin user '{settings.ADMIN_USERNAME}' already exists. Skipping.")
            return

        # Create admin user
        password_hash = bcrypt.hash(settings.ADMIN_PASSWORD, rounds=12)

        admin = User(
            username=settings.ADMIN_USERNAME,
            password_hash=password_hash,
            role="admin",
        )
        db.add(admin)
        await db.commit()

        print(f"Admin user '{settings.ADMIN_USERNAME}' created successfully.")
        print(f"Login at: https://fubot.bedikian.cc/login")


if __name__ == "__main__":
    asyncio.run(create_admin())
