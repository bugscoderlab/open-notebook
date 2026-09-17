"""
Pytest configuration file.

This file ensures that the project root is in the Python path,
allowing tests to import from the api and open_notebook modules.
"""

import sys
from pathlib import Path

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
