---
knowledgebase: open-notebook
tags:
  - knowledgebase
  - llm-reference
  - open-notebook
  - surrealdb
  - migrations
---

# SurrealDB v2 DEFINE FIELD does not overwrite — IF NOT EXISTS silently skips

## Challenge

Migration 30 needed to change `analytics_query_log.dataset` from `record<dataset>` to `option<record<dataset>>` (AN-010 refusal logs have no dataset). Written as `DEFINE FIELD IF NOT EXISTS dataset ... TYPE option<record<dataset>>`, the migration "applied" (version bumped, no error) but the field never changed — writes with `dataset: None` still failed: *"Found NONE for field `dataset` … but expected a record<dataset>"*. Worse, the failed `CREATE` didn't raise a Python exception — `conn.query` returned the error as a plain **string**, which surfaced downstream as a confusing `AttributeError: 'str' object has no attribute 'get'` in `from_record`.

## Cause

Two SurrealDB v2 behaviours:
1. `DEFINE FIELD IF NOT EXISTS` on an existing field is a **no-op** — it skips the field entirely, so redefinition migrations appear to succeed while doing nothing.
2. Plain `DEFINE FIELD` on an existing field **errors** ("The field already exists") — v2 does not overwrite definitions. You must `REMOVE FIELD` first, then `DEFINE FIELD`.

## Solution

Migration that changes a field definition:

```sql
REMOVE FIELD IF EXISTS <field> ON TABLE <table>;
DEFINE FIELD <field> ON TABLE <table> TYPE <new type>;
```

Safe when the type change is relaxing (all existing rows satisfy the new type, as with non-optional → optional). Also: after any `conn.query` on a write path, treat a **str return** as an error signal — check `isinstance(result, list)` before indexing into records.

## Result

Migration 30 uses REMOVE + DEFINE; the live AN-010 refusal path writes dataset-less audit rows correctly.

## Related files

- `open_notebook/database/migrations/30.surrealql`
- `open_notebook/domain/analytics.py` (`create_query_log`)
- `open_notebook/database/repository.py`

## Related notes

- [[SurrealDB FROM $ids returns strings for string elements]]
- [[Local SurrealDB must be v2]]
