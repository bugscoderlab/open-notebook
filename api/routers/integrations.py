"""Messenger integrations router (T1: linking spine).

Thin routes only — the linking state machine lives in
``api.integrations_service`` (routes → services → models). Two audiences,
two auth schemes (ADR-018):

- **Web UI** (cookie session, ADR-010): create a pending linking code, list
  the caller's own integration links, delete one. Mutations are CSRF-checked.
- **Gateway** (internal token): claim a code to bind a messenger identity,
  and a status endpoint to verify the token at startup.

The gateway never sends a user identity — ``claim`` matches a code and the
API resolves everything else. Message routing joins this router in the
message spine (T2).
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from api import integrations_service as service
from api.access import CurrentUser, get_current_user, require_csrf
from api.internal_auth import get_internal_caller
from api.routers._chat_shared import SuccessResponse
from open_notebook.domain.integration_link import Platform

router = APIRouter()


class CreateLinkRequest(BaseModel):
    platform: Platform


class LinkCodeResponse(BaseModel):
    platform: Platform
    code: str = Field(..., description="6-digit linking code")
    expires_at: datetime


class IntegrationLinkResponse(BaseModel):
    id: str
    platform: Platform
    identity: str = Field(
        ..., description="Display-safe identity (handles shown, numeric ids masked)"
    )
    linked_at: datetime


class ClaimRequest(BaseModel):
    platform: Platform
    external_id: str = Field(..., min_length=1, max_length=128)
    code: str = Field(..., min_length=6, max_length=6)


class ClaimResponse(BaseModel):
    success: bool
    message: str
    email: str = Field(..., description="Linked user's email, for the chat confirmation")


class StatusResponse(BaseModel):
    ok: bool
    service: str


# ---------------------------------------------------------------------------
# Web UI (cookie session + CSRF)
# ---------------------------------------------------------------------------


@router.post("/integrations/link", response_model=LinkCodeResponse)
async def create_linking_code(
    request: CreateLinkRequest,
    _: None = Depends(require_csrf),
    user: CurrentUser = Depends(get_current_user),
):
    """Create a pending linking code for a platform (10-minute expiry)."""
    started = datetime.now(timezone.utc)
    link = await service.create_pending_link(user, request.platform)
    service.audit(
        platform=request.platform,
        external_id=None,
        user_id=user.id,
        endpoint="link.create",
        outcome="pending",
        latency_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
    )
    return LinkCodeResponse(
        platform=request.platform,
        code=link.code or "",
        expires_at=link.expires_at,  # type: ignore[arg-type]
    )


@router.get("/integrations/links", response_model=List[IntegrationLinkResponse])
async def list_integration_links(user: CurrentUser = Depends(get_current_user)):
    """List the caller's active integration links (masked identities)."""
    return [
        IntegrationLinkResponse(
            id=link.id or "",
            platform=link.platform,
            identity=service.mask_identity(link.platform, link.external_id),
            linked_at=service.as_utc(link.created),  # type: ignore[arg-type]
        )
        for link in await service.list_active_links(user)
    ]


@router.delete("/integrations/links/{link_id}", response_model=SuccessResponse)
async def delete_integration_link(
    link_id: str = Path(..., description="Integration link ID"),
    _: None = Depends(require_csrf),
    user: CurrentUser = Depends(get_current_user),
):
    """Unlink one of the caller's own integration links."""
    started = datetime.now(timezone.utc)
    link = await service.get_owned_link(link_id, user)
    platform, external_id = link.platform, link.external_id
    await link.delete()
    service.audit(
        platform=platform,
        external_id=external_id,
        user_id=user.id,
        endpoint="link.delete",
        outcome="unlinked",
        latency_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
    )
    return SuccessResponse(success=True, message="Integration link removed")


# ---------------------------------------------------------------------------
# Gateway (internal token only)
# ---------------------------------------------------------------------------


@router.post("/integrations/claim", response_model=ClaimResponse)
async def claim_linking_code(
    request: ClaimRequest,
    _: None = Depends(get_internal_caller),
):
    """Bind a messenger identity to the account that created the code.

    Idempotent for an already-linked same-user identity; rejected when the
    identity belongs to a different account (one external id, one account —
    enforced atomically in the service layer).
    """
    started = datetime.now(timezone.utc)
    outcome = "invalid"
    resolved_user_id: Optional[str] = None
    try:
        user, result = await service.claim_link(
            request.platform, request.external_id, request.code
        )
        outcome = result
        resolved_user_id = user.id
        return ClaimResponse(
            success=True,
            message="Already linked" if result == "already-linked" else "Linked",
            email=user.email,
        )
    finally:
        service.audit(
            platform=request.platform,
            external_id=request.external_id,
            user_id=resolved_user_id,
            endpoint="link.claim",
            outcome=outcome,
            latency_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
        )


@router.get("/integrations/status", response_model=StatusResponse)
async def integrations_status(_: None = Depends(get_internal_caller)):
    """Token-verification ping for gateway startup (fail-closed by design:
    a bad token never reaches this handler)."""
    return StatusResponse(ok=True, service="open-notebook-integrations")
