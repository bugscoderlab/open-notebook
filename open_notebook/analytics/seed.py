"""Idempotent CSV seed for the analytics database (ADR-009, issue #8).

DEV ONLY — never run against a production analytics database. Loads
`_jobbrief/testdata/sales_transactions_2026.csv` (or a `--csv` path) into
`sales_transactions`. Rows whose `transaction_id` already exists are
skipped (`ON CONFLICT DO NOTHING`), so re-running never duplicates data.

Usage:
    uv run python -m open_notebook.analytics.seed [--csv PATH] [--database-url URL]
"""

import argparse
import asyncio
import csv
import os
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = REPO_ROOT / "_jobbrief" / "testdata" / "sales_transactions_2026.csv"
DEFAULT_DATABASE_URL = "postgresql+asyncpg://z@localhost:5432/open_notebook_analytics"

ALLOWED_STATUSES = {"completed", "refunded", "voided"}

INSERT_SQL = text("""
    INSERT INTO sales_transactions (
        transaction_id, transaction_date, customer_id, customer_name,
        service, amount_myr, status, data_team
    ) VALUES (
        :transaction_id, :transaction_date, :customer_id, :customer_name,
        :service, :amount_myr, :status, :data_team
    )
    ON CONFLICT (transaction_id) DO NOTHING
""")


def _iter_rows(csv_path: Path) -> Iterator[dict[str, object]]:
    """Yield one parameter dict per CSV row, validating as we go.

    Sync file/CSV parsing is deliberate and tiny (77 rows); all database
    access stays async per the async-first rule.
    """
    with csv_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            status = row["status"].strip()
            if status not in ALLOWED_STATUSES:
                msg = f"{csv_path}: {row['transaction_id']}: invalid status {status!r}"
                raise ValueError(msg)
            yield {
                "transaction_id": row["transaction_id"].strip(),
                "transaction_date": date.fromisoformat(row["transaction_date"].strip()),
                "customer_id": row["customer_id"].strip(),
                "customer_name": row["customer_name"].strip(),
                "service": row["service"].strip(),
                "amount_myr": Decimal(row["amount_myr"].strip()),
                "status": status,
                "data_team": row["data_team"].strip(),
            }


async def seed(csv_path: Path, database_url: str) -> tuple[int, int]:
    """Load the CSV into sales_transactions. Returns (inserted, skipped)."""
    rows = list(_iter_rows(csv_path))
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as conn:
            # asyncpg executemany does not report a reliable rowcount with
            # ON CONFLICT DO NOTHING, so diff table counts inside the
            # transaction instead.
            before = await conn.scalar(text("SELECT count(*) FROM sales_transactions"))
            await conn.execute(INSERT_SQL, rows)
            after = await conn.scalar(text("SELECT count(*) FROM sales_transactions"))
    finally:
        await engine.dispose()
    inserted = (after or 0) - (before or 0)
    return inserted, len(rows) - inserted


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help=f"path to the sales CSV (default: {DEFAULT_CSV})",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("ANALYTICS_DATABASE_URL", DEFAULT_DATABASE_URL),
        help="asyncpg DSN (default: $ANALYTICS_DATABASE_URL or the local "
        "Postgres.app open_notebook_analytics database)",
    )
    args = parser.parse_args()
    if not args.csv.exists():
        parser.error(f"CSV not found: {args.csv}")
    inserted, skipped = asyncio.run(seed(args.csv, args.database_url))
    print(f"seeded {inserted} rows, skipped {skipped} existing rows")


if __name__ == "__main__":
    main()
