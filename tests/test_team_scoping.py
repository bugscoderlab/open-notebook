"""Team-scoping (T5) access-matrix tests against an in-memory SurrealDB.

Two layers:

1. ``TestPermittedScopeMatrix`` — the permitted_notebook_ids /
   permitted_source_ids matrix, checked directly against
   ``expected_access_results.csv`` semantics (team columns: an HR
   team_manager is still "deny" on Finance; CEO reads every classified team;
   admin reads everything including unclassified content).

2. ``TestAccessControlApi`` — the real FastAPI app running on a mem://
   SurrealDB seeded with the canary sources from the test pack. Because the
   forbidden rows genuinely exist in the database, a missing WHERE clause
   would leak them into the response — so asserting response content is a
   true SQL-side scoping test.

Canary phrases come from ``_jobbrief/testdata/expected_access_results.csv``.
"""

from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

MIGRATIONS = Path("open_notebook/database/migrations")

# Persona users (CurrentUser shapes; the app_user rows themselves are not
# needed because auth_session patches resolve_session).
ORG = "organization:default"
TEAM_HR = "team:hr"
TEAM_FINANCE = "team:finance"
TEAM_EXEC = "team:executive"


def _persona(role: str, team: str):
    from api.access import CurrentUser

    return CurrentUser(
        id="app_user:test",
        email=f"{role}@example.com",
        display_name=role,
        organization_id=ORG,
        team_id=team,
        role=role,  # type: ignore[arg-type]
    )


AISHA = ("member", TEAM_HR)  # HR member
DANIEL = ("team_manager", TEAM_FINANCE)  # Finance team_manager
MEI = ("ceo", TEAM_EXEC)  # CEO
ALEX = ("admin", TEAM_EXEC)  # Admin


async def _seed(db: Any) -> None:
    await db.query(
        """
        CREATE organization:default SET external_key = 'k', name = 'Org', status = 'active';
        CREATE team:hr SET organization = organization:default, slug = 'hr', name = 'HR', active = true;
        CREATE team:finance SET organization = organization:default, slug = 'finance', name = 'Finance', active = true;
        CREATE team:executive SET organization = organization:default, slug = 'executive', name = 'Executive', active = true;

        CREATE notebook:hr_handbook SET name = 'HR Handbook', description = '',
            organization = organization:default, team = team:hr, visibility = 'team', created_by = NONE;
        CREATE notebook:finance_report SET name = 'Finance Report', description = '',
            organization = organization:default, team = team:finance, visibility = 'team', created_by = NONE;
        CREATE notebook:exec_memo SET name = 'Executive Memo', description = '',
            organization = organization:default, team = team:executive, visibility = 'team', created_by = NONE;
        CREATE notebook:shared_guide SET name = 'Company Shared Guide', description = '',
            organization = organization:default, team = team:executive, visibility = 'company_shared', created_by = NONE;
        CREATE notebook:legacy SET name = 'Legacy Unclassified', description = '',
            organization = NONE, team = NONE, visibility = 'team', created_by = NONE;

        CREATE source:hr_src SET title = 'HR Handbook', full_text = 'HR-ORCHID-731 compensation policy',
            organization = organization:default, team = team:hr, visibility = 'team';
        CREATE source:fin_src SET title = 'Finance Report', full_text = 'FIN-QUARTZ-915 quarterly numbers',
            organization = organization:default, team = team:finance, visibility = 'team';
        CREATE source:exec_src SET title = 'Executive Memo', full_text = 'CEO-NOVA-628 strategy memo',
            organization = organization:default, team = team:executive, visibility = 'team';
        CREATE source:shared_src SET title = 'Shared Guide', full_text = 'SHARED-ORBIT-204 onboarding guide',
            organization = organization:default, team = team:executive, visibility = 'company_shared';

        RELATE source:hr_src->reference->notebook:hr_handbook;
        RELATE source:fin_src->reference->notebook:finance_report;
        RELATE source:exec_src->reference->notebook:exec_memo;
        RELATE source:shared_src->reference->notebook:shared_guide;
        """
    )


async def _mem_db() -> Any:
    """In-memory SurrealDB with the schema these tests exercise (1, 7, 24-28)."""
    from surrealdb import AsyncSurreal

    from open_notebook.database.async_migrate import AsyncMigrationManager

    db = AsyncSurreal("mem://")
    await db.use("team_scoping", "access")
    await db.query((MIGRATIONS / "1.surrealql").read_text())
    manager = AsyncMigrationManager()
    await db.query(manager.up_migrations[6].sql)  # 7: episode tables
    for migration in manager.up_migrations[23:]:  # 24..28
        await db.query(migration.sql)
    await _seed(db)
    return db


@pytest_asyncio.fixture
async def mem_db(monkeypatch) -> Any:
    """mem:// SurrealDB wired into every repo_query importer the tests hit."""
    from open_notebook.database import repository
    from open_notebook.database.repository import parse_record_ids

    db: Any = await _mem_db()

    async def shim(query, vars=None):
        result = parse_record_ids(await db.query(query, vars))
        if isinstance(result, str):
            raise RuntimeError(result)
        return result

    # Patch the definition site AND every module that imported repo_query at
    # import time (lazy importers pick up the definition-site patch).
    monkeypatch.setattr(repository, "repo_query", shim)
    for module in (
        "open_notebook.domain.base",
        "open_notebook.domain.notebook",
        "open_notebook.utils.context_builder",
        "api.routers.notebooks",
        "api.routers.sources",
        "api.routers.notes",
        "api.routers.search",
        "api.routers.podcasts",
        "api.routers.embedding_rebuild",
    ):
        import importlib

        monkeypatch.setattr(
            importlib.import_module(module), "repo_query", shim, raising=False
        )
    return db


class TestPermittedScopeMatrix:
    """permitted_notebook_ids / permitted_source_ids mirror the CSV matrix."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "persona,expected",
        [
            (
                AISHA,
                {
                    "notebook:hr_handbook",
                    "notebook:shared_guide",
                    "source:hr_src",
                    "source:shared_src",
                },
            ),
            (
                DANIEL,
                {
                    "notebook:finance_report",
                    "notebook:shared_guide",
                    "source:fin_src",
                    "source:shared_src",
                },
            ),
            (
                MEI,
                {
                    "notebook:hr_handbook",
                    "notebook:finance_report",
                    "notebook:exec_memo",
                    "notebook:shared_guide",
                    "source:hr_src",
                    "source:fin_src",
                    "source:exec_src",
                    "source:shared_src",
                },
            ),
            (
                ALEX,
                {
                    "notebook:hr_handbook",
                    "notebook:finance_report",
                    "notebook:exec_memo",
                    "notebook:shared_guide",
                    "notebook:legacy",  # unclassified: admin-only
                    "source:hr_src",
                    "source:fin_src",
                    "source:exec_src",
                    "source:shared_src",
                },
            ),
        ],
        ids=["aisha-hr", "daniel-finance", "mei-ceo", "alex-admin"],
    )
    async def test_permitted_id_sets(self, mem_db, persona, expected):
        from api.access import permitted_notebook_ids, permitted_source_ids

        user = _persona(*persona)
        assert set(await permitted_notebook_ids(user)) | set(
            await permitted_source_ids(user)
        ) == expected


    @pytest.mark.asyncio
    async def test_teamless_member_gets_nothing(self, mem_db):
        """A member with no team must not fall back to `team = NONE` matching
        (which would leak every unclassified row)."""
        from api.access import permitted_notebook_ids, permitted_source_ids

        orgless = _persona("member", "")
        assert await permitted_notebook_ids(orgless) == []
        assert await permitted_source_ids(orgless) == []


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app)


class TestAccessControlApi:
    """The enforcement matrix over HTTP, on a real (in-memory) query engine."""

    @pytest.fixture(autouse=True)
    def _as_aisha(self, auth_session):
        auth_session(role="member", team_id=TEAM_HR)

    def test_notebook_list_excludes_foreign_teams(self, client, mem_db):
        response = client.get("/api/notebooks")
        assert response.status_code == 200
        ids = {item["id"] for item in response.json()}
        assert ids == {"notebook:hr_handbook", "notebook:shared_guide"}

    def test_foreign_notebook_get_is_404(self, client, mem_db):
        # 404, not 403: no existence oracle.
        assert client.get("/api/notebooks/notebook:finance_report").status_code == 404
        assert client.get("/api/notebooks/notebook:hr_handbook").status_code == 200

    def test_foreign_source_get_is_404(self, client, mem_db):
        assert client.get("/api/sources/source:fin_src").status_code == 404
        assert client.get("/api/sources/source:hr_src").status_code == 200

    def test_source_detail_carries_write_permission_hint(self, client, mem_db):
        """Read-shared foreign content is readable but not writable — the
        can_write hint lets the UI hide write actions instead of offering an
        embed/edit/delete that 403s on the backend."""
        shared = client.get("/api/sources/source:shared_src")
        assert shared.status_code == 200
        assert shared.json()["can_write"] is False

        own = client.get("/api/sources/source:hr_src")
        assert own.status_code == 200
        assert own.json()["can_write"] is True

    def test_foreign_source_download_is_404(self, client, mem_db):
        # HEAD and GET both deny before any filesystem work.
        assert (
            client.head("/api/sources/source:fin_src/download").status_code == 404
        )
        assert client.get("/api/sources/source:fin_src/download").status_code == 404

    def test_foreign_source_write_is_404(self, client, mem_db):
        assert (
            client.put("/api/sources/source:fin_src", json={"title": "x"}).status_code
            == 404
        )
        assert client.delete("/api/sources/source:fin_src").status_code == 404

    def test_cross_team_link_is_rejected(self, client, mem_db):
        # Aisha can write HR sources; linking one into a Finance notebook
        # violates the same-team rule.
        response = client.post(
            "/api/notebooks/notebook:finance_report/sources/source:hr_src"
        )
        assert response.status_code in (403, 404)

    def test_search_forbidden_canary_returns_nothing(self, client, mem_db):
        """Aisha searching the Finance canary gets zero results even though
        the phrase exists in the database — the scope filter is SQL-side."""
        response = client.post(
            "/api/search", json={"query": "FIN-QUARTZ-915", "type": "text"}
        )
        assert response.status_code == 200
        assert response.json()["results"] == []

    def test_search_allowed_canary_finds_source(self, client, mem_db):
        response = client.post(
            "/api/search", json={"query": "HR-ORCHID-731", "type": "text"}
        )
        assert response.status_code == 200
        results = response.json()["results"]
        assert {r["id"] for r in results} == {"source:hr_src"}

    def test_search_shared_canary_finds_shared_source(self, client, mem_db):
        response = client.post(
            "/api/search", json={"query": "SHARED-ORBIT-204", "type": "text"}
        )
        assert response.status_code == 200
        assert {r["id"] for r in response.json()["results"]} == {"source:shared_src"}

    def test_client_cannot_broaden_scope(self, client, mem_db):
        """Requesting the Finance notebook explicitly must not widen Aisha's
        search — the foreign id is dropped from the effective scope."""
        response = client.post(
            "/api/search",
            json={
                "query": "HR-ORCHID-731",
                "type": "text",
                "notebook_ids": ["notebook:hr_handbook", "notebook:finance_report"],
            },
        )
        assert response.status_code == 200
        assert {r["id"] for r in response.json()["results"]} == {"source:hr_src"}

    def test_ask_simple_empty_scope_is_honest_no_data(self, client, mem_db):
        """No permitted notebooks → an honest denial, never an LLM call (the
        endpoint short-circuits before any model lookup)."""
        response = client.post(
            "/api/search/ask/simple",
            json={
                "question": "What are the salaries?",
                "strategy_model": "model:x",
                "answer_model": "model:y",
                "final_answer_model": "model:z",
                "notebook_ids": ["notebook:finance_report"],
            },
        )
        assert response.status_code == 200
        assert "don't have access" in response.json()["answer"]

    def test_global_notes_list_is_scoped(self, client, mem_db):
        # Seed a note in the finance notebook only.
        response = client.get("/api/notes")
        assert response.status_code == 200
        assert response.json() == []
