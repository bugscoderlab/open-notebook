# Team Access + Analytics — Frozen API Contracts (approved 2026-09-16)

Frontend tracks mock against these shapes until the backend lands. Changes here require updating this file first.

## Auth & identity

- `POST /api/auth/login` — body `{email, password}` → `204`; sets session cookie; failures: `429` (rate limited) or generic `401` (invalid credentials). Sets CSRF cookie.
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
- `PATCH /api/users/{id}` — `{team_id?, role?, status?}`; `status: disabled` revokes all sessions
- `GET /api/teams`, `POST /api/teams`, `PATCH /api/teams/{id}` — `{slug, name, description, manager_id, active}`
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

## Errors

- `401` unauthenticated · `403` authenticated but outside permitted scope · `404` unknown id · `422` validation · `429` rate limited. No stack traces in responses (existing behavior).
