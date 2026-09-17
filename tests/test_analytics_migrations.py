"""Integration test: Alembic upgrade/downgrade roundtrip on the analytics database.

Runs only when ANALYTICS_TEST_DATABASE_URL is set (a scratch database the test
may create/drop schemas in). Not part of the default unit tier — CI/dev runs
set the variable explicitly:

    ANALYTICS_TEST_DATABASE_URL=postgresql+asyncpg://z@localhost:5432/open_notebook_analytics_test \\
        uv run pytest tests/test_analytics_migrations.py -v
"""

import os

import pytest
from conftest import requires_analytics_pg
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = [
    pytest.mark.integration,
    requires_analytics_pg(),
]

ALEMBIC_INI = "open_notebook/analytics/alembic.ini"

EXPECTED_COLUMNS = {
    "transaction_id",
    "transaction_date",
    "customer_id",
    "customer_name",
    "service",
    "amount_myr",
    "status",
    "data_team",
}


def _alembic(*args: str) -> None:
    import subprocess

    env = dict(os.environ)
    env["ANALYTICS_DATABASE_URL"] = os.environ["ANALYTICS_TEST_DATABASE_URL"]
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", ALEMBIC_INI, *args],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def _table_exists() -> bool:
    engine = create_async_engine(os.environ["ANALYTICS_TEST_DATABASE_URL"])
    async with engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: inspect(sync_conn).get_table_names()
        )
    await engine.dispose()
    return "sales_transactions" in tables


def test_alembic_upgrade_then_downgrade() -> None:
    _alembic("upgrade", "head")
    import asyncio

    assert asyncio.run(_table_exists()), "upgrade head did not create sales_transactions"

    _alembic("downgrade", "base")
    assert not asyncio.run(_table_exists()), "downgrade base left sales_transactions behind"