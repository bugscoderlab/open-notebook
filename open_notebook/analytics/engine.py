"""Async PostgreSQL engine for the analytics subsystem (ADR-009, issue #9).

Single cached SQLAlchemy 2.0 async engine pointed at the analytics database
(``ANALYTICS_DATABASE_URL``, Postgres.app ``open_notebook_analytics`` by
default). Every query runs through :func:`run_readonly_query`, which:

- opens a transaction with ``SET TRANSACTION READ ONLY`` (the LLM never
  writes SQL, and the session could not execute a write even if a template
  regressed),
- sets a 15-second statement timeout,
- enforces the 500-row cap at the API boundary (templates also carry their
  own ``LIMIT``).

The engine is created lazily so tests can point ``ANALYTICS_DATABASE_URL``
at a scratch database before the first query.
"""

import os
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.sql import Executable

DEFAULT_DATABASE_URL = "postgresql+asyncpg://z@localhost:5432/open_notebook_analytics"
STATEMENT_TIMEOUT_MS = 15_000
MAX_ROWS = 500

_engine: Optional[AsyncEngine] = None
_engine_url: Optional[str] = None


def analytics_database_url() -> str:
    """Resolve the analytics DSN from the environment (defaults to Postgres.app)."""
    return os.environ.get("ANALYTICS_DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine() -> AsyncEngine:
    """Return the cached async engine, recreating it if the URL changed.

    Uses NullPool deliberately: connections are never shared across event
    loops (the API, the worker, and each pytest-asyncio test each run their
    own loop), matching the repository's no-connection-pooling stance for
    SurrealDB.
    """
    global _engine, _engine_url
    url = analytics_database_url()
    if _engine is None or _engine_url != url:
        if _engine is not None:
            # Best effort: the old engine is disposed lazily by the event loop.
            _engine.sync_engine.dispose(close=False)
        _engine = create_async_engine(url, poolclass=NullPool)
        _engine_url = url
    return _engine


async def dispose_engine() -> None:
    """Dispose the cached engine (test teardown / URL switches)."""
    global _engine, _engine_url
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _engine_url = None


async def run_readonly_query(
    sql: Executable, params: Optional[Dict[str, Any]] = None
) -> Tuple[List[Dict[str, Any]], int]:
    """Execute one parameterized query in a read-only, time-limited transaction.

    Args:
        sql: Parameterized SQL (named bind params only — never an f-string).
            May be a plain ``text()`` clause or a compiled template statement
            with expanding IN-list binds.
        params: Bind values. The caller (server) controls every value.

    Returns:
        Tuple of (rows as dicts, row count). At most :data:`MAX_ROWS` rows.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        # First statement in the transaction: this transaction can only read.
        await conn.execute(text("SET TRANSACTION READ ONLY"))
        await conn.execute(
            text(f"SET LOCAL statement_timeout = {int(STATEMENT_TIMEOUT_MS)}")
        )
        result = await conn.execute(sql, params or {})
        rows = [dict(row._mapping) for row in result.fetchmany(MAX_ROWS + 1)]
    if len(rows) > MAX_ROWS:
        logger.warning(
            f"Analytics query hit the {MAX_ROWS}-row cap; truncating result"
        )
        rows = rows[:MAX_ROWS]
    return rows, len(rows)
