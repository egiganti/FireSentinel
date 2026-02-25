"""Database engine setup for async SQLite via aiosqlite.

Provides factory functions for creating the async engine, session maker,
and initializing the database schema.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from firesentinel.db.models import Base


def get_engine(db_path: str) -> AsyncEngine:
    """Create an async SQLAlchemy engine for the given SQLite path.

    Args:
        db_path: Path to the SQLite database file (e.g., ./data/firesentinel.db).

    Returns:
        Configured async engine using aiosqlite.
    """
    url = f"sqlite+aiosqlite:///{db_path}"
    return create_async_engine(url, echo=False)


def get_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given engine.

    Args:
        engine: The async SQLAlchemy engine.

    Returns:
        An async session maker that produces AsyncSession instances.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db(engine: AsyncEngine) -> None:
    """Create all database tables if they don't exist.

    Args:
        engine: The async SQLAlchemy engine.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
