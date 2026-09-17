"""Tests for cookie-session auth (ADR-010, issue #4/T3).

Service-layer tests monkeypatch the domain queries; router tests run against
the full app with the service layer patched (no database needed — TestClient
without a context manager does not run the lifespan).
"""

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from api import auth_service, migration_service
from open_notebook.domain.user import AppUser, Team, UserSession
from open_notebook.exceptions import AuthenticationError


def _user(**overrides) -> AppUser:
    values = {
        "id": "app_user:aisha",
        "organization_id": "organization:default",
        "email": "aisha@company.com",
        "password_hash": "argon2id$fake",
        "display_name": "Aisha Hassan",
        "team_id": "team:hr",
        "role": "member",
        "status": "active",
    }
    values.update(overrides)
    return AppUser(**values)


def _session(**overrides: Any) -> UserSession:
    values: Dict[str, Any] = {
        "id": "user_session:1",
        "user_id": "app_user:aisha",
        "token_hash": "hash",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        "last_seen_at": None,
        "revoked_at": None,
    }
    values.update(overrides)
    return UserSession(**values)


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    auth_service.login_rate_limiter.reset()
    yield
    auth_service.login_rate_limiter.reset()


class TestSharedPasswordMiddlewareRemoved:
    def test_no_password_auth_middleware_registered(self, client):
        """The old shared-password middleware is gone (acceptance criterion)."""
        middleware_names = [
            getattr(m, "cls", m).__name__ if not isinstance(m, type) else m.__name__
            for m in client.app.user_middleware
        ]
        assert "PasswordAuthMiddleware" not in middleware_names


class TestLogin:
    def test_login_success_sets_session_and_csrf_cookies(self, client, monkeypatch):
        async def fake_authenticate(email, password):
            return _user()

        monkeypatch.setattr(auth_service, "authenticate_user", fake_authenticate)
        captured = {}

        async def fake_issue_session(user_id):
            captured["user_id"] = user_id
            return ("opaque-token", "csrf-token", datetime.now(timezone.utc))

        monkeypatch.setattr(auth_service, "issue_session", fake_issue_session)
        # T6: member sign-in is gated on the classification pass — treat the
        # organization as classified so this cookie-issuance test stays focused.
        monkeypatch.setattr(
            migration_service, "member_login_allowed", lambda org_id: _async(True)
        )

        response = client.post(
            "/api/auth/login",
            json={"email": "aisha@company.com", "password": "password"},
        )

        assert response.status_code == 204
        assert captured["user_id"] == "app_user:aisha"
        set_cookies = response.headers.get_list("set-cookie")
        assert len(set_cookies) == 2
        session_cookie = next(
            c for c in set_cookies if c.startswith("open_notebook_session=")
        )
        csrf_cookie = next(c for c in set_cookies if c.startswith("open_notebook_csrf="))
        assert "opaque-token" in session_cookie
        assert "HttpOnly" in session_cookie
        assert "SameSite=Lax" in session_cookie
        assert "Path=/" in session_cookie
        assert "csrf-token" in csrf_cookie
        assert "HttpOnly" not in csrf_cookie  # frontend must read it

    def test_login_invalid_credentials_returns_generic_401(self, client, monkeypatch):
        async def fake_authenticate(email, password):
            raise AuthenticationError(auth_service.INVALID_CREDENTIALS)

        monkeypatch.setattr(auth_service, "authenticate_user", fake_authenticate)

        response = client.post(
            "/api/auth/login",
            json={"email": "aisha@company.com", "password": "wrong"},
        )

        assert response.status_code == 401
        assert response.json() == {"detail": auth_service.INVALID_CREDENTIALS}

    def test_login_rate_limited_after_five_failures(self, client, monkeypatch):
        async def fake_authenticate(email, password):
            raise AuthenticationError(auth_service.INVALID_CREDENTIALS)

        monkeypatch.setattr(auth_service, "authenticate_user", fake_authenticate)

        for _ in range(5):
            response = client.post(
                "/api/auth/login",
                json={"email": "aisha@company.com", "password": "wrong"},
            )
            assert response.status_code == 401

        response = client.post(
            "/api/auth/login",
            json={"email": "aisha@company.com", "password": "password"},
        )
        assert response.status_code == 429
        assert response.json() == {"detail": auth_service.RATE_LIMITED}


class TestAuthenticateUser:
    @pytest.mark.asyncio
    async def test_unknown_email_raises_generic_error(self, monkeypatch):
        async def no_user(email):
            return None

        monkeypatch.setattr(auth_service, "get_user_by_email", no_user)

        with pytest.raises(AuthenticationError) as excinfo:
            await auth_service.authenticate_user("nobody@company.com", "password")
        assert str(excinfo.value) == auth_service.INVALID_CREDENTIALS

    @pytest.mark.asyncio
    async def test_wrong_password_raises_generic_error(self, monkeypatch):
        async def found_user(email):
            return _user()

        monkeypatch.setattr(auth_service, "get_user_by_email", found_user)
        monkeypatch.setattr(auth_service, "verify_password", lambda pw, h: False)

        with pytest.raises(AuthenticationError) as excinfo:
            await auth_service.authenticate_user("aisha@company.com", "wrong")
        assert str(excinfo.value) == auth_service.INVALID_CREDENTIALS

    @pytest.mark.asyncio
    async def test_disabled_user_cannot_authenticate(self, monkeypatch):
        async def found_user(email):
            return _user(status="disabled")

        monkeypatch.setattr(auth_service, "get_user_by_email", found_user)
        monkeypatch.setattr(auth_service, "verify_password", lambda pw, h: True)

        with pytest.raises(AuthenticationError):
            await auth_service.authenticate_user("aisha@company.com", "password")

    @pytest.mark.asyncio
    async def test_active_user_with_correct_password_authenticates(self, monkeypatch):
        async def found_user(email):
            return _user(status="invited")

        monkeypatch.setattr(auth_service, "get_user_by_email", found_user)
        monkeypatch.setattr(auth_service, "verify_password", lambda pw, h: True)

        user = await auth_service.authenticate_user("aisha@company.com", "password")
        assert user.id == "app_user:aisha"


class TestSessionLifecycle:
    @pytest.mark.asyncio
    async def test_issue_session_stores_only_sha256_hash(self, monkeypatch):
        captured = {}

        async def fake_create_user_session(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(auth_service, "create_user_session", fake_create_user_session)

        token, csrf_token, expires_at = await auth_service.issue_session("app_user:aisha")

        assert captured["user_id"] == "app_user:aisha"
        assert captured["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
        assert captured["token_hash"] != token
        assert token not in str(captured.values())
        assert csrf_token and csrf_token != token
        assert expires_at > datetime.now(timezone.utc)

    @pytest.mark.asyncio
    async def test_resolve_session_returns_none_without_token(self):
        assert await auth_service.resolve_session(None) is None
        assert await auth_service.resolve_session("") is None

    @pytest.mark.asyncio
    async def test_resolve_session_unknown_token_returns_none(self, monkeypatch):
        async def no_session(token_hash):
            return None

        monkeypatch.setattr(auth_service, "get_session_by_token_hash", no_session)
        assert await auth_service.resolve_session("opaque") is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "session_kwargs",
        [
            {"revoked_at": datetime.now(timezone.utc)},
            {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
            {"expires_at": None},
        ],
        ids=["revoked", "expired", "no-expiry"],
    )
    async def test_resolve_session_rejects_dead_sessions(self, monkeypatch, session_kwargs):
        async def found_session(token_hash):
            return _session(**session_kwargs)

        monkeypatch.setattr(auth_service, "get_session_by_token_hash", found_session)
        assert await auth_service.resolve_session("opaque") is None

    @pytest.mark.asyncio
    async def test_resolve_session_rejects_disabled_user(self, monkeypatch):
        monkeypatch.setattr(
            auth_service,
            "get_session_by_token_hash",
            lambda token_hash: _async(_session()),
        )
        monkeypatch.setattr(
            auth_service,
            "get_user_by_id",
            lambda user_id: _async(_user(status="disabled")),
        )
        assert await auth_service.resolve_session("opaque") is None

    @pytest.mark.asyncio
    async def test_resolve_session_valid_bumps_activity(self, monkeypatch):
        touch_session = AsyncMock()
        touch_user_activity = AsyncMock()
        monkeypatch.setattr(auth_service, "touch_session", touch_session)
        monkeypatch.setattr(auth_service, "touch_user_activity", touch_user_activity)
        monkeypatch.setattr(
            auth_service,
            "get_session_by_token_hash",
            lambda token_hash: _async(_session()),
        )
        monkeypatch.setattr(
            auth_service,
            "get_user_by_id",
            lambda user_id: _async(_user()),
        )

        resolved = await auth_service.resolve_session("opaque")

        assert resolved is not None
        user, session = resolved
        assert user.id == "app_user:aisha"
        assert session.id == "user_session:1"
        touch_session.assert_awaited_once()
        touch_user_activity.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_session_token_revokes_by_hash(self, monkeypatch):
        revoked = {}
        real_session = _session()

        async def fake_get_session(token_hash):
            assert token_hash == hashlib.sha256(b"opaque").hexdigest()
            return real_session

        async def fake_revoke(session_id):
            revoked["id"] = session_id

        monkeypatch.setattr(auth_service, "get_session_by_token_hash", fake_get_session)
        monkeypatch.setattr(auth_service, "revoke_session", fake_revoke)

        await auth_service.revoke_session_token("opaque")
        assert revoked == {"id": "user_session:1"}

    @pytest.mark.asyncio
    async def test_revoke_unknown_token_is_noop(self, monkeypatch):
        async def no_session(token_hash):
            return None

        monkeypatch.setattr(auth_service, "get_session_by_token_hash", no_session)
        await auth_service.revoke_session_token("opaque")  # must not raise


def _async(value):
    async def coro():
        return value

    return coro()


class TestLogout:
    def test_logout_without_csrf_header_is_forbidden(self, client, monkeypatch):
        revoked = AsyncMock()
        monkeypatch.setattr(auth_service, "revoke_session_token", revoked)

        response = client.post(
            "/api/auth/logout",
            cookies={auth_service.SESSION_COOKIE: "opaque"},
        )

        assert response.status_code == 403
        revoked.assert_not_awaited()

    def test_logout_with_csrf_header_revokes_and_clears_cookies(self, client, monkeypatch):
        revoked = AsyncMock()
        monkeypatch.setattr(auth_service, "revoke_session_token", revoked)

        response = client.post(
            "/api/auth/logout",
            cookies={
                auth_service.SESSION_COOKIE: "opaque",
                auth_service.CSRF_COOKIE: "csrf-token",
            },
            headers={auth_service.CSRF_HEADER: "csrf-token"},
        )

        assert response.status_code == 204
        assert revoked.await_count == 1
        set_cookies = response.headers.get_list("set-cookie")
        cleared = [c for c in set_cookies if "Max-Age=0" in c]
        assert any(c.startswith("open_notebook_session=") for c in cleared)
        assert any(c.startswith("open_notebook_csrf=") for c in cleared)


class TestMe:
    def test_me_without_session_is_401(self, client):
        response = client.get("/api/auth/me")
        assert response.status_code == 401

    def test_me_returns_frozen_contract_shape(self, client, monkeypatch):
        async def fake_resolve(token):
            assert token == "opaque"
            return _user(), _session()

        monkeypatch.setattr(auth_service, "resolve_session", fake_resolve)

        async def fake_team(team_id):
            return Team(
                id="team:hr",
                organization_id="organization:default",
                slug="hr",
                name="HR",
            )

        monkeypatch.setattr("api.routers.auth.get_team_by_id", fake_team)

        response = client.get(
            "/api/auth/me",
            cookies={auth_service.SESSION_COOKIE: "opaque"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body == {
            "id": "app_user:aisha",
            "email": "aisha@company.com",
            "display_name": "Aisha Hassan",
            "role": "member",
            "team": {"id": "team:hr", "slug": "hr", "name": "HR"},
        }
        assert "password_hash" not in body

    def test_me_never_serializes_password_hash(self, client, monkeypatch):
        async def fake_resolve(token):
            return _user(), _session()

        monkeypatch.setattr(auth_service, "resolve_session", fake_resolve)
        monkeypatch.setattr(
            "api.routers.auth.get_team_by_id", lambda team_id: _async(None)
        )

        response = client.get(
            "/api/auth/me",
            cookies={auth_service.SESSION_COOKIE: "opaque"},
        )

        assert response.status_code == 200
        assert "argon2id$fake" not in response.text
        assert response.json()["team"] is None


class TestAuthStatus:
    def test_status_disabled_when_no_users_exist(self, client, monkeypatch):
        monkeypatch.setattr("api.routers.auth.count_users", lambda: _async(0))
        response = client.get("/api/auth/status")
        assert response.status_code == 200
        assert response.json()["auth_enabled"] is False

    def test_status_enabled_once_users_exist(self, client, monkeypatch):
        monkeypatch.setattr("api.routers.auth.count_users", lambda: _async(3))
        response = client.get("/api/auth/status")
        assert response.status_code == 200
        assert response.json()["auth_enabled"] is True


class TestLoginRateLimiter:
    def test_success_clears_failures(self):
        limiter = auth_service.LoginRateLimiter(max_attempts=2, window_seconds=60)
        limiter.record_failure("ip", "a@b.c")
        limiter.check("ip", "a@b.c")  # 1 failure: under budget
        limiter.record_success("ip", "a@b.c")
        limiter.record_failure("ip", "a@b.c")
        limiter.check("ip", "a@b.c")  # budget reset by the success
        limiter.record_failure("ip", "a@b.c")
        with pytest.raises(Exception):
            limiter.check("ip", "a@b.c")  # now over budget

    def test_window_expiry_resets_budget(self):
        limiter = auth_service.LoginRateLimiter(max_attempts=1, window_seconds=10)
        limiter.record_failure("ip", "a@b.c")
        with pytest.raises(Exception):
            limiter.check("ip", "a@b.c")
        # simulate the window elapsing
        key = ("ip", "a@b.c")
        start, count = limiter._failures[key]
        limiter._failures[key] = (start - 11, count)
        limiter.check("ip", "a@b.c")

    def test_keys_are_per_email(self):
        limiter = auth_service.LoginRateLimiter(max_attempts=1, window_seconds=60)
        limiter.record_failure("ip", "a@b.c")
        limiter.check("ip", "other@b.c")  # different email, same ip: unaffected
