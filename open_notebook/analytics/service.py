"""Analytics service pipeline (issue #9, ADR-009).

Question → classify intent → select allowlisted template → inject
server-controlled filters (period, statuses, mandatory team filter) →
execute read-only against PostgreSQL → small result to the configured
default chat model for the natural-language explanation.

The LLM never writes SQL and never sees credentials: it only picks a
template_id (validated against the allowlist, with a deterministic keyword
fallback when no chat model is configured) and explains the already-
computed result. When no chat model is available, a deterministic
explanation is rendered from the rows instead — answers always come from
the database, never from the model.
"""

import json
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Protocol

from loguru import logger
from sqlalchemy import text

from open_notebook.analytics import text_to_sql
from open_notebook.analytics.engine import run_readonly_query
from open_notebook.analytics.query_templates import (
    TEMPLATES,
    build_template_params,
    classify_intent_keywords,
    get_template,
    parse_period,
)
from open_notebook.domain import analytics as analytics_domain
from open_notebook.domain.analytics import AnalyticsQueryLog, Dataset
from open_notebook.exceptions import InvalidInputError
from open_notebook.utils.text_utils import clean_thinking_content, extract_text_content

Status = Literal["ok", "denied", "no_data"]


class AnalyticsCaller(Protocol):
    """Minimal caller shape the service needs (api.access.CurrentUser satisfies it)."""

    id: str
    team_id: str
    role: Literal["member", "team_manager", "ceo", "admin"]


@dataclass
class AnalyticsAnswer:
    """Result of one analytics question (frozen contract: AnalyticsAnswer)."""

    status: Status
    answer_text: str
    query_id: Optional[str] = None
    kpis: List[Dict[str, Any]] = field(default_factory=list)
    table: Optional[Dict[str, Any]] = None
    chart: Optional[Dict[str, Any]] = None
    scope: Optional[Dict[str, Any]] = None
    freshness_at: Optional[str] = None
    query_template: Optional[str] = None


def _money(value: Any) -> str:
    """Format a numeric amount the way the frozen contract shows it."""
    amount = Decimal(str(value))
    if amount == amount.to_integral_value():
        return f"MYR {int(amount):,}"
    return f"MYR {amount:,.2f}"


def _plain(value: Any) -> Any:
    """Make a DB value JSON-serializable (Decimal → int/float, date → iso)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _jsonable(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [{key: _plain(value) for key, value in row.items()} for row in rows]


DENIED_NO_DATASET = (
    "You don't have access to any analytics datasets, so I can't answer "
    "that question. Contact an administrator if you need access."
)
DENIED_DATASET = (
    "You don't have permission to access that dataset, so I can't answer "
    "that question. Contact the dataset owner if you need access."
)
DENIED_INJECTION = (
    "I can't change or bypass access permissions, and I can't answer "
    "questions about other teams' data. I can only analyse the datasets "
    "your team is permitted to see."
)

# AN-010: permission-injection refusal. The question is checked BEFORE any
# template classification or dataset lookup, so an injected question can
# never reach the allowlisted queries or the LLM explainer. The patterns
# deliberately target permission-bypass language only — ordinary analytical
# questions never contain these phrases.
_INJECTION_PATTERNS = (
    "ignore permission",
    "ignoring permission",
    "bypass permission",
    "without permission",
    "no permission",
    "disable permission",
    "skip permission",
    "permission check",
    "access control",
    "all teams",
    "other team",
    "other department",
    "salary",
    "salaries",
    "confidential",
)


def is_permission_injection(question: str) -> bool:
    """True when the question asks to bypass permissions or probe other
    teams' data (AN-010). Whole-phrase, case-insensitive substring match."""
    lowered = question.lower()
    return any(pattern in lowered for pattern in _INJECTION_PATTERNS)


async def permitted_dataset_ids_for(caller: AnalyticsCaller) -> List[str]:
    """Dataset ids the caller may query: own team owns it, or CEO/admin read all."""
    datasets = await analytics_domain.list_datasets(active_only=True)
    if caller.role in {"ceo", "admin"}:
        return [d.id for d in datasets]
    return [d.id for d in datasets if d.team_id == caller.team_id]


# Backwards-compatible alias (kept for the api.access dependency).
permitted_dataset_ids = permitted_dataset_ids_for


async def list_datasets_for_caller(
    caller: AnalyticsCaller,
) -> List[Dict[str, Any]]:
    """Permitted datasets in the frozen contract shape (incl. team_name)."""
    allowed = set(await permitted_dataset_ids(caller))
    datasets = [d for d in await analytics_domain.list_datasets() if d.id in allowed]
    names = await analytics_domain.team_names_by_ids(
        [d.team_id for d in datasets if d.team_id]
    )
    return [
        {
            "id": d.id,
            "name": d.name,
            "team_id": d.team_id,
            "team_name": names.get(d.team_id or "", ""),
            "source_type": d.source_type,
            "freshness_at": d.freshness_at,
        }
        for d in datasets
    ]


async def _authorized_team_names(caller: AnalyticsCaller, dataset: Dataset) -> List[str]:
    """data_team values the caller may see for this dataset.

    Every query still carries the mandatory ``data_team IN``
    filter — for a permitted caller the owning team's name is always in
    the list, so a permitted question is never over-filtered.
    """
    if caller.role in {"ceo", "admin"}:
        team_ids = await analytics_domain.list_team_ids()
    else:
        team_ids = [caller.team_id]
    names = await analytics_domain.team_names_by_ids(team_ids)
    return [name for name in names.values() if name]


async def _classify_intent(question: str, include_refunds: bool) -> Optional[str]:
    """Pick an allowlisted template_id for the question.

    The configured default chat model classifies when available; its answer
    is validated against the registry and falls back to the deterministic
    keyword classifier whenever the model is missing, errors, or returns an
    unknown id. The LLM only ever names a template — it never sees SQL.
    """
    llm_id = await _classify_with_llm(question)
    if llm_id in TEMPLATES:
        return llm_id
    return classify_intent_keywords(question)


async def _classify_with_llm(question: str) -> Optional[str]:
    try:
        from langchain_core.messages import HumanMessage

        from open_notebook.ai.provision import provision_langchain_model

        catalog = "\n".join(
            f"- {t.template_id}: {t.description}" for t in TEMPLATES.values()
        )
        prompt = (
            "You route analytics questions to exactly one approved query template.\n"
            f"Approved templates:\n{catalog}\n\n"
            "Question: "
            f"{question}\n\n"
            "Reply with ONLY the template_id, or NONE if no template fits."
        )
        model = await provision_langchain_model(prompt, None, "chat", max_tokens=50)
        response = await model.ainvoke([HumanMessage(content=prompt)])
        answer = clean_thinking_content(
            extract_text_content(response.content)
        ).strip()
        candidate = answer.strip().strip("`").lower()
        return candidate if candidate in TEMPLATES else None
    except Exception as e:
        logger.warning(f"Analytics intent classification fell back to keywords: {e}")
        return None


async def _explain(question: str, rows: List[Dict[str, Any]], scope: Dict[str, Any]) -> str:
    """Natural-language explanation of the computed result.

    Uses the configured default chat model when available; otherwise a
    deterministic, exact explanation rendered from the rows (AN-008's
    honesty guarantee never depends on a model being configured).
    """
    payload = json.dumps({"question": question, "scope": scope, "result": rows})
    llm_text = await _explain_with_llm(question, payload)
    if llm_text:
        return llm_text
    return _fallback_explanation(question, rows)


async def _explain_with_llm(question: str, payload: str) -> Optional[str]:
    try:
        from langchain_core.messages import HumanMessage, SystemMessage

        from open_notebook.ai.provision import provision_langchain_model

        system = (
            "You explain analytics results that were already computed by a "
            "database. Never invent customers, values, or rankings — use only "
            "the provided result rows. Be concise: one or two sentences."
        )
        model = await provision_langchain_model(
            system + payload, None, "chat", max_tokens=500
        )
        response = await model.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=payload)]
        )
        return clean_thinking_content(extract_text_content(response.content)).strip()
    except Exception as e:
        logger.warning(f"Analytics explanation fell back to template text: {e}")
        return None


def _fallback_explanation(question: str, rows: List[Dict[str, Any]]) -> str:
    """Deterministic explanation for when no chat model is configured."""
    if not rows:
        return "No data was found for that period."
    first = rows[0]
    lowered = question.lower()
    if "service" in lowered and "customer_name" in first and "service" in first:
        return (
            f"{first['service']} is {first['customer_name']}'s most-used service "
            f"with {first['transaction_count']} transactions."
        )
    if "service" in lowered and "service" in first:
        return (
            f"{first['service']} is the top service with "
            f"{first['transaction_count']} transactions."
        )
    if "average" in lowered or "avg" in lowered:
        count = first.get("transaction_count", 0)
        whose = f"{first['customer_name']}'s " if "customer_name" in first else "The "
        return (
            f"{whose}average transaction value is "
            f"{_money(first['average_transaction_value'])} across {count} "
            f"transactions."
        )
    if "rank" in lowered:
        ranking = "; ".join(
            f"{i + 1}. {r['customer_name']} ({_money(r['total_spend'])})"
            for i, r in enumerate(rows)
        )
        return f"Customer ranking by total spend: {ranking}."
    return (
        f"{first['customer_name']} is the highest spender with "
        f"{_money(first['total_spend'])} across {first['transaction_count']} "
        f"transactions (average {_money(first['average_transaction_value'])})."
    )


def _build_kpis(template_id: str, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    first = rows[0]
    if template_id in {"highest_spender", "customer_ranking"}:
        return [
            {"label": "HIGHEST SPENDER", "value": str(first["customer_name"]), "note": ""},
            {"label": "TOTAL SPEND", "value": _money(first["total_spend"]), "note": ""},
            {
                "label": "TRANSACTIONS",
                "value": str(first["transaction_count"]),
                "note": "",
            },
            {
                "label": "AVERAGE TICKET",
                "value": _money(first["average_transaction_value"]),
                "note": "",
            },
        ]
    if template_id in {"average_ticket", "customer_average_ticket"}:
        return [
            {
                "label": "AVERAGE TICKET",
                "value": _money(first["average_transaction_value"]),
                "note": "",
            },
            {"label": "TRANSACTIONS", "value": str(first["transaction_count"]), "note": ""},
        ]
    if template_id == "customer_top_service":
        return [
            {"label": "TOP SERVICE", "value": str(first["service"]), "note": ""},
            {"label": "TRANSACTIONS", "value": str(first["transaction_count"]), "note": ""},
        ]
    if template_id == "top_service":
        return [
            {"label": "TOP SERVICE", "value": str(first["service"]), "note": ""},
            {"label": "TRANSACTIONS", "value": str(first["transaction_count"]), "note": ""},
        ]
    return []


def _build_table(template_id: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    first = rows[0]
    columns = list(first.keys())
    if template_id in {"highest_spender", "customer_ranking"}:
        columns = ["customer_name", "total_spend", "transaction_count"]
    elif template_id in {"average_ticket", "customer_average_ticket"}:
        columns = list(first.keys())
    elif template_id == "top_service":
        columns = ["service", "transaction_count", "total_spend"]
    elif template_id == "customer_top_service":
        columns = ["customer_name", "service", "transaction_count"]
    return {
        "columns": columns,
        "rows": [[row.get(column) for column in columns] for row in rows],
    }


def _build_chart(template_id: str, rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    if template_id in {"highest_spender", "customer_ranking"}:
        return {
            "kind": "bars",
            "title": "Top customers by spend",
            "items": [
                {"label": str(r["customer_name"]), "value": _plain(r["total_spend"])}
                for r in rows
            ],
        }
    if template_id in {"top_service", "customer_top_service"}:
        return {
            "kind": "bars",
            "title": "Top services by transactions",
            "items": [
                {"label": str(r["service"]), "value": _plain(r["transaction_count"])}
                for r in rows
            ],
        }
    return None


# Bar charts are only useful up to this many rows; wider results stay
# table-only (500 bars are unreadable — the table carries that answer).
_CHART_MAX_ROWS = 90


def _chart_from_table(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Synthesize a bars chart from a free-form result table (ADR-015 path).

    Text-to-SQL answers have no fixed column shape, so the chart is derived:
    the first date-like column (else the first non-numeric column) becomes
    the label, the first numeric column the value — e.g. daily revenue rows
    (transaction_date, total) chart as one bar per day. Returns None when
    no sane label/value pairing exists or the result is too wide to chart.
    """
    if not rows or len(rows) > _CHART_MAX_ROWS:
        return None
    columns = list(rows[0].keys())
    numeric = [
        c
        for c in columns
        if all(
            isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool)
            for r in rows
        )
    ]
    labels = [c for c in columns if c not in numeric]
    date_labels = [c for c in labels if "date" in c.lower() or "day" in c.lower()]
    label_col = date_labels[0] if date_labels else (labels[0] if labels else None)
    value_col = next((c for c in numeric if c != label_col), None)
    if not label_col or not value_col:
        return None
    return {
        "kind": "bars",
        "title": f"{value_col.replace('_', ' ')} by {label_col.replace('_', ' ')}",
        "items": [
            {"label": str(r.get(label_col)), "value": _plain(r.get(value_col))}
            for r in rows
        ],
    }


async def ask_analytics_question(
    *,
    caller: AnalyticsCaller,
    question: str,
    dataset_id: Optional[str],
    include_refunds: bool,
    permitted_dataset_ids: Optional[List[str]] = None,
) -> AnalyticsAnswer:
    """Run the full analytics pipeline for one question.

    ``permitted_dataset_ids`` is injected by the API layer (derived from the
    caller by the access seam); when omitted it is derived here from the
    caller's role/team.

    Raises:
        InvalidInputError: The question matches no approved template (the
            request is refused — never guessed, never leaked).
    """
    if is_permission_injection(question):
        # AN-010: refuse before anything else; the attempt is logged with
        # the same denied audit shape as a permission denial.
        log = await analytics_domain.create_query_log(
            user_id=caller.id,
            dataset_id=None,
            question=question,
            template_id=None,
            duration_ms=None,
            row_count=None,
            status="denied",
        )
        logger.warning(
            f"Analytics permission-injection attempt refused for user {caller.id}"
        )
        return AnalyticsAnswer(
            status="denied", answer_text=DENIED_INJECTION, query_id=log.id
        )

    datasets = await analytics_domain.list_datasets(active_only=True)
    if permitted_dataset_ids is None:
        permitted_dataset_ids = await permitted_dataset_ids_for(caller)
    permitted = set(permitted_dataset_ids)

    target: Optional[Dataset] = None
    if dataset_id:
        target = next((d for d in datasets if d.id == dataset_id), None)
        if target is None:
            from open_notebook.exceptions import NotFoundError

            raise NotFoundError(f"Dataset {dataset_id} not found")
        if target.id not in permitted:
            log = await analytics_domain.create_query_log(
                user_id=caller.id,
                dataset_id=target.id,
                question=question,
                template_id=None,
                duration_ms=None,
                row_count=None,
                status="denied",
            )
            return AnalyticsAnswer(
                status="denied", answer_text=DENIED_DATASET, query_id=log.id
            )
    else:
        target = next((d for d in datasets if d.id in permitted), None)

    if target is None:
        return AnalyticsAnswer(status="denied", answer_text=DENIED_NO_DATASET)

    team_names = await _authorized_team_names(caller, target)

    # Primary path (ADR-015): free-form question → model-written SQL →
    # validation gate → read-only execution. "unavailable" (no model or no
    # schema artifact) falls through to the template path unchanged.
    llm_attempt = await text_to_sql.attempt_text_to_sql(
        question=question, dataset=target, team_names=team_names
    )
    if llm_attempt.kind == "executed":
        return await _answer_from_generated(caller, target, question, llm_attempt)
    if llm_attempt.kind == "rejected":
        raise InvalidInputError(
            "I generated SQL for that question but it failed the safety "
            f"checks ({llm_attempt.reason}). Try rephrasing the question."
        )

    refunds_scope = include_refunds or "refund" in question.lower()
    template_id = await _classify_intent(question, refunds_scope)
    if not template_id:
        raise InvalidInputError(
            "That question does not match an approved analytics query, so I "
            "can't answer it. Try a question about spend, rankings, averages, "
            "or services."
        )
    template = get_template(template_id)

    params = build_template_params(
        question, template, team_names, refunds_scope
    )
    if template.requires_customer and not params.get("customer_name"):
        raise InvalidInputError(
            "That question needs a customer name, e.g. 'What is the average "
            "transaction value for Sarah Lim?'"
        )

    started = time.perf_counter()
    executable_sql, executable_params = template.expand_in_lists(params)
    rows, row_count = await run_readonly_query(
        text(executable_sql), executable_params
    )
    duration_ms = int((time.perf_counter() - started) * 1000)

    start, end = parse_period(question)
    scope = {
        "dataset": target.name,
        "period": f"{start.isoformat()} → {end.isoformat()}",
        "refunds": "included" if refunds_scope else "excluded",
    }
    query_template = template.sql
    freshness = target.freshness_at

    if row_count == 0:
        log = await analytics_domain.create_query_log(
            user_id=caller.id,
            dataset_id=target.id,
            question=question,
            template_id=template_id,
            duration_ms=duration_ms,
            row_count=0,
            status="no_data",
        )
        return AnalyticsAnswer(
            status="no_data",
            answer_text=(
                f"No data was found in {target.name} for the period "
                f"{scope['period']} ({scope['refunds']} refunds). I won't "
                f"invent values — try a different period."
            ),
            query_id=log.id,
            scope=scope,
            freshness_at=freshness.isoformat() if freshness else None,
            query_template=query_template,
        )

    json_rows = _jsonable(rows)
    answer_text = await _explain(question, json_rows, scope)
    log = await analytics_domain.create_query_log(
        user_id=caller.id,
        dataset_id=target.id,
        question=question,
        template_id=template_id,
        duration_ms=duration_ms,
        row_count=row_count,
        status="ok",
    )
    return AnalyticsAnswer(
        status="ok",
        answer_text=answer_text,
        query_id=log.id,
        kpis=_build_kpis(template_id, json_rows),
        table=_build_table(template_id, json_rows),
        chart=_build_chart(template_id, json_rows),
        scope=scope,
        freshness_at=freshness.isoformat() if freshness else None,
        query_template=query_template,
    )


def _generic_fallback_explanation(rows: List[Dict[str, Any]]) -> str:
    """Deterministic explanation for free-form result shapes (LLM path).

    Unlike the template-path fallback, generated rows have no fixed column
    shape, so this renders the first row verbatim instead of keying on
    known column names.
    """
    first = rows[0]
    rendered = ", ".join(f"{key}={value}" for key, value in first.items())
    plural = "row" if len(rows) == 1 else "rows"
    return f"The query returned {len(rows)} {plural}. First row: {rendered}."


async def _answer_from_generated(
    caller: AnalyticsCaller,
    target: Dataset,
    question: str,
    attempt: text_to_sql.TextToSqlAttempt,
) -> AnalyticsAnswer:
    """Build the frozen-contract answer for an executed generated query."""
    json_rows = _jsonable(attempt.rows)
    scope = {
        "dataset": target.name,
        "period": "as specified in your question",
        "refunds": "model-controlled",
    }
    freshness = target.freshness_at
    freshness_iso = freshness.isoformat() if freshness else None

    if not json_rows:
        log = await analytics_domain.create_query_log(
            user_id=caller.id,
            dataset_id=target.id,
            question=question,
            template_id=None,
            duration_ms=attempt.duration_ms,
            row_count=0,
            status="no_data",
            generated_sql=attempt.sql,
        )
        return AnalyticsAnswer(
            status="no_data",
            answer_text=(
                f"No data was found in {target.name} for that question. "
                "I won't invent values — try a different period or filters."
            ),
            query_id=log.id,
            scope=scope,
            freshness_at=freshness_iso,
            query_template=attempt.sql,
        )

    payload = json.dumps(
        {"question": question, "scope": scope, "result": json_rows}
    )
    answer_text = await _explain_with_llm(question, payload)
    if not answer_text:
        answer_text = _generic_fallback_explanation(json_rows)
    log = await analytics_domain.create_query_log(
        user_id=caller.id,
        dataset_id=target.id,
        question=question,
        template_id=None,
        duration_ms=attempt.duration_ms,
        row_count=len(json_rows),
        status="ok",
        generated_sql=attempt.sql,
    )
    columns = list(json_rows[0].keys())
    return AnalyticsAnswer(
        status="ok",
        answer_text=answer_text,
        query_id=log.id,
        table={
            "columns": columns,
            "rows": [[row.get(column) for column in columns] for row in json_rows],
        },
        chart=_chart_from_table(json_rows),
        scope=scope,
        freshness_at=freshness_iso,
        query_template=attempt.sql,
    )


async def get_query_for_caller(
    caller: AnalyticsCaller, query_id: str, permitted_dataset_ids: List[str]
) -> Optional[Dict[str, Any]]:
    """Fetch one stored query log, enforcing dataset ownership (T9/ADR-014).

    Same policy as asking: a log whose dataset the caller may not query is
    indistinguishable from a missing one (None). Dataset-less logs (AN-010
    refusals, no-permitted-dataset denials) are readable by their author,
    admins, and the CEO only.
    """
    record = await get_query(query_id)
    if record is None:
        return None
    log_dataset = record.get("dataset_id")
    if caller.role in ("admin", "ceo"):
        return record
    if log_dataset is None:
        if record.get("user_id") == caller.id:
            return record
        return None
    if log_dataset not in set(permitted_dataset_ids):
        return None
    return record


async def get_query(query_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a stored query-log entry with its rendered parameterized query.

    AN-009: the rendered query shows named placeholders only — the team
    filter is visible and no credential or literal value can leak.
    """
    log = await analytics_domain.get_query_log(query_id)
    if log is None:
        return None
    dataset_name = None
    if log.dataset_id:
        try:
            records = await analytics_domain.list_datasets(active_only=False)
            dataset_name = next(
                (d.name for d in records if d.id == log.dataset_id), None
            )
        except Exception:
            dataset_name = None
    return {
        "query_id": log.id,
        "user_id": log.user_id,
        "dataset_id": log.dataset_id,
        "dataset_name": dataset_name,
        "question": log.question,
        "template_id": log.template_id,
        "duration_ms": log.duration_ms,
        "row_count": log.row_count,
        "status": log.status,
        "created": log.created.isoformat() if log.created else None,
        "query_template": _display_query_for_log(log),
    }


def _render_template_for_display(template_id: str) -> Optional[str]:
    try:
        return get_template(template_id).sql
    except KeyError:
        return None


def _display_query_for_log(log: AnalyticsQueryLog) -> Optional[str]:
    """AN-009 display SQL: generated placeholder SQL wins; else template text."""
    if log.generated_sql:
        return log.generated_sql
    if log.template_id:
        return _render_template_for_display(log.template_id)
    return None


async def refresh_dataset_freshness(dataset_id: str) -> None:
    """Best-effort freshness refresh from max(transaction_date) in PostgreSQL."""
    try:
        from sqlalchemy import text

        rows, _ = await run_readonly_query(
            text("SELECT MAX(transaction_date) AS freshness FROM sales_transactions")
        )
        freshness = rows[0].get("freshness") if rows else None
        if freshness:
            await analytics_domain.touch_dataset_freshness(dataset_id, freshness)
    except Exception as e:
        logger.debug(f"Could not refresh dataset freshness: {e}")
