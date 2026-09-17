"""Analytics API (issue #9/T8, permissions #10/T9) — datasets, ask, stored queries.

Frozen contract: docs/7-DEVELOPMENT/team-access/api-contracts.md (Analytics).
The service accepts a CurrentUser plus permitted_dataset_ids from the
api/access seam; T9 removed the dev bypass — every analytics endpoint
authenticates like every other router, and stored queries enforce the
same dataset ownership as asking (a caller outside the dataset's team
gets a 404, not an existence oracle).
"""

from typing import Any, List

from fastapi import APIRouter, Depends
from loguru import logger

from api.access import CurrentUser, get_current_user, get_permitted_dataset_ids
from api.models import (
    AnalyticsAnswerResponse,
    AnalyticsAskRequest,
    AnalyticsDatasetResponse,
)
from open_notebook.analytics.service import (
    AnalyticsAnswer,
    ask_analytics_question,
    get_query_for_caller,
    list_datasets_for_caller,
)
from open_notebook.domain.analytics import ensure_sales_2026_dataset
from open_notebook.exceptions import (
    DatabaseOperationError,
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)

router = APIRouter()


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, "isoformat") else value


def _to_response(answer: AnalyticsAnswer) -> AnalyticsAnswerResponse:
    return AnalyticsAnswerResponse(
        query_id=answer.query_id,
        status=answer.status,
        answer_text=answer.answer_text,
        kpis=answer.kpis,
        table=answer.table,
        chart=answer.chart,
        scope=answer.scope,
        freshness_at=answer.freshness_at,
        query_template=answer.query_template,
    )


@router.get("/analytics/datasets", response_model=List[AnalyticsDatasetResponse])
async def list_analytics_datasets(
    user: CurrentUser = Depends(get_current_user),
    permitted_ids: List[str] = Depends(get_permitted_dataset_ids),
):
    """List analytics datasets permitted for the caller."""
    try:
        await ensure_sales_2026_dataset()
        datasets = await list_datasets_for_caller(user)
        return [
            AnalyticsDatasetResponse(
                id=d["id"],
                name=d["name"],
                team_id=d["team_id"],
                team_name=d["team_name"],
                source_type=d["source_type"],
                freshness_at=_iso(d["freshness_at"]),
            )
            for d in datasets
            if d["id"] in set(permitted_ids)
        ]
    except (InvalidInputError, NotFoundError, OpenNotebookError):
        raise
    except Exception as e:
        logger.error(f"Error listing analytics datasets: {e}")
        raise DatabaseOperationError(f"Failed to list analytics datasets: {e}")


@router.post("/analytics/ask")
async def ask_analytics(
    request: AnalyticsAskRequest,
    user: CurrentUser = Depends(get_current_user),
    permitted_ids: List[str] = Depends(get_permitted_dataset_ids),
):
    """Answer one analytics question from allowlisted, permission-filtered templates."""
    try:
        # The service owns the full policy: unknown dataset_id → 404,
        # permitted check → zero-leakage denied shape (logged), pipeline otherwise.
        answer = await ask_analytics_question(
            caller=user,
            question=request.question,
            dataset_id=request.dataset_id,
            include_refunds=request.include_refunds,
            permitted_dataset_ids=permitted_ids,
        )
        if answer.status == "denied":
            # Frozen contract: denied carries only status + answer_text.
            return {"status": "denied", "answer_text": answer.answer_text}
        return _to_response(answer)
    except (InvalidInputError, NotFoundError, OpenNotebookError):
        raise
    except Exception as e:
        logger.error(f"Error answering analytics question: {e}")
        raise DatabaseOperationError(f"Analytics query failed: {e}")


@router.get("/analytics/queries/{query_id}")
async def get_analytics_query(
    query_id: str,
    user: CurrentUser = Depends(get_current_user),
    permitted_ids: List[str] = Depends(get_permitted_dataset_ids),
):
    """Fetch a stored query-log entry with its rendered parameterized query (AN-009).

    Same ownership as asking: a log whose dataset the caller may not query
    returns 404 (no existence oracle). Logs with no dataset (AN-010
    refusals, no-permitted-dataset denials) are visible to their owner,
    admins, and the CEO only.
    """
    try:
        record = await get_query_for_caller(user, query_id, permitted_ids)
        if record is None:
            raise NotFoundError(f"Analytics query {query_id} not found")
        return record
    except (NotFoundError, OpenNotebookError):
        raise
    except Exception as e:
        logger.error(f"Error fetching analytics query {query_id}: {e}")
        raise DatabaseOperationError(f"Failed to fetch analytics query: {e}")
