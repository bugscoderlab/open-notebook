"""Home chat session domain model.

The knowledge-only Home ask chat (ADR-017) persists its sessions as
``home_chat_session`` records owned by a user via a plain ``user_id`` field.
The conversation itself (messages and per-turn suggestion metadata) lives in
LangGraph SqliteSaver checkpoints keyed by the session id — same pattern as
``ChatSession``.
"""

from typing import ClassVar, Optional

from open_notebook.domain.base import ObjectModel


class HomeChatSession(ObjectModel):
    table_name: ClassVar[str] = "home_chat_session"
    nullable_fields: ClassVar[set[str]] = {"model_override", "user_id"}
    title: Optional[str] = None
    model_override: Optional[str] = None
    user_id: Optional[str] = None
