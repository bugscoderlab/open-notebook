# Team Access + Analytics — Frozen API Contracts (approved 2026-09-16)

Frontend tracks mock against these shapes until the backend lands. Changes here require updating this file first.

## Auth & identity

- `POST /api/auth/login` — body `{email, password}` → `204`; sets session cookie; failures: `429` (rate limited) or generic `401` (invalid credentials). Sets CSRF cookie. T6 adds one deliberate non-credential failure: `403` when a `member`/`team_manager` signs in before the content-classification pass has completed (the response `detail` explains the migration gate; admins and the CEO are exempt).
- `POST /api/auth/logout` — `204`; requires CSRF header.
- `GET /api/auth/me` → **AuthUser**:
  ```json
  {
    "id": "app_user:…",
    "email": "aisha@company.com",
    "display_name": "Aisha Hassan",
    "role": "member",
    "team": {"id": "team:…", "slug": "hr", "name": "HR"}
  }
  ```
  `role ∈ member|team_manager|ceo|admin`. Unauthenticated → `401`.
- `POST /api/auth/invite` (admin) — `{email, display_name, team_id, role, temp_password}` → `201`; no email is sent (SMTP is future development).

## Users & teams (admin)

- `GET /api/users` — list with `{id, email, display_name, role, status, team_id, team_name, last_active_at}`
- `PATCH /api/users/{id}` — `{team_id?, role?, status?, temp_password?}`; `status: disabled` revokes all sessions; `temp_password` regenerates the sign-in password (the no-SMTP "resend invite" — the admin re-shares it out-of-band) and revokes all sessions
- `GET /api/teams` — list with `{id, slug, name, description, manager_id, manager_name, active, member_count, notebook_count}`
- `POST /api/teams` — `{slug, name, description?}` → `201`; slug unique, immutable afterwards (T6 classification tokens match on it)
- `PATCH /api/teams/{id}` — `{name?, description?, manager_id?, active?}` (`manager_id: null` unassigns; `active: false` archives)
- Mutations require the CSRF header (logout rule, ADR-010).
- Non-admin → `403` on all of the above.

## Notebooks & sources (payload additions)

- Notebook/source objects gain: `team_id`, `team_name`, `visibility` (`team|company_shared`), `created_by`. Lists are pre-filtered server-side to the caller's permitted scope.
- `POST /api/notebooks` body gains optional `visibility`; `team_id` is taken from the caller (members cannot choose another team); admins may pass `team_id`.
- Link validation: cross-team `POST /api/notebooks/{id}/sources/{sid}` → `403`.

## Search & ask (scope)

- Existing `scope_notebook_ids` semantics change: **empty = all permitted notebooks** (never the whole KB); non-empty = intersection with permitted set. Response shape unchanged.

## Analytics

- `GET /api/analytics/datasets` → `[{id, name, team_id, team_name, source_type, freshness_at}]` (permitted only)
- `POST /api/analytics/ask` — `{question, dataset_id?, include_refunds?: false}` → **AnalyticsAnswer**:
  ```json
  {
    "query_id": "analytics_query_log:…",
    "status": "ok | denied | no_data",
    "answer_text": "Sarah Lim is the highest spender …",
    "kpis": [{"label": "TOTAL SPEND", "value": "MYR 8,460", "note": "+18.4% vs last year"}],
    "table": {"columns": ["customer", "total_spend", "transactions"], "rows": [["Sarah Lim", 8460, 24]]},
    "chart": {"kind": "bars", "title": "Top customers by spend", "items": [{"label": "Sarah Lim", "value": 8460}]},
    "scope": {"dataset": "Sales 2026", "period": "2026-01-01 → 2026-09-14", "refunds": "excluded"},
    "freshness_at": "2026-09-14T00:00:00Z",
    "query_template": "SELECT … WHERE … AND data_team IN (:authorized_team_ids) …"
  }
  ```
  `denied`: only `{status: "denied", answer_text}` — no names/values/rankings, ever (AN-003/010).
- `GET /api/analytics/queries/{id}` — the stored answer + rendered parameterized query (AN-009).

## Migration (T6, admin-only)

- `GET /api/migration/status` → `{completed, completed_at, flagged_notebooks, flagged_sources}` — `completed` is `organization.classification_completed_at` (migration 29); member/team_manager login is blocked until it is set. Completion is **one-way**: once set it is never cleared, so content uploaded later cannot lock members out again (ADR-013).
- `GET /api/migration/items` → `{notebooks: [{id, name, reason, linked_teams}], sources: [{id, title, reason, linked_teams}]}` — teamless rows the pass cannot auto-place. `reason ∈ ambiguous_tokens | mixed_team_sources | cross_team_link | unclassified`; teamless rows are admin-only by the T5 policy (ADR-012), a flag needs no storage (ADR-013).
- `POST /api/migration/run` → one idempotent classification pass: filename-token matching on sources (`hr|finance|executive|company_shared`, whole-word, case-insensitive), notebooks inherit from their linked sources, no-token ⇒ `company_shared` (Executive-owned; recorded deviation). Returns `{classified_sources, classified_notebooks, flagged_sources, flagged_notebooks, completed, actions}`.
- `PATCH /api/migration/items/{kind}/{id}` (kind ∈ `notebook|source`) — `{team_id, visibility?}` → manual resolution; stamping the last flagged item completes the migration and enables member sign-in.

## Errors

- `401` unauthenticated · `403` authenticated but outside permitted scope · `404` unknown id · `422` validation · `429` rate limited. No stack traces in responses (existing behavior).

## Auth notes

- `GET /api/auth/status` (compatibility probe, pre-team-access): `{"auth_enabled": <any app_user exists>}`. The T3 frontend probes it to detect open mode (no users seeded yet → the app stays unlocked, matching the old dev default). Removal is deferred until the app requires users (after T6).
- `POST /api/auth/invite` (admin) — implemented in T4 (see Users & teams above).
