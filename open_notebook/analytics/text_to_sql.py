"""Agentic text-to-SQL: generation, validation, execution (ADR-015).

The primary analytics path once a default chat model is configured: the model
writes SQL from the dataset's schema context + the question; the server
validates it (``sql_gate``), binds the mandatory team filter (and the period
placeholders when the model used them), and executes through the read-only
engine. When the model or the schema artifact is unavailable, the attempt is
``unavailable`` and the caller falls back to the template path — the
pipeline's honesty guarantees live in the engine and the gate, not in the
model.

Retry-with-feedback (bounded agent loop) is layered on top of
:func:`generate_sql` by the service; this module owns one generate → gate →
execute pass.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional, Tuple

from loguru import logger
from sqlalchemy import text

from open_notebook.analytics.engine import run_readonly_query
from open_notebook.analytics.query_templates import parse_period
from open_notebook.analytics.schema_snapshot import (
    load_schema,
    render_schema_context,
)
from open_notebook.analytics.sql_gate import validate_sql
from open_notebook.domain.analytics import Dataset
from open_notebook.utils.text_utils import clean_thinking_content, extract_text_content

MAX_GENERATED_ATTEMPTS = 3


def expand_in_list_params(
    sql: str, params: Dict[str, Any], list_key: str
) -> Tuple[str, Dict[str, Any]]:
    """Expand one IN-list bind param into individual named placeholders.

    Same constraint as the template path (ADR-011): asyncpg cannot consume
    SQLAlchemy "expanding" binds, so the server-controlled list becomes
    ``:key_0, :key_1, …``. Every value stays bound; only placeholder names
    are generated, and they come from this module (never from user input).
    """
    flat: Dict[str, Any] = {}
    remaining = dict(params)
    values = list(remaining.pop(list_key))
    placeholders = ", ".join(f":{list_key}_{i}" for i in range(len(values)))
    sql = sql.replace(f"(:{list_key})", f"({placeholders})")
    for i, value in enumerate(values):
        flat[f"{list_key}_{i}"] = value
    flat.update(remaining)
    return sql, flat


@dataclass(frozen=True)
class TextToSqlAttempt:
    """Outcome of one generate → gate → execute pass.

    kind:
        ``unavailable`` — no schema artifact or no model; the caller falls
        back to the template path.
        ``rejected`` — the gate refused the generated SQL; ``reason`` is the
        machine-readable gate code to surface as guidance.
        ``executed`` — the query ran; ``sql`` is the validated placeholder
        form, ``rows`` the database result (JSON-plain conversion happens in
        the service).
    """

    kind: Literal["unavailable", "rejected", "executed"]
    reason: Optional[str] = None
    sql: Optional[str] = None
    rows: List[Dict[str, Any]] = field(default_factory=list)
    duration_ms: Optional[int] = None


def build_generation_prompt(question: str, schema_context: str) -> str:
    """Assemble the generation prompt: schema, question, and the hard rules."""
    return (
        "You write one PostgreSQL query that answers the analytics question.\n"
        "Reply with SQL only — no markdown fences, no commentary.\n\n"
        f"{schema_context}\n\n"
        f"Question: {question}\n\n"
        "Rules:\n"
        "- One SELECT statement only (CTEs allowed, no UNION, no writes).\n"
        "- The outer WHERE MUST contain `data_team IN (:authorized_team_ids)`;\n"
        "  never write team names as literals, never put the predicate in a\n"
        "  subquery or under OR/NOT.\n"
        "- Reference only the tables and columns listed above; literals for\n"
        "  dates, names, and statuses are allowed.\n"
    )


async def generate_sql(question: str, schema_context: str) -> Optional[str]:
    """Ask the default chat model for one SQL string.

    Returns None when no model is configured or the call fails (mirrors the
    graceful-fallback contract of the template-path classifiers).
    """
    try:
        from langchain_core.messages import HumanMessage

        from open_notebook.ai.provision import provision_langchain_model

        prompt = build_generation_prompt(question, schema_context)
        model = await provision_langchain_model(prompt, None, "chat", max_tokens=800)
        response = await model.ainvoke([HumanMessage(content=prompt)])
        answer = clean_thinking_content(
            extract_text_content(response.content)
        ).strip()
        # Models often wrap SQL in fences despite the instruction.
        answer = answer.removeprefix("```sql").removeprefix("```").removesuffix("```").strip()
        return answer or None
    except Exception as e:
        logger.warning(f"Analytics text-to-SQL generation unavailable: {e}")
        return None


def load_schema_columns() -> Optional[List[Dict[str, Any]]]:
    """Schema artifact rows, or None when no snapshot has been taken yet."""
    try:
        return load_schema()
    except FileNotFoundError:
        return None


def allowed_tables_for(
    dataset: Dataset, schema_columns: List[Dict[str, Any]]
) -> List[str]:
    """Tables the generated SQL may reference for this dataset.

    The dataset registry's ``schema_metadata.table`` wins; otherwise every
    table seen in the schema artifact.
    """
    table = (dataset.schema_metadata or {}).get("table")
    if table:
        return [str(table)]
    return sorted({str(row["table_name"]) for row in schema_columns})


async def attempt_text_to_sql(
    *, question: str, dataset: Dataset, team_names: List[str]
) -> TextToSqlAttempt:
    """One full pass: load context → generate → gate → bind → execute."""
    columns = load_schema_columns()
    if columns is None:
        logger.info(
            "Analytics text-to-SQL skipped: no schema artifact "
            "(run python -m open_notebook.analytics.schema_snapshot)"
        )
        return TextToSqlAttempt(kind="unavailable")

    generated = await generate_sql(
        question, render_schema_context(columns, dataset.name)
    )
    if generated is None:
        return TextToSqlAttempt(kind="unavailable")

    verdict = validate_sql(generated, allowed_tables_for(dataset, columns))
    if not verdict.ok or verdict.sql is None:
        return TextToSqlAttempt(kind="rejected", reason=verdict.reason or "rejected")

    start, end = parse_period(question)
    params: Dict[str, Any] = {"authorized_team_ids": list(team_names)}
    if "start_date" in verdict.placeholders:
        params["start_date"] = start
    if "end_date" in verdict.placeholders:
        params["end_date"] = end
    executable_sql, executable_params = expand_in_list_params(
        verdict.sql, params, list_key="authorized_team_ids"
    )

    started = time.perf_counter()
    rows, row_count = await run_readonly_query(text(executable_sql), executable_params)
    duration_ms = int((time.perf_counter() - started) * 1000)
    return TextToSqlAttempt(
        kind="executed",
        sql=verdict.sql,
        rows=rows,
        duration_ms=duration_ms,
    )
