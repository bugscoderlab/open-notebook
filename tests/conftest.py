"""
Pytest configuration file. 

This file ensures that the project root is in the Python path,
allowing tests to import from the api and open_notebook modules.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Iterator

# Load environment variables from .env file
# This must be done BEFORE any imports that depend on environment variables
from dotenv import load_dotenv

# Load .env file from project root
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)
    print(f"Loaded environment variables from {dotenv_path}")
else:
    print(f"Warning: .env file not found at {dotenv_path}")

# Add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest  # noqa: E402

# --- Test tiers (T10) -------------------------------------------------------
# unit      (default): hermetic, xdist-safe — the PR fast path (`make test`).
# integration:        needs live SurrealDB; isolated per worker (namespace
#                     open_notebook_test_gwN).
# testpack:           the synthetic acceptance pack (canary sweep, access
#                     matrix, real-PDF extraction); hermetic on mem:// but
#                     runs in its own CI job alongside integration.
# The tier is activated with OPEN_NOTEBOOK_TEST_TIER=integration|testpack
# (the Makefile targets and the CI testpack job set it). Without it the
# integration suites skip, exactly as before.
TIER_ENV_VAR = "OPEN_NOTEBOOK_TEST_TIER"
INTEGRATION_TIERS = ("integration", "testpack")


def _worker_suffix() -> str:
    """xdist worker id ('gw0' when running serially)."""
    return os.environ.get("PYTEST_XDIST_WORKER", "gw0")


def _tier_active() -> bool:
    return os.environ.get(TIER_ENV_VAR) in INTEGRATION_TIERS


async def _prepare_surreal_namespace() -> None:
    """Migrations + org/team seeds in the per-worker test namespace."""
    from open_notebook.database.async_migrate import AsyncMigrationManager
    from open_notebook.domain.user import ensure_default_organization, ensure_team

    manager = AsyncMigrationManager()
    await manager.ping()
    await manager.run_migration_up()
    org_id = await ensure_default_organization()
    await ensure_team(org_id, "hr", "HR")
    await ensure_team(org_id, "finance", "Finance")
    await ensure_team(org_id, "executive", "Executive")


@pytest.fixture(scope="session", autouse=True)
def integration_surreal_namespace() -> Iterator[None]:
    """Point the integration/testpack tier at an isolated SurrealDB namespace.

    Every xdist worker gets its own namespace (open_notebook_test_gwN) with
    migrations applied and the three department teams seeded, so parallel
    workers never see each other's rows. Unreachable
    SurrealDB is not an error here — the integration suites skip themselves.
    """
    if not _tier_active():
        yield
        return

    previous = os.environ.get("SURREAL_NAMESPACE")
    os.environ["SURREAL_NAMESPACE"] = f"open_notebook_test_{_worker_suffix()}"
    try:
        asyncio.run(_prepare_surreal_namespace())
    except Exception as exc:  # SurrealDB not reachable → suites skip
        print(f"SurrealDB test namespace unavailable ({exc}); suites skip")
    yield
    if previous is None:
        os.environ.pop("SURREAL_NAMESPACE", None)
    else:
        os.environ["SURREAL_NAMESPACE"] = previous


@pytest.fixture
def client():
    """TestClient over the FastAPI app (no lifespan: migrations do NOT run).

    Imported inside the fixture so conftest's env load happens first —
    same pattern as the per-suite fixtures it replaces.
    """
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


@pytest.fixture
def auth_session(monkeypatch):
    """Authenticate API requests as a persona (T5: every router requires a
    session cookie). Defaults to admin so characterization tests keep full
    access; pass role/team to exercise the permission matrix.

    Usage::

        def test_x(client, auth_session):
            auth_session(role="member", team_id="team:hr")
            response = client.get("/api/notebooks")
    """

    def _auth(
        role: str = "admin",
        team_id: str = "team:hr",
        organization_id: str = "organization:default",
    ):
        from api import auth_service
        from open_notebook.domain.user import AppUser

        user = AppUser(
            id="app_user:test",
            organization_id=organization_id,
            email="test@example.com",
            password_hash="x",
            display_name="Test User",
            team_id=team_id,
            role=role,
            status="active",
        )

        async def _resolve(token):
            return (user, None)

        monkeypatch.setattr(auth_service, "resolve_session", _resolve)
        return user

    return _auth


@pytest.fixture
def auth_cookie():
    """Session cookie dict to send alongside authenticated requests."""
    from api.auth_service import SESSION_COOKIE

    return {SESSION_COOKIE: "test-opaque-token"}
