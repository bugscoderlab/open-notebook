"""Domain-level tests for T5 ownership fields and the same-team link rule.

- Notebook/Source parse the migration-26 record fields (organization/team/
  created_by/visibility) through their pydantic aliases.
- ``_assert_same_team_link`` enforces: same team only; company-shared
  content only into company-shared notebooks; unclassified items link
  freely (the T6 pass flags resulting mixed-team notebooks).
"""

import pytest

from open_notebook.domain.notebook import (
    Notebook,
    Source,
    _assert_same_team_link,
)
from open_notebook.exceptions import ForbiddenError


def _notebook(team_id: str = "team:hr", visibility: str = "team") -> Notebook:
    return Notebook(
        id="notebook:one",
        name="One",
        description="",
        organization="organization:default",
        team=team_id,
        visibility=visibility,
    )


def _source(team_id: str = "team:hr", visibility: str = "team") -> Source:
    return Source(
        id="source:one",
        title="One",
        organization="organization:default",
        team=team_id,
        visibility=visibility,
    )


class TestOwnershipFields:
    def test_notebook_parses_record_fields_via_aliases(self):
        nb = Notebook(
            id="notebook:abc",
            name="N",
            description="",
            organization="organization:default",
            team="team:hr",
            created_by="app_user:x",
            visibility="company_shared",
        )
        assert nb.organization_id == "organization:default"
        assert nb.team_id == "team:hr"
        assert nb.created_by == "app_user:x"
        assert nb.visibility == "company_shared"

    def test_source_defaults_to_team_visibility(self):
        src = Source(id="source:abc", title="S")
        assert src.visibility == "team"
        assert src.team_id is None
        assert src.organization_id is None

    def test_fields_populate_by_name_too(self):
        nb = Notebook(
            id="notebook:abc",
            name="N",
            description="",
            organization_id="organization:default",
            team_id="team:hr",
        )
        assert nb.organization_id == "organization:default"
        assert nb.team_id == "team:hr"


class TestSameTeamLink:
    def test_same_team_link_allowed(self):
        _assert_same_team_link(
            "team:hr", "team", set(), _notebook(team_id="team:hr")
        )

    def test_cross_team_link_rejected(self):
        with pytest.raises(ForbiddenError, match="cross teams"):
            _assert_same_team_link(
                "team:hr", "team", set(), _notebook(team_id="team:finance")
            )

    def test_source_with_other_team_links_rejected(self):
        """A source already linked to HR cannot be linked to Finance."""
        with pytest.raises(ForbiddenError, match="cross teams"):
            _assert_same_team_link(
                "team:hr", "team", {"team:hr"}, _notebook(team_id="team:finance")
            )

    def test_company_shared_source_needs_company_shared_notebook(self):
        with pytest.raises(ForbiddenError, match="company-shared"):
            _assert_same_team_link(
                "team:executive",
                "company_shared",
                set(),
                _notebook(team_id="team:executive", visibility="team"),
            )

    def test_company_shared_to_company_shared_allowed(self):
        _assert_same_team_link(
            "team:executive",
            "company_shared",
            set(),
            _notebook(team_id="team:executive", visibility="company_shared"),
        )

    def test_unclassified_item_links_freely(self):
        """No team anywhere → allowed; T6 flags the mixed notebook."""
        _assert_same_team_link(None, "team", set(), _notebook(team_id="team:hr"))
        _assert_same_team_link("team:hr", "team", set(), _notebook(team_id=""))

    def test_note_link_uses_existing_artifact_teams(self):
        """Notes carry no team fields; their existing notebook links decide."""
        with pytest.raises(ForbiddenError, match="cross teams"):
            _assert_same_team_link(None, None, {"team:hr"}, _notebook("team:finance"))
        _assert_same_team_link(None, None, {"team:hr"}, _notebook("team:hr"))


class TestPrepareSaveData:
    def test_notebook_serializes_record_fields_as_record_ids(self):
        nb = _notebook()
        data = nb._prepare_save_data()
        assert data["organization"] is not None
        assert str(data["organization"]) == "organization:default"
        assert str(data["team"]) == "team:hr"
        assert "visibility" in data

    def test_source_serializes_record_fields_as_record_ids(self):
        src = _source()
        data = src._prepare_save_data()
        assert str(data["organization"]) == "organization:default"
        assert str(data["team"]) == "team:hr"

    def test_none_record_fields_are_kept_for_schemafull_nullable(self):
        src = Source(id="source:x", title="T")
        data = src._prepare_save_data()
        assert "organization" in data and data["organization"] is None
        assert "team" in data and data["team"] is None
        assert "created_by" in data and data["created_by"] is None
