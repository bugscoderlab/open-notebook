"""Integration-link business logic (routes → services → models).

The linking state machine lives here, not in the router: pending-code
allocation, the claim lifecycle (validate → expiry → identity dedupe →
activation), ownership-guarded reads/writes, identity masking for display,
and the audit line ADR-018 requires.

Race safety: activation is a single conditional UPDATE — status transition,
expiry, and the one-account-per-identity rule are all enforced atomically by
SurrealDB, so concurrent claims of the same code (or of the same identity
with two codes) have exactly one winner.
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger

from api.access import CurrentUser, effective_notebook_scope
from open_notebook.ai.models import DefaultModels
from open_notebook.database.repository import repo_query
from open_notebook.domain.home_chat import HomeChatSession
from open_notebook.domain.integration_link import IntegrationLink, Platform
from open_notebook.domain.notebook import Notebook, text_search
from open_notebook.domain.user import AppUser, get_user_by_id
from open_notebook.exceptions import (
    ForbiddenError,
    InvalidInputError,
    NotFoundError,
)
from open_notebook.graphs.home_chat import get_home_chat_graph
from open_notebook.utils.error_classifier import classify_error

CODE_TTL = timedelta(minutes=10)
CODE_SPACE = 900_000  # 6-digit codes 100000–999999

# Activation is atomic: the status transition, the expiry check, and the
# one-account-per-identity rule all live in this one statement, so concurrent
# claims resolve to a single winner at the database.
_ACTIVATE_SQL = """
UPDATE integration_link
SET status = 'active', external_id = $external_id
WHERE platform = $platform
  AND code = $code
  AND status = 'pending'
  AND expires_at > time::now()
  AND count(
    SELECT VALUE id FROM integration_link
    WHERE platform = $platform AND external_id = $external_id AND status = 'active'
  ) = 0
"""

_PENDING_BY_CODE_SQL = """
SELECT * FROM integration_link
WHERE platform = $platform AND code = $code AND status = 'pending'
"""

_ACTIVE_BY_EXTERNAL_SQL = """
SELECT * FROM integration_link
WHERE platform = $platform AND external_id = $external_id AND status = 'active'
"""

_ACTIVE_BY_USER_SQL = """
SELECT * FROM integration_link
WHERE user_id = $user_id AND status = 'active'
"""


def audit(
    *,
    platform: Platform,
    external_id: Optional[str],
    user_id: Optional[str],
    endpoint: str,
    outcome: str,
    latency_ms: float,
) -> None:
    """Structured audit line per ADR-018: external ids hashed, never logged
    in full."""
    external_ref = (
        f"sha256:{hashlib.sha256(external_id.encode()).hexdigest()[:12]}"
        if external_id
        else "-"
    )
    logger.info(
        "integrations endpoint={} platform={} external={} user={} outcome={} latency_ms={:.1f}",
        endpoint,
        platform,
        external_ref,
        user_id or "-",
        outcome,
        latency_ms,
    )


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Stored timestamps are naive server-local strings (ObjectModel.save);
    normalize for comparisons and serialization. Server deployments run UTC,
    so attaching UTC to a naive value is the honest reading."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def mask_identity(platform: Platform, external_id: Optional[str]) -> str:
    """Display-safe identity: public handles (@username) shown as-is, numeric
    ids (phone numbers, numeric chat ids) masked."""
    if not external_id:
        return ""
    if platform == "telegram" and (
        external_id.startswith("@") or not external_id.isdigit()
    ):
        return external_id
    if len(external_id) <= 4:
        return "••••"
    return f"{external_id[:2]}{'•' * (len(external_id) - 4)}{external_id[-2:]}"


async def _generate_unique_code(platform: Platform) -> str:
    for _ in range(5):
        code = str(secrets.randbelow(CODE_SPACE) + 100_000)
        rows = await repo_query(_PENDING_BY_CODE_SQL, {"platform": platform, "code": code})
        if not rows:
            return code
    raise InvalidInputError("Could not allocate a linking code, try again")


async def create_pending_link(user: CurrentUser, platform: Platform) -> IntegrationLink:
    """Create a fresh pending linking code (10-minute expiry)."""
    link = IntegrationLink(
        platform=platform,
        user_id=user.id,
        code=await _generate_unique_code(platform),
        status="pending",
        expires_at=datetime.now(timezone.utc) + CODE_TTL,
    )
    await link.save()
    return link


async def find_pending_by_code(
    platform: Platform, code: str
) -> Optional[IntegrationLink]:
    rows = await repo_query(
        _PENDING_BY_CODE_SQL, {"platform": platform, "code": code}
    )
    if not rows:
        return None
    return IntegrationLink(**rows[0])


async def find_active_by_external_id(
    platform: Platform, external_id: str
) -> Optional[IntegrationLink]:
    rows = await repo_query(
        _ACTIVE_BY_EXTERNAL_SQL,
        {"platform": platform, "external_id": external_id},
    )
    if not rows:
        return None
    return IntegrationLink(**rows[0])


async def claim_link(
    platform: Platform, external_id: str, code: str
) -> Tuple[AppUser, str]:
    """Bind a messenger identity to the account that created the code.

    Returns (user, outcome) where outcome is 'linked' or 'already-linked'.
    Raises InvalidInputError for invalid/expired codes and foreign identities.

    Ordering matters: the code is validated (and expired codes consumed)
    before the idempotency shortcut, and activation is the atomic conditional
    UPDATE above — a code can activate exactly once and an identity can bind
    to exactly one account, regardless of concurrency.
    """
    pending = await find_pending_by_code(platform, code)
    if pending is None:
        raise InvalidInputError("Invalid or expired linking code")

    expires_at = as_utc(pending.expires_at)
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        pending.status = "expired"
        await pending.save()
        raise InvalidInputError("Invalid or expired linking code")

    existing = await find_active_by_external_id(platform, external_id)
    if existing is not None:
        if existing.user_id == pending.user_id:
            user = await get_user_by_id(pending.user_id or "")
            if user is None:
                raise NotFoundError("Linked user not found")
            return user, "already-linked"
        raise InvalidInputError(
            "This chat identity is already linked to a different account"
        )

    rows = await repo_query(
        _ACTIVATE_SQL,
        {"platform": platform, "code": code, "external_id": external_id},
    )
    if rows:
        user = await get_user_by_id(pending.user_id or "")
        if user is None:
            raise NotFoundError("Linked user not found")
        return user, "linked"

    # The conditional update lost the race: either the code was activated by
    # a concurrent claim, or the identity bound to another account between
    # our checks. Re-read the identity to say something useful.
    existing = await find_active_by_external_id(platform, external_id)
    if existing is not None and existing.user_id == pending.user_id:
        user = await get_user_by_id(pending.user_id or "")
        if user is None:
            raise NotFoundError("Linked user not found")
        return user, "already-linked"
    raise InvalidInputError("Invalid or expired linking code")


async def list_active_links(user: CurrentUser) -> List[IntegrationLink]:
    rows = await repo_query(_ACTIVE_BY_USER_SQL, {"user_id": user.id})
    links = [IntegrationLink(**row) for row in rows or []]
    links.sort(
        key=lambda link: as_utc(link.created) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return links


async def get_owned_link(link_id: str, user: CurrentUser) -> IntegrationLink:
    """Load one of the caller's links or 404 (no existence oracle for
    foreign/missing ids — same convention as the content guards)."""
    from api.routers._chat_shared import normalize_record_id

    link = await IntegrationLink.get(
        normalize_record_id("integration_link", link_id)
    )
    if link is None or link.user_id != user.id:
        raise NotFoundError("Integration link not found")
    return link


def code_ttl_seconds() -> int:
    return int(CODE_TTL.total_seconds())


# ---------------------------------------------------------------------------
# Message spine (T2): one inbound message from a linked identity → one reply
# ---------------------------------------------------------------------------

MESSAGE_RATE_LIMIT_SECONDS = 3.0
COMMAND_HELP = (
    "I answer questions about your knowledge base. Special commands:\n"
    "/new — start a fresh conversation\n"
    "/search <query> — search without asking the AI\n"
    "/unlink — disconnect this chat from your account\n"
    "/help — show this message\n"
    "Anything else is a question."
)

_rate_limit_state: Dict[Tuple[str, str], float] = {}
_RATE_LIMIT_STATE_CAP = 5_000  # crude bound; entries are tiny and self-limiting


def reset_rate_limit_state() -> None:
    """Test hook."""
    _rate_limit_state.clear()


def check_rate_limit(platform: Platform, external_id: str) -> bool:
    """One message per MESSAGE_RATE_LIMIT_SECONDS per identity. True = allowed."""
    if len(_rate_limit_state) >= _RATE_LIMIT_STATE_CAP:
        _rate_limit_state.clear()
    key = (platform, external_id)
    now = time.monotonic()
    last = _rate_limit_state.get(key, 0.0)
    if now - last < MESSAGE_RATE_LIMIT_SECONDS:
        return False
    _rate_limit_state[key] = now
    return True


async def resolve_linked_user(
    platform: Platform, external_id: str
) -> Tuple[IntegrationLink, AppUser]:
    """Resolve an active integration link to its user. Unlinked identities
    404 (the gateway replies with how-to-link); a disabled user is 403 with
    an authorization message — links are kept so re-enabling restores
    service (ADR-018 failure modes)."""
    rows = await repo_query(
        "SELECT * FROM integration_link WHERE platform = $platform AND external_id = $external_id AND status = 'active'",
        {"platform": platform, "external_id": external_id},
    )
    if not rows:
        raise NotFoundError("No integration link found for this chat identity")
    link = IntegrationLink(**rows[0])
    user = await get_user_by_id(link.user_id or "")
    if user is None:
        raise NotFoundError("No integration link found for this chat identity")
    if user.status != "active":
        raise ForbiddenError("This account is no longer authorized")
    return link, user


def current_user_for(user: AppUser) -> CurrentUser:
    """Map an AppUser onto the CurrentUser the access seam consumes (same
    shape get_current_user builds for a cookie session)."""
    from api.access import role_or_default

    return CurrentUser(
        id=user.id or "",
        email=user.email,
        display_name=user.display_name or "",
        organization_id=user.organization_id or "",
        team_id=user.team_id or "",
        role=role_or_default(user.role),
    )


async def _resolve_default_model() -> Optional[str]:
    defaults = await DefaultModels.get_instance()
    return defaults.default_chat_model


async def _new_conversation(link: IntegrationLink) -> str:
    session = HomeChatSession(
        title=f"{link.platform} chat", user_id=link.user_id or ""
    )
    await session.save()
    link.home_chat_session_id = session.id
    await link.save()
    return session.id or ""


async def reset_conversation(link: IntegrationLink) -> str:
    """/new: point the link at a fresh home chat session."""
    await _new_conversation(link)
    return "New conversation started."


async def _ensure_session(link: IntegrationLink) -> str:
    if link.home_chat_session_id:
        return link.home_chat_session_id
    return await _new_conversation(link)


async def run_home_chat_ask(
    link: IntegrationLink, user: CurrentUser, question: str
) -> Tuple[str, List[str]]:
    """Conversational ask on the link's home chat session (memory server-side,
    same graph as the web UI). Returns (final_answer, suggestions).

    Mid-pipeline errors degrade to a typed, chat-friendly message — the reply
    is honest, the conversation checkpoint is left alone (per #41: a failed
    turn must not poison the session).
    """
    strategy_model = answer_model = final_answer_model = await _resolve_default_model()
    if not strategy_model:
        raise InvalidInputError(
            "This Open Notebook instance has no default chat model yet — "
            "ask an admin to set one in Models settings."
        )

    notebook_ids = await effective_notebook_scope(user, [])
    session_id = await _ensure_session(link)

    try:
        graph = await get_home_chat_graph()
        current_state = await graph.aget_state(
            config=RunnableConfig(configurable={"thread_id": session_id})
        )
        messages = list(
            (current_state.values if current_state else {}).get("messages", [])
        )
        messages.append(HumanMessage(content=question))

        final_answer: Optional[str] = None
        suggestions: List[str] = []
        input_state = {
            "messages": messages,
            "question": question,
            "notebook_ids": notebook_ids,
            "final_answer": None,
            "suggestions": None,
        }
        async for chunk in graph.astream(  # type: ignore[call-overload]
            input=input_state,
            config=RunnableConfig(
                configurable={
                    "thread_id": session_id,
                    "strategy_model": strategy_model,
                    "answer_model": answer_model,
                    "final_answer_model": final_answer_model,
                }
            ),
            stream_mode="updates",
        ):
            if "knowledge_final" in chunk:
                final_answer = chunk["knowledge_final"].get("final_answer")
            elif "suggest" in chunk:
                suggestions = chunk["suggest"].get("suggestions") or []

        if not final_answer:
            return (
                "I couldn't come up with an answer for that one — try rephrasing.",
                [],
            )
        return final_answer, suggestions[:3]
    except (InvalidInputError, NotFoundError, ForbiddenError):
        raise
    except Exception as e:  # noqa: BLE001 - classified for the chat surface
        _, message = classify_error(e)
        logger.error(f"Home chat ask via integrations failed: {e}")
        return message, []


async def run_search(user: CurrentUser, query: str) -> str:
    """/search: plain text search over the user's permitted scope only.
    Empty scope answers honestly — the search functions treat an empty list
    as unscoped, so it must never reach them (access.py contract)."""
    notebook_ids = await effective_notebook_scope(user, [])
    if not notebook_ids:
        return (
            "I don't have access to any notebooks that could answer this "
            "question."
        )
    results = await text_search(
        keyword=query,
        results=5,
        source=True,
        note=True,
        notebook_ids=notebook_ids,
    )
    if not results:
        return f'No results for "{query}".'

    notebook_names: Dict[str, str] = {}
    lines = []
    for index, row in enumerate(results[:5], start=1):
        record_id = str(row.get("id", ""))
        kind = "note" if record_id.startswith("note:") else "source"
        parent_id = str(row.get("parent_id", ""))
        if parent_id and parent_id not in notebook_names:
            try:
                notebook = await Notebook.get(parent_id)
                notebook_names[parent_id] = (
                    notebook.name if notebook else "notebook"
                )
            except Exception:  # noqa: BLE001 - display fallback only
                notebook_names[parent_id] = "notebook"
        title = row.get("title") or "(untitled)"
        lines.append(
            f"{index}. {title} — {notebook_names.get(parent_id, 'notebook')} ({kind})"
        )
    return "\n".join(lines)
