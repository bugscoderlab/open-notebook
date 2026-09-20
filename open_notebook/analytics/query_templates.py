"""Allowlisted, parameterized analytics query templates (issue #9).

The LLM never writes SQL. Intent classification (LLM-assisted, with a
deterministic keyword fallback — see ``classify_intent``) only ever picks a
``template_id`` from this registry; the server then injects every bind value
(period, statuses, row limit, and the mandatory ``authorized_team_ids``
filter). Template SQL is static, reviewed, and always contains
``AND data_team IN (:authorized_team_ids)``.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from open_notebook.analytics.engine import MAX_ROWS

TEAM_FILTER_SQL = "AND data_team IN (:authorized_team_ids)"

COMPLETED_ONLY_STATUSES = ["completed"]
REFUNDS_INCLUSIVE_STATUSES = ["completed", "refunded", "voided"]

_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?: [A-Z][a-z]+)+)\b")
# Full dates ("1 October 2026") and month-year pairs ("October 2026").
_FULL_DATE_RE = re.compile(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})\b")
_MONTH_YEAR_RE = re.compile(r"\b([A-Za-z]+)\s+(20\d{2})\b")
# "after <date>" / "since <date>" / "from <date>" leave the period open-ended.
_OPEN_ENDED_RE = re.compile(r"\b(after|since|from)\b", re.IGNORECASE)
# Sentinel end for open-ended periods; SQL compares transaction_date < :end_date.
_OPEN_END = date(9999, 12, 31)

_MONTHS = {
    name.lower(): number
    for number, name in enumerate(calendar.month_name)
    if name
}

# Capitalized multi-word phrases that are NOT customer names (service names
# from the test-pack data and dataset names). Keeps the deterministic
# customer-name extractor from latching onto e.g. "Full Groom".
_NAME_STOPWORDS = {
    "Full Groom",
    "Basic Groom",
    "Spa Treatment",
    "Boarding",
    "Sales",
    "Last Year",
    "This Year",
}


@dataclass(frozen=True)
class QueryTemplate:
    """One allowlisted analytics query.

    Attributes:
        template_id: Stable identifier stored in ``analytics_query_log``.
        description: What the template answers (also shown to the classifier LLM).
        sql: Static parameterized SQL. Never built from user input.
        default_limit: Server-controlled LIMIT bind value.
        requires_customer: Whether the question must name a customer.
    """

    template_id: str
    description: str
    sql: str
    default_limit: int = 10
    requires_customer: bool = False

    def expand_in_lists(self, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        """Produce the executable form of the SQL plus flat bind values.

        asyncpg cannot consume SQLAlchemy "expanding" IN-list binds (they
        render as an anonymous record), so the two server-controlled list
        params — ``statuses`` and ``authorized_team_ids`` — are expanded here
        into individual named placeholders. Every value stays bound; only the
        placeholder names are generated, and those come from this module
        (never from user input). The display form (:attr:`sql`) is unchanged.
        """
        flat: Dict[str, Any] = {}
        sql = self.sql
        remaining = dict(params)
        for key in ("statuses", "authorized_team_ids"):
            values = list(remaining.pop(key))
            placeholders = ", ".join(f":{key}_{i}" for i in range(len(values)))
            sql = sql.replace(f"(:{key})", f"({placeholders})")
            for i, value in enumerate(values):
                flat[f"{key}_{i}"] = value
        flat.update(remaining)
        return sql, flat


CUSTOMER_RANKING_SQL = f"""
SELECT
    customer_name,
    SUM(amount_myr) AS total_spend,
    COUNT(*) AS transaction_count,
    AVG(amount_myr) AS average_transaction_value
FROM sales_transactions
WHERE transaction_date >= :start_date
  AND transaction_date < :end_date
  AND status IN (:statuses)
  {TEAM_FILTER_SQL}
GROUP BY customer_id, customer_name
ORDER BY total_spend DESC
LIMIT :limit
""".strip()

HIGHEST_SPENDER_SQL = CUSTOMER_RANKING_SQL

AVERAGE_TICKET_SQL = f"""
SELECT
    SUM(amount_myr) AS total_spend,
    COUNT(*) AS transaction_count,
    AVG(amount_myr) AS average_transaction_value
FROM sales_transactions
WHERE transaction_date >= :start_date
  AND transaction_date < :end_date
  AND status IN (:statuses)
  {TEAM_FILTER_SQL}
LIMIT :limit
""".strip()

CUSTOMER_AVERAGE_TICKET_SQL = f"""
SELECT
    customer_name,
    SUM(amount_myr) AS total_spend,
    COUNT(*) AS transaction_count,
    AVG(amount_myr) AS average_transaction_value
FROM sales_transactions
WHERE transaction_date >= :start_date
  AND transaction_date < :end_date
  AND status IN (:statuses)
  {TEAM_FILTER_SQL}
  AND customer_name = :customer_name
GROUP BY customer_id, customer_name
ORDER BY total_spend DESC
LIMIT :limit
""".strip()

TOP_SERVICE_SQL = f"""
SELECT
    service,
    COUNT(*) AS transaction_count,
    SUM(amount_myr) AS total_spend
FROM sales_transactions
WHERE transaction_date >= :start_date
  AND transaction_date < :end_date
  AND status IN (:statuses)
  {TEAM_FILTER_SQL}
GROUP BY service
ORDER BY transaction_count DESC, total_spend DESC
LIMIT :limit
""".strip()

CUSTOMER_TOP_SERVICE_SQL = f"""
SELECT
    customer_name,
    service,
    COUNT(*) AS transaction_count,
    SUM(amount_myr) AS total_spend
FROM sales_transactions
WHERE transaction_date >= :start_date
  AND transaction_date < :end_date
  AND status IN (:statuses)
  {TEAM_FILTER_SQL}
  AND customer_name = :customer_name
GROUP BY customer_id, customer_name, service
ORDER BY transaction_count DESC, total_spend DESC
LIMIT :limit
""".strip()

TEMPLATES: Dict[str, QueryTemplate] = {
    t.template_id: t
    for t in [
        QueryTemplate(
            template_id="highest_spender",
            description="Who is the highest spender in a period (top customers by total spend)",
            sql=HIGHEST_SPENDER_SQL,
            default_limit=10,
        ),
        QueryTemplate(
            template_id="customer_ranking",
            description="Rank all customers by total spend in a period",
            sql=CUSTOMER_RANKING_SQL,
            default_limit=MAX_ROWS,
        ),
        QueryTemplate(
            template_id="average_ticket",
            description="Average transaction value across all transactions in a period",
            sql=AVERAGE_TICKET_SQL,
            default_limit=1,
        ),
        QueryTemplate(
            template_id="customer_average_ticket",
            description="Average transaction value for one named customer",
            sql=CUSTOMER_AVERAGE_TICKET_SQL,
            default_limit=1,
            requires_customer=True,
        ),
        QueryTemplate(
            template_id="top_service",
            description="Most-sold services across all customers in a period",
            sql=TOP_SERVICE_SQL,
            default_limit=10,
        ),
        QueryTemplate(
            template_id="customer_top_service",
            description="Most-used service for one named customer",
            sql=CUSTOMER_TOP_SERVICE_SQL,
            default_limit=10,
            requires_customer=True,
        ),
    ]
}


def get_template(template_id: str) -> QueryTemplate:
    """Return a template by id, raising KeyError for unknown ids."""
    return TEMPLATES[template_id]


def render_parameterized_query(template_id: str) -> str:
    """Return the display form of a template: named placeholders, no values.

    Used for AN-009 (``GET /api/analytics/queries/{id}``) — the team filter
    is visible and no credential or literal value can leak because the SQL is
    the static, reviewed template text.
    """
    return get_template(template_id).sql


def extract_customer_name(question: str) -> Optional[str]:
    """Pull a customer name (Capitalized Words) out of a question, if any."""
    for match in _NAME_RE.finditer(question):
        candidate = match.group(1)
        if candidate not in _NAME_STOPWORDS:
            return candidate
    return None


def parse_period(question: str) -> Tuple[date, date]:
    """Resolve the [start, end) date range for a question.

    Precedence: a full date ("1 October 2026") wins over a month-year pair
    ("October 2026"), which wins over a bare 4-digit year ("in 2027"); with
    no date mention, the current calendar year is used. "after <date>" leaves
    the period open-ended (end = 9999-12-31) and is exclusive ("after 1
    October" starts on 2 October); "since"/"from" are inclusive and also
    open-ended. AN-008 relies on this: "after 1 October 2026" resolves to a
    future, still-empty range, which the service reports honestly as
    ``no_data`` instead of silently falling back to the year's data.
    """
    full = _FULL_DATE_RE.search(question)
    if full and full.group(2).lower() in _MONTHS:
        day, year = int(full.group(1)), int(full.group(3))
        start = date(year, _MONTHS[full.group(2).lower()], day)
        if _OPEN_ENDED_RE.search(question, 0, full.start()):
            if "after" in question.lower()[: full.start()]:
                start += timedelta(days=1)
            return start, _OPEN_END
        return start, date(year + 1, 1, 1)

    month_year = _MONTH_YEAR_RE.search(question)
    if month_year and month_year.group(1).lower() in _MONTHS:
        year = int(month_year.group(2))
        start = date(year, _MONTHS[month_year.group(1).lower()], 1)
        if _OPEN_ENDED_RE.search(question, 0, month_year.start()):
            if "after" in question.lower()[: month_year.start()]:
                start = date(
                    year + (start.month == 12), start.month % 12 + 1, 1
                )
            return start, _OPEN_END
        return start, date(
            year + (start.month == 12), start.month % 12 + 1, 1
        )

    year_match = _YEAR_RE.search(question)
    year = int(year_match.group(1)) if year_match else date.today().year
    return date(year, 1, 1), date(year + 1, 1, 1)


def build_template_params(
    question: str,
    template: QueryTemplate,
    authorized_team_ids: List[str],
    include_refunds: bool,
) -> Dict[str, Any]:
    """Assemble the server-controlled bind values for a template."""
    start, end = parse_period(question)
    params: Dict[str, Any] = {
        "start_date": start,
        "end_date": end,
        "statuses": (
            REFUNDS_INCLUSIVE_STATUSES if include_refunds else COMPLETED_ONLY_STATUSES
        ),
        "authorized_team_ids": list(authorized_team_ids),
        "limit": template.default_limit,
    }
    if template.requires_customer:
        params["customer_name"] = extract_customer_name(question)
    return params


def classify_intent_keywords(question: str) -> Optional[str]:
    """Deterministic fallback intent classifier (keyword rules).

    Covers the approved question shapes in the test pack. Deliberately
    conservative: anything unmatched returns None, and the service refuses
    to guess (no invented analytics). Prompt-injection phrasing such as
    "ignore permissions" never matches a rule.
    """
    lowered = question.lower()
    customer = extract_customer_name(question)
    if customer:
        if any(k in lowered for k in ("service", "groom", "spa", "boarding")):
            return "customer_top_service"
        if any(k in lowered for k in ("average", "avg")):
            return "customer_average_ticket"
    if any(
        k in lowered
        for k in (
            "highest spender",
            "top spender",
            "biggest spender",
            "spends the most",
            "spent the most",
        )
    ):
        return "highest_spender"
    if any(k in lowered for k in ("rank", "ranking", "top customer")):
        return "customer_ranking"
    if "average" in lowered or "avg" in lowered:
        return "average_ticket"
    if "service" in lowered:
        return "top_service"
    return None
