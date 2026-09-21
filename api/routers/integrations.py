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
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from api import integrations_service as service
from api.access import CurrentUser, get_current_user, require_admin, require_csrf
from api.internal_auth import get_internal_caller
from api.routers._chat_shared import SuccessResponse
from open_notebook.domain.integration_link import Platform
from open_notebook.exceptions import (
    ConfigurationError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
)

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


class MessageRequest(BaseModel):
    platform: Platform
    external_id: str = Field(..., min_length=1, max_length=128)
    text: str = Field(..., min_length=1, max_length=4096)


class MessageResponse(BaseModel):
    reply: str
    suggestions: List[str] = Field(default_factory=list)
    conversation_reset: bool = False


class WhatsappPairingRequest(BaseModel):
    status: Literal["pairing", "connected", "disconnected", "logged_out"]
    qr: Optional[str] = Field(default=None, max_length=2048)
    identity: Optional[str] = Field(
        default=None, max_length=128, description="Own JID, reported on connect"
    )


class WhatsappPairingResponse(BaseModel):
    status: str
    qr: Optional[str] = None
    identity: Optional[str] = None
    updated_at: Optional[datetime] = None


class ClaimSelfRequest(BaseModel):
    code: str = Field(..., min_length=6, max_length=6)


class WhatsappResetResponse(BaseModel):
    nonce: Optional[str] = None


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


@router.get("/integrations/whatsapp/pairing", response_model=WhatsappPairingResponse)
async def get_whatsapp_pairing_status(user: CurrentUser = Depends(get_current_user)):
    """Latest WhatsApp pairing state for the web UI (QR + connection status).

    The gateway (Baileys) is the source of truth; it pushes every change via
    the internal endpoint below. State is in-memory and ephemeral — a fresh
    API reports ``disconnected`` until the gateway's next push.
    """
    return WhatsappPairingResponse(**service.get_whatsapp_pairing())


@router.put("/integrations/whatsapp/pairing", response_model=WhatsappPairingResponse)
async def push_whatsapp_pairing(
    request: WhatsappPairingRequest,
    _: None = Depends(get_internal_caller),
):
    """Gateway-internal: report a WhatsApp pairing change (QR rotation,
    connect, disconnect, logout) so the web UI can drive pairing."""
    started = datetime.now(timezone.utc)
    service.set_whatsapp_pairing(request.status, request.qr, request.identity)
    service.audit(
        platform="whatsapp",
        external_id=request.identity,
        user_id=None,
        endpoint="whatsapp.pairing",
        outcome=request.status,
        latency_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
    )
    return WhatsappPairingResponse(**service.get_whatsapp_pairing())


@router.post("/integrations/whatsapp/claim-self", response_model=ClaimResponse)
async def claim_own_whatsapp(
    request: ClaimSelfRequest,
    _: None = Depends(require_csrf),
    user: CurrentUser = Depends(get_current_user),
):
    """Link the WhatsApp identity the gateway is connected as to the caller.

    One-click alternative to the ``/start <code>`` chat flow for setups where
    the operator's own number runs the bot (WhatsApp does not relay
    self-chat messages to linked devices reliably): the caller proves
    possession with a fresh linking code from their own session, the gateway
    proves the connected identity — no chat round-trip needed. Reuses the
    same atomic claim path as the gateway's claim endpoint.
    """
    started = datetime.now(timezone.utc)
    outcome = "invalid"
    resolved_user_id: Optional[str] = None
    identity: Optional[str] = None
    try:
        state = service.get_whatsapp_pairing()
        identity = str(state["identity"]) if state.get("identity") else None
        if state.get("status") != "connected" or not identity:
            raise ConfigurationError(
                "WhatsApp is not connected on the gateway — pair it first "
                "(Settings → Chat integrations → Connect WhatsApp)."
            )
        linked_user, result = await service.claim_link("whatsapp", identity, request.code)
        outcome = result
        resolved_user_id = linked_user.id
        return ClaimResponse(
            success=True,
            message="Already linked" if result == "already-linked" else "Linked",
            email=linked_user.email,
        )
    finally:
        service.audit(
            platform="whatsapp",
            external_id=identity,
            user_id=resolved_user_id or user.id,
            endpoint="link.claim-self",
            outcome=outcome,
            latency_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
        )


# ---------------------------------------------------------------------------
# WhatsApp one-click re-pair (admin → gateway poll → fresh QR)
# ---------------------------------------------------------------------------


@router.post("/integrations/whatsapp/reset", response_model=WhatsappResetResponse)
async def request_whatsapp_repair(
    _: None = Depends(require_csrf),
    user: CurrentUser = Depends(require_admin),
):
    """Bump the re-pair nonce. The gateway polls for it and, on change, wipes
    its session and reconnects — the web UI then shows a fresh pairing QR.

    Admin-gated: re-pairing logs the shared bot out for every user.
    """
    started = datetime.now(timezone.utc)
    nonce = service.request_whatsapp_repair()
    service.audit(
        platform="whatsapp",
        external_id=None,
        user_id=user.id,
        endpoint="whatsapp.reset",
        outcome="requested",
        latency_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
    )
    return WhatsappResetResponse(nonce=nonce)


@router.get("/integrations/whatsapp/reset", response_model=WhatsappResetResponse)
async def get_whatsapp_repair_nonce(_: None = Depends(get_internal_caller)):
    """Gateway-internal: poll for the admin's re-pair request."""
    return WhatsappResetResponse(nonce=service.get_whatsapp_reset_nonce())


@router.post("/integrations/message", response_model=MessageResponse)
async def route_inbound_message(
    request: MessageRequest,
    _: None = Depends(get_internal_caller),
):
    """Route one inbound chat message for a linked identity.

    Commands (/new, /search, /unlink, /help) are handled directly; anything
    else is a conversational ask on the link's home chat session. Error
    details are chat-friendly — the gateway renders them as message text.
    """
    started = datetime.now(timezone.utc)
    outcome = "ok"
    resolved_user_id: Optional[str] = None
    try:
        if not service.check_rate_limit(request.platform, request.external_id):
            outcome = "rate-limited"
            raise RateLimitError(
                "You're sending messages too quickly — wait a few seconds."
            )

        try:
            link, user = await service.resolve_linked_user(
                request.platform, request.external_id
            )
        except NotFoundError:
            outcome = "unlinked"
            raise
        except ForbiddenError:
            outcome = "disabled"
            raise
        resolved_user_id = user.id
        current_user = service.current_user_for(user)
        text = request.text.strip()

        suggestions: List[str] = []
        conversation_reset = False

        if text.startswith("/"):
            command, _sep, argument = text[1:].partition(" ")
            command = command.lower()
            if command == "new":
                reply = await service.reset_conversation(link)
                conversation_reset = True
            elif command == "search":
                query = argument.strip()
                if not query:
                    reply = "Usage: /search <query>"
                else:
                    reply = await service.run_search(current_user, query)
            elif command == "unlink":
                await link.delete()
                reply = (
                    "Unlinked. Reconnect anytime from Settings → Chat "
                    "integrations."
                )
                conversation_reset = True
            elif command == "help":
                reply = service.COMMAND_HELP
            else:
                reply = service.COMMAND_HELP
        else:
            outcome = "ask"
            reply, suggestions = await service.run_home_chat_ask(
                link, current_user, text
            )

        return MessageResponse(
            reply=reply,
            suggestions=suggestions,
            conversation_reset=conversation_reset,
        )
    except (RateLimitError, NotFoundError, ForbiddenError):
        raise
    except Exception:
        outcome = "error"
        raise
    finally:
        service.audit(
            platform=request.platform,
            external_id=request.external_id,
            user_id=resolved_user_id,
            endpoint="message",
            outcome=outcome,
            latency_ms=(datetime.now(timezone.utc) - started).total_seconds() * 1000,
        )
