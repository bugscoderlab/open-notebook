"""Shared fixtures for the synthetic acceptance test pack (T10).

The pack loads the 7 real PDFs from ``_jobbrief/testdata/`` into an in-memory
SurrealDB (real schema, real ``fn::text_search`` BM25 index) and drives the
real FastAPI app over it. Because the forbidden documents genuinely exist in
the database, a missing WHERE clause would leak them into the response — asserting
response content is a true SQL-side scoping test.

The access matrix is read from ``expected_access_results.csv`` — the CSV is
the source of truth, not a copy of it.
"""

import csv
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

TESTDATA = Path(__file__).parent.parent.parent / "_jobbrief" / "testdata"
ACCESS_MATRIX_CSV = TESTDATA / "expected_access_results.csv"

ORG = "organization:default"

# CSV "team" column -> (team slug, visibility). Company Shared is a
# visibility value owned by the Executive team, not a team (CONTEXT.md).
TEAM_MAP: Dict[str, Tuple[str, str]] = {
    "Company Shared": ("executive", "company_shared"),
    "HR": ("hr", "team"),
    "Finance": ("finance", "team"),
    "Executive": ("executive", "team"),
}

# The four UAT personas (issue #1): column name in the CSV -> (role, team).
PERSONAS: Dict[str, Tuple[str, str]] = {
    "hr": ("member", "team:hr"),
    "finance": ("member", "team:finance"),
    "ceo": ("ceo", "team:executive"),
    "admin": ("admin", "team:executive"),
}

# 00_access_control_test_guide.pdf has no CSV row (it is the QA guide) but
# carries its own canary and is company-shared, like its filename token
# implies (unclassified -> company_shared, ADR-013).
GUIDE_FILE = "00_access_control_test_guide.pdf"
GUIDE_CANARY = "QA-GUIDE-002"

MIGRATIONS = Path("open_notebook/database/migrations")
# Schema these tests exercise: base (1), episode tables (7), scoped search +
# team access + analytics (24-28). Same set as tests/test_team_scoping.py.
MIGRATION_NUMBERS = [1, 7, *range(24, 29)]


def matrix_rows() -> List[Dict[str, str]]:
    """The access matrix straight from the test pack CSV."""
    with ACCESS_MATRIX_CSV.open(newline="") as fh:
        return list(csv.DictReader(fh))


def pdf_files() -> List[Path]:
    """The seven pack PDFs."""
    return sorted(TESTDATA.glob("*.pdf"))


def extract_pdf_text(path: Path) -> str:
    """Extract a pack PDF's text with pdfplumber (seed input for full_text)."""
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


async def _seed_pack(db: Any, pdf_texts: Dict[str, str]) -> None:
    """Seed org, teams, one notebook+source per PDF, reference edges.

    Record texts are bound as parameters, never interpolated — multi-line
    PDF bodies would be fragile inside SurrealQL string literals.
    """
    await db.query(
        """
        CREATE organization:default SET external_key = 'k', name = 'Org', status = 'active';
        CREATE team:hr SET organization = organization:default, slug = 'hr', name = 'HR', active = true;
        CREATE team:finance SET organization = organization:default, slug = 'finance', name = 'Finance', active = true;
        CREATE team:executive SET organization = organization:default, slug = 'executive', name = 'Executive', active = true;
        """
    )
    for row in matrix_rows():
        await _create_document(
            db,
            key=f"doc_{row['file'].removesuffix('.pdf').replace('-', '_').lower()}",
            title=row["file"],
            text=pdf_texts[row["file"]],
            team_slug=TEAM_MAP[row["team"]][0],
            visibility=TEAM_MAP[row["team"]][1],
        )
    # The QA guide: company-shared, own canary, no CSV row.
    await _create_document(
        db,
        key="access_control_test_guide",
        title="Access Control Test Guide",
        text=pdf_texts[GUIDE_FILE],
        team_slug="executive",
        visibility="company_shared",
    )


async def _create_document(
    db: Any, key: str, title: str, text: str, team_slug: str, visibility: str
) -> None:
    # Record ids are derived from filenames (safe identifiers); the document
    # body is bound as parameters so multi-line PDF text never gets
    # interpolated into SurrealQL string literals.
    await db.query(
        f"""
        CREATE source:{key}_src SET title = $title, full_text = $text,
            organization = organization:default, team = type::thing('team', $team_slug),
            visibility = $visibility;
        CREATE notebook:{key} SET name = $title, description = '',
            organization = organization:default, team = type::thing('team', $team_slug),
            visibility = $visibility, created_by = NONE;
        RELATE source:{key}_src->reference->notebook:{key};
        """,
        {
            "title": title,
            "text": text,
            "team_slug": team_slug,
            "visibility": visibility,
        },
    )


@pytest.fixture(scope="module")
def pdf_texts() -> Dict[str, str]:
    """Extract each pack PDF's text once per module (pdfplumber)."""
    return {p.name: extract_pdf_text(p) for p in pdf_files()}


@pytest_asyncio.fixture
async def pack_db(monkeypatch: pytest.MonkeyPatch, pdf_texts: Dict[str, str]) -> Any:
    """mem:// SurrealDB with the full pack loaded, wired into repo_query."""
    from surrealdb import AsyncSurreal

    from open_notebook.database import repository
    from open_notebook.database.repository import parse_record_ids

    db: Any = AsyncSurreal("mem://")
    await db.use("testpack", "canary")
    for number in MIGRATION_NUMBERS:
        await db.query((MIGRATIONS / f"{number}.surrealql").read_text())
    await _seed_pack(db, pdf_texts)

    async def shim(query: str, vars: Any = None) -> Any:
        result = parse_record_ids(await db.query(query, vars))
        if isinstance(result, str):
            raise RuntimeError(result)
        return result

    # Same dual-patch pattern as tests/test_team_scoping.py: the definition
    # site plus every module that bound repo_query at import time.
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


@pytest.fixture
def client() -> TestClient:
    """FastAPI test client over the real app."""
    from api.main import app

    return TestClient(app)
