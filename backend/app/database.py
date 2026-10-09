from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_async_database_url, get_settings

# Guarded checks hold a session across a judge call; parallel evaluations run many at once.
# Each process has its own pool, so the API (several processes) sets a smaller one.
engine = create_async_engine(
    get_async_database_url(),
    pool_pre_ping=True,
    pool_size=get_settings().db_pool_size,
    max_overflow=get_settings().db_max_overflow,
)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with session_factory() as session:
        yield session
