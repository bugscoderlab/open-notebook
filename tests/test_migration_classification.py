"""Tests for content classification + migration (issue #7/T6).

Layers:
- Pure decision functions (token extraction, source/notebook rules) —
  parametrized, including the real test-pack filenames.
- The service against a fake in-memory repo (no SurrealDB): classification
  pass, flags, completion, manual assignment, login gate, idempotency.
- The router over the full app with the service patched — no database.
- The login gate on POST /auth/login.

External behavior only: returned counts/shapes, HTTP status/body, and the
records the service writes.
"""

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from surrealdb import RecordID

import api.migration_service as migration_service
from api import auth_service
from api.access import CurrentUser
from open_notebook.domain.user import AppUser, Organization, Team
from open_notebook.exceptions import InvalidInputError, NotFoundError


def _rid(record_id: str) -> str:
    """Canonical SurrealDB id string (numeric ids round-trip as ⟨02⟩)."""
    return str(RecordID.parse(record_id))

CSRF_COOKIE = "csrf-cookie"
CSRF_HEADERS = {"x-csrf-token": CSRF_COOKIE}

ADMIN = CurrentUser(
    id="app_user:alex",
    email="alex@company.com",
    display_name="Alex Admin",
    organization_id="organization:default",
    team_id="team:executive",
    role="admin",
)


# ---------------------------------------------------------------------------
# Pure decision functions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,team_slug,visibility,ambiguous",
    [
        # The real test pack (acceptance criteria)
        ("02_hr_employee_handbook.pdf", "hr", "team", False),
        ("03_hr_recruitment_compensation.pdf", "hr", "team", False),
        ("04_finance_monthly_report.pdf", "finance", "team", False),
        ("05_finance_budget_forecast.pdf", "finance", "team", False),
        ("06_executive_strategy_memo.pdf", "executive", "team", False),
        ("01_company_shared_guide.pdf", None, "company_shared", False),
        ("00_access_control_test_guide.pdf", None, "company_shared", False),
        # Case-insensitivity + separator variants
        ("HR_Policies.pdf", "hr", "team", False),
        ("Finance-2026.pdf", "finance", "team", False),
        ("company-shared onboarding", None, "company_shared", False),
        ("Executive Briefing", "executive", "team", False),
        # Team token + company token → team owner, shared visibility
        ("hr_company_shared_guide.pdf", "hr", "company_shared", False),
        # Substring traps must not match
        ("quarterly_charts.pdf", None, "company_shared", False),
        ("refinance_plan.pdf", None, "company_shared", False),
        ("the_hr_handbook_finance_rules.pdf", None, "team", True),
    ],
)
def test_classify_source_title(title, team_slug, visibility, ambiguous):
    decision = migration_service.classify_source_title(title)
    assert decision.team_slug == team_slug
    assert decision.visibility == visibility
    assert decision.ambiguous is ambiguous


@pytest.mark.parametrize(
    "text,expected",
    [
        ("02_hr_employee_handbook.pdf", {"hr"}),
        ("HR Finance mixed.pdf", {"hr", "finance"}),
        ("company-shared guide", {"company_shared"}),
        ("nothing here", set()),
        ("", set()),
    ],
)
def test_extract_tokens(text, expected):
    assert migration_service.extract_tokens(text) == expected


@pytest.mark.parametrize(
    "teams,visibilities,team_slug,visibility,ambiguous",
    [
        (["hr"], ["team"], "hr", "team", False),
        (["hr", "hr"], ["team", "team"], "hr", "team", False),
        (["hr"], ["company_shared"], "hr", "company_shared", False),
        (["hr", "finance"], ["team", "team"], None, "team", True),
        ([], [], None, "company_shared", False),
        ([None, None], ["team", "team"], None, "company_shared", False),
    ],
)
def test_decide_notebook_team(teams, visibilities, team_slug, visibility, ambiguous):
    decision = migration_service.decide_notebook_team(teams, visibilities)
    assert decision.team_slug == team_slug
    assert decision.visibility == visibility
    assert decision.ambiguous is ambiguous


# ---------------------------------------------------------------------------
# Fake in-memory repo for the service layer
# ---------------------------------------------------------------------------


class FakeDB:
    """Minimal repo_query stand-in covering the migration service's queries."""

    def __init__(
        self,
        *,
        teams: List[Team],
        sources: Optional[List[Dict[str, Any]]] = None,
        notebooks: Optional[List[Dict[str, Any]]] = None,
        edges: Optional[List[tuple]] = None,
        org: Optional[Organization] = None,
    ):
        self.teams = {t.slug: t for t in teams}
        self.sources = {_rid(s["id"]): dict(s, id=_rid(s["id"])) for s in (sources or [])}
        self.notebooks = {
            _rid(n["id"]): dict(n, id=_rid(n["id"])) for n in (notebooks or [])
        }
        self.edges = [
            (_rid(sid), _rid(nid)) for sid, nid in (edges or [])
        ]  # (source_id, notebook_id)
        self.org = org or Organization(
            id="organization:default",
            external_key="org-open-notebook-default",
            name="Open Notebook",
            status="active",
        )
        self.update_calls: List[Dict[str, Any]] = []
        self.org_updates: List[Dict[str, Any]] = []

    def _table_for(self, record_id: str) -> Dict[str, Any]:
        if record_id.startswith("source:"):
            return self.sources[record_id]
        if record_id.startswith("notebook:"):
            return self.notebooks[record_id]
        raise KeyError(record_id)

    async def repo_query(self, query: str, params: Optional[Dict[str, Any]] = None):
        params = params or {}
        q = " ".join(query.split())
        if q == "SELECT * FROM source WHERE team IS NONE":
            return [dict(s) for s in self.sources.values() if s.get("team") is None]
        if q == "SELECT * FROM source WHERE team IS NOT NONE":
            return [dict(s) for s in self.sources.values() if s.get("team") is not None]
        if q == "SELECT * FROM notebook WHERE team IS NONE":
            return [dict(n) for n in self.notebooks.values() if n.get("team") is None]
        if q.startswith("SELECT in AS source FROM reference WHERE out = $id"):
            nb = str(params["id"])
            return [
                {"source": sid}
                for sid, nid in self.edges
                if nid == nb and sid in self.sources
            ]
        if q.startswith(
            "SELECT out.team AS team FROM reference WHERE in = $id AND out.team IS NOT NONE"
        ):
            sid = str(params["id"])
            return [
                {"team": self.notebooks[nid]["team"]}
                for s, nid in self.edges
                if s == sid and self.notebooks[nid].get("team") is not None
            ]
        if q == "SELECT * FROM source WHERE id IN $ids":
            return [dict(self.sources[str(i)]) for i in params["ids"]]
        if q == "SELECT * FROM $id":
            record_id = str(params["id"])
            table = (
                self.sources
                if record_id.startswith("source:")
                else self.notebooks
                if record_id.startswith("notebook:")
                else {}
            )
            return [dict(table[record_id])] if record_id in table else []
        if q == "UPDATE $id MERGE $data":
            record_id = str(params["id"])
            data = {
                key: (str(value) if value is not None else None)
                for key, value in params["data"].items()
            }
            self.update_calls.append({"id": record_id, "data": data})
            self._table_for(record_id).update(data)
            return []
        raise AssertionError(f"Unexpected query: {q}")

    # -- user_domain stand-ins -------------------------------------------

    async def list_teams(self) -> List[Team]:
        return list(self.teams.values())

    async def get_organization_by_id(self, org_id: str) -> Optional[Organization]:
        return self.org if str(self.org.id) == org_id else None  # type: ignore[return-value]

    async def update_organization(self, org_id: str, **changes: Any) -> None:
        self.org_updates.append(changes)
        for key, value in changes.items():
            setattr(self.org, key, value)

    async def get_team_by_id(self, team_id: str) -> Optional[Team]:
        for team in self.teams.values():
            if str(team.id) == team_id:
                return team
        return None


def _teams() -> List[Team]:
    return [
        Team(
            id="team:hr",
            organization_id="organization:default",
            slug="hr",
            name="HR",
        ),
        Team(
            id="team:finance",
            organization_id="organization:default",
            slug="finance",
            name="Finance",
        ),
        Team(
            id="team:executive",
            organization_id="organization:default",
            slug="executive",
            name="Executive",
        ),
    ]


def _source(source_id: str, title: str, **extra: Any) -> Dict[str, Any]:
    record = {"id": source_id, "title": title, "team": None, "visibility": "team"}
    record.update(extra)
    return record


def _notebook(notebook_id: str, name: str, **extra: Any) -> Dict[str, Any]:
    record = {"id": notebook_id, "name": name, "team": None, "visibility": "team"}
    record.update(extra)
    return record


@pytest.fixture
def fake_db(monkeypatch):
    """Wire a FakeDB into the service's repo + domain call sites."""

    def _install(db: FakeDB) -> FakeDB:
        monkeypatch.setattr(migration_service, "repo_query", db.repo_query)
        monkeypatch.setattr(
            migration_service.user_domain, "list_teams", db.list_teams
        )
        monkeypatch.setattr(
            migration_service.user_domain,
            "get_organization_by_id",
            db.get_organization_by_id,
        )
        monkeypatch.setattr(
            migration_service.user_domain,
            "update_organization",
            db.update_organization,
        )
        monkeypatch.setattr(
            migration_service.user_domain, "get_team_by_id", db.get_team_by_id
        )
        return db

    return _install


# ---------------------------------------------------------------------------
# Service: classification pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pass_classifies_test_pack_sources(fake_db):
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[
                _source("source:s02", "02_hr_employee_handbook.pdf"),
                _source("source:s03", "03_hr_recruitment_compensation.pdf"),
                _source("source:s04", "04_finance_monthly_report.pdf"),
                _source("source:s05", "05_finance_budget_forecast.pdf"),
                _source("source:s06", "06_executive_strategy_memo.pdf"),
                _source("source:s01", "01_company_shared_guide.pdf"),
                _source("source:s00", "00_access_control_test_guide.pdf"),
            ],
        )
    )
    summary = await migration_service.run_classification(ADMIN)

    assert summary["classified_sources"] == 7
    assert summary["flagged_sources"] == 0
    assert db.sources[_rid("source:s02")]["team"] == "team:hr"
    assert db.sources[_rid("source:s04")]["team"] == "team:finance"
    assert db.sources[_rid("source:s06")]["team"] == "team:executive"
    # No token → company-shared, owned by the Executive team (deviation default)
    assert db.sources[_rid("source:s00")]["visibility"] == "company_shared"
    assert db.sources[_rid("source:s00")]["team"] == "team:executive"
    assert db.sources[_rid("source:s01")]["visibility"] == "company_shared"
    # Every stamp carries the team's organization and the admin's id
    assert db.sources[_rid("source:s02")]["organization"] == "organization:default"
    assert db.sources[_rid("source:s02")]["created_by"] == "app_user:alex"
    # Nothing left to flag → completion stamped
    assert summary["completed"] is True
    assert db.org.classification_completed_at is not None


@pytest.mark.asyncio
async def test_pass_is_idempotent(fake_db):
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[_source("source:s02", "02_hr_employee_handbook.pdf")],
        )
    )
    first = await migration_service.run_classification(ADMIN)
    second = await migration_service.run_classification(ADMIN)

    assert first["classified_sources"] == 1
    assert second["classified_sources"] == 0
    assert second["flagged_sources"] == 0
    # The second pass never rewrites the first pass's stamps
    stamp_updates = [
        call for call in db.update_calls if "visibility" in call["data"]
    ]
    assert len(stamp_updates) == 1
    assert second["completed"] is True


@pytest.mark.asyncio
async def test_ambiguous_source_is_flagged_and_blocks_completion(fake_db):
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[_source("source:x", "hr_finance_mix.pdf")],
        )
    )
    summary = await migration_service.run_classification(ADMIN)

    assert summary["classified_sources"] == 0
    assert summary["flagged_sources"] == 1
    assert db.sources[_rid("source:x")]["team"] is None  # admin-only
    assert summary["completed"] is False
    assert db.org.classification_completed_at is None


@pytest.mark.asyncio
async def test_notebook_inherits_single_source_team(fake_db):
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[_source("source:s02", "02_hr_employee_handbook.pdf")],
            notebooks=[_notebook("notebook:n1", "HR handbook")],
            edges=[("source:s02", "notebook:n1")],
        )
    )
    summary = await migration_service.run_classification(ADMIN)

    assert summary["classified_notebooks"] == 1
    assert db.notebooks[_rid("notebook:n1")]["team"] == "team:hr"
    assert db.notebooks[_rid("notebook:n1")]["visibility"] == "team"


@pytest.mark.asyncio
async def test_notebook_inherits_company_shared_visibility(fake_db):
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[_source("source:s01", "01_company_shared_guide.pdf")],
            notebooks=[_notebook("notebook:n1", "Guide")],
            edges=[("source:s01", "notebook:n1")],
        )
    )
    await migration_service.run_classification(ADMIN)

    assert db.notebooks[_rid("notebook:n1")]["team"] == "team:executive"
    assert db.notebooks[_rid("notebook:n1")]["visibility"] == "company_shared"


@pytest.mark.asyncio
async def test_mixed_team_notebook_is_flagged_admin_only(fake_db):
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[
                _source("source:s02", "02_hr_employee_handbook.pdf"),
                _source("source:s04", "04_finance_monthly_report.pdf"),
            ],
            notebooks=[_notebook("notebook:n1", "Mixed")],
            edges=[("source:s02", "notebook:n1"), ("source:s04", "notebook:n1")],
        )
    )
    summary = await migration_service.run_classification(ADMIN)

    assert summary["classified_notebooks"] == 0
    assert summary["flagged_notebooks"] == 1
    assert db.notebooks[_rid("notebook:n1")]["team"] is None  # admin-only
    assert summary["completed"] is False


@pytest.mark.asyncio
async def test_cross_team_source_link_is_unclassified_and_flagged(fake_db):
    # Source token says finance, but the notebook it links to was already
    # stamped HR (post-T5 creation) — the classic pre-migration cross-link.
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[
                _source("source:s02", "02_hr_employee_handbook.pdf"),
                _source("source:s04", "04_finance_monthly_report.pdf"),
            ],
            notebooks=[_notebook("notebook:n1", "HR docs", team="team:hr")],
            edges=[
                ("source:s02", "notebook:n1"),
                ("source:s04", "notebook:n1"),
            ],
        )
    )
    await migration_service.run_classification(ADMIN)

    # HR source matches the notebook's team; the finance source conflicts
    # with every linked notebook's team → cross-team flag → source teamless.
    assert db.sources[_rid("source:s02")]["team"] == "team:hr"
    assert db.sources[_rid("source:s04")]["team"] is None
    assert db.notebooks[_rid("notebook:n1")]["team"] == "team:hr"

    # Second pass reaches the same end state (idempotent)
    second = await migration_service.run_classification(ADMIN)
    assert second["classified_sources"] == 1  # only source:s04 re-stamped
    assert db.sources[_rid("source:s04")]["team"] is None


@pytest.mark.asyncio
async def test_cross_team_flag_fires_on_any_mismatched_notebook(fake_db):
    # Same-team link rule: ONE matching notebook does not excuse a second
    # cross-team link — any mismatch flags the source.
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[_source("source:s02", "02_hr_employee_handbook.pdf")],
            notebooks=[
                _notebook("notebook:hr", "HR docs", team="team:hr"),
                _notebook("notebook:fin", "Finance docs", team="team:finance"),
            ],
            edges=[
                ("source:s02", "notebook:hr"),
                ("source:s02", "notebook:fin"),
            ],
        )
    )
    summary = await migration_service.run_classification(ADMIN)

    assert db.sources[_rid("source:s02")]["team"] is None
    assert summary["flagged_sources"] == 1
    assert summary["completed"] is False


@pytest.mark.asyncio
async def test_pass_requires_teams(fake_db):
    fake_db(FakeDB(teams=[]))
    with pytest.raises(InvalidInputError):
        await migration_service.run_classification(ADMIN)


# ---------------------------------------------------------------------------
# Service: flags listing, manual assignment, completion, login gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_flagged_items_report_reasons_and_team_names(fake_db):
    fake_db(
        FakeDB(
            teams=_teams(),
            sources=[
                _source("source:x", "hr_finance_mix.pdf"),
                _source("source:s02", "02_hr_employee_handbook.pdf"),
            ],
            notebooks=[
                _notebook("notebook:mixed", "Mixed"),
                ],
            edges=[("source:s02", "notebook:mixed")],
        )
    )
    items = await migration_service.list_flagged_items(ADMIN)

    assert [n["id"] for n in items["notebooks"]] == []
    assert {s["id"] for s in items["sources"]} == {"source:x"}


@pytest.mark.asyncio
async def test_flagged_items_show_mixed_notebook_with_linked_teams(fake_db):
    fake_db(
        FakeDB(
            teams=_teams(),
            sources=[
                _source("source:s02", "02_hr_employee_handbook.pdf"),
                _source("source:s04", "04_finance_monthly_report.pdf"),
            ],
            notebooks=[_notebook("notebook:mixed", "Mixed")],
            edges=[
                ("source:s02", "notebook:mixed"),
                ("source:s04", "notebook:mixed"),
            ],
        )
    )
    await migration_service.run_classification(ADMIN)
    items = await migration_service.list_flagged_items(ADMIN)

    assert [n["id"] for n in items["notebooks"]] == ["notebook:mixed"]
    assert items["notebooks"][0]["reason"] == "mixed_team_sources"
    assert items["notebooks"][0]["linked_teams"] == ["Finance", "HR"]


@pytest.mark.asyncio
async def test_assign_item_resolves_flag_and_completes(fake_db):
    db = fake_db(
        FakeDB(
            teams=_teams(),
            sources=[_source("source:x", "hr_finance_mix.pdf")],
        )
    )
    await migration_service.run_classification(ADMIN)
    assert db.org.classification_completed_at is None

    result = await migration_service.assign_item(
        ADMIN, "source", "source:x", team_id="team:hr", visibility="team"
    )

    assert result["team_name"] == "HR"
    assert db.sources[_rid("source:x")]["team"] == "team:hr"
    # Last flag resolved → completion stamped, members may log in
    assert db.org.classification_completed_at is not None
    assert await migration_service.member_login_allowed("organization:default")


@pytest.mark.asyncio
async def test_assign_item_validates_kind_team_and_visibility(fake_db):
    fake_db(FakeDB(teams=_teams()))
    with pytest.raises(InvalidInputError):
        await migration_service.assign_item(
            ADMIN, "widget", "source:x", team_id="team:hr"  # type: ignore[arg-type]
        )
    with pytest.raises(NotFoundError):
        await migration_service.assign_item(
            ADMIN, "source", "source:missing", team_id="team:hr"
        )
    with pytest.raises(NotFoundError):
        await migration_service.assign_item(
            ADMIN, "source", "source:x", team_id="team:ghost"
        )


@pytest.mark.asyncio
async def test_member_login_allowed_fails_closed(fake_db):
    fake_db(FakeDB(teams=_teams()))
    assert await migration_service.member_login_allowed("organization:default") is False
    assert await migration_service.member_login_allowed("") is False
    assert await migration_service.member_login_allowed("organization:ghost") is False


@pytest.mark.asyncio
async def test_status_reports_counts(fake_db):
    fake_db(
        FakeDB(
            teams=_teams(),
            sources=[_source("source:x", "hr_finance_mix.pdf")],
        )
    )
    status = await migration_service.migration_status(ADMIN)
    assert status == {
        "completed": False,
        "completed_at": None,
        "flagged_notebooks": 0,
        "flagged_sources": 1,
    }


# ---------------------------------------------------------------------------
# Router: admin enforcement + CSRF (service patched, no database)
# ---------------------------------------------------------------------------


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
    monkeypatch.setattr(
        auth_service,
        "resolve_session",
        lambda token: _async(
            (
                AppUser(
                    id="app_user:alex",
                    organization_id="organization:default",
                    email="alex@company.com",
                    password_hash="argon2id$fake",
                    display_name="Alex",
                    team_id="team:executive",
                    role="admin",
                    status="active",
                ),
                None,
            )
        ),
    )
    return {auth_service.SESSION_COOKIE: "opaque"}


@pytest.fixture
def member_cookie(client, monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "resolve_session",
        lambda token: _async(
            (
                AppUser(
                    id="app_user:aisha",
                    organization_id="organization:default",
                    email="aisha@company.com",
                    password_hash="argon2id$fake",
                    display_name="Aisha",
                    team_id="team:hr",
                    role="member",
                    status="active",
                ),
                None,
            )
        ),
    )
    return {auth_service.SESSION_COOKIE: "opaque"}


def _csrf_cookies(base: Dict[str, str]) -> Dict[str, str]:
    return {**base, auth_service.CSRF_COOKIE: CSRF_COOKIE}


class TestMigrationRouter:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/api/migration/status"),
            ("GET", "/api/migration/items"),
            ("POST", "/api/migration/run"),
            ("PATCH", "/api/migration/items/source/source:x"),
        ],
    )
    def test_unauthenticated_is_401(self, client, monkeypatch, method, path):
        monkeypatch.setattr(auth_service, "resolve_session", lambda token: _async(None))
        response = client.request(
            method, path, cookies={auth_service.SESSION_COOKIE: "opaque"}
        )
        assert response.status_code == 401

    @pytest.mark.parametrize(
        "method,path,kwargs",
        [
            ("GET", "/api/migration/status", {}),
            ("GET", "/api/migration/items", {}),
            ("POST", "/api/migration/run", {}),
            (
                "PATCH",
                "/api/migration/items/source/source:x",
                {"json": {"team_id": "team:hr"}},
            ),
        ],
    )
    def test_member_is_403(self, client, member_cookie, method, path, kwargs):
        response = client.request(
            method, path, cookies=member_cookie, **kwargs
        )
        assert response.status_code == 403

    def test_run_requires_csrf(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(
            migration_service, "run_classification", AsyncMock(return_value={})
        )
        response = client.post("/api/migration/run", cookies=admin_cookie)
        assert response.status_code == 403

    def test_status_happy_path(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(
            migration_service,
            "migration_status",
            AsyncMock(
                return_value={
                    "completed": False,
                    "completed_at": None,
                    "flagged_notebooks": 1,
                    "flagged_sources": 2,
                }
            ),
        )
        response = client.get("/api/migration/status", cookies=admin_cookie)
        assert response.status_code == 200
        assert response.json()["flagged_sources"] == 2

    def test_run_happy_path(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(
            migration_service,
            "run_classification",
            AsyncMock(return_value={"classified_sources": 7, "completed": True}),
        )
        response = client.post(
            "/api/migration/run",
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["completed"] is True

    def test_assign_happy_path(self, client, admin_cookie, monkeypatch):
        monkeypatch.setattr(
            migration_service,
            "assign_item",
            AsyncMock(
                return_value={
                    "id": "source:x",
                    "kind": "source",
                    "team_id": "team:hr",
                    "team_name": "HR",
                    "visibility": "team",
                }
            ),
        )
        response = client.patch(
            "/api/migration/items/source/source:x",
            json={"team_id": "team:hr"},
            cookies=_csrf_cookies(admin_cookie),
            headers=CSRF_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["team_name"] == "HR"


# ---------------------------------------------------------------------------
# Login gate (TDD §12.11)
# ---------------------------------------------------------------------------


def _seed_user(role: str) -> AppUser:
    return AppUser(
        id=f"app_user:{role}",
        organization_id="organization:default",
        email=f"{role}@company.com",
        password_hash="argon2id$fake",
        display_name=role,
        team_id="team:hr",
        role=role,
        status="active",
    )


class TestLoginGate:
    def _patch_login(self, monkeypatch, role: str, allowed: bool):
        issue_session = AsyncMock(return_value=("token", "csrf", None))
        member_login_allowed = AsyncMock(return_value=allowed)
        monkeypatch.setattr(
            auth_service,
            "attempt_login",
            AsyncMock(return_value=_seed_user(role)),
        )
        monkeypatch.setattr(auth_service, "issue_session", issue_session)
        monkeypatch.setattr(
            migration_service, "member_login_allowed", member_login_allowed
        )
        return issue_session, member_login_allowed

    @pytest.mark.parametrize("role", ["member", "team_manager"])
    def test_member_login_blocked_before_completion(self, client, monkeypatch, role):
        issue_session, _ = self._patch_login(monkeypatch, role, allowed=False)
        response = client.post(
            "/api/auth/login", json={"email": f"{role}@x.com", "password": "pw"}
        )
        assert response.status_code == 403
        assert "migration" in response.json()["detail"].lower()
        issue_session.assert_not_called()

    @pytest.mark.parametrize("role", ["member", "team_manager"])
    def test_member_login_allowed_after_completion(self, client, monkeypatch, role):
        self._patch_login(monkeypatch, role, allowed=True)
        response = client.post(
            "/api/auth/login", json={"email": f"{role}@x.com", "password": "pw"}
        )
        assert response.status_code == 204

    @pytest.mark.parametrize("role", ["admin", "ceo"])
    def test_admin_and_ceo_login_never_gated(self, client, monkeypatch, role):
        _, member_login_allowed = self._patch_login(monkeypatch, role, allowed=False)
        response = client.post(
            "/api/auth/login", json={"email": f"{role}@x.com", "password": "pw"}
        )
        assert response.status_code == 204
        member_login_allowed.assert_not_called()
