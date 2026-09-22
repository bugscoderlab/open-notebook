"""Tests for the Ask pipeline knobs on the settings endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


def _settings_mock(**overrides):
    values = {
        "default_content_processing_engine_doc": "auto",
        "default_content_processing_engine_url": "auto",
        "default_embedding_option": "ask",
        "auto_delete_files": "yes",
        "docling_ocr": True,
        "docling_formulas": False,
        "docling_vision": False,
        "youtube_preferred_languages": ["en"],
        "ask_max_searches": 3,
        "ask_search_answer_max_tokens": 2048,
    }
    values.update(overrides)
    settings = MagicMock(**values)
    settings.update = AsyncMock()
    return settings


class TestAskPipelineSettings:
    def test_get_includes_ask_knobs(self, client, auth_session, auth_cookie):
        auth_session()
        settings = _settings_mock()
        with patch(
            "api.routers.settings.ContentSettings"
        ) as mock_cls:
            mock_cls.get_instance = AsyncMock(return_value=settings)
            response = client.get("/api/settings", cookies=auth_cookie)

        assert response.status_code == 200
        body = response.json()
        assert body["ask_max_searches"] == 3
        assert body["ask_search_answer_max_tokens"] == 2048

    def test_put_updates_ask_knobs(self, client, auth_session, auth_cookie):
        auth_session()  # defaults to admin, which PUT requires
        settings = _settings_mock()
        with patch(
            "api.routers.settings.ContentSettings"
        ) as mock_cls:
            mock_cls.get_instance = AsyncMock(return_value=settings)
            response = client.put(
                "/api/settings",
                cookies=auth_cookie,
                json={
                    "ask_max_searches": 2,
                    "ask_search_answer_max_tokens": 4096,
                },
            )

        assert response.status_code == 200
        assert settings.ask_max_searches == 2
        assert settings.ask_search_answer_max_tokens == 4096
        settings.update.assert_awaited_once()
        body = response.json()
        assert body["ask_max_searches"] == 2
        assert body["ask_search_answer_max_tokens"] == 4096

    def test_put_rejects_out_of_range_knobs(self, client, auth_session, auth_cookie):
        auth_session()
        for payload in (
            {"ask_max_searches": 0},
            {"ask_max_searches": 6},
            {"ask_search_answer_max_tokens": 100},
            {"ask_search_answer_max_tokens": 9000},
        ):
            response = client.put(
                "/api/settings", cookies=auth_cookie, json=payload
            )
            assert response.status_code == 422, payload

    def test_put_member_is_forbidden(self, client, auth_session, auth_cookie):
        auth_session(role="member")
        response = client.put(
            "/api/settings",
            cookies=auth_cookie,
            json={"ask_max_searches": 2},
        )
        assert response.status_code == 403
