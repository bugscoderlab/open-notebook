"""Cookie-session auth service (ADR-010, issue #4/T3).

Business logic behind the auth router: Argon2id verification, opaque
session tokens (only the SHA-256 hash is persisted in ``user_session``),
CSRF token issuance, and in-process login rate limiting with a generic
invalid-credentials error (no user enumeration, per the frozen contract).
"""

import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from fastapi import Request

from open_notebook.domain.user import (
    AppUser,
    UserSession,
    create_user_session,
    get_session_by_token_hash,
    get_user_by_email,
    get_user_by_id,
    revoke_session,
    touch_session,
    touch_user_activity,
    verify_password,
)
from open_notebook.exceptions import AuthenticationError, RateLimitError

SESSION_COOKIE = "open_notebook_session"
CSRF_COOKIE = "open_notebook_csrf"
CSRF_HEADER = "x-csrf-token"
SESSION_TTL = timedelta(days=7)
SESSION_MAX_AGE_SECONDS = int(SESSION_TTL.total_seconds())
TOKEN_BYTES = 32  # 256 bits of entropy, urlsafe-encoded

INVALID_CREDENTIALS = "Invalid email or password"
RATE_LIMITED = "Too many login attempts. Try again later."

LOGIN_RATE_LIMIT_MAX_ATTEMPTS = 5
LOGIN_RATE_LIMIT_WINDOW_SECONDS = 300


def hash_session_token(token: str) -> str:
    """SHA-256 of the opaque token — the only form that reaches SurrealDB."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class LoginRateLimiter:
    """Fixed-window per (client, email) failure limiter (in-process).

    Counts failed attempts per key; a success clears the key. When the
    window is exceeded, ``check`` raises ``RateLimitError`` (429) even if
    the credentials are correct — the generic message leaks nothing.
    """

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._failures: dict[tuple[str, str], tuple[float, int]] = {}

    def _key(self, client: str, email: str) -> tuple[str, str]:
        return (client, email.strip().lower())

    def check(self, client: str, email: str) -> None:
        """Raise RateLimitError when the key is over the failure budget."""
        window_start, count = self._failures.get(self._key(client, email), (0.0, 0))
        now = time.monotonic()
        if now - window_start < self.window_seconds and count >= self.max_attempts:
            raise RateLimitError(RATE_LIMITED)

    def record_failure(self, client: str, email: str) -> None:
        now = time.monotonic()
        key = self._key(client, email)
        window_start, count = self._failures.get(key, (now, 0))
        if now - window_start >= self.window_seconds:
            window_start, count = now, 0
        self._failures[key] = (window_start, count + 1)

    def record_success(self, client: str, email: str) -> None:
        self._failures.pop(self._key(client, email), None)

    def reset(self) -> None:
        """Clear all windows (tests)."""
        self._failures.clear()


login_rate_limiter = LoginRateLimiter(
    max_attempts=LOGIN_RATE_LIMIT_MAX_ATTEMPTS,
    window_seconds=LOGIN_RATE_LIMIT_WINDOW_SECONDS,
)


def client_ip(request: Request) -> str:
    """Client address for rate-limit keys (best effort)."""
    return request.client.host if request.client else "unknown"


async def attempt_login(client: str, email: str, password: str) -> AppUser:
    """Rate-limited login: checks the limiter, authenticates, records the outcome.

    Raises RateLimitError (429) when the failure budget is exhausted, or
    AuthenticationError (generic 401) on bad credentials / disabled users.
    """
    login_rate_limiter.check(client, email)
    try:
        user = await authenticate_user(email, password)
    except Exception:
        login_rate_limiter.record_failure(client, email)
        raise
    login_rate_limiter.record_success(client, email)
    return user


async def authenticate_user(email: str, password: str) -> AppUser:
    """Verify credentials. Raises AuthenticationError (generic) on any failure."""
    user = await get_user_by_email(email)
    if user is None or not verify_password(password, user.password_hash):
        raise AuthenticationError(INVALID_CREDENTIALS)
    if user.status == "disabled":
        raise AuthenticationError(INVALID_CREDENTIALS)
    return user


async def issue_session(user_id: str) -> tuple[str, str, datetime]:
    """Create a session. Returns (opaque token, csrf token, expires_at).

    The opaque token is returned to the caller exactly once — only its
    SHA-256 hash is stored (``user_session.token_hash``, unique index).
    """
    token = secrets.token_urlsafe(TOKEN_BYTES)
    csrf_token = secrets.token_urlsafe(TOKEN_BYTES)
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    await create_user_session(
        user_id=user_id,
        token_hash=hash_session_token(token),
        expires_at=expires_at,
    )
    return token, csrf_token, expires_at


async def resolve_session(token: Optional[str]) -> Optional[Tuple[AppUser, UserSession]]:
    """Load (user, session) for an opaque token; None when invalid/expired.

    Best-effort ``last_seen_at`` / ``last_active_at`` bumps never raise —
    a failed touch must not fail the request (ADR-010: every protected
    request loads the session + user; bumps are telemetry).
    """
    if not token:
        return None
    session = await get_session_by_token_hash(hash_session_token(token))
    if session is None:
        return None
    now = datetime.now(timezone.utc)
    if (
        session.revoked_at is not None
        or session.expires_at is None
        or session.expires_at <= now
    ):
        return None
    user = await get_user_by_id(session.user_id)
    if user is None or user.status == "disabled":
        return None
    await touch_session(session.id or "")
    await touch_user_activity(user.id or "")
    return user, session


async def revoke_session_token(token: Optional[str]) -> None:
    """Revoke the session behind an opaque token (logout); no-op if unknown."""
    if not token:
        return
    session = await get_session_by_token_hash(hash_session_token(token))
    if session is not None and session.id:
        await revoke_session(session.id)


def read_csrf_token(request: Request) -> Optional[str]:
    """The CSRF header value on a mutating request, if present."""
    return request.headers.get(CSRF_HEADER)


def csrf_matches(request: Request) -> bool:
    """Header token must equal the CSRF cookie (constant-time)."""
    header = read_csrf_token(request)
    cookie = request.cookies.get(CSRF_COOKIE)
    if not header or not cookie:
        return False
    return secrets.compare_digest(header.encode("utf-8"), cookie.encode("utf-8"))
