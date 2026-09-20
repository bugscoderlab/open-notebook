"""Internal-caller auth (ADR-018).

The messenger gateway is a service, not a browser, so the cookie session of
ADR-010 cannot serve it. Instead a single **internal token** authenticates
callers to the dedicated ``/api/integrations/*`` router — and only that
router. The token comes from ``OPEN_NOTEBOOK_INTERNAL_TOKEN`` or is
auto-generated into the data folder on first use (mirroring how the
encryption key is treated); it is never stored in the database, and only a
SHA-256 prefix is ever logged.

An internal caller never asserts a user identity: integrations endpoints
resolve the user server-side through an integration link. The gateway sends
``platform`` + ``external_id``; who that maps to is the API's decision.
"""

import hashlib
import hmac
import os
import secrets
from typing import Optional

from fastapi import Request
from loguru import logger

from open_notebook.config import DATA_FOLDER
from open_notebook.exceptions import AuthenticationError

TOKEN_ENV_VAR = "OPEN_NOTEBOOK_INTERNAL_TOKEN"
AUTH_SCHEME = "Internal"
_TOKEN_FILE = os.path.join(DATA_FOLDER, "internal-token")

_token_cache: Optional[str] = None


def _preview(token: str) -> str:
    """Log-safe identifier for the token (never the token itself)."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def reset_internal_token_cache() -> None:
    """Drop the cached token (tests, rotation-by-env-change)."""
    global _token_cache
    _token_cache = None


def resolve_internal_token() -> str:
    """The internal token: env wins, else the data-folder file, else generate.

    Generation is race-safe: O_EXCL create wins; a concurrent creator's file
    is read back. The file is mode 0600 and lives outside the database so a
    DB compromise yields no network access (ADR-018).
    """
    global _token_cache
    if _token_cache:
        return _token_cache

    env_token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if env_token:
        _token_cache = env_token
        logger.debug(
            "Internal token resolved from {} (sha256:{})",
            TOKEN_ENV_VAR,
            _preview(env_token),
        )
        return env_token

    try:
        with open(_TOKEN_FILE, "r", encoding="utf-8") as fh:
            file_token = fh.read().strip()
        if file_token:
            _token_cache = file_token
            return file_token
    except FileNotFoundError:
        pass

    generated = secrets.token_urlsafe(32)
    try:
        fd = os.open(_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(generated)
        _token_cache = generated
        logger.info(
            "Generated internal token for service callers (sha256:{})",
            _preview(generated),
        )
        return generated
    except FileExistsError:
        # Concurrent creator beat us — read theirs.
        with open(_TOKEN_FILE, "r", encoding="utf-8") as fh:
            _token_cache = fh.read().strip()
        return _token_cache


async def get_internal_caller(request: Request) -> None:
    """Dependency: validate the internal token (Authorization: Internal <token>).

    Returns None on success; raises AuthenticationError (401) otherwise. The
    token is compared in constant time. Valid *only* on the integrations
    router — this dependency is never attached anywhere else.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, presented = header.partition(" ")
    if scheme != AUTH_SCHEME or not presented.strip():
        raise AuthenticationError("Invalid internal token")
    expected = resolve_internal_token()
    if not hmac.compare_digest(presented.strip(), expected):
        raise AuthenticationError("Invalid internal token")
