---
knowledgebase: open-notebook
tags:
  - knowledgebase
  - llm-reference
  - open-notebook
  - surrealdb
---

# SELECT * FROM $ids returns strings when elements are plain record-id strings

## Challenge

During T6 (content classification), `GET /api/migration/status` returned a 500 in the live API while all unit tests (fake repo) passed: `AttributeError: 'str' object has no attribute 'get'` inside the linked-sources query.

## Cause

`repo_query` runs `parse_record_ids` on every result, so edge fields (`in`/`out` on `reference`) arrive as **plain strings** (e.g. `"source:abc"`). Passing those strings back as `"SELECT * FROM $ids", {"ids": ["source:abc"]}` makes SurrealDB return the unresolved strings themselves — a list of str, not records. With RecordID-typed elements it resolves; with strings it silently doesn't. The fake-repo tests couldn't see this because the fake returned records for any `$ids` query.

## Solution

Use a table-scoped query instead: `SELECT * FROM source WHERE id IN $ids` (with `ensure_record_id` on each element). `SELECT ... FROM <table> WHERE id IN $ids` has a stable result shape regardless of element count or type. Pattern applied in `api/migration_service.py::_linked_sources`.

## Result

Live smoke test green: status/run/items endpoints work against the real SurrealDB; `tests/test_migration_classification.py` updated so the fake matches the real query shape.

## Related files

- `api/migration_service.py`
- `tests/test_migration_classification.py`
- `open_notebook/database/repository.py` (`repo_query`/`parse_record_ids`)

## Related notes

- [[SurrealDB record fields reject string record ids]]
- [[Local SurrealDB must be v2]]
