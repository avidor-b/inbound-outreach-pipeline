"""Async Postgres connection pool using asyncpg.

Usage:
    from outreach_pipeline.db.pool import get_pool, init_pool, close_pool

    # At startup:
    await init_pool()

    # In async code:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM contacts WHERE id=$1", contact_id)

    # At shutdown:
    await close_pool()
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

_pool: asyncpg.Pool | None = None


_LOCAL_DEV_DATABASE_URL = "postgresql://pipeline:pipeline@localhost:5432/outreach"


def _get_database_url() -> str:
    """Resolve DATABASE_URL with environment-aware fallbacks.

    Resolution order:
      1. DATABASE_URL env var (always wins — this is what SSM/compose sets
         in cloud deployments)
      2. config.yaml database_url (local dev convenience)
      3. Local-dev default (postgresql://pipeline:pipeline@localhost:...)
         — only in non-production; production hard-errors to prevent silent
         localhost connection attempts from ECS/EC2 tasks.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        from outreach_pipeline.config import load_config
        configured = load_config().get("database_url")
        if configured:
            return configured
    except Exception:
        pass
    if os.environ.get("ENVIRONMENT") == "production":
        raise RuntimeError(
            "DATABASE_URL is required in production. Set it via env var "
            "(e.g. from SSM Parameter Store /outreach/DATABASE_URL)."
        )
    return _LOCAL_DEV_DATABASE_URL


async def init_pool(database_url: str | None = None, min_size: int = 2, max_size: int = 10) -> asyncpg.Pool:
    """Initialize the global connection pool. Call once at startup."""
    global _pool
    if _pool is not None:
        return _pool

    url = database_url or _get_database_url()

    _pool = await asyncpg.create_pool(url, min_size=min_size, max_size=max_size)

    # Run migrations — apply all *.sql files in lexical order so new
    # migrations land automatically. Files are idempotent (CREATE TABLE
    # IF NOT EXISTS, ALTER TABLE ... ADD COLUMN IF NOT EXISTS) so
    # re-running on an existing database is safe.
    async with _pool.acquire() as conn:
        migrations_dir = Path(__file__).parent / "migrations"
        if migrations_dir.exists():
            for migration_file in sorted(migrations_dir.glob("*.sql")):
                await conn.execute(migration_file.read_text())

    return _pool


def get_pool() -> asyncpg.Pool:
    """Get the global pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError(
            "Database pool not initialized. Call `await init_pool()` first."
        )
    return _pool


async def close_pool():
    """Close the global pool. Call at shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def now() -> datetime:
    """UTC datetime for Postgres TIMESTAMPTZ columns."""
    return datetime.now(timezone.utc)


def parse_date(value: str | None) -> "date | None":
    """Parse a date string (YYYY-MM-DD or ISO datetime) to a date object for asyncpg.

    Returns None for empty/None values.
    """
    if not value:
        return None
    from datetime import date as _date
    try:
        return _date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def get_config_value(key, default):
    """Read a value from config.yaml, with fallback to default."""
    try:
        from outreach_pipeline.config import load_config
        return load_config().get(key, default)
    except Exception:
        return default


# JSONB columns whose shape is a list (array). Kept separate so row_to_dict
# can decay them to [] instead of "" when null/unparseable.
_LIST_JSONB_FIELDS = frozenset({
    "promised_resources",
    "referenced_resource_ids",
    "unverified_promises",
})

_PARSED_JSONB_FIELDS = (
    "properties", "intent_signals", "properties_snapshot", "data",
    "promised_resources", "referenced_resource_ids", "unverified_promises",
)


def row_to_dict(row: asyncpg.Record | None) -> dict:
    """Convert an asyncpg Record to a dict with parsed JSON fields.

    Replaces None with '' to avoid null values in LLM tool responses.
    List-shaped JSONB fields (see _LIST_JSONB_FIELDS) decay to [] instead.
    """
    if row is None:
        return {}
    d = dict(row)
    # asyncpg returns JSONB as strings — parse to dicts/lists
    for field in _PARSED_JSONB_FIELDS:
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                import logging
                logging.getLogger(__name__).warning(
                    "Failed to parse JSONB field %s: %.100s", field, d[field],
                )
                # List-shaped fields decay to [], dict-shaped to {}
                d[field] = [] if field in _LIST_JSONB_FIELDS else {}
    # Replace None values with empty strings — but keep list-shaped JSONB
    # fields as [] so consumers can iterate without type-checking.
    for k, v in d.items():
        if v is None:
            d[k] = [] if k in _LIST_JSONB_FIELDS else ""
        elif isinstance(v, dict):
            d[k] = {dk: "" if dv is None else dv for dk, dv in v.items()}
    return d


def rows_to_dicts(rows: list[asyncpg.Record]) -> list[dict]:
    """Convert a list of asyncpg Records to dicts."""
    return [row_to_dict(r) for r in rows]
