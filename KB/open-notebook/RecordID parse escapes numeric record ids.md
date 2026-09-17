---
knowledgebase: open-notebook
tags:
  - knowledgebase
  - llm-reference
  - open-notebook
  - surrealdb
---

# RecordID.parse escapes numeric ids (source:02 becomes source:⟨02⟩)

## Challenge

Fake-repo tests for the T6 classification service failed with `KeyError: 'source:⟨⟨02\\⟩⟩'` (doubly-escaped angle brackets) even though the code looked correct.

## Cause

`surrealdb.RecordID.parse("source:02")` treats `02` as a numeric id and `str()` renders it escaped: `source:⟨02⟩`. Re-parsing that string escapes again (`source:⟨⟨02\⟩⟩`) — the round-trip is **not stable** for pure-numeric record ids. In production this never bites because SurrealDB generates random alphanumeric ids (`source:8f2k...`), so `str(RecordID.parse(x)) == x` holds. It only surfaces in tests that hand-make numeric-ish ids like `source:02`.

## Solution

In tests, use realistic alphanumeric record ids (`source:s02`, not `source:02`). When keying a fake store, canonicalize once with `str(RecordID.parse(id))` and never re-parse stored keys — pass `ensure_record_id(...)` only for query params, and only from the raw id, not from a canonicalized one.

## Result

`tests/test_migration_classification.py` uses `_rid()` canonicalization + alphanumeric ids; 58 tests green.

## Related files

- `tests/test_migration_classification.py`
- `open_notebook/database/repository.py` (`ensure_record_id`, `parse_record_ids`)

## Related notes

- [[SurrealDB FROM $ids returns strings for string elements]]
- [[SurrealDB record fields reject string record ids]]
