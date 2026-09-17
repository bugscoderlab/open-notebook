"""Unit tests for the team-access SurrealDB migrations (26/27/28).

These tests verify the migration *files* and their registration — they do not
need a live database. Applying the migrations to a real SurrealDB is covered
by the API startup path and the integration tier.
"""

from pathlib import Path

import pytest

from open_notebook.database.async_migrate import AsyncMigrationManager

MIGRATIONS_DIR = Path(__file__).parent.parent / "open_notebook" / "database" / "migrations"

EXPECTED_NEW_TABLES_26 = [
    "organization",
    "team",
    "app_user",
    "user_session",
]


def test_migrations_26_27_28_files_exist() -> None:
    for name in [
        "26.surrealql", "26_down.surrealql",
        "27.surrealql", "27_down.surrealql",
        "28.surrealql", "28_down.surrealql",
    ]:
        assert (MIGRATIONS_DIR / name).is_file(), f"missing migration file: {name}"


def test_migration_26_defines_team_access_tables() -> None:
    sql = (MIGRATIONS_DIR / "26.surrealql").read_text()
    for table in EXPECTED_NEW_TABLES_26:
        assert f"DEFINE TABLE IF NOT EXISTS {table}" in sql
    for field in ["email", "password_hash", "role", "status", "token_hash", "expires_at"]:
        assert field in sql, f"migration 26 lost field: {field}"
    assert sql.count("UNIQUE") >= 2, "expected unique indexes on email and token_hash"


def test_migration_26_adds_ownership_fields_to_notebook_and_source() -> None:
    sql = (MIGRATIONS_DIR / "26.surrealql").read_text()
    for table in ["notebook", "source"]:
        for field in ["organization", "team", "visibility", "created_by"]:
            assert f"DEFINE FIELD IF NOT EXISTS {field} ON TABLE {table}" in sql


def test_migration_26_down_removes_everything() -> None:
    sql = (MIGRATIONS_DIR / "26_down.surrealql").read_text()
    for table in EXPECTED_NEW_TABLES_26:
        assert f"REMOVE TABLE IF EXISTS {table}" in sql
    for field in ["organization", "team", "visibility", "created_by"]:
        assert sql.count(f"REMOVE FIELD IF EXISTS {field}") == 2, (
            f"down migration must remove {field} from both notebook and source"
        )


def test_migration_27_defines_analytics_metadata() -> None:
    sql = (MIGRATIONS_DIR / "27.surrealql").read_text()
    assert "DEFINE TABLE IF NOT EXISTS dataset" in sql
    assert "DEFINE TABLE IF NOT EXISTS analytics_query_log" in sql
    assert "connection_ref" in sql
    down = (MIGRATIONS_DIR / "27_down.surrealql").read_text()
    assert "REMOVE TABLE IF EXISTS dataset" in down
    assert "REMOVE TABLE IF EXISTS analytics_query_log" in down


def test_migration_28_adds_ownership_fields_to_episode() -> None:
    """T5: episodes derive team access from their source notebook."""
    sql = (MIGRATIONS_DIR / "28.surrealql").read_text()
    for field in ["notebook", "organization", "team", "visibility", "created_by"]:
        assert f"DEFINE FIELD IF NOT EXISTS {field} ON TABLE episode" in sql
    assert "episode_team_visibility" in sql
    down = (MIGRATIONS_DIR / "28_down.surrealql").read_text()
    for field in ["notebook", "organization", "team", "visibility", "created_by"]:
        assert f"REMOVE FIELD IF EXISTS {field} ON TABLE episode" in down
    assert "REMOVE INDEX IF EXISTS episode_team_visibility" in down


def test_migration_manager_registers_31_up_and_down_migrations() -> None:
    manager = AsyncMigrationManager()
    assert len(manager.up_migrations) == 31
    assert len(manager.down_migrations) == 31


@pytest.mark.parametrize("version", [26, 27, 28, 29, 30, 31])
def test_new_migrations_parse_into_non_empty_sql(version: int) -> None:
    """AsyncMigration.from_file strips comments/blank lines; result must be non-trivial."""
    from open_notebook.database.async_migrate import AsyncMigration

    up = AsyncMigration.from_file(f"open_notebook/database/migrations/{version}.surrealql")
    # 26-28 are table batches (>100 chars); 29/30 are single-field migrations.
    min_chars = 100 if version < 29 else 40
    assert len(up.sql) > min_chars
    assert "DEFINE" in up.sql
    down = AsyncMigration.from_file(
        f"open_notebook/database/migrations/{version}_down.surrealql"
    )
    assert "REMOVE" in down.sql
