# Team Access + Analytics — Ticket Metrics

Duration + token usage per ticket, for costing. Durations are measured from ticket start to verification-green. Token counts are read from the opencode session panel (TUI) at each ticket boundary — the agent cannot read its own token meter directly, so the `Tokens` column is filled from the session panel and may lag.

| Ticket | Started | Ended | Duration | Tokens (in/out) | Notes |
|---|---|---|---|---|---|
| T1 Scaffolding & plan docs | 2026-09-16 (session) | same session | ~25 min (est.) | see session panel | Spec #1 + tickets #2–#11 also published during this window; 751 tests green |
| T2 Prototype shell | same session | same session | ~15 min (est.) | see session panel | Subagent implementation; incl. subagent tokens (combined, best-effort) |
| Physical ERD (migrations 26/27 + Alembic) | 2026-09-16 (session) | same session | ~30 min | see session panel | Migrations 26/27 applied (version 27); sales_transactions + 2 indexes in Postgres; 751 tests green; tickets #2/#3 closed |

## Convention

- Start/end timestamps recorded per ticket; subagent tokens combined into the parent ticket row (best-effort).
- Token source: opencode TUI session panel at ticket boundaries.
