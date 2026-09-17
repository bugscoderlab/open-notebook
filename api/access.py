"""Central access seam for team-access (TDD §7).

``get_current_user`` resolves the caller from the session cookie (ADR-010):
the opaque token's SHA-256 hash is looked up in ``user_session`` and the
user must be active. The T9 dev bypass (``ANALYTICS_AUTH_BYPASS``) is gone —
analytics endpoints authenticate exactly like every other router.

T5 adds the team-enforcement layer consumed by every content router:

- ``can_read_team`` / ``can_write_team`` — the pure policy matrix
  (organization check first, then role/team/visibility). Members write
  their own team (CONTEXT.md deviation from TDD §7 recorded in the
  decision log); unclassified content (no team) is admin-only until the
  T6 classification pass.
- ``permitted_notebook_ids`` — the SQL-side scope used by search/ask:
  the server computes the permitted set and intersects it with any
  client-supplied scope. An empty client scope means "all permitted",
  never the whole knowledge base.
- ``require_*`` dependencies — per-object guards for notebooks, sources,
  notes and chat sessions. Read denials on single objects raise
  ``NotFoundError`` (no existence oracle); write denials raise
  ``ForbiddenError``.
"""

from typing import Any, Dict, List, Literal, Optional, cast, get_args

from fastapi import Depends, Request
from pydantic import BaseModel

from api import auth_service
from open_notebook.exceptions import (
    AuthenticationError,
    ForbiddenError,
    NotFoundError,
)

Role = Literal["member", "team_manager", "ceo", "admin"]
_ROLE_VALUES: tuple[str, ...] = get_args(Role)


class CurrentUser(BaseModel):
    """Authenticated caller shape consumed by services (TDD §6.2)."""

    id: str
    email: str
    display_name: str = ""
    organization_id: str = ""
    team_id: str = ""
    role: Role = "member"


async def get_current_user(request: Request) -> CurrentUser:
    """Resolve the caller from the session cookie."""
    resolved = await auth_service.resolve_session(
        request.cookies.get(auth_service.SESSION_COOKIE)
    )
    if resolved is None:
        raise AuthenticationError("Authentication required")
    user, _ = resolved
    return CurrentUser(
        id=user.id or "",
        email=user.email,
        display_name=user.display_name,
        organization_id=user.organization_id,
        team_id=user.team_id,
        role=cast(Role, user.role if user.role in _ROLE_VALUES else "member"),
    )


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Admin-only dependency (T4): users/teams management, invites.

    The backend check is authoritative — the role-aware nav only hides the
    entries client-side (frozen contract: non-admin → 403).
    """
    if user.role != "admin":
        raise ForbiddenError("Admin access required")
    return user


def require_csrf(request: Request) -> None:
    """Mutation guard (ADR-010): the x-csrf-token header must match the CSRF
    cookie. Shared by the admin routers and POST /auth/invite (T4)."""
    if not auth_service.csrf_matches(request):
        raise ForbiddenError("CSRF token missing or invalid")


# ---------------------------------------------------------------------------
# Team-access policy (T5, TDD §7)
# ---------------------------------------------------------------------------


def _same_organization(user: CurrentUser, organization_id: Optional[str]) -> bool:
    """Org equality guard — evaluated before role/team/visibility (TDD §7).

    Fail closed: a resource that belongs to an organization is never visible
    to a caller whose organization cannot be established. A legacy resource
    with no organization (pre-migration rows) has nothing to check against.
    """
    if not organization_id:
        return True
    return bool(user.organization_id) and organization_id == user.organization_id


def can_read_team(
    user: CurrentUser,
    resource: Any = None,
    *,
    team_id: str = "",
    visibility: str = "team",
    organization_id: Optional[str] = None,
) -> bool:
    """May ``user`` read content owned by ``team_id`` with ``visibility``?

    Also accepts a domain object (Notebook/Source-shaped) positionally:
    ``can_read_team(user, source)``.
    """
    if resource is not None:
        team_id = getattr(resource, "team_id", "") or ""
        visibility = getattr(resource, "visibility", "") or "team"
        organization_id = getattr(resource, "organization_id", None)
    if not _same_organization(user, organization_id):
        return False
    if user.role == "admin":
        return True
    if not team_id:
        # Unclassified content is admin-only until the T6 classification pass.
        return False
    if user.role == "ceo":
        return True
    if visibility == "company_shared":
        return True
    return user.team_id == team_id


def can_write_team(
    user: CurrentUser,
    resource: Any = None,
    *,
    team_id: str = "",
    organization_id: Optional[str] = None,
) -> bool:
    """May ``user`` create/edit content owned by ``team_id``?

    Members and team_managers write their own team; the CEO writes only the
    Executive team (their own team), so a plain team-equality check captures
    the CEO rule. Also accepts a domain object positionally.
    """
    if resource is not None:
        team_id = getattr(resource, "team_id", "") or ""
        organization_id = getattr(resource, "organization_id", None)
    if not _same_organization(user, organization_id):
        return False
    if user.role == "admin":
        return True
    if not team_id:
        return False
    return user.team_id == team_id


async def _permitted_ids(user: CurrentUser, table: str) -> List[str]:
    """Row ids in ``table`` (notebook/source/episode) the caller may read.

    Same matrix for every team-scoped table (migration 26/28 fields):
    admin sees in-org + unclassified, ceo sees every classified row,
    member/team_manager see own team + company_shared.
    """
    from open_notebook.database.repository import ensure_record_id, repo_query

    org = ensure_record_id(user.organization_id) if user.organization_id else None
    if user.role == "admin":
        rows = await repo_query(
            f"SELECT id FROM {table}"
            " WHERE organization IS NONE OR organization = $org",
            {"org": org},
        )
    elif user.role == "ceo":
        rows = await repo_query(
            f"SELECT id FROM {table}"
            " WHERE team IS NOT NONE AND (organization IS NONE OR organization = $org)",
            {"org": org},
        )
    else:
        # T5: a member/team_manager with no team of their own may read nothing
        # (an empty $team would otherwise match unclassified rows via
        # `team = NONE` — the unclassified set is admin-only).
        if not user.team_id:
            return []
        team = ensure_record_id(user.team_id)
        rows = await repo_query(
            f"SELECT id FROM {table}"
            " WHERE (organization IS NONE OR organization = $org)"
            " AND team IS NOT NONE"
            " AND (team = $team OR visibility = 'company_shared')",
            {"org": org, "team": team},
        )
    return [str(row["id"]) for row in rows]


async def permitted_notebook_ids(user: CurrentUser) -> List[str]:
    """Notebook ids the caller may read — the SQL-side scope for search/ask.

    - member/team_manager: own team + company-shared, in-organization.
    - ceo: every classified (team-owning) notebook.
    - admin: everything, including unclassified content awaiting T6.
    """
    return await _permitted_ids(user, "notebook")


async def permitted_source_ids(user: CurrentUser) -> List[str]:
    """Source ids the caller may read.

    Sources carry their own ownership fields (stamped from the target
    notebook at upload, T5); unclassified sources are admin-only until T6.
    """
    return await _permitted_ids(user, "source")


async def permitted_episode_ids(user: CurrentUser) -> List[str]:
    """Podcast episode ids the caller may read (migration 28 fields)."""
    return await _permitted_ids(user, "episode")


async def effective_notebook_scope(
    user: CurrentUser, requested_ids: List[str]
) -> List[str]:
    """Permitted ∩ requested scope for search/ask (TDD §9).

    Empty requested scope means "all permitted", never the whole knowledge
    base. Requested ids outside the permitted set are silently dropped (a
    broadening attempt — the caller must not learn which foreign notebooks
    exist), but malformed ids are still rejected and surviving ids are
    existence-checked. Callers must short-circuit to "no results" when this
    returns an empty list: the SurrealQL search functions treat an empty
    array as unscoped.
    """
    from open_notebook.domain.notebook import resolve_notebook_scope
    from open_notebook.exceptions import InvalidInputError

    # Format check first (mirrors resolve_notebook_scope): junk ids are a
    # client bug and must 400, not be silently dropped with the broadening
    # attempts.
    malformed = [
        nb_id
        for nb_id in requested_ids
        if not nb_id.startswith("notebook:") or not nb_id[len("notebook:") :]
    ]
    if malformed:
        raise InvalidInputError(f"Invalid notebook id(s): {', '.join(malformed)}")

    permitted = await permitted_notebook_ids(user)
    if not requested_ids:
        return permitted
    allowed = set(permitted)
    effective = [nb_id for nb_id in requested_ids if nb_id in allowed]
    if not effective:
        return []
    return await resolve_notebook_scope(effective)


# ---------------------------------------------------------------------------
# Per-object guards (T5). Routers depend on ``get_current_user`` and call
# these before their domain logic. Read denials raise NotFoundError so a
# guessed id is indistinguishable from a missing one; write denials raise
# ForbiddenError once readability is established.
# ---------------------------------------------------------------------------


async def check_notebook_read(user: CurrentUser, notebook_id: str) -> Any:
    """Load a notebook or 404; 404 again when the caller may not read it."""
    from open_notebook.domain.notebook import Notebook

    notebook = await Notebook.get(notebook_id)
    if notebook is None or not can_read_team(user, notebook):
        raise NotFoundError(f"Notebook with id {notebook_id} not found")
    return notebook


async def check_notebook_write(user: CurrentUser, notebook_id: str) -> Any:
    """Load a readable notebook (404) or reject the write with 403."""
    notebook = await check_notebook_read(user, notebook_id)
    if not can_write_team(user, notebook):
        raise ForbiddenError("You do not have write access to this notebook")
    return notebook


async def check_source_read(user: CurrentUser, source_id: str) -> Any:
    from open_notebook.domain.notebook import Source

    source = await Source.get(source_id)
    if source is None or not can_read_team(user, source):
        raise NotFoundError(f"Source with id {source_id} not found")
    return source


async def check_source_write(user: CurrentUser, source_id: str) -> Any:
    source = await check_source_read(user, source_id)
    if not can_write_team(user, source):
        raise ForbiddenError("You do not have write access to this source")
    return source


async def _parent_ids_via(edge: str, item_id: str, direction: str) -> List[str]:
    """Record ids on the other side of an edge from ``item_id``.

    ``direction`` is "out" when the item is the edge's ``in`` (artifact/
    reference/refers_to all point in=content → out=notebook/parent).
    """
    from open_notebook.database.repository import ensure_record_id, repo_query

    rows = await repo_query(
        f"SELECT {direction} AS parent FROM {edge} WHERE in = $id",
        {"id": ensure_record_id(item_id)},
    )
    return [str(row["parent"]) for row in rows if row.get("parent")]


async def check_note_read(user: CurrentUser, note_id: str) -> Any:
    """A note is readable when any of its parent notebooks is readable."""
    from open_notebook.domain.notebook import Note

    note = await Note.get(note_id)
    if note is None:
        raise NotFoundError(f"Note with id {note_id} not found")
    parents = await _parent_ids_via("artifact", note_id, "out")
    if not parents:
        if user.role != "admin":
            raise NotFoundError(f"Note with id {note_id} not found")
        return note
    for parent_id in parents:
        if parent_id.startswith("notebook:"):
            try:
                await check_notebook_read(user, parent_id)
                return note
            except NotFoundError:
                continue
    raise NotFoundError(f"Note with id {note_id} not found")


async def check_note_write(user: CurrentUser, note_id: str) -> Any:
    """A note is writable when any readable parent notebook is writable."""
    note = await check_note_read(user, note_id)
    parents = await _parent_ids_via("artifact", note_id, "out")
    for parent_id in parents:
        if not parent_id.startswith("notebook:"):
            continue
        try:
            notebook = await check_notebook_read(user, parent_id)
        except NotFoundError:
            continue
        if can_write_team(user, notebook):
            return note
    raise ForbiddenError("You do not have write access to this note")


async def check_chat_session_read(user: CurrentUser, session_id: str) -> Any:
    """A chat session is readable when its parent notebook/source is."""
    from open_notebook.domain.notebook import ChatSession

    session = await ChatSession.get(session_id)
    if session is None:
        raise NotFoundError(f"Chat session with id {session_id} not found")
    parents = await _parent_ids_via("refers_to", session_id, "out")
    if not parents:
        if user.role != "admin":
            raise NotFoundError(f"Chat session with id {session_id} not found")
        return session
    for parent_id in parents:
        try:
            if parent_id.startswith("notebook:"):
                await check_notebook_read(user, parent_id)
            elif parent_id.startswith("source:"):
                await check_source_read(user, parent_id)
            else:
                continue
            return session
        except NotFoundError:
            continue
    raise NotFoundError(f"Chat session with id {session_id} not found")


async def check_chat_session_write(user: CurrentUser, session_id: str) -> Any:
    session = await check_chat_session_read(user, session_id)
    parents = await _parent_ids_via("refers_to", session_id, "out")
    for parent_id in parents:
        if parent_id.startswith("notebook:"):
            notebook = await check_notebook_read(user, parent_id)
            if can_write_team(user, notebook):
                return session
        elif parent_id.startswith("source:"):
            source = await check_source_read(user, parent_id)
            if can_write_team(user, source):
                return session
    raise ForbiddenError("You do not have write access to this chat session")


async def check_episode_read(user: CurrentUser, episode_id: str) -> Any:
    """Load a podcast episode or 404; 404 again when not readable (T5)."""
    from open_notebook.podcasts.models import PodcastEpisode

    episode = await PodcastEpisode.get(episode_id)
    if episode is None or not can_read_team(user, episode):
        raise NotFoundError(f"Podcast episode with id {episode_id} not found")
    return episode


async def check_episode_write(user: CurrentUser, episode_id: str) -> Any:
    episode = await check_episode_read(user, episode_id)
    if not can_write_team(user, episode):
        raise ForbiddenError("You do not have write access to this podcast episode")
    return episode


async def check_insight_read(user: CurrentUser, insight_id: str) -> Any:
    """An insight is readable when its parent source is (T5)."""
    from open_notebook.domain.notebook import SourceInsight

    insight = await SourceInsight.get(insight_id)
    if insight is None:
        raise NotFoundError(f"Insight with id {insight_id} not found")
    source = await insight.get_source()
    if source is None or not can_read_team(user, source):
        raise NotFoundError(f"Insight with id {insight_id} not found")
    return insight


async def check_insight_write(user: CurrentUser, insight_id: str) -> Any:
    insight = await check_insight_read(user, insight_id)
    source = await insight.get_source()
    if source is None or not can_write_team(user, source):
        raise ForbiddenError("You do not have write access to this insight")
    return insight


def _with_table_prefix(table: str, record_id: str) -> str:
    return record_id if record_id.startswith(f"{table}:") else f"{table}:{record_id}"


async def filter_context_to_permitted(
    user: CurrentUser, context: Dict[str, Any]
) -> Dict[str, Any]:
    """Return a copy of a client-supplied chat context with out-of-scope ids
    removed (T5).

    ``context`` carries ``{"sources": {id: status}, "notes": {id: status}}``
    dicts as sent by the frontend. An item is kept only when it is linked (via
    ``reference``/``artifact``) to a notebook the caller may read; anything
    else is dropped before it can reach the LLM. A caller with zero permitted
    notebooks gets empty dicts.
    """
    from open_notebook.database.repository import ensure_record_id, repo_query

    permitted = set(await permitted_notebook_ids(user))
    filtered: Dict[str, Any] = dict(context)
    for key, edge, table in (
        ("sources", "reference", "source"),
        ("notes", "artifact", "note"),
    ):
        entries = context.get(key)
        if not isinstance(entries, dict) or not entries:
            filtered[key] = entries if isinstance(entries, dict) else {}
            continue
        if not permitted:
            filtered[key] = {}
            continue
        full_ids = [_with_table_prefix(table, item_id) for item_id in entries]
        rows = await repo_query(
            f"SELECT in AS item, out AS notebook FROM {edge} WHERE in IN $ids",
            {"ids": [ensure_record_id(i) for i in full_ids]},
        )
        allowed = {
            str(row["item"])
            for row in rows
            if row.get("notebook") and str(row["notebook"]) in permitted
        }
        filtered[key] = {
            item_id: status
            for item_id, status in entries.items()
            if _with_table_prefix(table, item_id) in allowed
        }
    return filtered


async def get_permitted_dataset_ids(
    user: CurrentUser = Depends(get_current_user),
) -> list[str]:
    """Dataset ids the caller may query (all of them under the bypass)."""
    from open_notebook.analytics.service import permitted_dataset_ids

    return await permitted_dataset_ids(user)
