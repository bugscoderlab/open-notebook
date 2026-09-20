"""Integration link domain model.

A binding between one messenger identity (a Telegram chat or a WhatsApp
number) and exactly one user account (see CONTEXT.md). The link itself grants
nothing: access scope always derives from the linked user, enforced by the
API's standard access seam when the gateway calls in as an internal caller
(ADR-018).

Rows lifecycle: ``pending`` rows carry the short-lived ``code`` created by the
web UI (proof of control of both sides); a successful ``claim`` stamps
``external_id`` and flips the row to ``active``. ``home_chat_session_id``
points at the per-link conversation on the home chat graph (ADR-016 pattern).
"""

from datetime import datetime
from typing import ClassVar, Literal, Optional

from open_notebook.domain.base import ObjectModel

Platform = Literal["telegram", "whatsapp"]
LinkStatus = Literal["pending", "active", "expired"]


class IntegrationLink(ObjectModel):
    table_name: ClassVar[str] = "integration_link"
    nullable_fields: ClassVar[set[str]] = {
        "external_id",
        "user_id",
        "code",
        "home_chat_session_id",
        "expires_at",
    }
    platform: Platform
    external_id: Optional[str] = None
    user_id: Optional[str] = None
    code: Optional[str] = None
    status: LinkStatus = "pending"
    home_chat_session_id: Optional[str] = None
    expires_at: Optional[datetime] = None
