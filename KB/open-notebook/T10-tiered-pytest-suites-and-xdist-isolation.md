---
tags:
  - knowledgebase
  - llm-reference
  - testing
  - pytest
  - xdist
  - ci
  - open-notebook
---

# Tiered pytest suites and xdist per-worker isolation (T10)

## Challenge

Building the T10 acceptance gate (unit / integration / testpack tiers, pytest-split CI sharding, per-worker `open_notebook_analytics_test_gwN` Postgres DBs + `open_notebook_test_gwN` SurrealDB namespaces) surfaced three distinct failures:

1. Under `pytest -n auto`, all xdist workers silently shared ONE scratch Postgres database, so `test_analytics_migrations.py`'s `alembic downgrade base` dropped `sales_transactions` under other workers' running tests (`asyncpg.exceptions.UndefinedTableError`).
2. `test_refusal_log_visible_to_owner_and_privileged` (from T9) failed everywhere: it read `query_id` from a denied `/api/analytics/ask` response, but the T9-frozen denied contract is exactly `{status, answer_text}` — and Aisha's no-dataset refusal is never logged anyway.
3. `test_analytics_seed.py` assumed a fresh table (`inserted_1 == 77`), which only held when `test_analytics_migrations.py` happened to run first in the same serial process.

## Cause

1. **xdist env inheritance.** The controller process exports whatever `pytest_configure` sets into spawned workers. The conftest set `ANALYTICS_TEST_DATABASE_URL` (gw0) in the controller, workers inherited it, and the workers' own `pytest_configure` saw the var already set and skipped per-worker derivation.
2. **Spec drift between two API tests.** One test asserted the frozen no-id denied contract; the other was written against a planned contract that returned `query_id`. Both cannot pass.
3. **Order-dependent fixture state.** Serial alphabetical collection (api → migrations → seed → service) masked the dependency: migrations' `downgrade base` left a clean slate for the seed test. xdist distribution broke the ordering guarantee.

## Solution

1. **Workers always derive their own DB.** In `tests/conftest.py::pytest_configure`, if `PYTEST_XDIST_WORKER` is set, unconditionally overwrite `ANALYTICS_TEST_DATABASE_URL` from `ANALYTICS_TEST_BASE_URL` (default `postgresql+asyncpg://z@localhost:5432/postgres`) with database `open_notebook_analytics_test_<worker>`. Creation is **create-if-missing** (never drop+create — the controller and worker gw0 race on creation; `DuplicateDatabaseError` is swallowed). Only a serial run with an explicit pre-set `ANALYTICS_TEST_DATABASE_URL` is respected as-is.
2. **Test the intent, not the contradictory contract.** The refusal-visibility test now creates the logged refusal through the service layer (`ask_analytics_question` returns `query_id` for logged refusals) and exercises the HTTP visibility matrix (author/admin/CEO 200, foreign team 404) via `GET /api/analytics/queries/{id}`. The frozen router contract stays untouched.
3. **Self-sufficient seed test.** `test_analytics_seed.py` starts with `_alembic("downgrade", "base")` + `upgrade head` for a guaranteed clean slate regardless of predecessor suites.

Bonus pattern: the skip marker is a **factory** (`requires_analytics_pg()` in conftest), not a module-level mark — a shared mark object would evaluate `ANALYTICS_TEST_DATABASE_URL` at conftest import time, before `pytest_configure` creates the per-worker DB.

## Result

- `make test` (unit, xdist): 1012 passed.
- `make test-testpack` (testpack + integration, xdist): 82 passed, per-worker DBs/namespaces visible in `psql -l` and SurrealDB.
- CI shards with pytest-split using committed `.test_durations`.

## Related files / commands

- `tests/conftest.py` — tier env var, per-worker PG creation, Surreal namespace fixture
- `tests/testpack/` — canary sweep, access matrix, PDF extraction proof
- `_jobbrief/testdata/uat-checklist.md` — four-persona manual pass
- `make test-integration` / `make test-testpack` / `make test-durations`

## Tags / keywords

#knowledgebase #llm-reference #pytest #xdist #pytest-split #ci #test-isolation #open-notebook
