"""Tests for the schema snapshot → parquet context builder (ticket #23).

Builder/render tests are pure (fixture artifact, no DB). The snapshot
round-trip against a real database lives in the integration tier
(``requires_analytics_pg``), matching tests/test_analytics_service.py.
"""

import os

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import pytest_asyncio
from conftest import (
    analytics_alembic_upgrade,
    analytics_seed_scratch,
    requires_analytics_pg,
)

from open_notebook.analytics.schema_snapshot import (
    load_schema,
    render_schema_context,
    snapshot_schema,
)

FIXTURE_COLUMNS = [
    {"table_name": "sales_transactions", "column_name": "transaction_id", "data_type": "integer", "ordinal_position": 1},
    {"table_name": "sales_transactions", "column_name": "customer_name", "data_type": "text", "ordinal_position": 2},
    {"table_name": "sales_transactions", "column_name": "amount_myr", "data_type": "numeric", "ordinal_position": 3},
    {"table_name": "sales_transactions", "column_name": "data_team", "data_type": "text", "ordinal_position": 4},
]


def write_fixture(path, rows=FIXTURE_COLUMNS):
    pq.write_table(
        pa.table(
            {
                "table_name": pa.array([r["table_name"] for r in rows], type=pa.string()),
                "column_name": pa.array([r["column_name"] for r in rows], type=pa.string()),
                "data_type": pa.array([r["data_type"] for r in rows], type=pa.string()),
                "ordinal_position": pa.array([r["ordinal_position"] for r in rows], type=pa.int64()),
            }
        ),
        path,
    )


class TestLoadAndRender:
    def test_round_trip(self, tmp_path):
        artifact = tmp_path / "schema.parquet"
        write_fixture(artifact)
        rows = load_schema(artifact)
        assert rows == FIXTURE_COLUMNS

    def test_missing_columns_rejected(self, tmp_path):
        artifact = tmp_path / "bad.parquet"
        pq.write_table(pa.table({"table_name": pa.array(["t"], type=pa.string())}), artifact)
        with pytest.raises(ValueError, match="missing columns"):
            load_schema(artifact)

    def test_render_lists_tables_columns_types(self, tmp_path):
        artifact = tmp_path / "schema.parquet"
        write_fixture(artifact)
        context = render_schema_context(load_schema(artifact), "Sales 2026")
        assert "Dataset: Sales 2026" in context
        assert "- sales_transactions" in context
        assert "- customer_name (text)" in context
        assert "- amount_myr (numeric)" in context

    def test_render_includes_mandatory_team_filter_rule(self, tmp_path):
        artifact = tmp_path / "schema.parquet"
        write_fixture(artifact)
        context = render_schema_context(load_schema(artifact))
        assert "data_team IN (:authorized_team_ids)" in context

    def test_render_groups_multiple_tables(self, tmp_path):
        artifact = tmp_path / "schema.parquet"
        rows = FIXTURE_COLUMNS + [
            {"table_name": "other_table", "column_name": "id", "data_type": "integer", "ordinal_position": 1},
        ]
        write_fixture(artifact, rows)
        context = render_schema_context(load_schema(artifact))
        assert "- sales_transactions" in context
        assert "- other_table" in context


@pytest.mark.integration
@requires_analytics_pg()
@pytest.mark.asyncio
class TestSnapshotRoundTrip:
    """Integration: snapshot the scratch Postgres and render from it."""

    @pytest_asyncio.fixture(scope="class", loop_scope="class")
    async def seeded_db(self, tmp_path_factory):
        """Scratch PG with migrations + seed applied (no SurrealDB needed)."""
        from open_notebook.analytics.engine import dispose_engine

        analytics_alembic_upgrade()

        previous_url = os.environ.get("ANALYTICS_DATABASE_URL")
        os.environ["ANALYTICS_DATABASE_URL"] = os.environ["ANALYTICS_TEST_DATABASE_URL"]
        await analytics_seed_scratch()

        yield

        await dispose_engine()
        if previous_url is None:
            os.environ.pop("ANALYTICS_DATABASE_URL", None)
        else:
            os.environ["ANALYTICS_DATABASE_URL"] = previous_url

    async def test_snapshot_matches_seeded_schema(self, seeded_db, tmp_path):
        artifact = await snapshot_schema(tmp_path / "schema.parquet")
        rows = load_schema(artifact)
        by_table = {r["table_name"] for r in rows}
        assert "sales_transactions" in by_table
        sales_columns = {
            r["column_name"] for r in rows if r["table_name"] == "sales_transactions"
        }
        assert {"transaction_id", "customer_name", "amount_myr", "data_team"} <= sales_columns

    async def test_snapshot_overwrite_is_idempotent(self, seeded_db, tmp_path):
        artifact = tmp_path / "schema.parquet"
        await snapshot_schema(artifact)
        first = load_schema(artifact)
        await snapshot_schema(artifact)
        assert load_schema(artifact) == first

    async def test_snapshot_renders_context(self, seeded_db, tmp_path):
        artifact = await snapshot_schema(tmp_path / "schema.parquet")
        context = render_schema_context(load_schema(artifact), "Sales 2026")
        assert "Dataset: Sales 2026" in context
        assert "data_team IN (:authorized_team_ids)" in context
