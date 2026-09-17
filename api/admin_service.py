"""Admin service (issue #5/T4) — user and team management business logic.

Sits behind the admin-only users/teams routers (and POST /auth/invite):
invite-by-email with a temp password (no SMTP — the admin shares it
out-of-band), team/role assignment, disable/reactivate (which revokes all
sessions immediately), and team create/edit/archive. Every function takes
the resolved admin caller; enforcement itself lives in ``api.access``.
"""

from typing import Any, Dict, List, Optional, get_args

from api.access import CurrentUser, Role
from open_notebook.domain import user as user_domain
from open_notebook.domain.user import AppUser, Team, hash_password
from open_notebook.exceptions import InvalidInputError, NotFoundError

ROLES: tuple = get_args(Role)
USER_STATUSES = ("invited", "active", "disabled")

USER_NOT_FOUND = "User not found"
TEAM_NOT_FOUND = "Team not found"
EMAIL_TAKEN = "A user with this email already exists"
SLUG_TAKEN = "A team with this slug already exists"
MANAGER_NOT_FOUND = "Manager user not found"


def user_to_response(user: AppUser, team_name: str = "") -> Dict[str, Any]:
    """Frozen users-contract shape (never includes password_hash)."""
    return {
        "id": user.id or "",
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role,
        "status": user.status,
        "team_id": user.team_id,
        "team_name": team_name,
        "last_active_at": user.last_active_at,
    }


async def _require_team(team_id: str) -> Team:
    team = await user_domain.get_team_by_id(team_id)
    if team is None or team.id is None:
        raise NotFoundError(TEAM_NOT_FOUND)
    return team


async def _require_user(user_id: str) -> AppUser:
    user = await user_domain.get_user_by_id(user_id)
    if user is None or user.id is None:
        raise NotFoundError(USER_NOT_FOUND)
    return user


async def list_users() -> List[Dict[str, Any]]:
    """All users with their team name (frozen contract order: created ASC)."""
    users = await user_domain.list_users()
    teams = {team.id or "": team.name for team in await user_domain.list_teams()}
    return [user_to_response(u, teams.get(u.team_id, "")) for u in users]


async def invite_user(
    *,
    email: str,
    display_name: str,
    team_id: str,
    role: str,
    temp_password: str,
) -> Dict[str, Any]:
    """Create an invited user; the admin shares the temp password out-of-band."""
    team = await _require_team(team_id)
    existing = await user_domain.get_user_by_email(email)
    if existing is not None:
        raise InvalidInputError(EMAIL_TAKEN)
    user = await user_domain.create_user(
        organization_id=team.organization_id,
        email=email,
        password_hash=hash_password(temp_password),
        display_name=display_name,
        team_id=team_id,
        role=role,
        status="invited",
    )
    return user_to_response(user, team.name)


async def update_user(
    user_id: str,
    *,
    team_id: Optional[str] = None,
    role: Optional[str] = None,
    status: Optional[str] = None,
    temp_password: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply team/role/status changes; disabling revokes all sessions.

    ``temp_password`` is the no-SMTP "resend invite": it regenerates the
    sign-in password (the admin re-shares it out-of-band) and revokes every
    existing session, exactly like a password reset.
    """
    user = await _require_user(user_id)
    changes: Dict[str, Any] = {}
    team_name = ""
    if team_id is not None:
        team = await _require_team(team_id)
        team_name = team.name
        changes["team_id"] = team_id
    if role is not None:
        if role not in ROLES:
            raise InvalidInputError(f"role must be one of {', '.join(ROLES)}")
        changes["role"] = role
    if status is not None:
        if status not in USER_STATUSES:
            raise InvalidInputError(
                f"status must be one of {', '.join(USER_STATUSES)}"
            )
        changes["status"] = status
    if temp_password is not None:
        changes["password_hash"] = hash_password(temp_password)
    if changes:
        await user_domain.update_user(user_id, **changes)
        if status == "disabled" or temp_password is not None:
            await user_domain.revoke_all_sessions(user_id)
    if not team_name:
        current_team = await user_domain.get_team_by_id(user.team_id)
        team_name = current_team.name if current_team else ""
    updated = await _require_user(user_id)
    return user_to_response(updated, team_name)


def team_to_response(
    team: Team,
    *,
    member_count: int = 0,
    notebook_count: int = 0,
    manager_name: str = "",
) -> Dict[str, Any]:
    """Teams-contract shape plus the counts/manager name the admin UI needs."""
    return {
        "id": team.id or "",
        "slug": team.slug,
        "name": team.name,
        "description": team.description,
        "manager_id": team.manager_id or "",
        "manager_name": manager_name,
        "active": team.active,
        "member_count": member_count,
        "notebook_count": notebook_count,
    }


async def list_teams() -> List[Dict[str, Any]]:
    """All teams with member/notebook counts and the manager's display name."""
    teams = await user_domain.list_teams()
    member_counts = await user_domain.count_users_per_team()
    notebook_counts = await user_domain.count_notebooks_per_team()
    users = {u.id or "": u for u in await user_domain.list_users()}
    return [
        team_to_response(
            team,
            member_count=member_counts.get(team.id or "", 0),
            notebook_count=notebook_counts.get(team.id or "", 0),
            manager_name=(
                users[team.manager_id].display_name
                if team.manager_id and team.manager_id in users
                else ""
            ),
        )
        for team in teams
    ]


async def create_team(
    admin: CurrentUser, *, slug: str, name: str, description: Optional[str] = None
) -> Dict[str, Any]:
    """Create a team in the caller's organization; slugs are unique."""
    existing = await user_domain.get_team_by_slug(slug)
    if existing is not None:
        raise InvalidInputError(SLUG_TAKEN)
    team = await user_domain.create_team(
        organization_id=admin.organization_id,
        slug=slug,
        name=name,
        description=description,
    )
    return team_to_response(team)


async def update_team(
    team_id: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    manager_id: Optional[str] = None,
    active: Optional[bool] = None,
) -> Dict[str, Any]:
    """Edit/archive a team; assigning a manager validates the user exists."""
    await _require_team(team_id)
    changes: Dict[str, Any] = {}
    if name is not None:
        changes["name"] = name
    if description is not None:
        changes["description"] = description
    if manager_id is not None:
        manager = await user_domain.get_user_by_id(manager_id)
        if manager is None or manager.id is None:
            raise NotFoundError(MANAGER_NOT_FOUND)
        changes["manager_id"] = manager_id
    if active is not None:
        changes["active"] = active
    if changes:
        await user_domain.update_team(team_id, **changes)
    updated = await _require_team(team_id)
    return team_to_response(updated)
