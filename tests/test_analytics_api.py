"""API-level tests for the analytics endpoints (issue #9, frozen contract).

Tier: integration — needs BOTH:
  - ANALYTICS_TEST_DATABASE_URL (scratch Postgres, seeded like #8's tests)
  - SurrealDB (dataset registry + query log)

The analytics endpoints are additionally gated by ANALYTICS_AUTH_BYPASS
(env-gated, default off): without the flag every analytics request is 401
until native sessions land (tested here without any database). Under the
flag a fixed dev persona (Daniel, Finance team_manager) is used and the
LLM layer is not needed — deterministic fallbacks answer exactly.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

ALEMBIC_INI = "open_notebook/analytics/alembic.ini"
CSV_PATH = (
    Path(__file__).parent.parent
    / "_jobbrief"
    / "testdata"
    / "sales_transactions_2026.csv"
)

requires_pg = pytest.mark.skipif(
    not os.environ.get("ANALYTICS_TEST_DATABASE_URL"),
    reason="ANALYTICS_TEST_DATABASE_URL not set",
)


def _alembic(*args: str) -> None:
    env = dict(os.environ)
    env["ANALYTICS_DATABASE_URL"] = os.environ["ANALYTICS_TEST_DATABASE_URL"]
    result = subprocess.run(
        ["uv", "run", "alembic", "-c", ALEMBIC_INI, *args],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def _surreal_available() -> bool:
    async def _ping() -> None:
        from open_notebook.database.async_migrate import AsyncMigrationManager

        await asyncio.wait_for(AsyncMigrationManager().ping(), timeout=5)

    try:
        asyncio.run(_ping())
        return True
    except Exception:
        return False


@pytest.fixture()
def client():
    from api.main import app

    return TestClient(app)


class TestBypassFlagDefaultOff:
    """No ANALYTICS_AUTH_BYPASS, no session auth → 401 on every analytics route."""

    def test_datasets_requires_auth(self, client, monkeypatch):
        monkeypatch.delenv("ANALYTICS_AUTH_BYPASS", raising=False)
        response = client.get("/api/analytics/datasets")
        assert response.status_code == 401

    def test_ask_requires_auth(self, client, monkeypatch):
        monkeypatch.delenv("ANALYTICS_AUTH_BYPASS", raising=False)
        response = client.post(
            "/api/analytics/ask", json={"question": "Who is the highest spender?"}
        )
        assert response.status_code == 401

    def test_queries_requires_auth(self, client, monkeypatch):
        monkeypatch.delenv("ANALYTICS_AUTH_BYPASS", raising=False)
        response = client.get("/api/analytics/queries/analytics_query_log:x")
        assert response.status_code == 401

    def test_bypass_flag_parsing(self, monkeypatch):
        from api.access import analytics_auth_bypass_enabled

        for value in ("", "false", "0", "no", "off", "TRUE "):
            monkeypatch.setenv("ANALYTICS_AUTH_BYPASS", value)
            expected = value.strip().lower() in {"1", "true", "yes", "on"}
            assert analytics_auth_bypass_enabled() is expected, value
        monkeypatch.delenv("ANALYTICS_AUTH_BYPASS", raising=False)
        assert analytics_auth_bypass_enabled() is False


@pytest.fixture(scope="module")
def seeded():
    """Scratch PG seeded + SurrealDB dataset registry, bypass flag on."""
    if not os.environ.get("ANALYTICS_TEST_DATABASE_URL"):
        pytest.skip("ANALYTICS_TEST_DATABASE_URL not set")
    if not _surreal_available():
        pytest.skip("SurrealDB not reachable")

    from open_notebook.analytics.engine import dispose_engine
    from open_notebook.analytics.seed import seed
    from open_notebook.domain.analytics import ensure_sales_2026_dataset

    previous_url = os.environ.get("ANALYTICS_DATABASE_URL")
    os.environ["ANALYTICS_DATABASE_URL"] = os.environ["ANALYTICS_TEST_DATABASE_URL"]
    os.environ["ANALYTICS_AUTH_BYPASS"] = "true"
    _alembic("upgrade", "head")
    inserted, skipped = asyncio.run(
        seed(CSV_PATH, os.environ["ANALYTICS_TEST_DATABASE_URL"])
    )
    assert inserted + skipped == 77
    dataset = asyncio.run(ensure_sales_2026_dataset())

    class _Context:
        dataset: Any

    context = _Context()
    context.dataset = dataset

    yield context

    asyncio.run(dispose_engine())
    if previous_url is None:
        os.environ.pop("ANALYTICS_DATABASE_URL", None)
    else:
        os.environ["ANALYTICS_DATABASE_URL"] = previous_url


@requires_pg
@pytest.mark.skipif(not _surreal_available(), reason="SurrealDB not reachable")
class TestAnalyticsApiUnderBypass:
    """AN-001…AN-009 through the HTTP API under ANALYTICS_AUTH_BYPASS=true."""

    def test_datasets_lists_sales_2026(self, client, seeded):
        response = client.get("/api/analytics/datasets")
        assert response.status_code == 200
        datasets = response.json()
        sales = next(d for d in datasets if d["name"] == "Sales 2026")
        assert sales["id"] == seeded.dataset.id
        assert sales["team_name"] == "Finance"
        assert sales["source_type"] == "postgres"
        assert sales["freshness_at"]

    def test_an001_highest_spender_via_api(self, client, seeded):
        response = client.post(
            "/api/analytics/ask",
            json={"question": "Who is the highest spender this year?"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["table"]["rows"][0] == ["Sarah Lim", 8460, 24]
        assert "Sarah Lim" in body["answer_text"]
        assert "8,460" in body["answer_text"]
        assert body["scope"]["refunds"] == "excluded"
        assert "data_team IN (:authorized_team_ids)" in body["query_template"]

    def test_an004_ranking_via_api(self, client, seeded):
        response = client.post(
            "/api/analytics/ask",
            json={"question": "Rank customers by total spend in 2026"},
        )
        body = response.json()
        assert body["status"] == "ok"
        assert [r[1] for r in body["table"]["rows"]] == [8460, 6940, 5920, 4990]

    def test_an005_average_ticket_via_api(self, client, seeded):
        response = client.post(
            "/api/analytics/ask",
            json={"question": "What is Sarah Lim's average transaction value?"},
        )
        body = response.json()
        assert body["status"] == "ok"
        assert body["table"]["rows"][0][3] == 352.5

    def test_an006_top_service_via_api(self, client, seeded):
        response = client.post(
            "/api/analytics/ask",
            json={"question": "What is Sarah Lim's most-used service?"},
        )
        body = response.json()
        assert body["status"] == "ok"
        assert body["table"]["rows"][0][1:] == ["Full Groom", 11]

    def test_an007_refunds_inclusive_via_api(self, client, seeded):
        response = client.post(
            "/api/analytics/ask",
            json={
                "question": "Who is the highest spender this year?",
                "include_refunds": True,
            },
        )
        body = response.json()
        assert body["status"] == "ok"
        assert body["table"]["rows"][0][1] == 9360
        assert body["scope"]["refunds"] == "included"

    def test_an008_future_period_no_data_via_api(self, client, seeded):
        response = client.post(
            "/api/analytics/ask",
            json={"question": "Who is the highest spender in 2027?"},
        )
        body = response.json()
        assert response.status_code == 200
        assert body["status"] == "no_data"
        assert body["table"] is None
        assert "No data" in body["answer_text"]
        assert "Sarah" not in body["answer_text"]

    def test_an009_stored_query_via_api(self, client, seeded):
        asked = client.post(
            "/api/analytics/ask",
            json={"question": "Who is the highest spender this year?"},
        ).json()
        query_id = asked["query_id"]
        response = client.get(f"/api/analytics/queries/{query_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["query_id"] == query_id
        assert body["status"] == "ok"
        assert body["template_id"] == "highest_spender"
        assert body["row_count"] == 4
        assert isinstance(body["duration_ms"], int)
        assert "AND data_team IN (:authorized_team_ids)" in body["query_template"]
        assert os.environ["ANALYTICS_TEST_DATABASE_URL"] not in body["query_template"]

    def test_unknown_query_returns_404(self, client, seeded):
        response = client.get("/api/analytics/queries/analytics_query_log:missing")
        assert response.status_code == 404

    def test_denied_shape_has_zero_leakage_fields(self, client, seeded, monkeypatch):
        """Router contract: denied carries only status + answer_text."""

        from open_notebook.analytics.service import AnalyticsAnswer

        monkeypatch.setattr(
            "api.routers.analytics.ask_analytics_question",
            AsyncMock(
                return_value=AnalyticsAnswer(status="denied", answer_text="denied text")
            ),
        )
        response = client.post(
            "/api/analytics/ask",
            json={
                "question": "Who is the highest spender this year?",
                "dataset_id": "dataset:foreign",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": "denied", "answer_text": "denied text"}

    def test_unknown_dataset_returns_404(self, client, seeded):
        response = client.post(
            "/api/analytics/ask",
            json={
                "question": "Who is the highest spender this year?",
                "dataset_id": "dataset:does-not-exist",
            },
        )
        assert response.status_code == 404
