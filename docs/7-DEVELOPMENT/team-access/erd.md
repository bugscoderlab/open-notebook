# Team Access — Frozen ERD (approved 2026-09-16, applied as migrations 26/27)

> **Field naming convention (repo style):** SurrealDB record fields are named by their target table (`organization`, `team`, `manager`, `created_by`, `user`), not `<x>_id`. Domain models expose them as `organization_id` / `team_id` / `created_by`. Applied and verified against the live database on 2026-09-16.

## SurrealDB (`open_notebook`) — new in migration 26

- **organization**: id, `external_key` (immutable UUID, future billing identity), name, status (`active|suspended`), created, updated
- **team**: id, `organization_id` → organization, slug (`hr|finance|executive`), name, description, `manager_id` (option → app_user), active, created, updated
- **app_user**: id, `organization_id`, **email (unique index)**, `password_hash` (never serialized), display_name, `team_id` → team, role (`member|team_manager|ceo|admin`), status (`invited|active|disabled`), `last_active_at`, created, updated
- **user_session**: id, `user_id` → app_user, **token_hash (unique index)**, expires_at, `last_seen_at`, revoked_at, created
- **notebook** (modified): + `organization_id`, `team_id`, `visibility` (`team|company_shared`), `created_by`; index `(team_id, visibility)`
- **source** (modified): + same four fields; index `(team_id, visibility)`

## SurrealDB — new in migration 27 (analytics; removed — ADR-017)

The `dataset` registry and `analytics_query_log` entities below were removed from the product with the analytics subsystem (ADR-017, superseding ADR-009). The tables and their rows are left in place — migrations are append-only history, no cleanup migration is written.

- ~~**dataset**: id, `organization_id`, name, `team_id` (owning team), `source_type` (`postgres` only, now), `connection_ref` (env key, **never raw credentials**), schema_metadata, `freshness_at`, active~~
- ~~**analytics_query_log**: id, `user_id`, `dataset_id`, question, `template_id`, `duration_ms`, `row_count`, status, created~~

## SurrealDB — unchanged (scope derived through parent)

- `reference` graph edge source→notebook (many-to-many; same-team validation on link; company-shared source only to company-shared notebook)
- `artifact` edge note→notebook; chat edge chat_session→notebook|source (migration 8)
- `source 1—N source_embedding`, `source 1—N source_insight`; `note` with embedding
- No `team_id` duplicated onto note/insight/chat/episode in MVP (duplicate later only if profiling shows slow relationship checks)
- Peripheral tables untouched: episode, podcast_config, transformation, credential, model, command, profiles

## PostgreSQL — removed (ADR-017)

~~`open_notebook_analytics` (Alembic) — new connection~~ Removed with the analytics subsystem (ADR-017, superseding ADR-009): the `sales_transactions` table, its indexes, and the Alembic/seed tooling are gone from the product. The database itself is **not dropped** — the application simply stops connecting to it.
