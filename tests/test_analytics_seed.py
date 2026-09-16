"""Integration test: idempotent CSV seed + exact test-pack numbers.

Runs only when ANALYTICS_TEST_DATABASE_URL is set (a scratch database the
test may create/drop schemas in). Not part of the default unit tier:

    ANALYTICS_TEST_DATABASE_URL=postgresql+asyncpg://z@localhost:5432/open_notebook_analytics_test \\
        uv run pytest tests/test_analytics_seed.py -v

Expected numbers come from _jobbrief/testdata/sales_transactions_2026.csv
(see issue #8 acceptance criteria).
"""

import os
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANALYTICS_TEST_DATABASE_URL"),
    reason="ANALYTICS_TEST_DATABASE_URL not set",
)

ALEMBIC_INI = "open_notebook/analytics/alembic.ini"
CSV_PATH = (
    Path(__file__).parent.parent
    / "_jobbrief"
    / "testdata"
    / "sales_transactions_2026.csv"
)


def _alembic(*args: str) -> None:
    env = dict(os.environ)
    env["ANALYTICS_DATABASE_URL"] = os.environ["ANALYTICS_TEST_DATABASE_URL"]
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", ALEMBIC_INI, *args],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


async def _seed() -> tuple[int, int]:
    from open_notebook.analytics.seed import seed

    return await seed(CSV_PATH, os.environ["ANALYTICS_TEST_DATABASE_URL"])


def test_seed_is_idempotent_and_matches_test_pack() -> None:
    import asyncio

    _alembic("upgrade", "head")
    try:
        inserted_1, skipped_1 = asyncio.run(_seed())
        assert inserted_1 == 77, inserted_1
        assert skipped_1 == 0, skipped_1

        inserted_2, skipped_2 = asyncio.run(_seed())
        assert inserted_2 == 0, inserted_2
        assert skipped_2 == 77, skipped_2

        asyncio.run(_assert_test_pack_numbers())
    finally:
        _alembic("downgrade", "base")


async def _assert_test_pack_numbers() -> None:
    engine = create_async_engine(os.environ["ANALYTICS_TEST_DATABASE_URL"])
    async with engine.connect() as conn:
        no_dupes = await conn.scalar(
            text(
                "SELECT count(*) = count(DISTINCT transaction_id) "
                "FROM sales_transactions"
            )
        )
        assert no_dupes

        total_rows = await conn.scalar(text("SELECT count(*) FROM sales_transactions"))
        assert total_rows == 77

        sarah = (
            await conn.execute(
                text(
                    "SELECT count(*), sum(amount_myr), avg(amount_myr) "
                    "FROM sales_transactions "
                    "WHERE customer_name = 'Sarah Lim' AND status = 'completed'"
                )
            )
        ).one()
        assert sarah[0] == 24  # completed transactions
        assert sarah[1] == Decimal("8460.00")  # total spend MYR
        assert sarah[2] == Decimal("352.5000000000000000")  # average MYR

        full_grooms = await conn.scalar(
            text(
                "SELECT count(*) FROM sales_transactions "
                "WHERE customer_name = 'Sarah Lim' "
                "AND service = 'Full Groom' AND status = 'completed'"
            )
        )
        assert full_grooms == 11

        ranking = (
            await conn.execute(
                text(
                    "SELECT customer_name, sum(amount_myr) AS total "
                    "FROM sales_transactions WHERE status = 'completed' "
                    "GROUP BY customer_name ORDER BY total DESC LIMIT 4"
                )
            )
        ).all()
        assert [row[1] for row in ranking] == [
            Decimal("8460.00"),
            Decimal("6940.00"),
            Decimal("5920.00"),
            Decimal("4990.00"),
        ]
        assert ranking[0][0] == "Sarah Lim"

        refunds_inclusive = await conn.scalar(
            text(
                "SELECT sum(amount_myr) FROM sales_transactions "
                "WHERE customer_name = 'Sarah Lim'"
            )
        )
        assert refunds_inclusive == Decimal("9360.00")
    await engine.dispose()
