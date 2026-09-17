"""Unit tests for the team-access bootstrap CLI (issue #4/T3, decision-log #4/#11).

Domain queries are mocked — no database needed. Live coverage (refusal on
re-run, persona reset) was verified against the local dev stack when T3
landed; these tests lock the contract in CI.
"""

import pytest

import open_notebook.admin as admin
from open_notebook.admin import BootstrapError, bootstrap, dev_seed
from open_notebook.domain.user import AppUser, Team


def _team(slug: str, team_id: str | None = None) -> Team:
    return Team(
        id=team_id or f"team:{slug}",
        organization_id="organization:default",
        slug=slug,
        name=slug.title(),
    )


class TestBootstrap:
    @pytest.mark.asyncio
    async def test_refuses_to_run_when_any_user_exists(self, monkeypatch):
        async def users_exist() -> int:
            return 3

        monkeypatch.setattr(admin, "count_users", users_exist)
        with pytest.raises(BootstrapError, match="already exist"):
            await bootstrap("admin@company.com", "pw")

    @pytest.mark.asyncio
    async def test_seeds_org_teams_and_first_admin(self, monkeypatch):
        monkeypatch.setattr(admin, "count_users", lambda: _async_value(0))
        monkeypatch.setattr(
            admin, "ensure_default_organization", lambda: _async_value("organization:default")
        )
        monkeypatch.setattr(
            admin,
            "ensure_team",
            lambda org, slug, name: _async_value(f"team:{slug}"),
        )
        created = {}

        async def fake_create_user(**kwargs):
            created.update(kwargs)
            return AppUser(
                id="app_user:new",
                organization_id=kwargs["organization_id"],
                email=kwargs["email"],
                password_hash=kwargs["password_hash"],
                display_name=kwargs["display_name"],
                team_id=kwargs["team_id"],
                role=kwargs["role"],
                status=kwargs["status"],
            )

        monkeypatch.setattr(admin, "create_user", fake_create_user)
        monkeypatch.setattr(admin, "hash_password", lambda pw: f"hashed:{pw}")

        result = await bootstrap("Admin@Company.com", "secret", "Boss")

        assert result["organization_id"] == "organization:default"
        assert result["admin_id"] == "app_user:new"
        assert created["email"] == "admin@company.com"  # lowercased
        assert created["password_hash"] == "hashed:secret"
        assert created["team_id"] == "team:executive"
        assert created["role"] == "admin"
        assert created["status"] == "active"

    @pytest.mark.asyncio
    async def test_is_idempotent_about_teams(self, monkeypatch):
        """Re-running after a partial seed only creates what's missing."""
        monkeypatch.setattr(admin, "count_users", lambda: _async_value(0))
        monkeypatch.setattr(
            admin, "ensure_default_organization", lambda: _async_value("organization:default")
        )
        seen_slugs = []

        async def ensure_team_once(org, slug, name):
            seen_slugs.append(slug)
            return f"team:{slug}"

        monkeypatch.setattr(admin, "ensure_team", ensure_team_once)
        monkeypatch.setattr(
            admin,
            "create_user",
            lambda **kw: _async_value(AppUser(id="app_user:x", **kw)),
        )
        monkeypatch.setattr(admin, "hash_password", lambda pw: pw)

        await bootstrap("a@b.c", "pw")
        assert sorted(seen_slugs) == ["executive", "finance", "hr"]


class TestDevSeed:
    @pytest.mark.asyncio
    async def test_creates_missing_personas(self, monkeypatch):
        monkeypatch.setattr(
            admin, "get_team_by_slug", lambda slug: _async_value(_team(slug))
        )
        monkeypatch.setattr(admin, "get_user_by_email", lambda email: _async_value(None))
        created = []

        async def fake_create_user(**kwargs):
            created.append(kwargs)
            return AppUser(id="app_user:x", **kwargs)

        monkeypatch.setattr(admin, "create_user", fake_create_user)
        monkeypatch.setattr(admin, "hash_password", lambda pw: f"hashed:{pw}")

        messages = await dev_seed()

        assert len(created) == 4
        assert {c["role"] for c in created} == {"member", "team_manager", "ceo", "admin"}
        assert all(c["password_hash"] == "hashed:password" for c in created)
        assert all(c["status"] == "active" for c in created)
        assert len(messages) == 4

    @pytest.mark.asyncio
    async def test_resets_existing_personas(self, monkeypatch):
        aisha = AppUser(
            id="app_user:aisha",
            organization_id="organization:default",
            email="aisha@company.com",
            password_hash="old",
            display_name="Aisha",
            team_id="team:hr",
            role="member",
            status="disabled",
        )
        monkeypatch.setattr(
            admin, "get_team_by_slug", lambda slug: _async_value(_team(slug))
        )
        async def find_user(email: str):
            return aisha if email == "aisha@company.com" else None

        monkeypatch.setattr(admin, "get_user_by_email", find_user)
        updated = []

        async def fake_update_user(user_id, **changes):
            updated.append((user_id, changes))

        created = []

        async def fake_create_user(**kwargs):
            created.append(kwargs)
            return AppUser(id="app_user:x", **kwargs)

        monkeypatch.setattr(admin, "update_user", fake_update_user)
        monkeypatch.setattr(admin, "create_user", fake_create_user)
        monkeypatch.setattr(admin, "hash_password", lambda pw: f"hashed:{pw}")

        messages = await dev_seed()

        # Aisha exists → reset via update, the other three are created.
        assert len(updated) == 1
        user_id, changes = updated[0]
        assert user_id == "app_user:aisha"
        assert changes["status"] == "active"
        assert changes["password_hash"] == "hashed:password"
        assert changes["team_id"] == "team:hr"
        assert len(created) == 3
        assert any("reset aisha@company.com" in m for m in messages)

    @pytest.mark.asyncio
    async def test_requires_bootstrap_first(self, monkeypatch):
        monkeypatch.setattr(admin, "get_team_by_slug", lambda slug: _async_value(None))
        with pytest.raises(BootstrapError, match="bootstrap"):
            await dev_seed()


def _async_value(value):
    async def coro():
        return value

    return coro()
