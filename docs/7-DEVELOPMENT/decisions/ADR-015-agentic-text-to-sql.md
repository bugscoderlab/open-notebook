# ADR-015: Agentic text-to-SQL for analytics — validation instead of forbidden

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: #20 (spec), #22–#28 (tickets), ADR-009 (analytics Postgres stack), ADR-011 (intent classification), ADR-014 (analytics permissions)

## Context

ADR-011 made "the LLM never writes SQL" the safety model: a classifier picked from an allowlist of six parameterized templates, and unmatched questions were refused. That made the question vocabulary a code artifact — every new analytical shape needed a template, a keyword rule, and tests, and phrasings like "who spend on full groom?" were unanswerable by construction. Spec #20 redirected the feature: analytics questions should be answered by LLM + database = result, with no hardcoded questions.

The hard requirements that motivated ADR-011 did not change: team permissions must be structurally unbypassable (ADR-014), execution must be read-only and resource-bounded (ADR-009), and answers must be honest (AN-008 — every number from the database, never invented).

## Decision

- **The model writes SQL; the server validates it.** The configured default chat model receives the dataset's schema context plus the question and generates SQL as text. Every generated string passes a pure validation gate (`sql_gate.validate_sql`, sqlglot/Postgres AST) before any value is bound or any statement executes: exactly one plain SELECT (set-operations rejected — no single outer WHERE for the mandatory filter), no write/DDL/utility nodes anywhere including CTE bodies, no server-access functions, every referenced table ⊆ the dataset allowlist, and a documented named-placeholder vocabulary.
- **The team filter becomes a provable placeholder.** Generated SQL must carry `data_team IN (:authorized_team_ids)` in its **outer** WHERE; the gate proves the predicate is present, rejects any other predicate on `data_team` at any nesting depth, and the values are bound server-side exactly like the template IN-list expansion (ADR-011's expansion mechanism is reused verbatim). A filter hidden in a subquery does not count: it would constrain the subquery's rows while leaking the outer query's.
- **Template path retained as the no-LLM fallback.** With no chat model configured, the existing classifier + templates answer from the real database exactly as before (AN-008's deterministic tier, fresh installs, the exact-number test-pack). Nothing is removed; the text-to-SQL branch sits in front.
- **Agentic, bounded loop.** Generation → validation → read-only execution, with gate rejections, SQL errors, and zero-row results fed back to the model as revision context, max 3 attempts. Outcomes map deterministically: legitimate zero rows → `no_data`; exhausted attempts → honest refusal (400 with guidance). The model never touches the database; its only tool is validate-and-execute.
- **Schema context is a one-time parquet snapshot** (tables, columns, types — never row data; rows would leak across teams). Interim by decision; live `information_schema` introspection replaces it later.
- **ADR-011 statements superseded**: the "LLM never sees SQL" rule, the template registry as the sole query vocabulary, and "unmatched questions are refused" as the default UX. **ADR-011 statements that still hold**: the keyword classifier + template fallback for no-model operation, IN-list placeholder expansion, period parsing on the template path, and NullPool engine pooling.

## Alternatives considered

- **Keep templates, few-shot the classifier harder** — the vocabulary stays a code artifact; every analytical shape remains a release. Rejected as the destination.
- **Text-to-SQL with string checks only** — pattern-matching a determined prompt ("hide the filter in a subquery") is a losing game; the AST gate makes the team-filter proof structural. sqlglot chosen as the parser (pure-Python, Postgres dialect); sqlparse's AST is too weak and blind `EXPLAIN` cannot see predicates.
- **Server wraps the generated query in an outer team filter** — requires the model to project `data_team` through every CTE; fragile and failure-prone. Rejected in favour of the required-placeholder contract.
- **Post-filter rows in Python** — cannot un-leak aggregates computed across all teams. Rejected.
- **Unbounded agent loop** — a hopeless question retries forever; the 3-attempt bound fails fast into an honest refusal.

## Consequences

- Adding an analytical question type is now a prompt away, not a template away; the six templates remain only as the deterministic fallback.
- The gate is a pure function, unit-testable without a database; the hostile matrix (stacked statements, DDL in CTEs, literal team filters, subquery-hidden filters, blocked functions) runs in the fast unit tier.
- New dependencies: sqlglot (validation), pyarrow (schema snapshot parquet) — both scoped to `open_notebook/analytics/` per ADR-009's module rule.
- The gate and the snapshot are interim-shaped on purpose: the schema source will move to live introspection, and the placeholder vocabulary may grow, without touching the pipeline shape.
