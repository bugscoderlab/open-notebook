"""sales_transactions: single-table schema mirroring _jobbrief/testdata/sales_transactions_2026.csv.

A normalized customers/services split was considered and rejected — the
approved query templates target one relation and the CSV maps 1:1
(docs/7-DEVELOPMENT/team-access/erd.md).

Revision ID: 0001
Revises:
Create Date: 2026-09-16

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sales_transactions",
        sa.Column("transaction_id", sa.Text(), primary_key=True),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("customer_id", sa.Text(), nullable=False),
        sa.Column("customer_name", sa.Text(), nullable=False),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("amount_myr", sa.Numeric(10, 2), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("data_team", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_sales_team_status_date",
        "sales_transactions",
        ["data_team", "status", "transaction_date"],
    )
    op.create_index("ix_sales_customer", "sales_transactions", ["customer_id"])


def downgrade() -> None:
    op.drop_index("ix_sales_customer", table_name="sales_transactions")
    op.drop_index("ix_sales_team_status_date", table_name="sales_transactions")
    op.drop_table("sales_transactions")
