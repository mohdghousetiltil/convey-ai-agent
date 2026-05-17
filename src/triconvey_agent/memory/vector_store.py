"""
Vector memory store — PostgreSQL+pgvector when available, SQLite fallback otherwise.

Each memory record:
  user_id     TEXT
  session_id  TEXT
  content     TEXT     — raw exchange text (Q + A)
  embedding   BLOB/vector(1536)
  created_at  TIMESTAMP
  expires_at  TIMESTAMP  (= created_at + 20 days TTL)

All public functions are async and fail gracefully — callers always get
an empty result / False / 0 on any failure so the chat pipeline continues.
"""
from __future__ import annotations

import json
import logging
import math
import os
import struct
from datetime import UTC, datetime, timedelta
from typing import Any

LOG = logging.getLogger(__name__)

_TTL_DAYS = 20
_POOL: Any = None        # asyncpg pool (PostgreSQL path)
_SQLITE_DB: Any = None   # aiosqlite connection (SQLite path)
_BACKEND: str = "none"   # "postgres" | "sqlite" | "none"


# ---------------------------------------------------------------------------
# Backend detection + init
# ---------------------------------------------------------------------------


async def _init_backend() -> str:
    """Detect and initialise the best available backend. Returns backend name."""
    global _POOL, _SQLITE_DB, _BACKEND

    if _BACKEND != "none":
        return _BACKEND

    dsn = os.getenv("DATABASE_URL") or os.getenv("CONVEY_DATABASE_URL") or ""

    # --- Try PostgreSQL+pgvector ---
    if dsn and not dsn.startswith("sqlite"):
        try:
            import asyncpg
            # asyncpg only accepts "postgresql://" or "postgres://".
            # Strip SQLAlchemy dialect suffixes like "+asyncpg" before connecting.
            asyncpg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1) \
                             .replace("postgres+asyncpg://", "postgres://", 1)
            pool = await asyncpg.create_pool(asyncpg_dsn, min_size=1, max_size=5, command_timeout=10)
            # Check pgvector is available
            async with pool.acquire() as conn:
                await conn.execute("SELECT 1 FROM pg_extension WHERE extname='vector'")
            _POOL = pool
            _BACKEND = "postgres"
            LOG.info("vector_store: using PostgreSQL+pgvector backend")
            return _BACKEND
        except Exception as exc:
            LOG.warning("vector_store: PostgreSQL/pgvector unavailable (%s) — trying SQLite", exc)

    # --- Fall back to SQLite ---
    try:
        import aiosqlite
        local_app = os.getenv("LOCALAPPDATA") or os.getenv("HOME") or ""
        if local_app:
            import pathlib
            db_path = pathlib.Path(local_app) / "TriConveyAgent" / "vector_memory.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            db_path = ":memory:"

        _SQLITE_DB = await aiosqlite.connect(str(db_path))
        await _SQLITE_DB.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                session_id TEXT NOT NULL,
                content    TEXT NOT NULL,
                embedding  BLOB NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )
        """)
        await _SQLITE_DB.execute("CREATE INDEX IF NOT EXISTS ix_mem_user ON memories(user_id)")
        await _SQLITE_DB.execute("CREATE INDEX IF NOT EXISTS ix_mem_exp  ON memories(expires_at)")
        await _SQLITE_DB.commit()
        _BACKEND = "sqlite"
        LOG.info("vector_store: using SQLite backend at %s", db_path)
        return _BACKEND
    except Exception as exc:
        LOG.warning("vector_store: SQLite backend unavailable (%s) — memory disabled", exc)
        _BACKEND = "none"
        return _BACKEND


async def close_pool() -> None:
    global _POOL, _SQLITE_DB, _BACKEND
    if _POOL is not None:
        await _POOL.close()
        _POOL = None
    if _SQLITE_DB is not None:
        await _SQLITE_DB.close()
        _SQLITE_DB = None
    _BACKEND = "none"


# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------


def _vec_to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _blob_to_vec(blob: bytes) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def save_memory(
    user_id: str,
    session_id: str,
    content: str,
    embedding: list[float],
) -> bool:
    backend = await _init_backend()
    if backend == "none":
        return False

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=_TTL_DAYS)

    if backend == "postgres":
        try:
            vec_str = "[" + ",".join(f"{v:.8f}" for v in embedding) + "]"
            async with _POOL.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO memories (user_id, session_id, content, embedding, created_at, expires_at)
                    VALUES ($1, $2, $3, $4::vector, $5, $6)
                    """,
                    user_id, session_id, content, vec_str, now, expires_at,
                )
            return True
        except Exception as exc:
            LOG.warning("vector_store.save_memory (postgres) failed: %s", exc)
            return False

    # SQLite path
    try:
        import uuid
        await _SQLITE_DB.execute(
            """
            INSERT INTO memories (id, user_id, session_id, content, embedding, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                user_id,
                session_id,
                content,
                _vec_to_blob(embedding),
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
        await _SQLITE_DB.commit()
        return True
    except Exception as exc:
        LOG.warning("vector_store.save_memory (sqlite) failed: %s", exc)
        return False


async def search_memory(
    user_id: str,
    query_embedding: list[float],
    limit: int = 5,
) -> list[dict[str, Any]]:
    backend = await _init_backend()
    if backend == "none":
        return []

    now = datetime.now(UTC)

    if backend == "postgres":
        try:
            vec_str = "[" + ",".join(f"{v:.8f}" for v in query_embedding) + "]"
            async with _POOL.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT id, session_id, content, created_at,
                           (embedding <=> $1::vector) AS distance
                    FROM   memories
                    WHERE  user_id = $2 AND expires_at > $3
                    ORDER BY distance ASC
                    LIMIT $4
                    """,
                    vec_str, user_id, now, limit,
                )
            return [
                {
                    "id": str(r["id"]),
                    "session_id": str(r["session_id"]),
                    "content": r["content"],
                    "created_at": r["created_at"].isoformat(),
                    "similarity": round(1.0 - float(r["distance"]), 4),
                }
                for r in rows
            ]
        except Exception as exc:
            LOG.warning("vector_store.search_memory (postgres) failed: %s", exc)
            return []

    # SQLite path — load unexpired rows and rank by cosine similarity in Python
    try:
        async with _SQLITE_DB.execute(
            "SELECT id, session_id, content, embedding, created_at FROM memories "
            "WHERE user_id = ? AND expires_at > ? LIMIT 200",
            (user_id, now.isoformat()),
        ) as cursor:
            rows = await cursor.fetchall()

        scored = []
        for row in rows:
            vec = _blob_to_vec(row[3])
            sim = _cosine_similarity(query_embedding, vec)
            scored.append((sim, row))
        scored.sort(key=lambda x: x[0], reverse=True)

        return [
            {
                "id": row[0],
                "session_id": row[1],
                "content": row[2],
                "created_at": row[4],
                "similarity": round(sim, 4),
            }
            for sim, row in scored[:limit]
        ]
    except Exception as exc:
        LOG.warning("vector_store.search_memory (sqlite) failed: %s", exc)
        return []


async def cleanup_expired() -> int:
    backend = await _init_backend()
    if backend == "none":
        return 0

    now = datetime.now(UTC)
    try:
        if backend == "postgres":
            async with _POOL.acquire() as conn:
                result = await conn.execute(
                    "DELETE FROM memories WHERE expires_at <= $1", now
                )
            try:
                n = int(str(result).split()[-1])
            except (ValueError, IndexError):
                n = 0
        else:
            cursor = await _SQLITE_DB.execute(
                "DELETE FROM memories WHERE expires_at <= ?", (now.isoformat(),)
            )
            n = cursor.rowcount
            await _SQLITE_DB.commit()

        LOG.info("vector_store.cleanup_expired: deleted %d rows", n)
        return n
    except Exception as exc:
        LOG.warning("vector_store.cleanup_expired failed: %s", exc)
        return 0
