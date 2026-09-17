"""Auth router (issue #4/T3, ADR-010) — cookie-session endpoints.

Frozen contract: docs/7-DEVELOPMENT/team-access/api-contracts.md (Auth & identity).
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from api import admin_service, auth_service
from api.access import CurrentUser, Role, require_admin, require_csrf
from open_notebook.domain.user import count_users, get_team_by_id
from open_notebook.exceptions import AuthenticationError, ForbiddenError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """Credential pair accepted by POST /auth/login."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class AuthTeamResponse(BaseModel):
    """Team block of the AuthUser contract."""

    id: str
    slug: str
    name: str


class AuthUserResponse(BaseModel):
    """GET /auth/me contract shape (role restricted to the approved enum).

    Response validation enforces the frozen contract: a malformed role in
    the database surfaces as a 500 here instead of leaking an invalid enum
    to clients.
    """

    id: Optional[str]
    email: str
    display_name: str
    role: Role
    team: Optional[AuthTeamResponse]


def _cookie(name: str, value: str, max_age: int, *, http_only: bool) -> str:
    """Build one Set-Cookie value (session cookies are Path=/, SameSite=Lax)."""
    parts = [f"{name}={value}", "Path=/", f"Max-Age={max_age}", "SameSite=Lax"]
    if http_only:
        parts.append("HttpOnly")
    return "; ".join(parts)


@router.post("/login", status_code=204)
async def login(request: Request, response: Response, body: LoginRequest) -> None:
    """Verify credentials (rate-limited, generic 401) and open a session.

    Sets the HttpOnly session cookie plus the readable CSRF cookie; failures
    are a generic 401 (invalid credentials) or 429 (rate limited).
    """
    user = await auth_service.attempt_login(
        auth_service.client_ip(request), body.email, body.password
    )
    token, csrf_token, _ = await auth_service.issue_session(user.id or "")
    max_age = auth_service.SESSION_MAX_AGE_SECONDS
    # Session cookie: HttpOnly. CSRF cookie: deliberately readable — the
    # frontend echoes it in the x-csrf-token header on mutations.
    response.headers["set-cookie"] = _cookie(
        auth_service.SESSION_COOKIE, token, max_age, http_only=True
    )
    response.headers.append(
        "set-cookie", _cookie(auth_service.CSRF_COOKIE, csrf_token, max_age, http_only=False)
    )


@router.post("/logout", status_code=204)
async def logout(request: Request, response: Response) -> None:
    """Revoke the current session (requires the CSRF header) and clear cookies."""
    if not auth_service.csrf_matches(request):
        raise ForbiddenError("CSRF token missing or invalid")
    await auth_service.revoke_session_token(request.cookies.get(auth_service.SESSION_COOKIE))
    response.headers["set-cookie"] = _cookie(
        auth_service.SESSION_COOKIE, "", 0, http_only=True
    )
    response.headers.append(
        "set-cookie", _cookie(auth_service.CSRF_COOKIE, "", 0, http_only=False)
    )


@router.get("/me", response_model=AuthUserResponse)
async def me(request: Request) -> AuthUserResponse:
    """Return the frozen AuthUser contract shape for the session; 401 otherwise."""
    resolved = await auth_service.resolve_session(
        request.cookies.get(auth_service.SESSION_COOKIE)
    )
    if resolved is None:
        raise AuthenticationError("Authentication required")
    user, _ = resolved
    team = await get_team_by_id(user.team_id) if user.team_id else None
    return AuthUserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,  # validated against the contract enum by the response model
        team=(
            AuthTeamResponse(id=team.id or "", slug=team.slug, name=team.name)
            if team
            else None
        ),
    )


class InviteRequest(BaseModel):
    """Admin invite payload (T4): no email is sent — the admin hands the temp
    password to the new user out-of-band."""

    email: str = Field(min_length=3, max_length=320)
    display_name: str = Field(min_length=1, max_length=200)
    team_id: str = Field(min_length=1)
    role: Role = "member"
    temp_password: str = Field(min_length=8, max_length=1024)


@router.post("/invite", response_model=Dict[str, Any], status_code=201)
async def invite(
    body: InviteRequest,
    admin: CurrentUser = Depends(require_admin),
    _: None = Depends(require_csrf),
) -> Dict[str, Any]:
    """Create an invited user (admin only, CSRF-checked; frozen contract)."""
    return await admin_service.invite_user(
        email=body.email,
        display_name=body.display_name,
        team_id=body.team_id,
        role=body.role,
        temp_password=body.temp_password,
    )


@router.get("/status")
async def get_auth_status() -> dict:
    """Whether per-user auth is active (any app_user exists).

    Kept for legacy clients; the shared-password ``OPEN_NOTEBOOK_PASSWORD``
    mode is gone (ADR-010). Before bootstrap seeds the first admin there are
    no users, so the app stays unlocked exactly as in the old dev default.
    """
    enabled = await count_users() > 0
    return {
        "auth_enabled": enabled,
        "message": "Authentication is required"
        if enabled
        else "No users yet — run `python -m open_notebook.admin bootstrap`",
    }
