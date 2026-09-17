"""Tests for admin users/teams management (issue #5/T4).

Router tests run against the full app with api.admin_service (or the domain
layer) patched — no database needed. Service tests patch the domain layer.
External behavior only: HTTP status/body, session revocation, error mapping.
"""

from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import api.admin_service as admin_service
from api import auth_service
from open_notebook.domain.user import AppUser, Team
from open_notebook.exceptions import InvalidInputError, NotFoundError

CSRF_COOKIE = "csrf-cookie"
CSRF_HEADERS = {"x-csrf-token": CSRF_COOKIE}


def _user(**overrides) -> AppUser:
    values = {
        "id": "app_user:alex",
        "organization_id": "organization:default",
        "email": "alex@company.com",
        "password_hash": "argon2id$fake",
        "display_name": "Alex Admin",
        "team_id": "team:executive",
        "role": "admin",
        "status": "active",
    }
    values.update(overrides)
    return AppUser(**values)


def _team(**overrides) -> Team:
    values = {
        "id": "team:finance",
        "organization_id": "organization:default",
        "slug": "finance",
        "name": "Finance",
    }
    values.update(overrides)
    return Team(**values)


def _async(value):
    async def coro():
        return value

    return coro()


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.fixture
def admin_cookie(client, monkeypatch):
    """A session cookie that resolves to the admin user."""
    monkeypatch.setattr(
        auth_service,
        "resolve_session",
        lambda token: _async((_user(), None)),
    )
    return {auth_service.SESSION_COOKIE: "opaque"}


@pytest.fixture
def member_cookie(client, monkeypatch):
    """A session cookie that resolves to a non-admin (HR member) user."""
    monkeypatch.setattr(
        auth_service,
        "resolve_session",
        lambda token: _async((_user(id="app_user:aisha", role="member"), None)),
    )
    return {auth_service.SESSION_COOKIE: "opaque"}


def _csrf_cookies(base: Dict[str, str]) -> Dict[str, str]:
    return {**base, auth_service.CSRF_COOKIE: CSRF_COOKIE}


class TestAdminEnforcement:
    """Non-admin → 403 on every users/teams surface; unauthenticated → 401."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/users"),
            ("GET", "/api/teams"),
            ("POST", "/api/teams"),
            ("PATCH", "/api/teams/team:hr"),
            ("PATCH", "/api/users/app_user:aisha"),
        ],
    )
    def test_unauthenticated_is_401(self, client, monkeypatch, method, path):
        monkeypatch.setattr(auth_service, "resolve_session", lambda token: _async(None))
        response = client.request(method, path, cookies={auth_service.SESSION_COOKIE: "opaque"})
        assert response.status_code == 401

    @pytest.mark.parametrize("role", ["member", "team_manager", "ceo"])
    @pytest.mark.parametrize(
        "method,path,kwargs",
        [
            ("GET", "/api/users", {}),
            ("GET", "/api/teams", {}),
            (
                "POST",
                "/api/teams",
                {"json": {"slug": "legal", "name": "Legal"}},
            ),
            ("PATCH", "/api/teams/team:hr", {"json": {"name": "HR 2"}}),
            (
                "PATCH",
                "/api/users/app_user:aisha",
                {"json": {"role": "member"}},
            ),
            (
                "POST",
                "/api/auth/invite",
                {
                    "json": {
                        "email": "new@company.com",
                        "display_name": "New User",
                        "team_id": "team:hr",
                        "temp_password": "temppassword123",
                    }
                },
            ),
        ],
    )
    def test_non_admin_is_403_regardless_of_ui(
        self, client, monkeypatch, role, method, path, kwargs
    ):
        monkeypatch.setattr(
            auth_service,
            "resolve_session",
            lambda token: _async((_user(id=f"app_user:{role}", role=role), None)),
        )
        cookies = _csrf_cookies({auth_service.SESSION_COOKIE: "opaque"})
        response = client.request(method, path, cookies=cookies, headers=CSRF_HEADERS, **kwargs)
        assert response.status_code == 403

    def test_admin_gets_users_list(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(
            admin_service,
            "list_users",
            lambda: _async(
                [
                    {
                        "id": "app_user:aisha",
                        "email": "aisha@company.com",
                        "display_name": "Aisha Hassan",
                        "role": "member",
                        "status": "active",
                        "team_id": "team:hr",
                        "team_name": "HR",
                        "last_active_at": None,
                    }
                ]
            ),
        )
        response = client.get("/api/users", cookies=admin_cookie)
        assert response.status_code == 200
        body = response.json()
        assert body[0]["team_name"] == "HR"
        assert "password_hash" not in str(body)


class TestCsrfOnMutations:
    def test_invite_without_csrf_header_is_403(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(admin_service, "invite_user", AsyncMock())
        response = client.post(
            "/api/auth/invite",
            cookies=_csrf_cookies(admin_cookie),
            json={
                "email": "new@company.com",
                "display_name": "New User",
                "team_id": "team:hr",
                "temp_password": "temppassword123",
            },
        )
        assert response.status_code == 403

    def test_patch_user_without_csrf_header_is_403(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(admin_service, "update_user", AsyncMock())
        response = client.patch(
            "/api/users/app_user:aisha",
            cookies=_csrf_cookies(admin_cookie),
            json={"status": "disabled"},
        )
        assert response.status_code == 403

    def test_create_team_without_csrf_header_is_403(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(admin_service, "create_team", AsyncMock())
        response = client.post(
            "/api/teams",
            cookies=_csrf_cookies(admin_cookie),
            json={"slug": "legal", "name": "Legal"},
        )
        assert response.status_code == 403

    def test_patch_team_without_csrf_header_is_403(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(admin_service, "update_team", AsyncMock())
        response = client.patch(
            "/api/teams/team:hr",
            cookies=_csrf_cookies(admin_cookie),
            json={"active": False},
        )
        assert response.status_code == 403


class TestInvite:
    def test_admin_invite_returns_201_contract_shape(
        self, client, admin_cookie, monkeypatch
    ):
        captured = {}

        async def fake_invite(**kwargs):
            captured.update(kwargs)
            return {
                "id": "app_user:daniel",
                "email": "daniel@company.com",
                "display_name": "Daniel Fong",
                "role": "team_manager",
                "status": "invited",
                "team_id": "team:finance",
                "team_name": "Finance",
                "last_active_at": None,
            }

        monkeypatch.setattr(admin_service, "invite_user", fake_invite)

        response = client.post(
            "/api/auth/invite",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={
                "email": "daniel@company.com",
                "display_name": "Daniel Fong",
                "team_id": "team:finance",
                "role": "team_manager",
                "temp_password": "temppassword123",
            },
        )

        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "invited"
        assert body["team_name"] == "Finance"
        assert "password_hash" not in str(body)
        assert captured["email"] == "daniel@company.com"
        assert captured["role"] == "team_manager"

    def test_invite_with_invalid_role_is_422(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(admin_service, "invite_user", AsyncMock())
        response = client.post(
            "/api/auth/invite",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={
                "email": "new@company.com",
                "display_name": "New User",
                "team_id": "team:hr",
                "role": "superuser",
                "temp_password": "temppassword123",
            },
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invite_service_hashes_temp_password_and_marks_invited(
        self, monkeypatch
    ):
        from open_notebook.domain import user as user_domain

        monkeypatch.setattr(
            user_domain, "get_team_by_id", lambda team_id: _async(_team())
        )
        monkeypatch.setattr(user_domain, "get_user_by_email", lambda email: _async(None))
        created = {}

        async def fake_create_user(**kwargs):
            created.update(kwargs)
            return _user(
                id="app_user:daniel",
                email="daniel@company.com",
                role=kwargs["role"],
                status=kwargs["status"],
            )

        monkeypatch.setattr(user_domain, "create_user", fake_create_user)
        monkeypatch.setattr(
            admin_service,
            "hash_password",
            lambda password: f"hashed:{password}",
        )

        result = await admin_service.invite_user(
            email="Daniel@Company.com",
            display_name="Daniel Fong",
            team_id="team:finance",
            role="team_manager",
            temp_password="temppassword123",
        )

        assert created["status"] == "invited"
        assert created["password_hash"] == "hashed:temppassword123"
        assert created["organization_id"] == "organization:default"
        assert result["role"] == "team_manager"
        assert result["team_name"] == "Finance"

    @pytest.mark.asyncio
    async def test_invite_service_rejects_duplicate_email(self, monkeypatch):
        from open_notebook.domain import user as user_domain

        monkeypatch.setattr(
            user_domain, "get_team_by_id", lambda team_id: _async(_team())
        )
        monkeypatch.setattr(
            user_domain, "get_user_by_email", lambda email: _async(_user())
        )

        with pytest.raises(InvalidInputError):
            await admin_service.invite_user(
                email="daniel@company.com",
                display_name="Daniel Fong",
                team_id="team:finance",
                role="member",
                temp_password="temppassword123",
            )

    @pytest.mark.asyncio
    async def test_invite_service_unknown_team_is_404(self, monkeypatch):
        from open_notebook.domain import user as user_domain

        monkeypatch.setattr(user_domain, "get_team_by_id", lambda team_id: _async(None))

        with pytest.raises(NotFoundError):
            await admin_service.invite_user(
                email="daniel@company.com",
                display_name="Daniel Fong",
                team_id="team:unknown",
                role="member",
                temp_password="temppassword123",
            )


class TestUpdateUser:
    def test_patch_user_success(self, client, admin_cookie, monkeypatch):
        async def fake_update(user_id, **changes):
            assert user_id == "app_user:aisha"
            assert changes == {"role": "team_manager"}
            return {
                "id": "app_user:aisha",
                "email": "aisha@company.com",
                "display_name": "Aisha Hassan",
                "role": "team_manager",
                "status": "active",
                "team_id": "team:hr",
                "team_name": "HR",
                "last_active_at": None,
            }

        monkeypatch.setattr(admin_service, "update_user", fake_update)

        response = client.patch(
            "/api/users/app_user:aisha",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={"role": "team_manager"},
        )
        assert response.status_code == 200
        assert response.json()["role"] == "team_manager"

    def test_patch_user_invalid_status_is_422(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(admin_service, "update_user", AsyncMock())
        response = client.patch(
            "/api/users/app_user:aisha",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={"status": "banned"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_disabling_user_revokes_all_sessions_immediately(self, monkeypatch):
        from open_notebook.domain import user as user_domain

        revoke_all_sessions = AsyncMock()
        monkeypatch.setattr(user_domain, "revoke_all_sessions", revoke_all_sessions)
        monkeypatch.setattr(
            user_domain,
            "get_team_by_id",
            lambda team_id: _async(_team(id=team_id, slug="executive")),
        )
        updated: Dict[str, Any] = {}
        stored = _user(id="app_user:aisha")

        async def fake_get_user(user_id):
            return stored.model_copy(update=updated)

        async def fake_update_user(user_id, **changes):
            updated.update(changes)

        monkeypatch.setattr(user_domain, "get_user_by_id", fake_get_user)
        monkeypatch.setattr(user_domain, "update_user", fake_update_user)

        result = await admin_service.update_user(
            "app_user:aisha", team_id=None, role=None, status="disabled"
        )

        assert updated == {"status": "disabled"}
        revoke_all_sessions.assert_awaited_once_with("app_user:aisha")
        assert result["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_temp_password_regenerates_hash_and_revokes_sessions(
        self, monkeypatch
    ):
        """Resend invite: a new temp password replaces the hash and revokes
        every session, exactly like a password reset."""
        from open_notebook.domain import user as user_domain

        revoke_all_sessions = AsyncMock()
        monkeypatch.setattr(user_domain, "revoke_all_sessions", revoke_all_sessions)
        monkeypatch.setattr(
            user_domain,
            "get_team_by_id",
            lambda team_id: _async(_team(id=team_id, slug="executive")),
        )
        updated: Dict[str, Any] = {}
        stored = _user(id="app_user:aisha")

        async def fake_get_user(user_id):
            return stored.model_copy(update=updated)

        async def fake_update_user(user_id, **changes):
            updated.update(changes)

        monkeypatch.setattr(user_domain, "get_user_by_id", fake_get_user)
        monkeypatch.setattr(user_domain, "update_user", fake_update_user)
        monkeypatch.setattr(
            admin_service, "hash_password", lambda password: f"hashed:{password}"
        )

        result = await admin_service.update_user(
            "app_user:aisha", temp_password="brandnewpassword123"
        )

        assert updated == {"password_hash": "hashed:brandnewpassword123"}
        revoke_all_sessions.assert_awaited_once_with("app_user:aisha")
        assert "brandnewpassword123" not in str(result)

    def test_patch_user_with_temp_password_success(self, client, admin_cookie, monkeypatch):
        async def fake_update(user_id, **changes):
            assert changes == {"temp_password": "brandnewpassword123"}
            return {
                "id": "app_user:nur",
                "email": "nur@company.com",
                "display_name": "Nur Amal",
                "role": "member",
                "status": "invited",
                "team_id": "team:hr",
                "team_name": "HR",
                "last_active_at": None,
            }

        monkeypatch.setattr(admin_service, "update_user", fake_update)

        response = client.patch(
            "/api/users/app_user:nur",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={"temp_password": "brandnewpassword123"},
        )
        assert response.status_code == 200
        assert "hashed" not in response.text

    def test_patch_user_short_temp_password_is_422(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(admin_service, "update_user", AsyncMock())
        response = client.patch(
            "/api/users/app_user:nur",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={"temp_password": "short"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_role_change_does_not_revoke_sessions(self, monkeypatch):
        from open_notebook.domain import user as user_domain

        revoke_all_sessions = AsyncMock()
        monkeypatch.setattr(user_domain, "revoke_all_sessions", revoke_all_sessions)
        monkeypatch.setattr(
            user_domain, "get_user_by_id", lambda user_id: _async(_user(id=user_id))
        )
        monkeypatch.setattr(
            user_domain,
            "get_team_by_id",
            lambda team_id: _async(_team(id=team_id, slug="executive")),
        )
        monkeypatch.setattr(user_domain, "update_user", AsyncMock())

        await admin_service.update_user(
            "app_user:aisha", team_id=None, role="team_manager", status=None
        )

        revoke_all_sessions.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_update_user_unknown_team_is_404(self, monkeypatch):
        from open_notebook.domain import user as user_domain

        monkeypatch.setattr(
            user_domain, "get_user_by_id", lambda user_id: _async(_user(id=user_id))
        )
        monkeypatch.setattr(user_domain, "get_team_by_id", lambda team_id: _async(None))

        with pytest.raises(NotFoundError):
            await admin_service.update_user(
                "app_user:aisha", team_id="team:unknown", role=None, status=None
            )


class TestTeams:
    def test_list_teams_contract_shape(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(
            admin_service,
            "list_teams",
            lambda: _async(
                [
                    {
                        "id": "team:finance",
                        "slug": "finance",
                        "name": "Finance",
                        "description": "Budgets and compliance",
                        "manager_id": "app_user:daniel",
                        "manager_name": "Daniel Fong",
                        "active": True,
                        "member_count": 5,
                        "notebook_count": 3,
                    }
                ]
            ),
        )
        response = client.get("/api/teams", cookies=admin_cookie)
        assert response.status_code == 200
        body = response.json()
        assert body[0]["member_count"] == 5
        assert body[0]["notebook_count"] == 3
        assert body[0]["manager_name"] == "Daniel Fong"

    def test_create_team_success(self, client, admin_cookie, monkeypatch):
        async def fake_create(admin, **kwargs):
            assert admin.role == "admin"
            assert kwargs == {"slug": "legal", "name": "Legal", "description": None}
            return {
                "id": "team:legal",
                "slug": "legal",
                "name": "Legal",
                "description": None,
                "manager_id": "",
                "manager_name": "",
                "active": True,
                "member_count": 0,
                "notebook_count": 0,
            }

        monkeypatch.setattr(admin_service, "create_team", fake_create)

        response = client.post(
            "/api/teams",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={"slug": "legal", "name": "Legal"},
        )
        assert response.status_code == 201
        assert response.json()["slug"] == "legal"

    def test_patch_team_archives(self, client, admin_cookie, monkeypatch):
        async def fake_update(team_id, **changes):
            assert team_id == "team:hr"
            assert changes == {"active": False}
            return {
                "id": "team:hr",
                "slug": "hr",
                "name": "HR",
                "description": None,
                "manager_id": "",
                "manager_name": "",
                "active": False,
                "member_count": 0,
                "notebook_count": 0,
            }

        monkeypatch.setattr(admin_service, "update_team", fake_update)

        response = client.patch(
            "/api/teams/team:hr",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
            json={"active": False},
        )
        assert response.status_code == 200
        assert response.json()["active"] is False

    @pytest.mark.asyncio
    async def test_create_team_service_rejects_duplicate_slug(self, monkeypatch):
        from open_notebook.domain import user as user_domain

        monkeypatch.setattr(
            user_domain, "get_team_by_slug", lambda slug: _async(_team())
        )

        with pytest.raises(InvalidInputError):
            await admin_service.create_team(
                admin=admin_service.CurrentUser(
                    id="app_user:alex",
                    email="alex@company.com",
                    organization_id="organization:default",
                    role="admin",
                ),
                slug="finance",
                name="Finance 2",
            )

    @pytest.mark.asyncio
    async def test_update_team_unknown_manager_is_404(self, monkeypatch):
        from open_notebook.domain import user as user_domain

        monkeypatch.setattr(
            user_domain, "get_team_by_id", lambda team_id: _async(_team())
        )
        monkeypatch.setattr(user_domain, "get_user_by_id", lambda user_id: _async(None))

        with pytest.raises(NotFoundError):
            await admin_service.update_team(
                "team:finance", manager_id="app_user:ghost"
            )
