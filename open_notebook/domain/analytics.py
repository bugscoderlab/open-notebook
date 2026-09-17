"""SurrealDB domain for the analytics subsystem: dataset registry + query log.

Record fields follow the repo naming convention (target table names:
``organization``, ``team``, ``user``); the API layer exposes them as
``organization_id`` / ``team_id`` / ``user_id``.

Migration 27 defines neither ``updated`` on ``dataset`` nor on
``analytics_query_log``, so writes go through ``CREATE ... CONTENT`` on a raw
connection instead of ``repo_create``/``ObjectModel.save()`` (both of which
would inject an ``updated`` field the SCHEMAFULL tables reject).
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel
from surrealdb import RecordID  # type: ignore

from open_notebook.database.repository import (
    db_connection,
    ensure_record_id,
    parse_record_ids,
    repo_query,
)
from open_notebook.domain.user import ensure_default_organization, ensure_team

SALES_DATASET_NAME = "Sales 2026"
SALES_DATASET_TEAM_SLUG = "finance"
# connection_ref names an env-configured DSN key — raw credentials are never
# stored in SurrealDB (ADR-009).
SALES_DATASET_CONNECTION_REF = "ANALYTICS_DATABASE_URL"


class Dataset(BaseModel):
    """Registered analytics dataset (metadata only — the data lives in PostgreSQL)."""

    id: str
    organization_id: Optional[str] = None
    name: str = ""
    team_id: Optional[str] = None
    source_type: str = "postgres"
    connection_ref: str = ""
    schema_metadata: Optional[Dict[str, Any]] = None
    freshness_at: Optional[datetime] = None
    active: bool = True

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "Dataset":
        return cls(
            id=str(record.get("id", "")),
            organization_id=record.get("organization"),
            name=record.get("name", ""),
            team_id=record.get("team"),
            source_type=record.get("source_type", "postgres"),
            connection_ref=record.get("connection_ref", ""),
            schema_metadata=record.get("schema_metadata"),
            freshness_at=record.get("freshness_at"),
            active=record.get("active", True),
        )


class AnalyticsQueryLog(BaseModel):
    """One analytics question/execution audit record."""

    id: str
    user_id: Optional[str] = None
    dataset_id: Optional[str] = None
    question: str = ""
    template_id: Optional[str] = None
    duration_ms: Optional[int] = None
    row_count: Optional[int] = None
    status: str = ""
    created: Optional[datetime] = None

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "AnalyticsQueryLog":
        return cls(
            id=str(record.get("id", "")),
            user_id=record.get("user"),
            dataset_id=record.get("dataset"),
            question=record.get("question", ""),
            template_id=record.get("template_id"),
            duration_ms=record.get("duration_ms"),
            row_count=record.get("row_count"),
            status=record.get("status", ""),
            created=record.get("created"),
        )


async def list_datasets(active_only: bool = True) -> List[Dataset]:
    """List registered datasets (optionally only active ones)."""
    where = "WHERE active = true" if active_only else ""
    records = await repo_query(f"SELECT * FROM dataset {where}")
    return [Dataset.from_record(r) for r in records]


async def team_names_by_ids(team_ids: List[str]) -> Dict[str, str]:
    """Map team record ids to display names (unknown ids are omitted)."""
    if not team_ids:
        return {}
    ids = [ensure_record_id(t) for t in team_ids]
    records = await repo_query(
        "SELECT id, name FROM team WHERE id IN $ids", {"ids": ids}
    )
    return {str(r.get("id")): r.get("name", "") for r in records}


async def list_team_ids() -> List[str]:
    """List all team record ids."""
    records = await repo_query("SELECT id FROM team")
    return [str(r.get("id")) for r in records]


async def get_query_log(query_id: str) -> Optional[AnalyticsQueryLog]:
    """Fetch one analytics_query_log record by id."""
    records = await repo_query("SELECT * FROM $id", {"id": ensure_record_id(query_id)})
    if not records:
        return None
    return AnalyticsQueryLog.from_record(records[0])


async def create_query_log(
    *,
    user_id: str,
    dataset_id: Optional[str],
    question: str,
    template_id: Optional[str],
    duration_ms: Optional[int],
    row_count: Optional[int],
    status: str,
) -> AnalyticsQueryLog:
    """Insert an analytics_query_log audit record (migration 27 schema).

    ``dataset_id`` is None for denials that never reached a dataset
    (AN-010 injection refusals, no-permitted-dataset denials)."""
    data: Dict[str, Any] = {
        "user": ensure_record_id(user_id),
        "dataset": ensure_record_id(dataset_id) if dataset_id else None,
        "question": question,
        "template_id": template_id,
        "duration_ms": duration_ms,
        "row_count": row_count,
        "status": status,
    }
    async with db_connection() as conn:
        result = parse_record_ids(
            await conn.query("CREATE analytics_query_log CONTENT $data", {"data": data})
        )
    return AnalyticsQueryLog.from_record(result[0])


async def ensure_sales_2026_dataset() -> Optional[Dataset]:
    """Idempotently register the finance-owned "Sales 2026" dataset.

    Creates the default organization and the finance team only if they do
    not exist yet (they normally come from the team-access bootstrap; this
    keeps the analytics dev flow self-contained until then). Returns None
    if the dataset exists but is inactive.
    """
    existing = await repo_query(
        "SELECT * FROM dataset WHERE name = $name LIMIT 1",
        {"name": SALES_DATASET_NAME},
    )
    if existing:
        return Dataset.from_record(existing[0])

    organization_id = await ensure_default_organization()
    team_id = await ensure_team(organization_id, SALES_DATASET_TEAM_SLUG, "Finance")
    async with db_connection() as conn:
        result = parse_record_ids(
            await conn.query(
                "CREATE dataset CONTENT $data",
                {
                    "data": {
                        "organization": RecordID.parse(organization_id),
                        "name": SALES_DATASET_NAME,
                        "team": RecordID.parse(team_id),
                        "source_type": "postgres",
                        "connection_ref": SALES_DATASET_CONNECTION_REF,
                        "schema_metadata": {
                            "table": "sales_transactions",
                            "grain": "transaction",
                        },
                        "freshness_at": datetime.now(timezone.utc),
                        "active": True,
                    }
                },
            )
        )
    return Dataset.from_record(result[0])


async def touch_dataset_freshness(dataset_id: str, freshness_at: datetime) -> None:
    """Refresh a dataset's freshness timestamp (best effort, never raises)."""
    try:
        await repo_query(
            "UPDATE $id SET freshness_at = $freshness",
            {"id": ensure_record_id(dataset_id), "freshness": freshness_at},
        )
    except Exception:
        pass
