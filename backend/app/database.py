from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_async_database_url

# Guarded checks hold a session across a judge call; parallel evaluations run many at once.
engine = create_async_engine(
    get_async_database_url(), pool_pre_ping=True, pool_size=10, max_overflow=20
)
session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with session_factory() as session:
        yield session
