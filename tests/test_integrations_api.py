"""Unit tests for the messenger integrations linking spine (T1, #47).

Hermetic: DB-touching helpers in the router module are monkeypatched, so no
SurrealDB is needed. Covers both audiences — cookie-session web UI endpoints
(auth + CSRF + ownership) and internal-token gateway endpoints (claim
lifecycle, idempotency, foreign-identity rejection, fail-closed auth).

Prior art: tests/test_chat_routers_characterization.py (TestClient + auth_session
+ monkeypatched seams), tests/test_admin_users_teams_api.py (CSRF pattern).
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from open_notebook.domain.integration_link import IntegrationLink
from open_notebook.exceptions import NotFoundError

SESSION_COOKIE = "open_notebook_session"
CSRF_COOKIE = "csrf-cookie"
CSRF_HEADERS = {"x-csrf-token": CSRF_COOKIE}
INTERNAL_TOKEN = "test-internal-token"


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _internal_token_env(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_INTERNAL_TOKEN", INTERNAL_TOKEN)
    from api import internal_auth

    internal_auth.reset_internal_token_cache()


def _link(**overrides) -> IntegrationLink:
    defaults = dict(
        id="integration_link:t1",
        platform="telegram",
        external_id="@zoran",
        user_id="app_user:test",
        code="123456",
        status="active",
        created=datetime(2026, 9, 20, tzinfo=timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
    )
    defaults.update(overrides)
    return IntegrationLink(**defaults)


class _StubLink:
    """Attribute-bag stand-in for IntegrationLink where the route calls
    save()/delete() — pydantic models don't accept per-instance method
    overrides, so tests that assert those calls use this instead."""

    id: str = ""
    platform: str = ""
    external_id: Optional[str] = None
    user_id: Optional[str] = None
    code: Optional[str] = None
    status: str = ""
    expires_at: Optional[datetime] = None
    save_calls: int = 0
    deleted: bool = False

    def __init__(self, **fields):
        self.__dict__.update(fields)

    async def save(self):
        self.save_calls += 1

    async def delete(self):
        self.deleted = True


def _auth_cookies() -> dict:
    return {SESSION_COOKIE: "opaque", "open_notebook_csrf": CSRF_COOKIE}


def _internal_headers() -> dict:
    return {"Authorization": f"Internal {INTERNAL_TOKEN}"}


# ---------------------------------------------------------------------------
# Web UI: create linking code
# ---------------------------------------------------------------------------


class TestCreateLinkingCode:
    def test_unauthenticated_is_401(self, client):
        # CSRF gate runs first: an unauthenticated request still needs a
        # matching CSRF cookie+header to reach the auth check.
        response = client.post(
            "/api/integrations/link",
            json={"platform": "telegram"},
            cookies={"open_notebook_csrf": CSRF_COOKIE},
            headers=CSRF_HEADERS,
        )
        assert response.status_code == 401

    def test_unauthenticated_without_csrf_is_403(self, client):
        response = client.post("/api/integrations/link", json={"platform": "telegram"})
        assert response.status_code == 403

    def test_without_csrf_is_403(self, client, auth_session):
        auth_session()
        response = client.post(
            "/api/integrations/link",
            json={"platform": "telegram"},
            cookies={SESSION_COOKIE: "opaque"},
        )
        assert response.status_code == 403

    def test_creates_pending_code(self, client, auth_session, monkeypatch):
        auth_session()
        fake = _link(status="pending", external_id=None)
        create = AsyncMock(return_value=fake)
        monkeypatch.setattr("api.integrations_service.create_pending_link", create)

        response = client.post(
            "/api/integrations/link",
            json={"platform": "telegram"},
            cookies=_auth_cookies(),
            headers=CSRF_HEADERS,
        )

        assert response.status_code == 200
        body = response.json()
        assert body["platform"] == "telegram"
        assert body["code"] == "123456"
        assert "expires_at" in body
        create.assert_awaited_once()

    def test_invalid_platform_is_422(self, client, auth_session):
        auth_session()
        response = client.post(
            "/api/integrations/link",
            json={"platform": "signal"},
            cookies=_auth_cookies(),
            headers=CSRF_HEADERS,
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Web UI: list + delete
# ---------------------------------------------------------------------------


class TestListLinks:
    def test_unauthenticated_is_401(self, client):
        response = client.get("/api/integrations/links")
        assert response.status_code == 401

    def test_lists_only_caller_links_with_masked_identity(
        self, client, auth_session, monkeypatch
    ):
        auth_session()
        monkeypatch.setattr(
            "api.integrations_service.list_active_links",
            AsyncMock(
                return_value=[
                    _link(id="integration_link:a", platform="telegram", external_id="@zoran"),
                    _link(
                        id="integration_link:b",
                        platform="whatsapp",
                        external_id="15551234567",
                    ),
                ]
            ),
        )

        response = client.get("/api/integrations/links", cookies=_auth_cookies())

        assert response.status_code == 200
        body = response.json()
        assert body[0]["identity"] == "@zoran"
        assert body[1]["identity"].startswith("15")
        assert body[1]["identity"].endswith("67")
        assert "•" in body[1]["identity"]
        assert all(item["id"] for item in body)
        assert all("linked_at" in item for item in body)


class TestDeleteLink:
    def test_unauthenticated_is_401(self, client):
        response = client.delete(
            "/api/integrations/links/integration_link:t1",
            cookies={"open_notebook_csrf": CSRF_COOKIE},
            headers=CSRF_HEADERS,
        )
        assert response.status_code == 401

    def test_without_csrf_is_403(self, client, auth_session):
        auth_session()
        response = client.delete(
            "/api/integrations/links/integration_link:t1",
            cookies={SESSION_COOKIE: "opaque"},
        )
        assert response.status_code == 403

    def test_foreign_or_missing_link_is_404(self, client, auth_session, monkeypatch):
        auth_session()

        async def _raise(_link_id, _user):
            raise NotFoundError("Integration link not found")

        monkeypatch.setattr("api.integrations_service.get_owned_link", _raise)
        response = client.delete(
            "/api/integrations/links/integration_link:foreign",
            cookies=_auth_cookies(),
            headers=CSRF_HEADERS,
        )
        assert response.status_code == 404

    def test_owner_can_unlink(self, client, auth_session, monkeypatch):
        auth_session()
        fake = _StubLink(
            id="integration_link:t1",
            platform="telegram",
            external_id="@zoran",
            user_id="app_user:test",
        )
        monkeypatch.setattr(
            "api.integrations_service.get_owned_link", AsyncMock(return_value=fake)
        )

        response = client.delete(
            "/api/integrations/links/integration_link:t1",
            cookies=_auth_cookies(),
            headers=CSRF_HEADERS,
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert fake.deleted is True


# ---------------------------------------------------------------------------
# Gateway: internal-token gate
# ---------------------------------------------------------------------------


class TestInternalAuth:
    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer whatever"},
            {"Authorization": "Internal wrong-token"},
            {"Authorization": "Internal"},
        ],
    )
    def test_claim_rejects_bad_auth(self, client, headers):
        response = client.post(
            "/api/integrations/claim",
            json={"platform": "telegram", "external_id": "42", "code": "123456"},
            headers=headers,
        )
        assert response.status_code == 401

    def test_status_rejects_bad_auth(self, client):
        assert client.get("/api/integrations/status").status_code == 401
        assert (
            client.get(
                "/api/integrations/status",
                headers={"Authorization": "Internal wrong"},
            ).status_code
            == 401
        )

    def test_status_ok_with_token(self, client):
        response = client.get("/api/integrations/status", headers=_internal_headers())
        assert response.status_code == 200
        assert response.json()["ok"] is True


# ---------------------------------------------------------------------------
# Gateway: claim lifecycle
# ---------------------------------------------------------------------------


class TestClaim:
    def _claim(self, client, **body_overrides):
        body = {"platform": "telegram", "external_id": "4242", "code": "123456"}
        body.update(body_overrides)
        return client.post(
            "/api/integrations/claim", json=body, headers=_internal_headers()
        )

    def test_service_error_surfaces_as_400(self, client, monkeypatch):
        from open_notebook.exceptions import InvalidInputError

        claim = AsyncMock(side_effect=InvalidInputError("Invalid or expired linking code"))
        monkeypatch.setattr("api.integrations_service.claim_link", claim)

        response = self._claim(client)

        assert response.status_code == 400
        assert "Invalid or expired" in response.json()["detail"]
        claim.assert_awaited_once_with("telegram", "4242", "123456")

    def test_foreign_identity_is_rejected(self, client, monkeypatch):
        from open_notebook.exceptions import InvalidInputError

        claim = AsyncMock(
            side_effect=InvalidInputError(
                "This chat identity is already linked to a different account"
            )
        )
        monkeypatch.setattr("api.integrations_service.claim_link", claim)

        response = self._claim(client, external_id="4242")

        assert response.status_code == 400
        assert "different account" in response.json()["detail"]

    def test_happy_path_returns_user_email(self, client, monkeypatch):
        fake_user = type("User", (), {"id": "app_user:test", "email": "test@example.com"})()
        claim = AsyncMock(return_value=(fake_user, "linked"))
        monkeypatch.setattr("api.integrations_service.claim_link", claim)

        response = self._claim(client, external_id="4242")

        assert response.status_code == 200
        assert response.json() == {
            "success": True,
            "message": "Linked",
            "email": "test@example.com",
        }

    def test_idempotent_reclaim(self, client, monkeypatch):
        fake_user = type("User", (), {"id": "app_user:test", "email": "test@example.com"})()
        claim = AsyncMock(return_value=(fake_user, "already-linked"))
        monkeypatch.setattr("api.integrations_service.claim_link", claim)

        response = self._claim(client, external_id="4242")

        assert response.status_code == 200
        assert response.json()["message"] == "Already linked"


# ---------------------------------------------------------------------------
# Service layer: claim lifecycle ordering and masking (mocked repo)
# ---------------------------------------------------------------------------


class _RepoStub:
    """Scripted repo_query for the service's claim flow, in call order."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    async def __call__(self, query, params=None):
        self.calls.append(query.strip().splitlines()[0])
        if not self._results:
            raise AssertionError("repo_query called more times than scripted")
        return self._results.pop(0)


def _user(**overrides):
    defaults = dict(
        id="app_user:test",
        email="test@example.com",
        display_name="Test User",
        organization_id="organization:default",
        team_id="team:hr",
        role="member",
        status="active",
    )
    defaults.update(overrides)
    return type("User", (), defaults)()


class TestClaimService:
    async def _claim(self, monkeypatch, repo_results):
        repo = _RepoStub(repo_results)
        monkeypatch.setattr("api.integrations_service.repo_query", repo)
        repo_update = AsyncMock(return_value=[{"id": "integration_link:t1"}])
        # ObjectModel.save() routes through repository.repo_update, which has
        # its own module-global repo_query — patch the seam it actually calls.
        monkeypatch.setattr("open_notebook.domain.base.repo_update", repo_update)
        monkeypatch.setattr(
            "api.integrations_service.get_user_by_id", AsyncMock(return_value=_user())
        )
        from api import integrations_service

        return integrations_service, repo, repo_update

    @pytest.mark.asyncio
    async def test_expired_code_consumed_before_activation(
        self, monkeypatch
    ):
        service, repo, repo_update = await self._claim(
            monkeypatch,
            [
                # find_pending_by_code: already expired
                [
                    {
                        "id": "integration_link:t1",
                        "platform": "telegram",
                        "user_id": "app_user:test",
                        "status": "pending",
                        "expires_at": (
                            datetime.now(timezone.utc) - timedelta(minutes=1)
                        ).isoformat(),
                    }
                ],
            ],
        )
        from open_notebook.exceptions import InvalidInputError

        with pytest.raises(InvalidInputError, match="expired"):
            await service.claim_link("telegram", "4242", "123456")
        # Exactly one repo call (the pending lookup) — the activation UPDATE
        # never reached the query stub (it would have raised "called more
        # times than scripted"). The only write was the expiry mark via
        # repo_update.
        assert len(repo.calls) == 1
        repo_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_atomic_activation_wins_and_binds(self, monkeypatch):
        service, repo, repo_update = await self._claim(
            monkeypatch,
            [
                # find_pending_by_code
                [{"id": "integration_link:t1", "platform": "telegram", "user_id": "app_user:test", "status": "pending", "expires_at": "2030-01-01T00:00:00Z"}],
                # find_active_by_external_id
                [],
                # conditional UPDATE returns the activated row
                [{"id": "integration_link:t1", "platform": "telegram", "user_id": "app_user:test", "status": "active", "external_id": "4242"}],
            ],
        )
        user, outcome = await service.claim_link("telegram", "4242", "123456")
        assert outcome == "linked"
        assert user.email == "test@example.com"
        # The activation step was the conditional UPDATE, not a read-modify-write.
        assert "UPDATE integration_link" in repo.calls[2]
        repo_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_lost_race_falls_back_to_identity_read(self, monkeypatch):
        service, _repo, _repo_update = await self._claim(
            monkeypatch,
            [
                # find_pending_by_code
                [{"id": "integration_link:t1", "platform": "telegram", "user_id": "app_user:test", "status": "pending", "expires_at": "2030-01-01T00:00:00Z"}],
                # find_active_by_external_id (pre-check)
                [],
                # conditional UPDATE matched nothing (raced)
                [],
                # re-read identity: bound to the same user concurrently
                [{"id": "integration_link:t2", "platform": "telegram", "user_id": "app_user:test", "status": "active", "external_id": "4242"}],
            ],
        )
        user, outcome = await service.claim_link("telegram", "4242", "123456")
        assert outcome == "already-linked"

    @pytest.mark.asyncio
    async def test_foreign_identity_rejected_before_activation(self, monkeypatch):
        service, _repo, _repo_update = await self._claim(
            monkeypatch,
            [
                [{"id": "integration_link:t1", "platform": "telegram", "user_id": "app_user:test", "status": "pending", "expires_at": "2030-01-01T00:00:00Z"}],
                # identity already active under a DIFFERENT user
                [{"id": "integration_link:t2", "platform": "telegram", "user_id": "app_user:other", "status": "active", "external_id": "4242"}],
            ],
        )
        from open_notebook.exceptions import InvalidInputError

        with pytest.raises(InvalidInputError, match="different account"):
            await service.claim_link("telegram", "4242", "123456")


class TestMasking:
    def test_telegram_handle_shown(self):
        from api.integrations_service import mask_identity

        assert mask_identity("telegram", "@zoran") == "@zoran"

    def test_telegram_numeric_masked(self):
        from api.integrations_service import mask_identity

        assert mask_identity("telegram", "424242424") == "42•••••24"

    def test_whatsapp_masked(self):
        from api.integrations_service import mask_identity

        masked = mask_identity("whatsapp", "15551234567")
        assert masked.startswith("15") and masked.endswith("67")


# ---------------------------------------------------------------------------
# Internal token: file lifecycle (ADR-018)
# ---------------------------------------------------------------------------


class TestInternalToken:
    def test_generated_token_round_trips_through_file(self, monkeypatch, tmp_path):
        from api import internal_auth

        monkeypatch.delenv("OPEN_NOTEBOOK_INTERNAL_TOKEN", raising=False)
        monkeypatch.setattr(internal_auth, "_TOKEN_FILE", str(tmp_path / "internal-token"))

        first = internal_auth.resolve_internal_token()
        internal_auth.reset_internal_token_cache()
        second = internal_auth.resolve_internal_token()

        assert first == second and len(first) >= 32
        token_file = tmp_path / "internal-token"
        assert token_file.read_text() == first
        # Mode 0600 on POSIX.
        assert oct(token_file.stat().st_mode & 0o777) == "0o600"

    def test_env_wins_over_file(self, monkeypatch, tmp_path):
        from api import internal_auth

        monkeypatch.setenv("OPEN_NOTEBOOK_INTERNAL_TOKEN", "env-token")
        monkeypatch.setattr(internal_auth, "_TOKEN_FILE", str(tmp_path / "internal-token"))

        assert internal_auth.resolve_internal_token() == "env-token"
        assert not (tmp_path / "internal-token").exists()


# ---------------------------------------------------------------------------
# Message spine (T2): POST /integrations/message
# ---------------------------------------------------------------------------


class TestMessageRouting:
    def _message(self, client, text, **body_overrides):
        body = {"platform": "telegram", "external_id": "4242", "text": text}
        body.update(body_overrides)
        return client.post(
            "/api/integrations/message", json=body, headers=_internal_headers()
        )

    @pytest.fixture(autouse=True)
    def _reset_rate_limit(self):
        from api import integrations_service

        integrations_service.reset_rate_limit_state()
        yield
        integrations_service.reset_rate_limit_state()

    def _linked(self, monkeypatch):
        """Standard resolve_linked_user stub: active link + active user."""
        from api import integrations_service

        link = _StubLink(
            id="integration_link:t1",
            platform="telegram",
            external_id="4242",
            user_id="app_user:test",
            home_chat_session_id="home_chat_session:x",
        )
        user = _user()
        resolve = AsyncMock(return_value=(link, user))
        monkeypatch.setattr(integrations_service, "resolve_linked_user", resolve)
        return link

    def test_bad_token_is_401(self, client):
        response = client.post(
            "/api/integrations/message",
            json={"platform": "telegram", "external_id": "4242", "text": "hi"},
            headers={"Authorization": "Internal wrong"},
        )
        assert response.status_code == 401

    def test_unlinked_identity_is_404(self, client, monkeypatch):
        from api import integrations_service
        from open_notebook.exceptions import NotFoundError

        monkeypatch.setattr(
            integrations_service,
            "resolve_linked_user",
            AsyncMock(side_effect=NotFoundError("No integration link found")),
        )
        response = self._message(client, "hello")
        assert response.status_code == 404

    def test_disabled_user_is_403(self, client, monkeypatch):
        from api import integrations_service
        from open_notebook.exceptions import ForbiddenError

        monkeypatch.setattr(
            integrations_service,
            "resolve_linked_user",
            AsyncMock(side_effect=ForbiddenError("This account is no longer authorized")),
        )
        response = self._message(client, "hello")
        assert response.status_code == 403

    def test_rate_limit_blocks_second_message(self, client, monkeypatch):
        from api import integrations_service

        self._linked(monkeypatch)
        monkeypatch.setattr(
            integrations_service,
            "run_home_chat_ask",
            AsyncMock(return_value=("answer", [])),
        )

        first = self._message(client, "question one")
        second = self._message(client, "question two")

        assert first.status_code == 200
        assert second.status_code == 429
        assert "too quickly" in second.json()["detail"]

    def test_conversational_ask_returns_answer_and_suggestions(
        self, client, monkeypatch
    ):
        from api import integrations_service

        self._linked(monkeypatch)
        ask = AsyncMock(return_value=("The answer is 42.", ["Follow-up one?", "Follow-up two?"]))
        monkeypatch.setattr(integrations_service, "run_home_chat_ask", ask)

        response = self._message(client, "what is the meaning of life?")

        assert response.status_code == 200
        body = response.json()
        assert body["reply"] == "The answer is 42."
        assert body["suggestions"] == ["Follow-up one?", "Follow-up two?"]
        assert body["conversation_reset"] is False
        # The ask ran scoped to the linked user's permitted notebooks.
        call_user = ask.await_args.args[1]
        assert call_user.id == "app_user:test"

    def test_new_command_resets_conversation(self, client, monkeypatch):
        from api import integrations_service

        link = self._linked(monkeypatch)
        reset = AsyncMock(return_value="New conversation started.")
        monkeypatch.setattr(integrations_service, "reset_conversation", reset)

        response = self._message(client, "/new")

        assert response.status_code == 200
        assert response.json()["conversation_reset"] is True
        assert "New conversation" in response.json()["reply"]
        reset.assert_awaited_once_with(link)

    def test_search_command_formats_results(self, client, monkeypatch):
        from api import integrations_service

        self._linked(monkeypatch)
        search = AsyncMock(return_value="1. Report — HR (source)")
        monkeypatch.setattr(integrations_service, "run_search", search)

        response = self._message(client, "/search quarterly report")

        assert response.status_code == 200
        assert response.json()["reply"] == "1. Report — HR (source)"
        assert search.await_args.args[1] == "quarterly report"

    def test_search_command_without_query_shows_usage(self, client, monkeypatch):
        from api import integrations_service

        self._linked(monkeypatch)
        search = AsyncMock()
        monkeypatch.setattr(integrations_service, "run_search", search)

        response = self._message(client, "/search")

        assert response.status_code == 200
        assert "Usage" in response.json()["reply"]
        search.assert_not_awaited()

    def test_unlink_command_deletes_link(self, client, monkeypatch):

        link = self._linked(monkeypatch)

        response = self._message(client, "/unlink")

        assert response.status_code == 200
        assert response.json()["conversation_reset"] is True
        assert link.deleted is True

    def test_help_and_unknown_command_reply_with_help(self, client, monkeypatch):
        from api import integrations_service

        self._linked(monkeypatch)
        for text in ("/help", "/frobnicate"):
            response = self._message(client, text)
            assert response.status_code == 200
            assert "/new" in response.json()["reply"]
            assert "/search" in response.json()["reply"]
            integrations_service.reset_rate_limit_state()


class TestRunSearchService:
    """Canary: /search must query with exactly the caller's permitted scope —
    never the whole knowledge base (access.py contract)."""

    @pytest.fixture(autouse=True)
    def _stubs(self, monkeypatch):
        from api import integrations_service

        self.scope = AsyncMock(return_value=["notebook:hr"])
        monkeypatch.setattr(integrations_service, "effective_notebook_scope", self.scope)
        self.text_search = AsyncMock(return_value=[])
        monkeypatch.setattr(integrations_service, "text_search", self.text_search)
        self.user = type(
            "U", (), {"id": "app_user:test", "email": "t@e.c", "display_name": "T",
                       "organization_id": "organization:default", "team_id": "team:hr", "role": "member"}
        )()

    @pytest.mark.asyncio
    async def test_search_uses_permitted_scope_only(self):
        from api import integrations_service

        await integrations_service.run_search(self.user, "salaries")

        assert self.text_search.await_count == 1
        kwargs = self.text_search.await_args.kwargs
        assert kwargs["notebook_ids"] == ["notebook:hr"]

    @pytest.mark.asyncio
    async def test_empty_scope_never_reaches_search(self):
        from api import integrations_service

        self.scope.return_value = []
        reply = await integrations_service.run_search(self.user, "salaries")

        assert "don't have access" in reply
        self.text_search.assert_not_awaited()


class TestRunHomeChatAskService:
    @pytest.mark.asyncio
    async def test_missing_default_model_fails_closed(self, monkeypatch):
        from api import integrations_service
        from open_notebook.exceptions import InvalidInputError

        monkeypatch.setattr(
            integrations_service, "_resolve_default_model", AsyncMock(return_value=None)
        )
        link = _StubLink(id="integration_link:t1", user_id="app_user:test")
        user = type("U", (), {"id": "app_user:test"})()

        with pytest.raises(InvalidInputError, match="default chat model"):
            await integrations_service.run_home_chat_ask(link, user, "hi")

    @pytest.mark.asyncio
    async def test_pipeline_error_degrades_to_friendly_reply(self, monkeypatch):
        from api import integrations_service

        monkeypatch.setattr(
            integrations_service,
            "_resolve_default_model",
            AsyncMock(return_value="model:1"),
        )
        monkeypatch.setattr(
            integrations_service,
            "effective_notebook_scope",
            AsyncMock(return_value=["notebook:hr"]),
        )
        monkeypatch.setattr(
            integrations_service,
            "_ensure_session",
            AsyncMock(return_value="home_chat_session:x"),
        )

        class _Boom:
            async def aget_state(self, config):
                return None

            def astream(self, *args, **kwargs):
                async def _gen():
                    raise RuntimeError("provider exploded")
                    yield  # pragma: no cover

                return _gen()

        monkeypatch.setattr(
            integrations_service,
            "get_home_chat_graph",
            AsyncMock(return_value=_Boom()),
        )

        link = _StubLink(id="integration_link:t1", user_id="app_user:test")
        user = type("U", (), {"id": "app_user:test"})()

        reply, suggestions = await integrations_service.run_home_chat_ask(
            link, user, "hi"
        )

        assert reply  # non-empty friendly message
        assert suggestions == []
