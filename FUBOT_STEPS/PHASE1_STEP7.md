# Phase 1 — Step 7: Create Admin User
## FUBOT: First-Time Admin Setup

---

## Objective
Create a one-time setup script that creates the first admin user in the database. This should only be run once — it checks if an admin already exists and skips if so.

---

## Context
- **Single user**: admin, username from `ADMIN_USERNAME` env var
- **Password**: from `ADMIN_PASSWORD` env var
- **Storage**: `users` table
- **Depends on**: Phase 1 Steps 1, 2, and 4

---

## Script: `scripts/create_admin.py`

```python
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
```

---

## Deliverables

### 1. `scripts/create_admin.py`
File as shown above.

### 2. `scripts/init_db.py` (bonus — creates DB if it doesn't exist)
```python
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
```

### 3. Run on VPS
```bash
# On VPS, as fubot user:
cd /home/vick/fubot

# First ensure DB exists
python scripts/init_db.py

# Run migrations
alembic upgrade head

# Create admin user
python scripts/create_admin.py
```

### 4. Validation
- `scripts/create_admin.py` exits cleanly with "Admin user created" (first run)
- `scripts/create_admin.py` exits cleanly with "already exists" (second run)
- Login via `POST /api/auth/login` with admin credentials returns JWT
- `GET /api/auth/me` with that JWT returns correct user

---

## Dependencies
- **Requires**: Phase 1 Steps 1, 2, 4

## Files Modified/Created
- `/home/vick/fubot/scripts/create_admin.py`
- `/home/vick/fubot/scripts/init_db.py`

## Next Step
**Phase 1 Complete!** — Proceed to Phase 2: Market Data & Signal Detection

---

*Read the full FUBOT product plan at: `FUBOT_PRODUCT_PLAN.md`*
