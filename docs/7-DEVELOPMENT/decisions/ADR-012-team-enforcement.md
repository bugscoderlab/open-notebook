# ADR-012: Team enforcement — the access seam, member write access, and empty-scope semantics

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: #1 (spec), #6 (T5), ADR-010 (cookie sessions — the identity layer this enforces against), CONTEXT.md (glossary), `_jobbrief/tdd.md` §7/§9/§12

## Context

T5 turns the single-user API into a team-scoped one: every retrieval path (lists, full-text/vector search, ask/chat, context building, downloads, podcasts, embeddings) must be filtered server-side by the caller's permitted scope, and guessed record ids must yield nothing. Three spec points needed pinning down because the source TDD and the glossary disagree or are silent:

1. **Member write access** — `_jobbrief/tdd.md` §7's `can_write_team` gives write to admin and team_manager only, but CONTEXT.md defines a member as "read/write access limited to their own team's content", and spec story 6 (Aisha creates/edits notebooks and sources in HR) requires it.
2. **Unclassified content** — rows created before the T6 classification pass have no team. TDD §12.10 says "until classified, existing content is Admin-only"; the spec's deviation note only covers what happens *after* classification (default to company-shared).
3. **Empty permitted scope** — `fn::text_search`/`fn::vector_search` (migrations 24/25) treat an empty `$notebook_ids` array as *unscoped*, so "this user may read zero notebooks" cannot be expressed in SQL.

## Decision

1. **Members write their own team.** `can_write_team` = org check, then admin → true, then plain `user.team_id == team_id` for everyone else (member, team_manager, and CEO — the CEO writes only Executive because that is their team; company-shared content is written through its owning team, Executive). Recorded as a deviation from TDD §7, superseded by CONTEXT.md + spec story 6.
2. **Unclassified content is admin-only** until the T6 classification pass (TDD §12.10 as written). CEO does *not* read unclassified rows — "reads every team's business content" implies a team exists. The permitted-scope queries add `team IS NOT NONE` for member/manager so an empty caller team can't match `team = NONE`.
3. **The org check fails closed**: a resource that carries an organization is never visible to a caller whose own organization can't be established; only org-less legacy rows skip the check.
4. **Zero-permission short-circuits in Python.** Routers check `effective_notebook_scope()` (permitted ∩ requested; empty request = all permitted; foreign requested ids silently dropped, malformed ids still 400, survivors existence-checked) and return an empty result / honest "I don't have access to any notebooks" answer without calling the SQL functions. The ask graph additionally treats an explicitly-empty `notebook_ids` as "no results" while an absent key keeps the legacy global behavior. No migration to the SQL functions was needed.
5. **Enforcement lives at the router seam**, per TDD §7: `api/access.py` exposes the policy (`can_read_team`/`can_write_team`), the scope (`permitted_notebook_ids`/`permitted_source_ids`/`permitted_episode_ids`, one indexed query per table), and per-object guards (`check_notebook_read/write`, `check_source_read/write`, `check_note_*`, `check_chat_session_*`, `check_episode_*`, `check_insight_*`). Read denials on single objects raise `NotFoundError` (no existence oracle); write denials raise `ForbiddenError` after readability is established. Notes/sessions/insights derive access from their parent notebook/source via `artifact`/`refers_to` edges — no duplicated team fields (spec: migration 28 is the exception, see 7).
6. **Search scope is server-authoritative** (TDD §9): the permitted set intersects any client scope *inside* the SurrealQL via the existing `$notebook_ids` parameter — never post-filtered, never widened.
7. **Episodes get ownership fields (migration 28)** because a podcast job's payload carries only a content string — there is no relationship to derive scope from at list time. Generation stamps team/visibility from the source notebook; the worker revalidates that the notebook's team still matches before creating the episode.
8. **Workers revalidate the recorded scope before processing** (TDD §8): podcast generation (notebook team match), `embed_source`/`embed_note` (caller team recorded in the payload by the API embed endpoint; foreign/unclassified-abort rules), `process_source` (source team must match every target notebook's team), and the rebuild collector (scope filters every query; admins submit the global scope). Domain-submitted embed jobs carry no scope and skip revalidation — their submit paths are already router-gated.
9. **Config surface**: models/credentials/settings/transformations/profiles/providers are authenticated reads; mutations and secret-bearing routes are `require_admin` (TDD §7). The generic `POST /commands/jobs` is admin-only (arbitrary remote job execution); job status/list/cancel stay authenticated because the frontend polls status and results carry no content for the gated flows.

## Alternatives considered

- **Retrofit scope into `ObjectModel.get`** — one choke point for every get-by-id, but it has no caller context and would thread a user/scope through every domain call. Router-level guards keep the domain layer identity-free. Rejected.
- **Migration 28 to make `[]` mean "match nothing" in the SQL functions** — correct but touches hot search functions for a case the router short-circuit already handles; revisit only if a second caller of the functions appears. Rejected for now.
- **CEO reads unclassified too** — tempting ("complete view"), but classification is the admin's job in the T6 flow and §12.10 is explicit. Rejected.
- **Allow members to submit any team_id at creation and filter later** — creation stamps the caller's team server-side; a client-submitted foreign team_id is impossible by construction, which is cheaper than validating. Accepted.

## Consequences

- ~110 endpoints across 20 routers now resolve `get_current_user`; public surface is `/`, `/health`, `/docs`, `/api/auth/*`, `/api/config`, `/api/languages`, `/api/capabilities`.
- Record-typed fields (organization/team/created_by) forced `Notebook.create`/`Source.create` onto the raw `CREATE ... CONTENT` path (per the AGENTS.md rule); updates use the `UPDATE ... MERGE` path with `RecordID` values.
- Same-team link rule lives in the domain (`add_to_notebook` for sources and notes): cross-team links and company-shared→non-shared links are rejected; unclassified items link freely and are left for the T6 flag pass.
- Test strategy: pure policy matrix (`test_access_policy.py`), domain link/aliasing (`test_team_linking.py`), and an in-memory-SurrealDB access matrix (`test_team_scoping.py`) where the canary phrases really exist in the DB — so a missing WHERE clause fails loudly. Characterization suites gained `auth_session` fixtures.
- T9 must remove the `ANALYTICS_AUTH_BYPASS` path from `get_current_user` (the bypass persona currently short-circuits team enforcement for the analytics router only).
