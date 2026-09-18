"""Structural validation gate for LLM-generated analytics SQL (ADR-015).

The text-to-SQL pipeline lets the model write SQL, so safety moves from
"forbidden" (ADR-011) to "validated": every generated string passes through
:func:`validate_sql` — a pure function, no database required — before the
server binds any value and the read-only engine executes it.

Enforced, via sqlglot (Postgres dialect):

- exactly one statement, and it must be a plain ``SELECT`` (CTEs allowed,
  ``UNION``/set-operations rejected — a set-operation has no single outer
  ``WHERE`` for the mandatory team filter);
- no write/DDL/utility node anywhere, including inside CTE bodies;
- no blocked server-access functions (file system, server program, …);
- every referenced table ⊆ the caller-supplied allowlist (CTE aliases are
  not tables and are ignored);
- the mandatory predicate ``data_team IN (:authorized_team_ids)`` present
  in the outer ``WHERE``, and no other predicate on ``data_team`` in any
  condition (``WHERE``/``HAVING``/join ``ON``, at any nesting depth) — a
  duplicate of the mandatory predicate itself is allowed;
- named placeholders limited to the documented server-bindable vocabulary.

The verdict's ``reason`` is a stable, machine-readable string so the agent
loop can feed rejections back to the model as revision context.
"""

import re
from dataclasses import dataclass, field
from typing import Collection, FrozenSet, Optional, TypeGuard

import sqlglot
from loguru import logger
from sqlglot import exp

TEAM_COLUMN = "data_team"
TEAM_FILTER_PLACEHOLDER = "authorized_team_ids"

#: Placeholders the server can bind. Anything else in generated SQL is a
#: gate rejection (reason ``placeholder_not_allowed:<name>``) rather than a
#: late execution error.
DEFAULT_PLACEHOLDER_VOCAB: FrozenSet[str] = frozenset(
    {TEAM_FILTER_PLACEHOLDER, "start_date", "end_date"}
)

#: Server/file-access functions that read-only mode may still allow to run;
#: there is no analytics use for them, so they are refused outright.
_BLOCKED_FUNCTIONS: FrozenSet[str] = frozenset(
    {
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "pg_ls_logdir",
        "pg_ls_waldir",
        "pg_stat_file",
        "pg_read_server_files",
        "pg_execute_server_program",
        "lo_import",
        "lo_export",
        "lo_get",
        "dblink",
    }
)

_WRITE_NODE_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
    exp.Merge,
    exp.Grant,
    exp.Revoke,
)


@dataclass(frozen=True)
class GateVerdict:
    """Outcome of validating one generated SQL string.

    Attributes:
        ok: True only when every check passed.
        sql: Normalized statement text (placeholder form) when ok.
        reason: Stable machine-readable rejection code when not ok
            (e.g. ``missing_team_filter``, ``table_not_allowed:foo``).
        placeholders: Named placeholders found in the statement.
    """

    ok: bool
    sql: Optional[str] = None
    reason: Optional[str] = None
    placeholders: FrozenSet[str] = field(default_factory=frozenset)


def _reject(reason: str, placeholders: FrozenSet[str] = frozenset()) -> GateVerdict:
    return GateVerdict(ok=False, reason=reason, placeholders=placeholders)


def _is_team_column(node: exp.Expression) -> bool:
    return isinstance(node, exp.Column) and node.name.lower() == TEAM_COLUMN


def _is_mandatory_team_in(node: Optional[exp.Expression]) -> TypeGuard[exp.In]:
    """True when the node is exactly ``data_team IN (:authorized_team_ids)``."""
    if not isinstance(node, exp.In) or not _is_team_column(node.this):
        return False
    items = list(node.expressions)
    return (
        len(items) == 1
        and isinstance(items[0], exp.Placeholder)
        and items[0].name == TEAM_FILTER_PLACEHOLDER
    )


def _outer_predicates(where: exp.Where) -> Collection[exp.Expression]:
    """Conjunct-level predicate nodes in the outer WHERE, without descending
    into subqueries or boolean operators that could vacuously satisfy the
    mandatory-filter check.

    ``WHERE data_team IN (:authorized_team_ids) OR 1=1`` and
    ``WHERE NOT (data_team IN (:authorized_team_ids))`` both look filter-ish
    while returning every team's rows, so the mandatory predicate only
    counts as a direct conjunct: reachable through ``AND`` chains (or alone),
    never under ``Or``/``Xor``/``Not``, and never inside a subquery — a team
    filter nested in a subquery does NOT protect the outer query's rows
    (e.g. ``WHERE id IN (SELECT id FROM t WHERE data_team IN (...))``
    returns outer rows of every team).
    """
    nodes: list = []
    stack = list(where.iter_expressions())
    while stack:
        node = stack.pop()
        if isinstance(node, (exp.Select, exp.Or, exp.Xor, exp.Not)):
            continue
        nodes.append(node)
        stack.extend(node.iter_expressions())
    return nodes


def _find_mandatory_team_in(stmt: exp.Select) -> Optional[exp.In]:
    """The required ``data_team IN (:authorized_team_ids)`` in the outer WHERE."""
    where = stmt.args.get("where")
    if where is None:
        return None
    for node in _outer_predicates(where):
        if _is_mandatory_team_in(node):
            return node
    return None


def _condition_nodes(stmt: exp.Select) -> Collection[exp.Expression]:
    """Every node that can carry row-filtering conditions, at any depth."""
    conditions = list(stmt.find_all(exp.Where, exp.Having))
    for join in stmt.find_all(exp.Join):
        on = join.args.get("on")
        if on is not None:
            conditions.append(on)
    return conditions


def validate_sql(
    sql: str,
    allowed_tables: Collection[str],
    placeholder_vocab: Optional[Collection[str]] = None,
) -> GateVerdict:
    """Validate one generated analytics SQL string.

    Args:
        sql: The model-generated SQL text.
        allowed_tables: Lowercase table names the query may reference.
        placeholder_vocab: Named placeholders the server can bind;
            defaults to :data:`DEFAULT_PLACEHOLDER_VOCAB`.

    Returns:
        A :class:`GateVerdict`; when ``ok``, ``sql`` is the normalized
        statement ready for server-side parameter binding and execution.
    """
    vocab = frozenset(placeholder_vocab or DEFAULT_PLACEHOLDER_VOCAB)
    allowed = {t.lower() for t in allowed_tables}

    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except Exception as e:
        logger.debug(f"sql_gate could not parse generated SQL: {e}")
        return _reject("unparseable")
    if len(statements) != 1:
        return _reject("multiple_or_empty_statements")

    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        return _reject("not_select")

    for node in stmt.find_all(*_WRITE_NODE_TYPES):
        return _reject(f"write_or_ddl_statement:{node.key}")

    for func in stmt.find_all(exp.Func):
        if func.name.lower() in _BLOCKED_FUNCTIONS:
            return _reject(f"function_blocked:{func.name.lower()}")

    cte_aliases = {c.alias.lower() for c in stmt.find_all(exp.CTE)}
    for table in stmt.find_all(exp.Table):
        name = table.name.lower()
        if name and name not in cte_aliases and name not in allowed:
            return _reject(f"table_not_allowed:{name}")

    placeholders = frozenset(p.name for p in stmt.find_all(exp.Placeholder))
    for name in placeholders:
        if name not in vocab:
            return _reject(f"placeholder_not_allowed:{name}", placeholders)

    mandatory_in = _find_mandatory_team_in(stmt)
    if mandatory_in is None:
        return _reject("missing_team_filter", placeholders)
    for condition in _condition_nodes(stmt):
        for column in condition.find_all(exp.Column):
            if _is_team_column(column):
                ancestor_in = column.find_ancestor(exp.In)
                # A Not anywhere above the column inverts the predicate
                # (``data_team NOT IN (:authorized_team_ids)`` parses as an
                # In under a Not) — never acceptable, fail closed.
                if (
                    ancestor_in is None
                    or column.find_ancestor(exp.Not) is not None
                    or not _is_mandatory_team_in(ancestor_in)
                ):
                    return _reject("team_column_predicated", placeholders)

    # sqlglot renders named placeholders in Python paramstyle; the pipeline
    # expects SQLAlchemy text-style ":name" (see query_templates expansion).
    normalized = stmt.sql(dialect="postgres")
    normalized = re.sub(
        r"%\((\w+)\)s", lambda m: f":{m.group(1)}", normalized
    )
    return GateVerdict(ok=True, sql=normalized, placeholders=placeholders)
