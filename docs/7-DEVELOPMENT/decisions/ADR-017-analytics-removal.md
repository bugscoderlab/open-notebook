# ADR-017: Remove the analytics chat subsystem — home chat reverts to knowledge-only

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: #30 (spec), #31–#35 (tickets); supersedes ADR-009, ADR-011, ADR-014, ADR-015; amends ADR-016

## Context

The product carried an analytics chat subsystem: a dedicated ask mode over a permission-filtered sales database in Postgres — ADR-009's second engine, ADR-011's templates, ADR-014's permissions, ADR-015's agentic text-to-SQL — plus auto-routing of business-data questions in the home chat (ADR-016). In practice the unified Ask/knowledge pipeline answers these questions better, and the subsystem is dead weight: five Python dependencies (SQLAlchemy, asyncpg, Alembic, sqlglot, pyarrow), a second database engine, a frozen API contract, 14 locales of UI strings, a dedicated CI Postgres service, and a classification hop on every home chat question.

## Decision

- **Full removal across all three tiers.** The analytics package, domain records, API router, home-chat classification/routing nodes, frontend analytics surface, translation keys, the five dependencies, and the CI Postgres service are deleted; the home chat graph becomes a linear knowledge pipeline (strategy → search → answer → suggestions). This **amends ADR-016**: home chat reverts to knowledge-only.
- **Supersedes ADR-009, ADR-011, ADR-014, and ADR-015** in full; the rationale for each subsystem choice is recorded in those records and no longer applies.
- **SurrealDB analytics tables stay in place.** `dataset` and `analytics_query_log` (migrations 27/30/31) and their rows are left untouched: migrations are append-only history, no cleanup migration is written, and existing audit rows persist (fresh installs simply create two small unused tables).
- **Postgres is ignored, not dropped.** No database deletion is performed or scripted; the application simply stops connecting to the external Postgres databases.
- **Re-introduction path**: revert this record.

## Alternatives considered

- **Keep the subsystem for business-data questions** — the knowledge pipeline already answers these questions better; keeping the subsystem means paying for a second engine and a routing hop forever. Rejected.
- **Drop the SurrealDB analytics tables too** — migrations are append-only history and the audit rows have value; a cleanup migration buys nothing. Rejected.
- **Drop the Postgres databases** — irreversible data destruction with no upside. The databases are left inert; only the connection goes away.

## Consequences

- Home chat answers every question through the knowledge pipeline; old sessions still load (legacy routing/refunds/analytics-payload state fields are ignored, never rendered).
- The dependency tree and CI shrink to SurrealDB-only infrastructure; the frozen API contract loses the analytics endpoints (breaking, shipped together with the frontend).
- The SurrealDB database carries two small unused tables until a future migration cycle chooses otherwise.
