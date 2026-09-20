# ADR-009: Analytics runs on PostgreSQL via SQLAlchemy/Alembic, scoped to one module

- **Status**: Accepted — superseded by [ADR-017](ADR-017-analytics-removal.md): the analytics subsystem is removed; the Postgres stack is no longer part of the product.
- **Date**: 2026-09
- **Related**: #1, `_jobbrief/tdd.md` §10, PDR-002 (provider-agnostic core)

## Context

The team-access MVP adds an analytics mode that answers questions like "highest spender this year" from structured sales data. The existing stack (SurrealDB + LangChain/esperanto) has no relational engine, no migration tool for SQL schemas, and no parameterized aggregate-query story the LLM can be fenced away from. The structured data cannot be served by the current stack without contorting SurrealDB into a job it does not do well.

## Decision

Add exactly one new connection: a dedicated PostgreSQL database (`open_notebook_analytics`) managed by SQLAlchemy 2.0 (async, asyncpg) and Alembic. All new dependencies are scoped to the `open_notebook/analytics/` module; the rest of the application never imports them. Local development uses Postgres.app (no Docker on localhost); dataset ownership metadata and the query log stay in SurrealDB. Queries are allowlisted templates assembled server-side with mandatory permission filters; the LLM only ever sees the small result set, never writes SQL.

## Alternatives considered

- **SurrealDB tables for analytics** — zero new deps, but SurrealDB's aggregation/parameterization story is weak, and it turns the knowledge-base DB into a mixed workload. Rejected.
- **DuckDB (embedded)** — excellent for files-in-process, but no real credential model for "read-only credentials", single-writer, and stalls at a second concurrent writer. Rejected.
- **A generalized external-connection framework (`source_type: postgres|parquet|csv` from the TDD)** — generalizes before the first dataset exists. Deferred; the schema keeps room to grow into it.

## Consequences

- `pyproject.toml` gains `sqlalchemy[asyncio]`, `asyncpg`, `alembic` — three deps, one module.
- Analytics correctness tests need a real Postgres; they run as the `integration` tier with per-worker test databases.
- The `dataset` record's `connection_ref` names an env-configured DSN key — raw credentials are never stored in SurrealDB.
