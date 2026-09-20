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
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from loguru import logger

from api.access import CurrentUser
from open_notebook.database.repository import repo_query
from open_notebook.domain.integration_link import IntegrationLink, Platform
from open_notebook.domain.user import AppUser, get_user_by_id
from open_notebook.exceptions import InvalidInputError, NotFoundError

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
