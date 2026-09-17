"""Integration tests: agentic text-to-SQL vertical path (ticket #25).

The tracer bullet: POST-able service entry → unchanged permission checks →
generation prompt → validation gate → real scratch Postgres → frozen
AnalyticsAnswer, with the chat model replaced by a scripted stub returning
fixed SQL. The fallback-to-templates branch is proven with the stub
unavailable, and gate rejections surface as guidance (InvalidInputError),
never crashes.
"""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import (
    analytics_alembic_upgrade,
    analytics_seed_scratch,
    requires_analytics_pg,
)

TEST_USER_ID = "app_user:t8texttosql"

SCRIPTED_SQL = """
SELECT
    customer_name,
    SUM(amount_myr) AS total_spend,
    COUNT(*) AS transaction_count
FROM sales_transactions
WHERE service = 'Full Groom'
  AND status = 'completed'
  AND data_team IN (:authorized_team_ids)
GROUP BY customer_id, customer_name
ORDER BY total_spend DESC
LIMIT 10
"""

NO_ROWS_SQL = """
SELECT customer_name
FROM sales_transactions
WHERE service = 'Nonexistent Service'
  AND data_team IN (:authorized_team_ids)
"""

GATE_REJECTED_SQL = "SELECT * FROM sales_transactions WHERE status = 'completed'"

BAD_COLUMN_SQL = """
SELECT bogus_column
FROM sales_transactions
WHERE data_team IN (:authorized_team_ids)
"""

SCHEMA_ROWS = [
    {"table_name": "sales_transactions", "column_name": "transaction_id", "data_type": "integer", "ordinal_position": 1},
    {"table_name": "sales_transactions", "column_name": "transaction_date", "data_type": "date", "ordinal_position": 2},
    {"table_name": "sales_transactions", "column_name": "customer_id", "data_type": "text", "ordinal_position": 3},
    {"table_name": "sales_transactions", "column_name": "customer_name", "data_type": "text", "ordinal_position": 4},
    {"table_name": "sales_transactions", "column_name": "service", "data_type": "text", "ordinal_position": 5},
    {"table_name": "sales_transactions", "column_name": "amount_myr", "data_type": "numeric", "ordinal_position": 6},
    {"table_name": "sales_transactions", "column_name": "status", "data_type": "text", "ordinal_position": 7},
    {"table_name": "sales_transactions", "column_name": "data_team", "data_type": "text", "ordinal_position": 8},
]

pytestmark = requires_analytics_pg()


def _surreal_available() -> bool:
    async def _ping() -> None:
        from open_notebook.database.async_migrate import AsyncMigrationManager

        await asyncio.wait_for(AsyncMigrationManager().ping(), timeout=5)

    try:
        asyncio.run(_ping())
        return True
    except Exception:
        return False


if not _surreal_available():
    pytestmark = pytest.mark.skipif(True, reason="SurrealDB not reachable")


def _write_schema_artifact(tmp_path, monkeypatch) -> None:
    artifact = tmp_path / "schema.parquet"
    pq.write_table(
        pa.table(
            {
                "table_name": pa.array([r["table_name"] for r in SCHEMA_ROWS], type=pa.string()),
                "column_name": pa.array([r["column_name"] for r in SCHEMA_ROWS], type=pa.string()),
                "data_type": pa.array([r["data_type"] for r in SCHEMA_ROWS], type=pa.string()),
                "ordinal_position": pa.array([r["ordinal_position"] for r in SCHEMA_ROWS], type=pa.int64()),
            }
        ),
        artifact,
    )
    monkeypatch.setenv("ANALYTICS_SCHEMA_PATH", str(artifact))


@pytest.fixture(scope="module")
def seeded():
    """Scratch PG with seed + registered dataset + finance team."""
    from open_notebook.analytics.engine import dispose_engine
    from open_notebook.database.repository import repo_query
    from open_notebook.domain.analytics import ensure_sales_2026_dataset

    previous_url = os.environ.get("ANALYTICS_DATABASE_URL")
    os.environ["ANALYTICS_DATABASE_URL"] = os.environ["ANALYTICS_TEST_DATABASE_URL"]
    analytics_alembic_upgrade()
    asyncio.run(analytics_seed_scratch())

    dataset = asyncio.run(ensure_sales_2026_dataset())
    teams = asyncio.run(repo_query("SELECT id FROM team WHERE slug = 'finance'"))
    assert teams, "finance team must exist after dataset registration"

    class _Context:
        dataset: object
        finance_team_id: str

    context = _Context()
    context.dataset = dataset
    context.finance_team_id = str(teams[0]["id"])

    yield context

    asyncio.run(dispose_engine())
    if previous_url is None:
        os.environ.pop("ANALYTICS_DATABASE_URL", None)
    else:
        os.environ["ANALYTICS_DATABASE_URL"] = previous_url


def _caller(seeded, role: str = "team_manager"):
    from api.access import CurrentUser

    return CurrentUser(
        id=TEST_USER_ID,
        email="daniel@example.com",
        display_name="Daniel",
        organization_id="organization:default",
        team_id=seeded.finance_team_id,
        role=role,  # type: ignore[arg-type]
    )


def _ask(seeded, question: str):
    from open_notebook.analytics.service import ask_analytics_question

    return ask_analytics_question(
        caller=_caller(seeded),
        question=question,
        dataset_id=seeded.dataset.id,
        include_refunds=False,
        permitted_dataset_ids=[seeded.dataset.id],
    )


@pytest.mark.integration
@pytest.mark.asyncio
class TestTextToSqlPath:
    async def test_free_form_question_returns_real_rows(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql",
            new=AsyncMock(return_value=SCRIPTED_SQL),
        ):
            answer = await _ask(seeded, "who spend on full groom?")

        assert answer.status == "ok"
        assert answer.kpis == []
        assert answer.chart is None
        assert answer.scope["dataset"] == "Sales 2026"
        assert answer.query_id is not None
        assert answer.query_template is not None
        assert "authorized_team_ids" in answer.query_template

        columns = answer.table["columns"]
        assert columns == ["customer_name", "total_spend", "transaction_count"]
        assert len(answer.table["rows"]) > 0

        # Honesty: the pipeline's rows must equal what the same SQL returns
        # when executed directly against the scratch Postgres.
        from sqlalchemy import text

        from open_notebook.analytics.engine import run_readonly_query
        from open_notebook.analytics.text_to_sql import expand_in_list_params

        sql, params = expand_in_list_params(
            SCRIPTED_SQL, {"authorized_team_ids": ["Finance"]},
            list_key="authorized_team_ids",
        )
        expected_rows, _ = await run_readonly_query(text(sql), params)
        assert [dict(zip(columns, row)) for row in answer.table["rows"]] == [
            {k: v for k, v in r.items()} for r in expected_rows
        ]

    async def test_gate_rejection_is_guidance_not_crash(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        from open_notebook.exceptions import InvalidInputError

        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql",
            new=AsyncMock(return_value=GATE_REJECTED_SQL),
        ):
            with pytest.raises(InvalidInputError, match="missing_team_filter"):
                await _ask(seeded, "show me everything")

    async def test_zero_rows_is_honest_no_data(self, seeded, tmp_path, monkeypatch):
        _write_schema_artifact(tmp_path, monkeypatch)
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql",
            new=AsyncMock(return_value=NO_ROWS_SQL),
        ):
            answer = await _ask(seeded, "who bought a nonexistent service?")

        assert answer.status == "no_data"
        assert "won't invent" in answer.answer_text

    async def test_model_unavailable_falls_back_to_templates(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql",
            new=AsyncMock(return_value=None),
        ):
            answer = await _ask(seeded, "Who is the highest spender this year?")

        # Template-path signature: KPI cards are built only by templates.
        assert answer.status == "ok"
        assert len(answer.kpis) > 0

    async def test_query_log_displays_validated_placeholder_sql(
        self, seeded, tmp_path, monkeypatch
    ):
        """AN-009: the log entry shows the generated SQL — placeholders only,
        never bound team literal values."""
        _write_schema_artifact(tmp_path, monkeypatch)
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql",
            new=AsyncMock(return_value=SCRIPTED_SQL),
        ):
            answer = await _ask(seeded, "who spend on full groom?")

        from open_notebook.analytics.service import get_query

        record = await get_query(answer.query_id)
        assert record is not None
        assert record["query_template"] is not None
        assert "authorized_team_ids" in record["query_template"]
        assert "Finance" not in record["query_template"]
        assert record["template_id"] is None
        assert record["status"] == "ok"
        assert record["row_count"] == len(answer.table["rows"])

    async def test_missing_artifact_falls_back_without_calling_model(
        self, seeded, tmp_path, monkeypatch
    ):
        monkeypatch.setenv(
            "ANALYTICS_SCHEMA_PATH", str(tmp_path / "does-not-exist.parquet")
        )
        generate_sql = AsyncMock(return_value=SCRIPTED_SQL)
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql", new=generate_sql
        ):
            answer = await _ask(seeded, "Who is the highest spender this year?")

        generate_sql.assert_not_called()
        assert answer.status == "ok"
        assert len(answer.kpis) > 0


@pytest.mark.integration
@pytest.mark.asyncio
class TestRetryLoop:
    """Bounded agent loop: rejections/errors/emptiness fed back, max 3 tries."""

    async def test_gate_rejection_feedback_then_success(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        generate_sql = AsyncMock(side_effect=[GATE_REJECTED_SQL, SCRIPTED_SQL])
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql", new=generate_sql
        ):
            answer = await _ask(seeded, "who spend on full groom?")

        assert answer.status == "ok"
        assert generate_sql.call_count == 2
        feedback = generate_sql.call_args_list[1].kwargs["feedback"]
        assert any("missing_team_filter" in note for note in feedback)

    async def test_execution_error_feedback_then_success(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        generate_sql = AsyncMock(side_effect=[BAD_COLUMN_SQL, SCRIPTED_SQL])
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql", new=generate_sql
        ):
            answer = await _ask(seeded, "who spend on full groom?")

        assert answer.status == "ok"
        assert generate_sql.call_count == 2
        feedback = generate_sql.call_args_list[1].kwargs["feedback"]
        assert any("failed to execute" in note for note in feedback)

    async def test_zero_rows_feedback_then_success(self, seeded, tmp_path, monkeypatch):
        _write_schema_artifact(tmp_path, monkeypatch)
        generate_sql = AsyncMock(side_effect=[NO_ROWS_SQL, SCRIPTED_SQL])
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql", new=generate_sql
        ):
            answer = await _ask(seeded, "who spend on full groom?")

        assert answer.status == "ok"
        assert generate_sql.call_count == 2
        feedback = generate_sql.call_args_list[1].kwargs["feedback"]
        assert any("0 rows" in note for note in feedback)

    async def test_exhausted_rejections_raise_guidance(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        from open_notebook.exceptions import InvalidInputError

        generate_sql = AsyncMock(
            side_effect=[GATE_REJECTED_SQL, GATE_REJECTED_SQL, GATE_REJECTED_SQL]
        )
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql", new=generate_sql
        ):
            with pytest.raises(InvalidInputError, match="missing_team_filter"):
                await _ask(seeded, "show me everything")
        assert generate_sql.call_count == 3

    async def test_exhausted_execution_errors_raise_guidance(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        from open_notebook.exceptions import InvalidInputError

        generate_sql = AsyncMock(
            side_effect=[BAD_COLUMN_SQL, BAD_COLUMN_SQL, BAD_COLUMN_SQL]
        )
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql", new=generate_sql
        ):
            with pytest.raises(InvalidInputError, match="execution_error"):
                await _ask(seeded, "who spend on full groom?")
        assert generate_sql.call_count == 3

    async def test_exhausted_zero_rows_is_honest_no_data(
        self, seeded, tmp_path, monkeypatch
    ):
        _write_schema_artifact(tmp_path, monkeypatch)
        generate_sql = AsyncMock(side_effect=[NO_ROWS_SQL, NO_ROWS_SQL, NO_ROWS_SQL])
        with patch(
            "open_notebook.analytics.text_to_sql.generate_sql", new=generate_sql
        ):
            answer = await _ask(seeded, "who bought a nonexistent service?")

        assert generate_sql.call_count == 3
        assert answer.status == "no_data"
        assert "won't invent" in answer.answer_text


class TestGenerationPrompt:
    def test_prompt_carries_schema_question_and_rules(self):
        from open_notebook.analytics.text_to_sql import build_generation_prompt

        prompt = build_generation_prompt(
            "who spend on full groom?", "Dataset: Sales 2026\n- sales_transactions"
        )
        assert "who spend on full groom?" in prompt
        assert "sales_transactions" in prompt
        assert "data_team IN (:authorized_team_ids)" in prompt
        assert "Reply with SQL only" in prompt
