"""Team-access identity domain: organization, team, app_user, user_session.

Record fields follow the repo naming convention (target table names:
``organization``, ``team``, ``manager``, ``user`` — see the frozen ERD);
the domain models expose them as ``organization_id`` / ``team_id`` /
``manager_id`` / ``user_id`` via pydantic aliases, and serialization to
SurrealDB goes through ``by_alias=True``.

Schemas come from migration 26. ``user_session`` has no ``updated`` field
(SCHEMAFULL rejects it), so sessions are written with raw
``CREATE ... CONTENT`` (same pattern as open_notebook/domain/analytics.py)
instead of ``ObjectModel.save()``.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import ConfigDict, Field

from open_notebook.database.repository import (
    db_connection,
    ensure_record_id,
    parse_record_ids,
    repo_query,
)
from open_notebook.domain.base import ObjectModel

Role = str  # member | team_manager | ceo | admin (kept as str; see CONTEXT.md)
UserStatus = str  # invited | active | disabled

DEFAULT_ORGANIZATION_EXTERNAL_KEY = "org-open-notebook-default"
DEFAULT_ORGANIZATION_NAME = "Open Notebook"

_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Argon2id hash for app_user.password_hash (ADR-010)."""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-work verification; False on mismatch or malformed hash."""
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False
    except Exception:
        return False


class Organization(ObjectModel):
    table_name = "organization"
    nullable_fields = {"external_key", "name", "status"}

    model_config = ConfigDict(populate_by_name=True)

    external_key: str = ""
    name: str = ""
    status: str = "active"

    def _prepare_save_data(self) -> Dict[str, Any]:
        data = self.model_dump(by_alias=True)
        return {
            key: value
            for key, value in data.items()
            if value is not None or key in self.__class__.nullable_fields
        }


class Team(ObjectModel):
    table_name = "team"
    nullable_fields = {"description", "manager"}

    model_config = ConfigDict(populate_by_name=True)

    organization_id: str = Field(alias="organization")
    slug: str = ""
    name: str = ""
    description: Optional[str] = None
    manager_id: Optional[str] = Field(default=None, alias="manager")
    active: bool = True

    def _prepare_save_data(self) -> Dict[str, Any]:
        data = self.model_dump(by_alias=True)
        return {
            key: value
            for key, value in data.items()
            if value is not None or key in self.__class__.nullable_fields
        }


class AppUser(ObjectModel):
    table_name = "app_user"
    nullable_fields = {"last_active_at"}

    model_config = ConfigDict(populate_by_name=True)

    organization_id: str = Field(alias="organization")
    email: str = ""
    password_hash: str = ""
    display_name: str = ""
    team_id: str = Field(alias="team")
    role: Role = "member"
    status: UserStatus = "invited"
    last_active_at: Optional[datetime] = None

    def _prepare_save_data(self) -> Dict[str, Any]:
        data = self.model_dump(by_alias=True)
        return {
            key: value
            for key, value in data.items()
            if value is not None or key in self.__class__.nullable_fields
        }


class UserSession:
    """One opaque cookie session (migration 26). Raw-created (no ``updated``)."""

    def __init__(
        self,
        *,
        id: Optional[str] = None,
        user_id: str = "",
        token_hash: str = "",
        expires_at: Optional[datetime] = None,
        last_seen_at: Optional[datetime] = None,
        revoked_at: Optional[datetime] = None,
        created: Optional[datetime] = None,
    ) -> None:
        self.id = id
        self.user_id = user_id
        self.token_hash = token_hash
        self.expires_at = expires_at
        self.last_seen_at = last_seen_at
        self.revoked_at = revoked_at
        self.created = created

    @classmethod
    def from_record(cls, record: Dict[str, Any]) -> "UserSession":
        return cls(
            id=str(record.get("id")) if record.get("id") else None,
            user_id=str(record.get("user", "")),
            token_hash=record.get("token_hash", ""),
            expires_at=record.get("expires_at"),
            last_seen_at=record.get("last_seen_at"),
            revoked_at=record.get("revoked_at"),
            created=record.get("created"),
        )


async def count_users() -> int:
    """Total app_user rows (bootstrap refuses to run when any exist)."""
    records = await repo_query("SELECT count() AS n FROM app_user GROUP ALL")
    if not records:
        return 0
    return int(records[0].get("n", 0))


async def ensure_default_organization() -> str:
    """Idempotently create the default organization; returns its record id."""
    records = await repo_query(
        "SELECT * FROM organization WHERE external_key = $key LIMIT 1",
        {"key": DEFAULT_ORGANIZATION_EXTERNAL_KEY},
    )
    if records:
        return str(records[0]["id"])
    async with db_connection() as conn:
        result = parse_record_ids(
            await conn.query(
                "CREATE organization CONTENT $data",
                {
                    "data": {
                        "external_key": DEFAULT_ORGANIZATION_EXTERNAL_KEY,
                        "name": DEFAULT_ORGANIZATION_NAME,
                        "status": "active",
                    }
                },
            )
        )
    return str(result[0]["id"])


async def ensure_team(organization_id: str, slug: str, name: str) -> str:
    """Idempotently create a team by slug; returns its record id."""
    records = await repo_query(
        "SELECT * FROM team WHERE slug = $slug LIMIT 1", {"slug": slug}
    )
    if records:
        return str(records[0]["id"])
    async with db_connection() as conn:
        result = parse_record_ids(
            await conn.query(
                "CREATE team CONTENT $data",
                {
                    "data": {
                        "organization": ensure_record_id(organization_id),
                        "slug": slug,
                        "name": name,
                        "active": True,
                    }
                },
            )
        )
    return str(result[0]["id"])


async def get_user_by_email(email: str) -> Optional[AppUser]:
    """Fetch an active-or-invited user by email (case-insensitive)."""
    records = await repo_query(
        "SELECT * FROM app_user WHERE string::lowercase(email) = $email LIMIT 1",
        {"email": email.strip().lower()},
    )
    if not records:
        return None
    return AppUser(**records[0])


async def get_user_by_id(user_id: str) -> Optional[AppUser]:
    records = await repo_query("SELECT * FROM $id", {"id": ensure_record_id(user_id)})
    if not records:
        return None
    return AppUser(**records[0])


async def create_user(
    *,
    organization_id: str,
    email: str,
    password_hash: str,
    display_name: str,
    team_id: str,
    role: Role = "member",
    status: UserStatus = "invited",
) -> AppUser:
    """Create an app_user (raw CREATE — record fields need RecordID values).

    ``repo_create``/``ObjectModel.save()`` pass record references as plain
    strings, which SurrealDB rejects for ``record<...>`` fields on this path.
    """
    data: Dict[str, Any] = {
        "organization": ensure_record_id(organization_id),
        "email": email.strip().lower(),
        "password_hash": password_hash,
        "display_name": display_name,
        "team": ensure_record_id(team_id),
        "role": role,
        "status": status,
        "last_active_at": None,
    }
    async with db_connection() as conn:
        result = parse_record_ids(
            await conn.query("CREATE app_user CONTENT $data", {"data": data})
        )
    return AppUser(**result[0])


async def update_user(user_id: str, **changes: Any) -> None:
    """Merge scalar changes into an app_user. ``team_id``/``organization_id``
    are converted to record references; the raw UPDATE path accepts RecordID
    values where ``repo_update``'s string serialization does not."""
    payload: Dict[str, Any] = dict(changes)
    if "team_id" in payload:
        payload["team"] = ensure_record_id(payload.pop("team_id"))
    if "organization_id" in payload:
        payload["organization"] = ensure_record_id(payload.pop("organization_id"))
    await repo_query(
        "UPDATE $id MERGE $data",
        {"id": ensure_record_id(user_id), "data": payload},
    )


async def list_users() -> List[AppUser]:
    records = await repo_query("SELECT * FROM app_user ORDER BY created ASC")
    return [AppUser(**r) for r in records]


async def list_teams() -> List[Team]:
    records = await repo_query("SELECT * FROM team ORDER BY slug ASC")
    return [Team(**r) for r in records]


async def get_team_by_slug(slug: str) -> Optional[Team]:
    records = await repo_query(
        "SELECT * FROM team WHERE slug = $slug LIMIT 1", {"slug": slug}
    )
    if not records:
        return None
    return Team(**records[0])


async def get_team_by_id(team_id: str) -> Optional[Team]:
    records = await repo_query(
        "SELECT * FROM $id", {"id": ensure_record_id(team_id)}
    )
    if not records:
        return None
    return Team(**records[0])


async def create_team(
    *,
    organization_id: str,
    slug: str,
    name: str,
    description: Optional[str] = None,
    manager_id: Optional[str] = None,
) -> Team:
    """Create a team (raw CREATE — record fields need RecordID values)."""
    data: Dict[str, Any] = {
        "organization": ensure_record_id(organization_id),
        "slug": slug,
        "name": name,
        "description": description,
        "manager": ensure_record_id(manager_id) if manager_id else None,
        "active": True,
    }
    async with db_connection() as conn:
        result = parse_record_ids(
            await conn.query("CREATE team CONTENT $data", {"data": data})
        )
    return Team(**result[0])


async def update_team(team_id: str, **changes: Any) -> None:
    """Merge scalar changes into a team. ``manager_id`` is converted to a
    record reference (same raw-UPDATE pattern as ``update_user``)."""
    payload: Dict[str, Any] = dict(changes)
    if "manager_id" in payload:
        manager = payload.pop("manager_id")
        payload["manager"] = ensure_record_id(manager) if manager else None
    await repo_query(
        "UPDATE $id MERGE $data",
        {"id": ensure_record_id(team_id), "data": payload},
    )


def _counts_by_ref(records: List[Dict[str, Any]]) -> Dict[str, int]:
    """GROUP BY record-field results keyed by record id string."""
    counts: Dict[str, int] = {}
    for record in records:
        ref = record.get("team")
        if ref is None:
            continue
        counts[str(ref)] = int(record.get("n", 0))
    return counts


async def count_users_per_team() -> Dict[str, int]:
    """Member counts per team id (``{team: x, n: y}`` GROUP BY rows)."""
    records = await repo_query(
        "SELECT team, count() AS n FROM app_user GROUP BY team"
    )
    return _counts_by_ref(records)


async def count_notebooks_per_team() -> Dict[str, int]:
    """Notebook counts per team id (notebooks without a team are excluded)."""
    records = await repo_query(
        "SELECT team, count() AS n FROM notebook GROUP BY team"
    )
    return _counts_by_ref(records)


async def create_user_session(
    *,
    user_id: str,
    token_hash: str,
    expires_at: datetime,
) -> UserSession:
    """Persist a new session (raw CREATE — user_session has no ``updated``)."""
    data: Dict[str, Any] = {
        "user": ensure_record_id(user_id),
        "token_hash": token_hash,
        "expires_at": expires_at,
        "last_seen_at": datetime.now(timezone.utc),
        "revoked_at": None,
    }
    async with db_connection() as conn:
        result = parse_record_ids(
            await conn.query("CREATE user_session CONTENT $data", {"data": data})
        )
    return UserSession.from_record(result[0])


async def get_session_by_token_hash(token_hash: str) -> Optional[UserSession]:
    """Fetch a session by its SHA-256 token hash (unique index)."""
    records = await repo_query(
        "SELECT * FROM user_session WHERE token_hash = $hash LIMIT 1",
        {"hash": token_hash},
    )
    if not records:
        return None
    return UserSession.from_record(records[0])


async def touch_session(session_id: str) -> None:
    """Best-effort ``last_seen_at`` bump (never raises)."""
    try:
        await repo_query(
            "UPDATE $id SET last_seen_at = $now",
            {"id": ensure_record_id(session_id), "now": datetime.now(timezone.utc)},
        )
    except Exception:
        pass


async def revoke_session(session_id: str) -> None:
    await repo_query(
        "UPDATE $id SET revoked_at = $now",
        {"id": ensure_record_id(session_id), "now": datetime.now(timezone.utc)},
    )


async def revoke_all_sessions(user_id: str) -> None:
    """Revoke every session for a user (disable / password reset)."""
    await repo_query(
        "UPDATE user_session SET revoked_at = $now WHERE user = $user AND revoked_at IS NONE",
        {
            "now": datetime.now(timezone.utc),
            "user": ensure_record_id(user_id),
        },
    )


async def touch_user_activity(user_id: str) -> None:
    """Best-effort ``last_active_at`` bump (never raises)."""
    try:
        await repo_query(
            "UPDATE $id SET last_active_at = $now",
            {"id": ensure_record_id(user_id), "now": datetime.now(timezone.utc)},
        )
    except Exception:
        pass


__all__ = [
    "AppUser",
    "Organization",
    "Role",
    "Team",
    "UserSession",
    "UserStatus",
    "count_notebooks_per_team",
    "count_users",
    "count_users_per_team",
    "create_team",
    "create_user",
    "create_user_session",
    "ensure_default_organization",
    "ensure_team",
    "get_session_by_token_hash",
    "get_team_by_id",
    "get_team_by_slug",
    "get_user_by_email",
    "get_user_by_id",
    "hash_password",
    "list_teams",
    "list_users",
    "revoke_all_sessions",
    "revoke_session",
    "touch_session",
    "touch_user_activity",
    "update_team",
    "update_user",
    "verify_password",
]
