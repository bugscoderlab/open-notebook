# Team Access + Analytics — Decision Log

The numbered, approved decisions from the grilling session (2026-09-16). The spec is https://github.com/bugscoderlab/open-notebook/issues/1; tickets #2–#11. Deviations from `_jobbrief/tdd.md` are marked **DEVIATION**.

1. **Stack**: existing stack untouched. One new subsystem — analytics on PostgreSQL 17 (Postgres.app, `localhost:5432`) + SQLAlchemy 2.0 async + asyncpg + Alembic, scoped to `open_notebook/analytics/`. New deps: `sqlalchemy[asyncio]`, `asyncpg`, `alembic`. → ADR-009
2. **No Docker on localhost**: SurrealDB via native `surreal` binary → `make database-local`; Postgres via Postgres.app. Docker kept only for release/CI.
3. **Vocabulary**: `team` = department (HR/Finance/Executive, one manager); `visibility` = `team | company_shared`; company-shared is a visibility value owned by the Executive team — never a team row. See `CONTEXT.md`.
4. **Users**: no first-run setup wizard. CLI bootstrap (`uv run python -m open_notebook.admin bootstrap --email … --password …`) seeds the three teams and the first admin (Executive team), refuses when users exist. Admin-invite flow afterwards; **no SMTP** — invite = admin shares a temp password out-of-band; email delivery is future development.
5. **Analytics data**: fresh database `open_notebook_analytics` — never touch existing Postgres.app databases incl. `petz`. Alembic schema; CSV seed is idempotent and reproduces the exact test-pack numbers. `dataset` + `analytics_query_log` metadata in SurrealDB.
6. **Auth**: HTTP-only SameSite=Lax cookie session, SHA-256-hashed opaque token in `user_session`, CSRF token on mutations, login rate-limited, nothing token-shaped in localStorage; shared-password middleware removed. → ADR-010
7. **Migration**: filename-token auto-classification (`hr`/`finance`/`executive`/`company_shared`, case-insensitive); sources classified first, notebooks inherit; mixed-team notebooks flagged Admin-only. **DEVIATION (TDD §12.10)**: unclassified ⇒ `company_shared` (TDD said Admin-only). Approved by user; leak-risk default accepted for the synthetic test pack.
8. **Roles**: CEO reads all teams, writes only Executive; admin sole manager of users/teams/models/credentials/settings; team_manager writes all content in own team, no user management.
9. **Peripherals**: podcasts/notes/chats/episodes inherit parent scope; transformations stay global; Advanced page hidden in nav for non-admins (API remains, admin-only full scope); `require_admin` on models/credentials/settings/embedding-rebuild-all.
10. **Analytics UX**: Knowledge/Analytics mode switch on Ask & Search; parameterized SQL shown in the answer (AN-009); explainer = configured default chat model (no new setting); denial returns no names/values/rankings (AN-003/010).
11. **Dev seed**: four personas — Aisha (HR member), Daniel (Finance team_manager), Mei (CEO), Alex (Admin) — all password `password`, dev-only, clearly marked never-for-production.
12. **Debugging**: `opencode.json` at repo root with a Postgres MCP scoped to `open_notebook_analytics` (read-only).
13. **Classification (T6)**: flags are teamlessness (no flag column — ADR-013); company-shared content is stamped Executive-owned; `member`/`team_manager` login is gated on `organization.classification_completed_at` (403 until set); completion is one-way and auto-recomputed when the flag set empties. → ADR-013
14. **Analytics permissions (T9)**: `ANALYTICS_AUTH_BYPASS` deleted (not deprecated); AN-010 injection attempts are `denied` answers checked before everything else and audited with no dataset/template/rows; stored query logs enforce the same dataset ownership (404, no oracle; dataset-less logs are owner/admin/CEO only); no company-shared datasets in the MVP. → ADR-014

## Standing rules for the implementation run

- Migrations append-only; next numbers 26, 27; register in `open_notebook/database/async_migrate.py`.
- KB capture: every challenge + solution is recorded in Obsidian (`KB/open-notebook/`, kb-create format) at the moment it lands; consult prior KB notes before debugging.
- Update `AGENTS.md` files when structure/commands change.
- No git mutations (commit/push) without explicit request.
- Single-owner rule: frontend files are owned by the frontend track; backend tracks never edit them.
