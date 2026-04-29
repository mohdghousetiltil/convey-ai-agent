"""Async SQLAlchemy engine + session factory.

One engine per process. Sessions are cheap and per-request.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
import asyncio

from sqlalchemy import text
from sqlalchemy.engine.url import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}

def _normalize_database_url(raw_url: str | None) -> str:
    value = (raw_url or "").strip()
    if not value:
        return "postgresql+asyncpg://postgres:sqlfordatamatically@localhost:5432/convey_agent"
    if value.startswith("postgresql+asyncpg://") or value.startswith("sqlite+aiosqlite://"):
        return value
    if value.startswith("postgres://"):
        return "postgresql+asyncpg://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+asyncpg://" + value[len("postgresql://") :]
    return value


DATABASE_URL = _normalize_database_url(
    os.getenv("CONVEY_DATABASE_URL") or os.getenv("DATABASE_URL")
)
DB_POOL_SIZE = int(os.getenv("CONVEY_DB_POOL_SIZE", "5"))
DB_MAX_OVERFLOW = int(os.getenv("CONVEY_DB_MAX_OVERFLOW", "10"))
DB_ECHO = _env_bool("CONVEY_DB_ECHO", False)
DB_POOL_PRE_PING = _env_bool("CONVEY_DB_POOL_PRE_PING", os.name != "nt")
DB_USE_NULL_POOL = _env_bool("CONVEY_DB_USE_NULL_POOL", os.name == "nt")
DB_POOL_RECYCLE = int(os.getenv("CONVEY_DB_POOL_RECYCLE_SECONDS", "1800"))


class Base(DeclarativeBase):
    """Single declarative base for all ORM models."""


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_runtime_schema_ready = False
_runtime_schema_lock = asyncio.Lock()


async def ensure_runtime_schema() -> None:
    global _runtime_schema_ready
    if _runtime_schema_ready:
        return
    async with _runtime_schema_lock:
        if _runtime_schema_ready:
            return
        engine = get_engine()
        async with engine.begin() as conn:
            dialect = make_url(DATABASE_URL).get_backend_name()
            if dialect == "sqlite":
                column_rows = await conn.exec_driver_sql("PRAGMA table_info(runs)")
                column_names = {str(row[1]) for row in column_rows.fetchall()}
                if "user_id" not in column_names:
                    await conn.exec_driver_sql("ALTER TABLE runs ADD COLUMN user_id TEXT")
                await conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_runs_user_id ON runs(user_id)"
                )
            else:
                await conn.execute(
                    text("ALTER TABLE runs ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id)")
                )
                await conn.execute(
                    text("CREATE INDEX IF NOT EXISTS ix_runs_user_id ON runs(user_id)")
                )
        _runtime_schema_ready = True


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        engine_kwargs = {
            "echo": DB_ECHO,
            "future": True,
        }
        if DB_USE_NULL_POOL:
            engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs.update(
                {
                    "pool_size": DB_POOL_SIZE,
                    "max_overflow": DB_MAX_OVERFLOW,
                    "pool_pre_ping": DB_POOL_PRE_PING,
                    "pool_recycle": DB_POOL_RECYCLE,
                    "pool_use_lifo": True,
                }
            )
        _engine = create_async_engine(DATABASE_URL, **engine_kwargs)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Opens a session, commits on success, rolls back on error."""
    await ensure_runtime_schema()
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Call on app shutdown."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
