"""Team-access bootstrap and dev seeding (decision-log #4 and #11, issue #4/T3).

``bootstrap`` seeds the default organization, the three department teams
(HR/Finance/Executive) and the first admin (Executive team), and refuses to
run when any app_user exists. ``dev_seed`` idempotently upserts the four
UAT personas (Aisha/Daniel/Mei/Alex, all password "password") — DEV ONLY,
never for production.

CLI entry point: ``python -m open_notebook.admin``.
"""

from typing import Dict, List, Tuple

from open_notebook.domain.user import (
    count_users,
    create_user,
    ensure_default_organization,
    ensure_team,
    get_team_by_slug,
    get_user_by_email,
    hash_password,
    update_user,
)

TEAMS: List[Tuple[str, str]] = [
    ("hr", "HR"),
    ("finance", "Finance"),
    ("executive", "Executive"),
]

DEV_PASSWORD = "password"
DEV_PERSONAS: List[Dict[str, str]] = [
    {
        "email": "aisha@company.com",
        "display_name": "Aisha Hassan",
        "team_slug": "hr",
        "role": "member",
    },
    {
        "email": "daniel@company.com",
        "display_name": "Daniel Krishnan",
        "team_slug": "finance",
        "role": "team_manager",
    },
    {
        "email": "mei@company.com",
        "display_name": "Mei Tan",
        "team_slug": "executive",
        "role": "ceo",
    },
    {
        "email": "alex@company.com",
        "display_name": "Alex Rivera",
        "team_slug": "executive",
        "role": "admin",
    },
]


class BootstrapError(Exception):
    """Bootstrap refused (users exist) or dev-seed prerequisites missing."""


async def bootstrap(email: str, password: str, display_name: str = "Admin") -> Dict[str, str]:
    """Seed org + teams + the first admin (Executive). Refuses when users exist."""
    if await count_users() > 0:
        raise BootstrapError(
            "Refusing to bootstrap: app_user rows already exist. "
            "Manage users via the admin UI (invite flow) instead."
        )
    organization_id = await ensure_default_organization()
    team_ids = {}
    for slug, name in TEAMS:
        team_ids[slug] = await ensure_team(organization_id, slug, name)

    admin = await create_user(
        organization_id=organization_id,
        email=email.strip().lower(),
        password_hash=hash_password(password),
        display_name=display_name,
        team_id=team_ids["executive"],
        role="admin",
        status="active",
    )
    return {
        "organization_id": organization_id,
        "admin_id": admin.id or "",
        "teams": ", ".join(sorted(team_ids)),
    }


async def dev_seed() -> List[str]:
    """Idempotently upsert the four UAT personas (DEV ONLY — never production).

    Re-running resets each persona's password to the dev password and their
    role/team/status to the canonical values, so the UAT state is always
    reproducible. Requires the bootstrap teams to exist.
    """
    messages: List[str] = []
    for persona in DEV_PERSONAS:
        team = await get_team_by_slug(persona["team_slug"])
        if team is None or team.id is None:
            raise BootstrapError(
                f"Team {persona['team_slug']!r} does not exist. "
                "Run `python -m open_notebook.admin bootstrap` first."
            )
        existing = await get_user_by_email(persona["email"])
        if existing is not None and existing.id:
            await update_user(
                existing.id,
                password_hash=hash_password(DEV_PASSWORD),
                display_name=persona["display_name"],
                team_id=team.id,
                role=persona["role"],
                status="active",
            )
            messages.append(f"reset {persona['email']} ({persona['role']})")
        else:
            await create_user(
                organization_id=team.organization_id,
                email=persona["email"],
                password_hash=hash_password(DEV_PASSWORD),
                display_name=persona["display_name"],
                team_id=team.id,
                role=persona["role"],
                status="active",
            )
            messages.append(f"created {persona['email']} ({persona['role']})")
    return messages
