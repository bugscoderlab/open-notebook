"""Agentic text-to-SQL: generation, validation, execution (ADR-015).

The primary analytics path once a default chat model is configured: the model
writes SQL from the dataset's schema context + the question; the server
validates it (``sql_gate``), binds the mandatory team filter (and the period
placeholders when the model used them), and executes through the read-only
engine. When the model or the schema artifact is unavailable, the attempt is
``unavailable`` and the caller falls back to the template path — the
pipeline's honesty guarantees live in the engine and the gate, not in the
model.

Retry-with-feedback (the bounded agent loop, :data:`MAX_GENERATED_ATTEMPTS`)
lives in :func:`attempt_text_to_sql`: gate rejections, execution errors, and
zero-row results are fed back to the model as revision context, and every
outcome maps deterministically — rows, honest ``no_data``, or guidance.
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


async def generate_sql(
    question: str, schema_context: str, feedback: Optional[List[str]] = None
) -> Optional[str]:
    """Ask the default chat model for one SQL string.

    ``feedback`` carries the previous attempts' rejection/execution/empty
    outcomes so the model can revise rather than repeat. Returns None when
    no model is configured or the call fails (mirrors the graceful-fallback
    contract of the template-path classifiers).
    """
    try:
        from langchain_core.messages import HumanMessage

        from open_notebook.ai.provision import provision_langchain_model

        revision = ""
        if feedback:
            revision = (
                "\nPrevious attempts (revise, do not repeat):\n- "
                + "\n- ".join(feedback)
                + "\n"
            )
        prompt = build_generation_prompt(question, schema_context) + revision
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
    """Bounded agent loop: generate → gate → execute, max 3 attempts.

    Gate rejections, execution errors, and zero-row outcomes are fed back to
    the model as revision context. Deterministic outcome mapping when
    attempts run out:

    - last outcome zero rows → ``executed`` with 0 rows (the service renders
      the honest ``no_data`` answer);
    - last outcome a rejection or execution error → ``rejected`` with the
      machine-readable reason surfaced as guidance.

    The model never talks to the database; its only tool is this loop.
    """
    columns = load_schema_columns()
    if columns is None:
        logger.info(
            "Analytics text-to-SQL skipped: no schema artifact "
            "(run python -m open_notebook.analytics.schema_snapshot)"
        )
        return TextToSqlAttempt(kind="unavailable")

    schema_context = render_schema_context(columns, dataset.name)
    allowed = allowed_tables_for(dataset, columns)
    feedback: List[str] = []
    last_rejection: Optional[str] = None
    last_zero: Optional[TextToSqlAttempt] = None

    for attempt_no in range(1, MAX_GENERATED_ATTEMPTS + 1):
        generated = await generate_sql(question, schema_context, feedback=feedback)
        if generated is None:
            # Model unavailable (mid-loop or throughout) → template fallback.
            return TextToSqlAttempt(kind="unavailable")

        verdict = validate_sql(generated, allowed)
        if not verdict.ok or verdict.sql is None:
            last_rejection = verdict.reason or "rejected"
            last_zero = None
            feedback.append(
                f"attempt {attempt_no} was rejected by the safety checks: "
                f"{last_rejection}. Fix the SQL accordingly."
            )
            continue

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
        try:
            rows, _ = await run_readonly_query(text(executable_sql), executable_params)
        except Exception as e:
            last_rejection = f"execution_error:{str(e)[:200]}"
            last_zero = None
            feedback.append(
                f"attempt {attempt_no} failed to execute: {str(e)[:200]}. "
                "Fix the SQL (check column and table names)."
            )
            continue
        duration_ms = int((time.perf_counter() - started) * 1000)

        if not rows:
            feedback.append(
                f"attempt {attempt_no} returned 0 rows. If the question "
                "implies data should exist, check the period and filters; "
                "otherwise a query that legitimately returns nothing is fine."
            )
            last_zero = TextToSqlAttempt(
                kind="executed", sql=verdict.sql, rows=[], duration_ms=duration_ms
            )
            continue

        return TextToSqlAttempt(
            kind="executed",
            sql=verdict.sql,
            rows=rows,
            duration_ms=duration_ms,
        )

    if last_zero is not None:
        return last_zero
    return TextToSqlAttempt(kind="rejected", reason=last_rejection or "rejected")
