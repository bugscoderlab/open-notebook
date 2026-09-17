"""Unit tests for the LLM-SQL validation gate (ticket #22).

Pure-function tests: no database, no LLM. The happy path uses SQL shaped
like what the text-to-SQL pipeline expects from the model, including the
mandatory team-filter placeholder.
"""

import pytest

from open_notebook.analytics.sql_gate import (
    DEFAULT_PLACEHOLDER_VOCAB,
    GateVerdict,
    validate_sql,
)

ALLOWED = {"sales_transactions"}

HAPPY = """
SELECT
    customer_name,
    SUM(amount_myr) AS total_spend,
    COUNT(*) AS transaction_count
FROM sales_transactions
WHERE transaction_date >= :start_date
  AND transaction_date < :end_date
  AND service = 'Full Groom'
  AND status IN ('completed')
  AND data_team IN (:authorized_team_ids)
GROUP BY customer_id, customer_name
ORDER BY total_spend DESC
LIMIT 10
"""

CTE_HAPPY = """
WITH scoped AS (
    SELECT customer_name, data_team, amount_myr
    FROM sales_transactions
)
SELECT customer_name, SUM(amount_myr) AS total_spend
FROM scoped
WHERE data_team IN (:authorized_team_ids)
GROUP BY customer_name
ORDER BY total_spend DESC
LIMIT 10
"""


def ok(sql: str, **kwargs) -> GateVerdict:
    return validate_sql(sql, ALLOWED, **kwargs)


class TestHappyPath:
    def test_simple_group_by_passes(self):
        verdict = ok(HAPPY)
        assert verdict.ok
        assert verdict.sql is not None
        assert "authorized_team_ids" in verdict.sql

    def test_placeholders_reported(self):
        verdict = ok(HAPPY)
        assert verdict.placeholders == frozenset(
            {"start_date", "end_date", "authorized_team_ids"}
        )

    def test_cte_referencing_allowlisted_table_passes(self):
        assert ok(CTE_HAPPY).ok

    def test_normalization_preserves_placeholder_form(self):
        verdict = ok("  SELECT   a FROM sales_transactions WHERE data_team IN (:authorized_team_ids)  ")
        assert verdict.ok
        assert ":authorized_team_ids" in verdict.sql


class TestStatementShape:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT 1; DROP TABLE sales_transactions",
            "SELECT 1; SELECT 2",
            "",
            "   ",
        ],
    )
    def test_rejected(self, sql):
        verdict = ok(sql)
        assert not verdict.ok
        assert verdict.reason in ("multiple_or_empty_statements", "unparseable")

    def test_union_rejected(self):
        verdict = ok(
            "SELECT customer_name FROM sales_transactions WHERE data_team IN (:authorized_team_ids) "
            "UNION SELECT customer_name FROM sales_transactions WHERE data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason == "not_select"

    def test_insert_rejected(self):
        verdict = ok(
            "INSERT INTO sales_transactions (customer_name) VALUES ('x')"
        )
        assert not verdict.ok
        assert verdict.reason == "not_select"

    def test_ddl_in_cte_rejected(self):
        verdict = ok(
            "WITH x AS (CREATE TABLE evil (a int)) "
            "SELECT * FROM sales_transactions WHERE data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason is not None
        assert verdict.reason.startswith("write_or_ddl_statement")

    def test_delete_in_cte_rejected(self):
        verdict = ok(
            "WITH x AS (DELETE FROM sales_transactions RETURNING *) "
            "SELECT * FROM sales_transactions WHERE data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason is not None
        assert verdict.reason.startswith("write_or_ddl_statement")


class TestTables:
    def test_non_allowlisted_table_rejected(self):
        verdict = ok(
            "SELECT * FROM users WHERE data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason == "table_not_allowed:users"

    def test_non_allowlisted_table_in_subquery_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions WHERE customer_id IN "
            "(SELECT id FROM employees) AND data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason == "table_not_allowed:employees"

    def test_cte_alias_not_treated_as_table(self):
        # `ranked` is a CTE alias, not a table reference — must not be allowlist-checked.
        assert ok(CTE_HAPPY).ok


class TestTeamFilter:
    def test_missing_team_filter_rejected(self):
        verdict = ok("SELECT * FROM sales_transactions WHERE status = 'completed'")
        assert not verdict.ok
        assert verdict.reason == "missing_team_filter"

    def test_team_filter_only_in_subquery_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions WHERE customer_id IN "
            "(SELECT customer_id FROM sales_transactions WHERE data_team IN (:authorized_team_ids))"
        )
        assert not verdict.ok
        assert verdict.reason == "missing_team_filter"

    def test_literal_team_filter_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions WHERE data_team IN ('Team A', 'Team B')"
        )
        assert not verdict.ok
        assert verdict.reason in ("missing_team_filter", "team_column_predicated")

    def test_negated_team_filter_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions WHERE data_team != 'Team A'"
        )
        assert not verdict.ok
        assert verdict.reason in ("missing_team_filter", "team_column_predicated")

    def test_second_team_predicate_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids) AND data_team <> 'Team A'"
        )
        assert not verdict.ok
        assert verdict.reason == "team_column_predicated"

    def test_team_predicate_in_having_rejected(self):
        verdict = ok(
            "SELECT customer_name, SUM(amount_myr) AS s FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids) "
            "GROUP BY customer_name HAVING data_team <> 'Team A'"
        )
        assert not verdict.ok
        assert verdict.reason == "team_column_predicated"

    def test_team_predicate_in_join_on_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions s "
            "JOIN sales_transactions t ON s.customer_id = t.customer_id AND t.data_team = 'Team A' "
            "WHERE s.data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason == "team_column_predicated"

    def test_qualified_team_column_accepted(self):
        verdict = ok(
            "SELECT s.customer_name FROM sales_transactions s "
            "WHERE s.data_team IN (:authorized_team_ids)"
        )
        assert verdict.ok


class TestPlaceholders:
    def test_unknown_placeholder_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids) AND customer_name = :name"
        )
        assert not verdict.ok
        assert verdict.reason == "placeholder_not_allowed:name"

    def test_custom_vocab_accepted(self):
        verdict = ok(
            "SELECT * FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids) AND customer_name = :name",
            placeholder_vocab=DEFAULT_PLACEHOLDER_VOCAB | {"name"},
        )
        assert verdict.ok


class TestFunctions:
    @pytest.mark.parametrize(
        "fn",
        ["pg_read_file", "pg_ls_dir", "pg_execute_server_program", "dblink", "lo_import"],
    )
    def test_blocked_function_rejected(self, fn):
        verdict = ok(
            f"SELECT {fn}('/etc/passwd') FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason == f"function_blocked:{fn}"


class TestObfuscation:
    def test_or_vacuous_filter_rejected(self):
        # `filter OR 1=1` returns every team's rows while looking filter-ish.
        verdict = ok(
            "SELECT * FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids) OR 1=1"
        )
        assert not verdict.ok
        assert verdict.reason == "missing_team_filter"

    def test_or_with_other_column_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids) OR status = 'completed'"
        )
        assert not verdict.ok
        assert verdict.reason == "missing_team_filter"

    def test_negated_mandatory_filter_rejected(self):
        verdict = ok(
            "SELECT * FROM sales_transactions "
            "WHERE NOT (data_team IN (:authorized_team_ids))"
        )
        assert not verdict.ok
        assert verdict.reason == "missing_team_filter"

    def test_not_in_team_filter_rejected(self):
        # sqlglot parses NOT IN as Not(In(...)) — no longer seen as the
        # mandatory conjunct, so the refusal surfaces as missing_team_filter.
        verdict = ok(
            "SELECT * FROM sales_transactions "
            "WHERE data_team NOT IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason == "missing_team_filter"

    def test_comment_obfuscation_does_not_hide_missing_filter(self):
        verdict = ok(
            "SELECT * FROM sales_transactions WHERE status = 'completed' -- data_team IN (:authorized_team_ids)"
        )
        assert not verdict.ok
        assert verdict.reason == "missing_team_filter"

    def test_nested_duplicate_team_filter_accepted(self):
        # Same mandatory predicate repeated in a subquery is still the mandatory filter.
        verdict = ok(
            "SELECT * FROM sales_transactions WHERE data_team IN (:authorized_team_ids) "
            "AND customer_id IN (SELECT customer_id FROM sales_transactions "
            "WHERE data_team IN (:authorized_team_ids))"
        )
        assert verdict.ok
