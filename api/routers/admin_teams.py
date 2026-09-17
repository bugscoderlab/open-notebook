"""Teams router (issue #5/T4) — admin-only team management.

Frozen contract: docs/7-DEVELOPMENT/team-access/api-contracts.md (Users & teams).
Business logic lives in api.admin_service; enforcement is the require_admin
dependency (api.access). Team slugs are immutable after creation (they are
the classification tokens T6 matches against). Mutations require the CSRF
header (ADR-010).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api import admin_service
from api.access import CurrentUser, require_admin, require_csrf

router = APIRouter(prefix="/teams", tags=["teams"])


class CreateTeamRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = None


class UpdateTeamRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    description: Optional[str] = None
    manager_id: Optional[str] = None
    active: Optional[bool] = None


@router.get("", response_model=List[Dict[str, Any]])
async def list_teams(admin: CurrentUser = Depends(require_admin)) -> List[Dict[str, Any]]:
    """All teams with member/notebook counts and the manager's name."""
    return await admin_service.list_teams()


@router.post("", response_model=Dict[str, Any], status_code=201)
async def create_team(
    body: CreateTeamRequest,
    admin: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> Dict[str, Any]:
    """Create a team in the caller's organization (slug must be unique)."""
    return await admin_service.create_team(
        admin, slug=body.slug, name=body.name, description=body.description
    )


@router.patch("/{team_id}", response_model=Dict[str, Any])
async def update_team(
    team_id: str,
    body: UpdateTeamRequest,
    admin: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> Dict[str, Any]:
    """Edit name/description, assign a manager, or archive/reactivate."""
    changes = body.model_dump(exclude_unset=True)
    return await admin_service.update_team(team_id, **changes)
