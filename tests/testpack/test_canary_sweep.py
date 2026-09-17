"""Canary sweep + access matrix — the document-isolation acceptance gate.

Driven entirely by ``_jobbrief/testdata/expected_access_results.csv`` (6
canary documents x 4 roles) plus the QA guide's own canary. Every cell is
exercised through the real FastAPI app on an in-memory SurrealDB running the
real ``fn::text_search`` — forbidden rows genuinely exist in the database, so
a missing scope filter would leak them into the response.

Tier: testpack (hermetic on mem://, but gated to the pack CI job).
"""

from typing import Any, Dict, List, Tuple

import pytest
from fastapi.testclient import TestClient

from .conftest import (
    GUIDE_CANARY,
    PERSONAS,
    matrix_rows,
)

pytestmark = pytest.mark.testpack


def _doc_key(file: str) -> str:
    """Seed record key for a pack PDF filename."""
    return f"doc_{file.removesuffix('.pdf').replace('-', '_').lower()}"


def _login(auth_session: Any, persona_column: str) -> None:
    """Authenticate the session as one of the four UAT personas."""
    role, team_id = PERSONAS[persona_column]
    auth_session(role=role, team_id=team_id)


def _search(client: TestClient, canary: str) -> Tuple[Dict[str, Any], str]:
    """Full-text search a canary; return (parsed body, raw response text)."""
    response = client.post("/api/search", json={"query": canary, "type": "text"})
    assert response.status_code == 200
    return response.json(), response.text


def _matrix_cases() -> List[Tuple[str, str, str, bool]]:
    """One case per (CSV row, role column): (file, canary, persona, allowed)."""
    cases = []
    for row in matrix_rows():
        for persona in PERSONAS:
            cases.append(
                (row["file"], row["canary"], persona, row[persona] == "allow")
            )
    return cases


class TestAccessMatrix:
    """Every cell of expected_access_results.csv, over HTTP, on a real engine."""

    @pytest.mark.parametrize(
        "file,canary,persona,allowed",
        _matrix_cases(),
        ids=[
            f"{file}::{persona}-{'allow' if allowed else 'deny'}"
            for file, canary, persona, allowed in _matrix_cases()
        ],
    )
    def test_canary_cell(
        self,
        client: TestClient,
        pack_db: Any,
        auth_session: Any,
        file: str,
        canary: str,
        persona: str,
        allowed: bool,
    ) -> None:
        """A canary search returns exactly the CSV-mandated visibility."""
        _login(auth_session, persona)
        body, raw = _search(client, canary)

        if allowed:
            # Exactly the owning source — no foreign, unclassified, or
            # duplicate hit may ride along.
            assert {r["id"] for r in body["results"]} == {
                f"source:{_doc_key(file)}_src"
            }
        else:
            # Zero leakage: no result row, and the canary string appears
            # nowhere in the response (not even in a highlight or excerpt).
            assert body["results"] == []
            assert canary not in raw


class TestGuideCanary:
    """The 7th PDF (QA guide) is company-shared: its canary is allow-all."""

    def test_guide_canary_visible_to_every_role(
        self, client: TestClient, pack_db: Any, auth_session: Any
    ) -> None:
        """QA-GUIDE-002 is searchable by all four personas."""
        for persona in PERSONAS:
            _login(auth_session, persona)
            body, _ = _search(client, GUIDE_CANARY)
            assert "source:access_control_test_guide_src" in {
                r["id"] for r in body["results"]
            }, f"{persona} must read the company-shared guide"

    @pytest.mark.asyncio
    async def test_all_seven_pdfs_seeded(self, pack_db: Any) -> None:
        """All 7 pack documents (and only those) are loaded in the database."""
        result = await pack_db.query("SELECT count() FROM source GROUP ALL")
        assert result[0]["count"] == 7
        result = await pack_db.query("SELECT count() FROM notebook GROUP ALL")
        assert result[0]["count"] == 7


class TestAskHonestDenial:
    """A forbidden scope must produce an honest denial, never an LLM answer
    built on leaked context (the endpoint short-circuits before any model
    call when the effective scope is empty)."""

    @pytest.mark.parametrize(
        "persona,forbidden_notebook",
        [
            ("hr", "notebook:doc_04_finance_monthly_report"),
            ("hr", "notebook:doc_06_executive_strategy_memo"),
            ("finance", "notebook:doc_02_hr_employee_handbook"),
            ("finance", "notebook:doc_06_executive_strategy_memo"),
        ],
        ids=["hr->finance", "hr->executive", "finance->hr", "finance->executive"],
    )
    def test_ask_simple_with_only_forbidden_scope_denies(
        self,
        client: TestClient,
        pack_db: Any,
        auth_session: Any,
        persona: str,
        forbidden_notebook: str,
    ) -> None:
        """Scope that resolves to nothing permitted → honest denial, no canary."""
        _login(auth_session, persona)
        response = client.post(
            "/api/search/ask/simple",
            json={
                "question": "Summarize the confidential figures",
                "strategy_model": "model:x",
                "answer_model": "model:y",
                "final_answer_model": "model:z",
                "notebook_ids": [forbidden_notebook],
            },
        )
        assert response.status_code == 200
        answer = response.json()["answer"]
        assert "access" in answer.lower()
        # No canary from any forbidden document may appear in the answer.
        for row in matrix_rows():
            assert row["canary"] not in answer


class TestAskRetrievedContextIsScoped:
    """The AI-answer leg of zero leakage: what the ask pipeline retrieves for
    the LLM must already be scope-filtered at the SQL layer.

    The ask graph is replaced by a stand-in that runs the same real
    ``text_search`` the graph's retrieval step runs, over the notebook ids
    the endpoint hands it, and answers from that retrieval only. If the
    endpoint leaked a forbidden notebook id into the graph input, the
    forbidden canary would show up in the retrieved context.
    """

    @pytest.mark.parametrize("persona", list(PERSONAS), ids=list(PERSONAS))
    def test_no_forbidden_canary_in_ask_context(
        self,
        client: TestClient,
        pack_db: Any,
        auth_session: Any,
        monkeypatch: pytest.MonkeyPatch,
        persona: str,
    ) -> None:
        """Forbidden canaries never reach the ask pipeline's retrieval."""
        import json
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        import api.routers.search as search_router

        forbidden_canaries = [
            row["canary"] for row in matrix_rows() if row[persona] == "deny"
        ]
        fake_model = SimpleNamespace(id="model:x")

        class _RecordingGraph:
            """Stand-in for the ask graph: retrieve like the graph does."""

            async def astream(self, input: Dict[str, Any], config: Any, stream_mode: str):
                from open_notebook.domain.notebook import text_search

                results = await text_search(
                    input["question"], 10, True, False, input["notebook_ids"]
                )
                context = json.dumps(results, default=str)
                yield {
                    "write_final_answer": {
                        "final_answer": f"Answer from permitted context: {context}"
                    }
                }

        monkeypatch.setattr(
            search_router.Model,
            "get",
            AsyncMock(return_value=fake_model),
        )
        monkeypatch.setattr(
            search_router.model_manager,
            "get_embedding_model",
            AsyncMock(return_value=object()),
        )
        monkeypatch.setattr(search_router, "ask_graph", _RecordingGraph())

        _login(auth_session, persona)
        question = "What do the confidential documents say about " + " ".join(
            forbidden_canaries
        )
        response = client.post(
            "/api/search/ask/simple",
            json={
                "question": question,
                "strategy_model": "model:x",
                "answer_model": "model:y",
                "final_answer_model": "model:z",
            },
        )
        assert response.status_code == 200
        answer = response.json()["answer"]
        for canary in forbidden_canaries:
            assert canary not in answer


class TestPackIntegrity:
    """The test pack itself is complete and self-consistent."""

    def test_all_seven_pdfs_have_text(self, pdf_texts: Dict[str, str]) -> None:
        """Every pack PDF yields extractable text."""
        assert len(pdf_texts) == 7
        for name, text in pdf_texts.items():
            assert len(text) > 100, f"{name} extracted too little text"

    def test_every_csv_canary_is_unique_and_present(
        self, pdf_texts: Dict[str, str]
    ) -> None:
        """CSV canaries are unique and actually inside their PDF."""
        canaries = [row["canary"] for row in matrix_rows()]
        assert len(canaries) == len(set(canaries))
        for row in matrix_rows():
            assert row["canary"] in pdf_texts[row["file"]]

    def test_csv_matrix_shape(self) -> None:
        """The CSV has the 6 documents x 4 roles shape the sweep relies on."""
        rows = matrix_rows()
        assert len(rows) == 6
        for row in rows:
            for persona in PERSONAS:
                assert row[persona] in ("allow", "deny")
