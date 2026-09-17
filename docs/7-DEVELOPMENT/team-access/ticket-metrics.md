# Team Access + Analytics — Ticket Metrics

Duration + token usage per ticket, for costing. Durations are measured from ticket start to verification-green. Token counts are read from the opencode session panel (TUI) at each ticket boundary — the agent cannot read its own token meter directly, so the `Tokens` column is filled from the session panel and may lag.

| Ticket | Started | Ended | Duration | Tokens (in/out) | Notes |
|---|---|---|---|---|---|
| T1 Scaffolding & plan docs | 2026-09-16 (session) | same session | ~25 min (est.) | see session panel | Spec #1 + tickets #2–#11 also published during this window; 751 tests green |
| T2 Prototype shell | same session | same session | ~15 min (est.) | see session panel | Subagent implementation; incl. subagent tokens (combined, best-effort) |
| Physical ERD (migrations 26/27 + Alembic) | 2026-09-16 (session) | same session | ~30 min | see session panel | Migrations 26/27 applied (version 27); sales_transactions + 2 indexes in Postgres; 751 tests green; tickets #2/#3 closed |
| T3 Identity core + login UI | 2026-09-17 (session) | same session | ~80 min (est., backend + frontend slices) | see session panel | Cookie-session auth end to end (ADR-010): backend (domain/auth_service/router, middleware removal, bootstrap + dev-seed CLI) + frontend (store rewrite, CSRF plumbing, login page, guard, identity in shell); 826 backend + 194 frontend tests green; live-verified all 4 personas; issue #4 closed |
| T6 Migration & classification | 2026-09-17 21:05 | 2026-09-17 22:05 | ~60 min | see session panel | Classification pass (ADR-013: flags = teamlessness), migration 29 + login gate (403 → migration_pending), admin migration screen + 14 locales; live-verified on dev stack; 1002 backend + 220 frontend tests green; issue #7 closed |

## Convention

- Start/end timestamps recorded per ticket; subagent tokens combined into the parent ticket row (best-effort).
- Token source: opencode TUI session panel at ticket boundaries.
