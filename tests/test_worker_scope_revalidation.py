"""Worker-side scope revalidation (TDD §8) — the payload recorded at submit
time is re-checked at run time, so a queued job cannot outlive a permission
revocation or a team reassignment."""

from unittest.mock import AsyncMock, patch

import pytest


class TestSourceTeamRevalidation:
    @pytest.mark.asyncio
    async def test_matching_team_passes(self):
        from commands.embedding_commands import _revalidate_source_team

        with patch(
            "commands.embedding_commands.repo_query",
            new_callable=AsyncMock,
            return_value=[{"team": "team:hr"}],
        ):
            await _revalidate_source_team("source:1", "team:hr")  # no raise

    @pytest.mark.asyncio
    async def test_foreign_team_aborts(self):
        from commands.embedding_commands import _revalidate_source_team

        with (
            patch(
                "commands.embedding_commands.repo_query",
                new_callable=AsyncMock,
                return_value=[{"team": "team:finance"}],
            ),
            pytest.raises(ValueError, match="changed team"),
        ):
            await _revalidate_source_team("source:1", "team:hr")

    @pytest.mark.asyncio
    async def test_missing_expected_team_skips_validation(self):
        """Legacy/domain-submitted jobs carry no scope; nothing to revalidate."""
        from commands.embedding_commands import _revalidate_source_team

        with patch(
            "commands.embedding_commands.repo_query",
            new_callable=AsyncMock,
            side_effect=AssertionError("must not query"),
        ):
            await _revalidate_source_team("source:1", None)

    @pytest.mark.asyncio
    async def test_unclassified_source_passes(self):
        from commands.embedding_commands import _revalidate_source_team

        with patch(
            "commands.embedding_commands.repo_query",
            new_callable=AsyncMock,
            return_value=[{"team": None}],
        ):
            await _revalidate_source_team("source:1", "team:hr")

    @pytest.mark.asyncio
    async def test_deleted_source_aborts(self):
        from commands.embedding_commands import _revalidate_source_team

        with (
            patch(
                "commands.embedding_commands.repo_query",
                new_callable=AsyncMock,
                return_value=[],
            ),
            pytest.raises(ValueError, match="no longer exists"),
        ):
            await _revalidate_source_team("source:gone", "team:hr")


class TestNoteTeamRevalidation:
    @pytest.mark.asyncio
    async def test_foreign_parent_notebook_aborts(self):
        from commands.embedding_commands import _revalidate_note_team

        rows = [{"team": "team:hr"}, {"team": "team:finance"}]
        with (
            patch(
                "commands.embedding_commands.repo_query",
                new_callable=AsyncMock,
                return_value=rows,
            ),
            pytest.raises(ValueError, match="outside the caller's team"),
        ):
            await _revalidate_note_team("note:1", "team:hr")

    @pytest.mark.asyncio
    async def test_own_team_passes(self):
        from commands.embedding_commands import _revalidate_note_team

        with patch(
            "commands.embedding_commands.repo_query",
            new_callable=AsyncMock,
            return_value=[{"team": "team:hr"}],
        ):
            await _revalidate_note_team("note:1", "team:hr")


class TestProcessSourceTeamRevalidation:
    @pytest.mark.asyncio
    async def test_cross_team_source_and_notebook_aborts(self):
        """process_source must refuse when the source's team no longer matches
        a target notebook's team (revocation/reassignment after submit)."""
        from commands.source_commands import (
            SourceProcessingInput,
            process_source_command,
        )

        source = AsyncMock()
        source.id = "source:1"
        source.team_id = "team:hr"
        source.save = AsyncMock()

        with (
            patch(
                "open_notebook.domain.notebook.Source.get",
                new_callable=AsyncMock,
                return_value=source,
            ),
            patch(
                "commands.source_commands.repo_query",
                new_callable=AsyncMock,
                return_value=[{"id": "notebook:finance", "team": "team:finance"}],
            ),
            pytest.raises(ValueError, match="different team"),
        ):
            await process_source_command(
                SourceProcessingInput(
                    source_id="source:1",
                    content_state={"content": "x"},
                    notebook_ids=["notebook:finance"],
                    transformations=[],
                    embed=False,
                )
            )
