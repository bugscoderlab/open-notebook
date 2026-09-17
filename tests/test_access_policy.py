"""Unit tests for the team-access policy functions (T5, TDD §7).

The can_read_team / can_write_team matrix mirrors expected_access_results.csv
(team columns are team-level: an HR team_manager is still "deny" on Finance;
CEO and admin read everything) plus the rules the spec layers on top:

- organization_id equality is checked BEFORE role/team/visibility;
- unclassified content (no team) is admin-only until the T6 classification
  pass (TDD §12.10);
- members write their own team (CONTEXT.md) — a deliberate, recorded
  deviation from TDD §7's can_write_team which omitted members;
- CEO writes only via the Executive team (their own team), so a plain
  team-equality check captures the CEO rule.

Tests are pure unit tests: no client, no DB.
"""

from typing import Optional

import pytest

from api.access import CurrentUser, can_read_team, can_write_team

ORG = "organization:default"
OTHER_ORG = "organization:other"
HR_TEAM = "team:hr"
FINANCE_TEAM = "team:finance"
EXEC_TEAM = "team:executive"

TEAM_VISIBILITY = "team"
SHARED_VISIBILITY = "company_shared"


def _user(role: str, team_id: str = HR_TEAM, organization_id: str = ORG) -> CurrentUser:
    return CurrentUser(
        id="app_user:test",
        email="test@example.com",
        display_name="Test",
        organization_id=organization_id,
        team_id=team_id,
        role=role,  # type: ignore[arg-type]
    )


# (role, user_team, resource_team, visibility, expected)
READ_MATRIX = [
    # Members: own team + company-shared only
    ("member", HR_TEAM, HR_TEAM, TEAM_VISIBILITY, True),
    ("member", HR_TEAM, HR_TEAM, SHARED_VISIBILITY, True),
    ("member", HR_TEAM, FINANCE_TEAM, TEAM_VISIBILITY, False),
    ("member", HR_TEAM, FINANCE_TEAM, SHARED_VISIBILITY, True),
    ("member", HR_TEAM, EXEC_TEAM, TEAM_VISIBILITY, False),
    # Team managers read exactly like members (expected_access_results.csv)
    ("team_manager", FINANCE_TEAM, FINANCE_TEAM, TEAM_VISIBILITY, True),
    ("team_manager", FINANCE_TEAM, HR_TEAM, TEAM_VISIBILITY, False),
    ("team_manager", FINANCE_TEAM, HR_TEAM, SHARED_VISIBILITY, True),
    # CEO reads every team
    ("ceo", EXEC_TEAM, HR_TEAM, TEAM_VISIBILITY, True),
    ("ceo", EXEC_TEAM, FINANCE_TEAM, TEAM_VISIBILITY, True),
    ("ceo", EXEC_TEAM, EXEC_TEAM, TEAM_VISIBILITY, True),
    # Admin reads everything
    ("admin", EXEC_TEAM, HR_TEAM, TEAM_VISIBILITY, True),
    ("admin", EXEC_TEAM, FINANCE_TEAM, TEAM_VISIBILITY, True),
]


@pytest.mark.parametrize(
    "role,user_team,resource_team,visibility,expected", READ_MATRIX
)
def test_can_read_team_matrix(
    role: str, user_team: str, resource_team: str, visibility: str, expected: bool
):
    user = _user(role, user_team)
    assert (
        can_read_team(
            user, team_id=resource_team, visibility=visibility, organization_id=ORG
        )
        is expected
    )


# (role, user_team, resource_team, expected)
WRITE_MATRIX = [
    # Members write their own team (CONTEXT.md deviation from TDD §7)
    ("member", HR_TEAM, HR_TEAM, True),
    ("member", HR_TEAM, FINANCE_TEAM, False),
    # Team managers write their own team only
    ("team_manager", FINANCE_TEAM, FINANCE_TEAM, True),
    ("team_manager", FINANCE_TEAM, HR_TEAM, False),
    # CEO writes only the Executive team (= own team)
    ("ceo", EXEC_TEAM, EXEC_TEAM, True),
    ("ceo", EXEC_TEAM, HR_TEAM, False),
    # Admin writes everything
    ("admin", EXEC_TEAM, HR_TEAM, True),
    ("admin", EXEC_TEAM, FINANCE_TEAM, True),
]


@pytest.mark.parametrize("role,user_team,resource_team,expected", WRITE_MATRIX)
def test_can_write_team_matrix(
    role: str, user_team: str, resource_team: str, expected: bool
):
    user = _user(role, user_team)
    assert (
        can_write_team(user, team_id=resource_team, organization_id=ORG) is expected
    )


def test_org_mismatch_denies_before_role():
    """A CEO/admin from another organization gets nothing (TDD §7)."""
    foreign_ceo = _user("ceo", EXEC_TEAM, organization_id=OTHER_ORG)
    foreign_admin = _user("admin", EXEC_TEAM, organization_id=OTHER_ORG)
    assert (
        can_read_team(
            foreign_ceo,
            team_id=HR_TEAM,
            visibility=TEAM_VISIBILITY,
            organization_id=ORG,
        )
        is False
    )
    assert (
        can_write_team(foreign_admin, team_id=HR_TEAM, organization_id=ORG) is False
    )


def test_missing_resource_org_skips_org_check():
    """Legacy resources (no org) don't fail the org guard."""
    legacy_user = _user("member", HR_TEAM, organization_id="")
    assert (
        can_read_team(
            legacy_user,
            team_id=HR_TEAM,
            visibility=TEAM_VISIBILITY,
            organization_id="",
        )
        is True
    )
    assert (
        can_read_team(
            _user("member", HR_TEAM),
            team_id=HR_TEAM,
            visibility=TEAM_VISIBILITY,
            organization_id="",
        )
        is True
    )


def test_unknown_user_org_is_denied_fail_closed():
    """A caller whose organization cannot be established gets nothing from
    org-owned resources (fail closed)."""
    orgless_user = _user("member", HR_TEAM, organization_id="")
    assert (
        can_read_team(
            orgless_user,
            team_id=HR_TEAM,
            visibility=TEAM_VISIBILITY,
            organization_id=ORG,
        )
        is False
    )
    assert can_write_team(orgless_user, team_id=HR_TEAM, organization_id=ORG) is False


@pytest.mark.parametrize("role", ["member", "team_manager", "ceo"])
def test_unclassified_content_is_admin_only(role: str):
    """Content with no team is invisible until the T6 classification pass."""
    user = _user(role, HR_TEAM)
    assert (
        can_read_team(
            user, team_id="", visibility=TEAM_VISIBILITY, organization_id=ORG
        )
        is False
    )
    assert can_write_team(user, team_id="", organization_id=ORG) is False
    admin = _user("admin", EXEC_TEAM)
    assert (
        can_read_team(
            admin, team_id="", visibility=TEAM_VISIBILITY, organization_id=ORG
        )
        is True
    )
    assert can_write_team(admin, team_id="", organization_id=ORG) is True


def test_can_read_accepts_positional_resource_shape():
    """can_read_team also accepts a resource object with the model field names."""
    user = _user("member", HR_TEAM)

    class _Resource:
        team_id = HR_TEAM
        visibility: Optional[str] = TEAM_VISIBILITY
        organization_id = ORG

    assert can_read_team(user, _Resource()) is True
