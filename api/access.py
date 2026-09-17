"""Central access seam for team-access (TDD §7).

Only the analytics surface is wired today (issue #9): ``get_current_user``
feeds the analytics router, and ``ANALYTICS_AUTH_BYPASS`` (env-gated,
default off) substitutes a fixed dev persona so the analytics endpoints are
exercisable before native sessions land (T3/T5). When the bypass is off and
no session auth exists yet, requests are rejected with 401.
"""

import os
from typing import Literal

from fastapi import Depends
from pydantic import BaseModel

from open_notebook.exceptions import AuthenticationError

Role = Literal["member", "team_manager", "ceo", "admin"]


class CurrentUser(BaseModel):
    """Authenticated caller shape consumed by services (TDD §6.2)."""

    id: str
    email: str
    display_name: str = ""
    organization_id: str = ""
    team_id: str = ""
    role: Role = "member"


def analytics_auth_bypass_enabled() -> bool:
    """True only when ANALYTICS_AUTH_BYPASS is explicitly set (default off)."""
    return os.environ.get("ANALYTICS_AUTH_BYPASS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Dev-only persona: Daniel, Finance team_manager (the analytics UAT persona
# from the spec). Used exclusively under ANALYTICS_AUTH_BYPASS=true. The
# team id is resolved from the finance team's slug at request time because
# team record ids are server-generated.
BYPASS_TEAM_SLUG = "finance"

# Pre-resolution fallback only; replaced by the real finance team id below.
BYPASS_USER_TEAM_FALLBACK = "team:finance"


async def _bypass_user() -> CurrentUser:
    team_id = BYPASS_USER_TEAM_FALLBACK
    try:
        from open_notebook.database.repository import repo_query

        records = await repo_query(
            "SELECT id FROM team WHERE slug = $slug LIMIT 1",
            {"slug": BYPASS_TEAM_SLUG},
        )
        if records:
            team_id = str(records[0]["id"])
    except Exception:
        pass
    return BYPASS_USER.model_copy(update={"team_id": team_id})


BYPASS_USER = CurrentUser(
    id="app_user:dev-bypass",
    email="dev-bypass@example.com",
    display_name="Dev Bypass (Daniel)",
    organization_id="organization:default",
    team_id=BYPASS_USER_TEAM_FALLBACK,
    role="team_manager",
)


async def get_current_user() -> CurrentUser:
    """Resolve the caller. Native sessions (T3) replace the bypass later."""
    if analytics_auth_bypass_enabled():
        return await _bypass_user()
    raise AuthenticationError(
        "Authentication required. Set ANALYTICS_AUTH_BYPASS=true for local "
        "development until native sessions land."
    )


async def get_permitted_dataset_ids(
    user: CurrentUser = Depends(get_current_user),
) -> list[str]:
    """Dataset ids the caller may query (all of them under the bypass)."""
    from open_notebook.analytics.service import permitted_dataset_ids

    return await permitted_dataset_ids(user)
