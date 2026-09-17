"""Content classification service (issue #7/T6) — migration business logic.

Classifies pre-team-access content (rows without a team) into teams:

- **Sources first**: the filename/title is matched against whole-word,
  case-insensitive tokens (``hr`` / ``finance`` / ``executive`` /
  ``company_shared`` / ``company-shared``). Exactly one team token → that
  team; a company token alongside → ``company_shared`` visibility; no token
  at all → ``company_shared`` (the recorded deviation from TDD §12.10).
  Multiple distinct team tokens → ambiguous: the source stays teamless
  (admin-only) and is listed for manual resolution.
- **Notebooks inherit**: a notebook takes the single distinct team of its
  linked sources (``company_shared`` visibility when any linked source is
  company-shared; no sources or only teamless sources → company-shared
  default). Linked sources spanning more than one team → the notebook
  stays teamless (mixed-team flag).
- **Cross-team links**: a classified source linked to notebooks owned by a
  different team is un-classified again (teamless = admin-only) and listed
  for manual resolution.

Flags need no storage: a flagged item is simply a row that still has no
team, and ``can_read_team`` already makes teamless content admin-only
(ADR-012). The migration screen lists teamless rows that the pass cannot
auto-place.

The pass is idempotent — it only looks at teamless rows, so a second run
classifies nothing new and never overwrites an existing stamp. Every
assignment is logged (loguru) and completion is recorded on the
organization (migration 29): member/team_manager login stays blocked
until no flagged content remains (TDD §12.11).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Set

from loguru import logger

from api.access import CurrentUser
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain import user as user_domain
from open_notebook.exceptions import InvalidInputError, NotFoundError

# Whole-token, case-insensitive match. ``-``/``_``/``.``/space are separators;
# ``hr`` must not match inside "chart" and ``company-shared`` accepts both
# spellings (TDD §5.2 uses the hyphen, the repo vocabulary uses the
# underscore).
_TOKEN_RE = re.compile(
    r"(?<![a-z0-9])(hr|finance|executive|company[-_]shared)(?![a-z0-9])",
    re.IGNORECASE,
)

TEAM_TOKENS = ("hr", "finance", "executive")
COMPANY_SHARED_VISIBILITY = "company_shared"
# Company-shared content is owned by the Executive team (CONTEXT.md
# vocabulary): it is a visibility value, never a team row, but rows still
# need a team stamp for the SQL-side permitted-scope queries
# (`team IS NOT NONE AND (team = $team OR visibility = 'company_shared')`).
COMPANY_SHARED_TEAM_SLUG = "executive"

REASON_AMBIGUOUS_TOKENS = "ambiguous_tokens"
REASON_MIXED_TEAM = "mixed_team_sources"
REASON_CROSS_TEAM_LINK = "cross_team_link"

TEAMS_MISSING = (
    "Classification teams are not set up — run "
    "`python -m open_notebook.admin bootstrap` first"
)
ITEM_NOT_FOUND = "Migration item not found"
INVALID_KIND = "kind must be 'notebook' or 'source'"
INVALID_VISIBILITY = "visibility must be 'team' or 'company_shared'"

MEMBER_LOGIN_BLOCKED = (
    "Content migration is in progress — member sign-in is temporarily "
    "disabled. Please contact your administrator."
)


@dataclass(frozen=True)
class SourceDecision:
    """Pure outcome of token-matching one source filename/title."""

    team_slug: Optional[str] = None  # None → company-shared default
    visibility: str = "team"
    ambiguous: bool = False


@dataclass
class RunSummary:
    """Counts returned by one classification pass."""

    classified_sources: int = 0
    classified_notebooks: int = 0
    flagged_sources: int = 0
    flagged_notebooks: int = 0
    completed: bool = False
    actions: List[str] = field(default_factory=list)


def extract_tokens(text: str) -> Set[str]:
    """Canonical (normalized) classification tokens found in ``text``.

    Whole-word, case-insensitive; the company token always returns as
    ``company_shared`` regardless of the ``-``/``_`` spelling in the text.
    """
    if not text:
        return set()
    return {_normalize_token(match.lower()) for match in _TOKEN_RE.findall(text)}


def _normalize_token(token: str) -> str:
    return "company_shared" if token in ("company-shared", "company_shared") else token


def classify_source_title(title: str) -> SourceDecision:
    """Map a source filename/title to its team classification (pure).

    - >1 distinct team tokens → ambiguous (flag, no team).
    - exactly 1 team token → that team; company token alongside upgrades
      visibility to ``company_shared``.
    - no team token → company-shared default (team stamped to Executive at
      write time).
    """
    tokens = {_normalize_token(t) for t in extract_tokens(title)}
    team_tokens = sorted(t for t in tokens if t in TEAM_TOKENS)
    has_company_token = COMPANY_SHARED_VISIBILITY in tokens
    if len(team_tokens) > 1:
        return SourceDecision(ambiguous=True)
    if len(team_tokens) == 1:
        visibility = COMPANY_SHARED_VISIBILITY if has_company_token else "team"
        return SourceDecision(team_slug=team_tokens[0], visibility=visibility)
    return SourceDecision(team_slug=None, visibility=COMPANY_SHARED_VISIBILITY)


def decide_notebook_team(
    source_teams: List[Optional[str]], source_visibilities: List[str]
) -> SourceDecision:
    """Notebook inheritance rule (pure): from its linked sources' team SLUGS.

    - 0 distinct source teams → company-shared default.
    - 1 distinct source team → that team; ``company_shared`` visibility when
      any linked source is company-shared.
    - >1 distinct source teams → ambiguous (mixed-team notebook, flag).
    """
    distinct = {team for team in source_teams if team}
    if len(distinct) > 1:
        return SourceDecision(ambiguous=True)
    if len(distinct) == 1:
        visibility = (
            COMPANY_SHARED_VISIBILITY
            if COMPANY_SHARED_VISIBILITY in source_visibilities
            else "team"
        )
        return SourceDecision(team_slug=next(iter(distinct)), visibility=visibility)
    return SourceDecision(team_slug=None, visibility=COMPANY_SHARED_VISIBILITY)


def _source_title(record: Dict[str, Any]) -> str:
    """Best filename evidence: title first, then the uploaded asset's path."""
    if record.get("title"):
        return str(record["title"])
    asset = record.get("asset")
    if isinstance(asset, dict) and asset.get("file_path"):
        return str(asset["file_path"])
    return ""


async def _load_teamless(table: str) -> List[Dict[str, Any]]:
    return await repo_query(f"SELECT * FROM {table} WHERE team IS NONE")


async def _linked_sources(notebook_id: str) -> List[Dict[str, Any]]:
    """Sources linked to a notebook via the reference edge."""
    rows = await repo_query(
        "SELECT in AS source FROM reference WHERE out = $id",
        {"id": ensure_record_id(notebook_id)},
    )
    ids = [ensure_record_id(str(row["source"])) for row in rows if row.get("source")]
    if not ids:
        return []
    # Table-scoped IN (not `FROM $ids`): the edge fields arrive as plain
    # strings (parse_record_ids already ran) and `SELECT * FROM $ids` on
    # string elements returns the strings unresolved.
    return await repo_query(
        "SELECT * FROM source WHERE id IN $ids", {"ids": ids}
    )


async def _linked_notebook_teams(source_id: str) -> Set[str]:
    """Teams of the notebooks a source is linked to (classified ones)."""
    rows = await repo_query(
        "SELECT out.team AS team FROM reference WHERE in = $id AND out.team IS NOT NONE",
        {"id": ensure_record_id(source_id)},
    )
    return {str(row["team"]) for row in rows if row.get("team")}


async def _team_index() -> Dict[str, Dict[str, Any]]:
    """slug → team record for the classification tokens."""
    teams = {t.slug: t for t in await user_domain.list_teams()}
    missing = [slug for slug in TEAM_TOKENS if slug not in teams]
    if missing:
        raise InvalidInputError(TEAMS_MISSING)
    return {slug: teams[slug].model_dump(by_alias=True) for slug in TEAM_TOKENS}


def _team_ref(teams: Dict[str, Dict[str, Any]], slug: Optional[str]) -> Dict[str, Any]:
    """(team record id, organization id) for a decision slug.

    The company-shared default resolves to the Executive team — the owner
    of company-shared content per the vocabulary.
    """
    team = teams[slug or COMPANY_SHARED_TEAM_SLUG]
    return {"team": team["id"], "organization": team.get("organization")}


async def _stamp_item(
    item_id: str,
    *,
    team: Any,
    organization: Any,
    visibility: str,
    created_by: Optional[str],
) -> None:
    await repo_query(
        "UPDATE $id MERGE $data",
        {
            "id": ensure_record_id(item_id),
            "data": {
                "team": ensure_record_id(str(team)) if team else None,
                "organization": ensure_record_id(str(organization)) if organization else None,
                "visibility": visibility,
                "created_by": ensure_record_id(created_by) if created_by else None,
            },
        },
    )


async def _clear_team(item_id: str) -> None:
    """Un-classify an item (cross-team link flag): teamless = admin-only."""
    await repo_query(
        "UPDATE $id MERGE $data",
        {
            "id": ensure_record_id(item_id),
            "data": {"team": None, "visibility": "team"},
        },
    )


async def _flagged_notebooks() -> List[Dict[str, Any]]:
    """Teamless notebooks the pass cannot auto-place (mixed-team)."""
    flagged = []
    for record in await _load_teamless("notebook"):
        sources = await _linked_sources(str(record["id"]))
        teams = [str(s["team"]) for s in sources if s.get("team")]
        if len({t for t in teams}) > 1:
            flagged.append(record)
    return flagged


async def _slug_id_map() -> Dict[str, str]:
    """slug → team record id; empty when the classification teams are not
    set up yet (cross-team evaluation is skipped until then)."""
    try:
        teams = await _team_index()
    except InvalidInputError:
        return {}
    return {slug: str(record["id"]) for slug, record in teams.items()}


async def _flagged_sources(slug_ids: Dict[str, str]) -> List[Dict[str, Any]]:
    """Teamless sources the pass cannot auto-place (ambiguous tokens, or a
    filename team that conflicts with every linked notebook's team)."""
    flagged = []
    for record in await _load_teamless("source"):
        decision = classify_source_title(_source_title(record))
        if decision.ambiguous:
            flagged.append(record)
            continue
        team_id = slug_ids.get(decision.team_slug or COMPANY_SHARED_TEAM_SLUG)
        if team_id:
            notebook_teams = await _linked_notebook_teams(str(record["id"]))
            # Same-team link rule (T5): ANY linked notebook owned by a
            # different team makes the link cross-team.
            if notebook_teams and any(team != team_id for team in notebook_teams):
                flagged.append(record)
    return flagged


async def _remaining_flags() -> Dict[str, int]:
    slug_ids = await _slug_id_map()
    flagged_sources = len(await _flagged_sources(slug_ids))
    flagged_notebooks = len(await _flagged_notebooks())
    return {"sources": flagged_sources, "notebooks": flagged_notebooks}


async def _recompute_completion(organization_id: str) -> bool:
    """Stamp (or confirm) completion when nothing flagged remains.

    Completion is one-way in the MVP: once set it is not cleared, so a
    later ambiguous upload cannot lock members out again.
    """
    remaining = await _remaining_flags()
    if remaining["sources"] or remaining["notebooks"]:
        return False
    org = await user_domain.get_organization_by_id(organization_id)
    if org is None:
        return False
    if org.classification_completed_at is None:
        await user_domain.update_organization(
            organization_id,
            classification_completed_at=datetime.now(timezone.utc),
        )
        logger.info(
            f"Classification complete for {organization_id} — "
            "member sign-in enabled"
        )
    return True


async def member_login_allowed(organization_id: str) -> bool:
    """False until the classification pass has completed (TDD §12.11).

    Fail closed: an organization that cannot be resolved keeps members out.
    """
    if not organization_id:
        return False
    org = await user_domain.get_organization_by_id(organization_id)
    return org is not None and org.classification_completed_at is not None


async def migration_status(admin: CurrentUser) -> Dict[str, Any]:
    """Status card for the admin migration screen."""
    org = await user_domain.get_organization_by_id(admin.organization_id)
    remaining = await _remaining_flags()
    return {
        "completed": org is not None and org.classification_completed_at is not None,
        "completed_at": org.classification_completed_at if org else None,
        "flagged_notebooks": remaining["notebooks"],
        "flagged_sources": remaining["sources"],
    }


async def list_flagged_items(admin: CurrentUser) -> Dict[str, Any]:
    """Teamless notebooks/sources that need manual resolution, with reasons."""
    teams = await _team_index()

    def team_name(team_id: Optional[str]) -> str:
        if not team_id:
            return ""
        for slug, record in teams.items():
            if record["id"] == team_id:
                return record.get("name") or slug
        return ""

    notebooks = []
    for record in await _flagged_notebooks():
        sources = await _linked_sources(str(record["id"]))
        linked = sorted(
            {
                team_name(str(s["team"]))
                for s in sources
                if s.get("team")
            }
        )
        notebooks.append(
            {
                "id": str(record["id"]),
                "name": record.get("name") or "",
                "reason": REASON_MIXED_TEAM,
                "linked_teams": linked,
            }
        )
    sources = []
    slug_ids = {slug: str(record["id"]) for slug, record in teams.items()}
    for record in await _flagged_sources(slug_ids):
        decision = classify_source_title(_source_title(record))
        reason = (
            REASON_AMBIGUOUS_TOKENS if decision.ambiguous else REASON_CROSS_TEAM_LINK
        )
        sources.append(
            {
                "id": str(record["id"]),
                "title": record.get("title") or _source_title(record),
                "reason": reason,
                "linked_teams": sorted(
                    team_name(team_id)
                    for team_id in await _linked_notebook_teams(str(record["id"]))
                ),
            }
        )
    return {"notebooks": notebooks, "sources": sources}


async def run_classification(admin: CurrentUser) -> Dict[str, Any]:
    """One idempotent classification pass (see module docstring)."""
    teams = await _team_index()
    summary = RunSummary()

    # 1. Sources first (notebooks inherit from them).
    for record in await _load_teamless("source"):
        item_id = str(record["id"])
        decision = classify_source_title(_source_title(record))
        if decision.ambiguous:
            summary.flagged_sources += 1
            summary.actions.append(
                f"flagged source {item_id}: ambiguous tokens "
                f"({extract_tokens(_source_title(record))})"
            )
            continue
        ref = _team_ref(teams, decision.team_slug)
        await _stamp_item(
            item_id,
            team=ref["team"],
            organization=ref["organization"],
            visibility=decision.visibility,
            created_by=admin.id,
        )
        summary.classified_sources += 1
        summary.actions.append(
            f"classified source {item_id} → team {decision.team_slug or COMPANY_SHARED_TEAM_SLUG}"
            f" ({decision.visibility})"
        )

    # 2. Notebooks inherit from their linked sources (team ids → slugs).
    id_to_slug = {str(record["id"]): slug for slug, record in teams.items()}
    for record in await _load_teamless("notebook"):
        item_id = str(record["id"])
        linked = await _linked_sources(item_id)
        decision = decide_notebook_team(
            [
                id_to_slug.get(str(source["team"]))
                for source in linked
                if source.get("team")
            ],
            [source.get("visibility") or "team" for source in linked],
        )
        if decision.ambiguous:
            summary.flagged_notebooks += 1
            summary.actions.append(f"flagged notebook {item_id}: mixed-team sources")
            continue
        ref = _team_ref(teams, decision.team_slug)
        await _stamp_item(
            item_id,
            team=ref["team"],
            organization=ref["organization"],
            visibility=decision.visibility,
            created_by=admin.id,
        )
        summary.classified_notebooks += 1
        summary.actions.append(
            f"classified notebook {item_id} → team {decision.team_slug or COMPANY_SHARED_TEAM_SLUG}"
            f" ({decision.visibility})"
        )

    # 3. Cross-team source links: a source whose filename team disagrees
    #    with every linked notebook's team is un-classified (admin-only).
    for record in await repo_query("SELECT * FROM source WHERE team IS NOT NONE"):
        item_id = str(record["id"])
        team_id = str(record["team"])
        notebook_teams = await _linked_notebook_teams(item_id)
        if notebook_teams and any(team != team_id for team in notebook_teams):
            await _clear_team(item_id)
            summary.flagged_sources += 1
            summary.actions.append(
                f"flagged source {item_id}: cross-team link (source team "
                f"{team_id} ∉ notebook teams {sorted(notebook_teams)})"
            )

    for action in summary.actions:
        logger.info(f"classification: {action}")

    summary.completed = await _recompute_completion(admin.organization_id)
    logger.info(
        "classification pass: "
        f"{summary.classified_sources} sources + {summary.classified_notebooks} "
        f"notebooks classified, {summary.flagged_sources} sources + "
        f"{summary.flagged_notebooks} notebooks flagged, "
        f"completed={summary.completed}"
    )
    return {
        "classified_sources": summary.classified_sources,
        "classified_notebooks": summary.classified_notebooks,
        "flagged_sources": summary.flagged_sources,
        "flagged_notebooks": summary.flagged_notebooks,
        "completed": summary.completed,
        "actions": summary.actions,
    }


async def assign_item(
    admin: CurrentUser,
    kind: Literal["notebook", "source"],
    item_id: str,
    *,
    team_id: str,
    visibility: str = "team",
) -> Dict[str, Any]:
    """Manual resolution from the migration screen: stamp team/visibility.

    Re-checks completion after the assignment — resolving the last flagged
    item enables member sign-in.
    """
    if kind not in ("notebook", "source"):
        raise InvalidInputError(INVALID_KIND)
    if visibility not in ("team", COMPANY_SHARED_VISIBILITY):
        raise InvalidInputError(INVALID_VISIBILITY)
    team = await user_domain.get_team_by_id(team_id)
    if team is None or team.id is None:
        raise NotFoundError(ITEM_NOT_FOUND)
    table = "notebook" if kind == "notebook" else "source"
    records = await repo_query("SELECT * FROM $id", {"id": ensure_record_id(item_id)})
    if not records or not str(records[0].get("id", "")).startswith(f"{table}:"):
        raise NotFoundError(ITEM_NOT_FOUND)
    await _stamp_item(
        item_id,
        team=team.id,
        organization=team.organization_id,
        visibility=visibility,
        created_by=admin.id,
    )
    logger.info(
        f"classification: admin {admin.id} assigned {kind} {item_id} "
        f"→ team {team.slug} ({visibility})"
    )
    await _recompute_completion(admin.organization_id)
    return {
        "id": item_id,
        "kind": kind,
        "team_id": team.id,
        "team_name": team.name,
        "visibility": visibility,
    }
