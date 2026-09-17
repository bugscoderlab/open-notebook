"""Users router (issue #5/T4) — admin-only user management.

Frozen contract: docs/7-DEVELOPMENT/team-access/api-contracts.md (Users & teams).
Business logic lives in api.admin_service; enforcement is the require_admin
dependency (api.access) — the UI nav is role-aware but the backend check is
authoritative. Mutations require the CSRF header (ADR-010).
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from api import admin_service
from api.access import CurrentUser, Role, require_admin, require_csrf

router = APIRouter(prefix="/users", tags=["users"])


class UpdateUserRequest(BaseModel):
    """Team/role/status changes; disabling revokes all sessions.

    ``temp_password`` regenerates the sign-in password (the no-SMTP "resend
    invite" — the admin re-shares it out-of-band) and revokes all sessions.
    """

    team_id: Optional[str] = None
    role: Optional[Role] = None
    status: Optional[Literal["invited", "active", "disabled"]] = None
    temp_password: Optional[str] = Field(default=None, min_length=8, max_length=1024)


@router.get("", response_model=List[Dict[str, Any]])
async def list_users(admin: CurrentUser = Depends(require_admin)) -> List[Dict[str, Any]]:
    """All users with team names and last activity (frozen contract shape)."""
    return await admin_service.list_users()


@router.patch("/{user_id}", response_model=Dict[str, Any])
async def update_user(
    user_id: str,
    body: UpdateUserRequest,
    admin: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> Dict[str, Any]:
    """Assign team/role or change status; disabling revokes sessions."""
    changes = body.model_dump(exclude_unset=True)
    return await admin_service.update_user(user_id, **changes)
