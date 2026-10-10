from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from backend.config import settings

engine = create_async_engine(settings.DATABASE_URL, echo=False, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db():
    """Create all tables (for initial setup only — use alembic for migrations in production)."""
    async with engine.begin() as conn:
        from backend.models import User, Account, Symbol, Setting, Signal, Position, Trade, Order, EquityLog, DailySummary
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    await engine.dispose()
