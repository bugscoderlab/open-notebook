"""Migration router (issue #7/T6) — admin-only content classification.

Frozen contract: docs/7-DEVELOPMENT/team-access/api-contracts.md (Migration).
Business logic lives in api.migration_service; enforcement is require_admin
(api.access). Mutations require the CSRF header (ADR-010).
"""

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api import migration_service
from api.access import CurrentUser, require_admin, require_csrf

router = APIRouter(prefix="/migration", tags=["migration"])


class AssignItemRequest(BaseModel):
    """Manual resolution payload from the migration screen."""

    team_id: str = Field(min_length=1)
    visibility: Literal["team", "company_shared"] = "team"


@router.get("/status", response_model=Dict[str, Any])
async def get_status(admin: CurrentUser = Depends(require_admin)) -> Dict[str, Any]:
    """Completion state + flagged-item counts for the status card."""
    return await migration_service.migration_status(admin)


@router.get("/items", response_model=Dict[str, Any])
async def get_items(admin: CurrentUser = Depends(require_admin)) -> Dict[str, Any]:
    """Teamless notebooks/sources needing manual resolution, with reasons."""
    return await migration_service.list_flagged_items(admin)


@router.post("/run", response_model=Dict[str, Any])
async def run_classification_pass(
    admin: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> Dict[str, Any]:
    """Run one idempotent classification pass; returns the run summary."""
    return await migration_service.run_classification(admin)


@router.patch("/items/{kind}/{item_id}", response_model=Dict[str, Any])
async def assign_item(
    kind: Literal["notebook", "source"],
    item_id: str,
    body: AssignItemRequest,
    admin: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> Dict[str, Any]:
    """Assign a team (and visibility) to one flagged notebook or source."""
    return await migration_service.assign_item(
        admin,
        kind,
        item_id,
        team_id=body.team_id,
        visibility=body.visibility,
    )
