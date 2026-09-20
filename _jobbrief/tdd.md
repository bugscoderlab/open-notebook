# TDD: Modify Open Notebook for Team Access and Analytics

**Document type:** Technical Design Document - existing-system modification  
**Base repository:** <https://github.com/lfnovo/open-notebook>  
**Implementation strategy:** Fork and extend Open Notebook; do not rebuild it  
**MVP scope:** Internal, single-company deployment  
**Date:** 2026-09-15

## 1. Purpose

This project starts from the existing `lfnovo/open-notebook` repository. The current application, database, API, frontend, AI providers, document processing, vector search, notes, chat, transformations, podcasts, and deployment model remain in place.

The work is limited to adding:

1. Individual users instead of one shared application password.
2. Teams and roles.
3. Team ownership on existing notebooks and sources.
4. Server-side permission checks in existing APIs and search functions.
5. Users and Teams administration pages.
6. A Knowledge/Analytics mode in the existing Ask & Search page.
7. A small analytics module for permission-filtered queries such as "Who is the highest spender?"
8. Updated navigation and visual layout without replacing existing feature implementations.
9. Subscription-ready identifiers and interfaces for a later Lago integration, without implementing billing in this MVP.

This is a modification TDD. Unless a section explicitly says **New**, the implementation should edit or wrap the existing Open Notebook code.

## 2. Existing System to Preserve

Open Notebook currently provides:

- Next.js and React frontend in `frontend/`.
- FastAPI REST API in `api/`.
- Python domain logic in `open_notebook/`.
- SurrealDB repositories and migrations.
- Notebooks and reusable sources.
- Notes, insights, full-text search, vector search, and RAG.
- Notebook chat and source chat.
- Content transformations.
- Podcast generation and audio storage.
- Provider credentials and model configuration.
- Background processing and embedding rebuilds.
- Docker deployment.

All these features must continue to work. Team access is inserted into their existing data retrieval paths. It is not implemented as a separate replacement application.

## 3. Existing-to-Modified Mapping

| Existing Open Notebook component | Modification |
|---|---|
| Shared password middleware | Replace with native per-user session authentication |
| `Notebook` record | Add `team_id`, `visibility`, `created_by` |
| `Source` record | Add `team_id`, `visibility`, `created_by` |
| Notes, chats, podcasts | Inherit team from their existing notebook/source |
| Notebook/source routers | Add authorization dependency before current logic |
| Existing text/vector search | Add mandatory permitted-notebook filter |
| Existing context builder | Validate every source before adding it to the prompt |
| Existing Ask & Search page | Add Knowledge/Analytics mode switch |
| Existing sidebar/layout | Restyle and add Users/Teams links for Admin |
| Existing Models/Settings pages | Restrict to Admin |
| No user/team management | Add two small Admin pages and routers |
| No structured analytics | Add isolated analytics service and endpoints |

## 4. Required Behaviour

| Account | Can access |
|---|---|
| HR member | HR notebooks/sources and Company Shared |
| Finance member | Finance notebooks/sources and Company Shared |
| Executive member | Executive notebooks/sources and Company Shared |
| CEO | Every team's business content |
| Admin | All content plus Users, Teams, Models, credentials, and Settings |

CEO and Admin remain separate. The CEO may read all business content but does not automatically manage accounts or AI credentials.

## 5. Minimal Data Changes

Use new SurrealDB migrations in the existing migration system.

### 5.1 New `organization` record

Create one default organization for the current internal deployment. This adds a tenant boundary now so a later subscription system does not require rewriting every record.

```text
organization
- id
- external_key         # stable application-owned UUID/string
- name
- status               # active | suspended
- created
- updated
```

`external_key` is immutable and may later map to Lago's external customer identifier. Do not use email, company name, team ID, or a Lago-generated ID as the application's primary organization identity.

### 5.2 New `team` record

```text
team
- id
- organization_id
- slug                 # hr, finance, executive, company-shared
- name
- description
- kind                 # department | executive | shared
- active
- created
- updated
```

### 5.3 New `app_user` record

```text
app_user
- id
- organization_id
- email
- password_hash        # Argon2id; never return through the API
- display_name
- team_id              # one primary team for MVP
- role                 # member | team_manager | ceo | admin
- status               # invited | active | disabled
- last_active_at
- created
- updated
```

### 5.4 New `user_session` record

```text
user_session
- id
- user_id
- token_hash           # SHA-256 of the opaque session token
- expires_at
- last_seen_at
- revoked_at
- created
```

The raw session token exists only in a Secure, HttpOnly, SameSite=Lax cookie. Store only its hash in SurrealDB.

### 5.5 Fields added to existing records

```text
notebook.team_id
notebook.organization_id
notebook.visibility    # team | company_shared
notebook.created_by

source.team_id
source.organization_id
source.visibility      # team | company_shared
source.created_by
```

Do not redesign the current notebook-source relationships. Add validation so a source can only be linked to a notebook with the same team. A Company Shared source can only be linked to a Company Shared notebook.

Notes, chat sessions, insights, embeddings, and podcasts continue using existing relationships. Their access is derived from the parent notebook or source. A duplicated `team_id` may be added later only if profiling shows that relationship checks are too slow.

All organization-scoped queries must require `organization_id` before applying the narrower team rule. The MVP has one organization, but code must not assume that every record in the database belongs to the same organization.

### 5.6 New analytics metadata

```text
dataset
- id
- organization_id
- name
- team_id
- source_type          # postgres | parquet | csv
- connection_ref
- schema_metadata
- freshness_at
- active

analytics_query_log
- user_id
- dataset_id
- question
- template_id
- duration_ms
- row_count
- status
- created
```

These records describe datasets. They do not replace SurrealDB or move existing Open Notebook documents.

## 6. Authentication Modification

### 6.1 Current behaviour

`api/auth.py` checks one `OPEN_NOTEBOOK_PASSWORD` bearer value. Every successful request has the same identity.

### 6.2 Modified behaviour

Keep authentication inside the existing Open Notebook stack:

1. `POST /api/auth/login` accepts an email and password over HTTPS.
2. FastAPI loads `app_user` from SurrealDB and verifies its Argon2id password hash.
3. FastAPI generates a cryptographically random opaque session token.
4. Store only the token's SHA-256 hash in `user_session` and set the raw token in a Secure, HttpOnly, SameSite=Lax cookie.
5. On every protected request, hash the cookie value, load the active session and user from SurrealDB, and set `request.state.user`.

Use the existing same-origin Next.js-to-FastAPI request path. Apply origin/CSRF validation to state-changing cookie-authenticated requests.

```python
class CurrentUser(BaseModel):
    id: str
    email: str
    organization_id: str
    team_id: str
    role: Literal["member", "team_manager", "ceo", "admin"]
```

Authentication endpoints:

```text
POST /api/auth/setup       # create the first Admin; unavailable after setup
POST /api/auth/login
POST /api/auth/logout
POST /api/auth/logout-all
GET  /api/auth/status
GET  /api/me
```

`GET /api/me` returns the current user's display name, team, role, and capabilities. Rate-limit login attempts by normalized email and network source, return a generic invalid-credentials error, rotate sessions after sensitive changes, and revoke all sessions when a user is disabled or their password is reset.

The old shared password may remain behind `LEGACY_PASSWORD_AUTH=true` during local migration only. Production must disable it.

## 7. New Authorization Helper

Add one small service, for example `api/access.py` or `open_notebook/security/access.py`.

```python
def can_read_team(user: CurrentUser, team_id: str, visibility: str) -> bool:
    return (
        user.role in {"ceo", "admin"}
        or visibility == "company_shared"
        or user.team_id == team_id
    )

def can_write_team(user: CurrentUser, team_id: str) -> bool:
    return user.role == "admin" or (
        user.role == "team_manager" and user.team_id == team_id
    )
```

Every policy also checks `user.organization_id == resource.organization_id` before evaluating role, team, or Company Shared visibility. `company_shared` means shared within one organization, never public or shared between customers.

Provide reusable dependencies:

```text
get_current_user
require_admin
require_notebook_read
require_notebook_write
require_source_read
require_source_write
permitted_notebook_ids
permitted_dataset_ids
```

Existing routers call these dependencies before their current domain methods. Do not duplicate role checks separately in every endpoint.

## 8. Existing Backend Files to Modify

### `api/auth.py`

- Replace shared-password authentication with native SurrealDB-backed user sessions.
- Set the current user on the request.
- Reject disabled users.

### `api/routers/notebooks.py`

- Filter list and recently viewed by permitted teams.
- Set `team_id` during notebook creation.
- Check permission before get, update, link, unlink, or delete.
- Ordinary users cannot submit a different `team_id`.

### `api/routers/sources.py`

- Filter the existing source list.
- Set team from the selected notebook during upload/import.
- Protect source details, status, retry, insights, download, update, and delete.
- Reject cross-team source linking.

### `api/routers/notes.py`

- Resolve the existing notebook relationship.
- Apply notebook permission before current note logic.

### `api/routers/chat.py` and `api/routers/source_chat.py`

- Validate the parent notebook/source before listing sessions or processing a message.
- Prevent access through guessed session IDs.

### `api/routers/search.py`

- Ignore any client attempt to broaden scope.
- Calculate permitted notebook IDs on the server.
- Intersect optional requested IDs with permitted IDs.
- Pass the effective IDs to current `text_search` and `vector_search` functions.

### `open_notebook/domain/notebook.py`

- Extend existing text/vector search queries with mandatory notebook scoping.
- Do not retrieve globally and filter results afterward.

### `open_notebook/utils/context_builder.py`

- Accept the current user or authorized notebook scope.
- Reject a source that is outside that scope before adding text to LLM context.

### Podcasts, transformations, embeddings, and commands

Do not rewrite their processing code. Add authorization at router entry and include requester/team scope in background-job payloads. Workers revalidate the resource scope before processing.

### Models, credentials, and settings

Keep existing implementations and add `require_admin` to their mutation and secret-bearing routes.

## 9. Search and RAG Modification

The current search API already accepts notebook scoping. The modification makes the server's authorized scope authoritative.

```python
permitted = await access.permitted_notebook_ids(current_user)
requested = request.scope_notebook_ids()
effective = permitted if not requested else list(set(permitted) & set(requested))

results = await vector_search(
    query=request.query,
    notebook_ids=effective,
    # keep existing parameters
)
```

Important rule: an empty client scope means all **permitted** notebooks, not every notebook in the database.

Apply the same restriction to:

- Full-text search.
- Vector search.
- Ask and Ask Simple.
- Notebook chat context.
- Source chat.
- Transformations.
- Podcast context generation.
- Citations and saved answers.

## 10. Analytics Addition

Analytics is a new module attached to the existing Ask & Search experience. It is not a replacement for Open Notebook RAG.

### 10.1 UI behaviour

Add two tabs/modes to the existing page:

- **Knowledge:** unchanged Open Notebook document RAG.
- **Analytics:** executes approved queries against structured data.

Examples:

- Who is the highest spender this year?
- Compare monthly revenue with last year.
- Which customers have not returned in 90 days?
- What are our top five services?

### 10.2 Minimal backend addition

Add:

```text
api/routers/analytics.py
open_notebook/analytics/service.py
open_notebook/analytics/metrics.py
open_notebook/analytics/query_templates.py
```

New endpoints:

```text
GET  /api/analytics/datasets
POST /api/analytics/ask
GET  /api/analytics/queries/{query_id}
```

### 10.3 Execution approach

For the MVP, use allowlisted metrics and query templates. Do not allow unrestricted LLM-generated SQL.

```text
Question
  -> select permitted dataset
  -> classify analytical intent
  -> select approved metric/query template
  -> inject server-controlled team/date/status filters
  -> run with read-only credentials
  -> send only the small result to the existing LLM layer
  -> return explanation, table/chart, scope, and freshness
```

The database calculates the answer. The LLM explains it.

### 10.4 Highest-spender template

```sql
SELECT
    customer_id,
    customer_name,
    SUM(amount_myr) AS total_spend,
    COUNT(*) AS transaction_count,
    AVG(amount_myr) AS average_transaction_value
FROM sales_transactions
WHERE status = 'completed'
  AND transaction_date >= :start_date
  AND transaction_date < :end_date
  AND data_team_id IN :authorized_team_ids
GROUP BY customer_id, customer_name
ORDER BY total_spend DESC
LIMIT :limit;
```

The model cannot alter `authorized_team_ids`. Use read-only database credentials, parameters, a 15-second timeout, and a 500-row maximum.

### 10.5 Analytics permissions

- Finance dataset: Finance, CEO, Admin.
- HR dataset: HR, CEO, Admin.
- Executive dataset: Executive, CEO, Admin.
- Company Shared dataset: all active users.

If HR asks for highest spender using a Finance-owned sales dataset, return no permitted dataset/access denied. Do not reveal customer names, values, ranking, or charts.

## 11. Frontend Modifications

Keep current Next.js pages and underlying hooks. Apply the new layout shown in `open-notebook-team-access-prototype.html`.

### 11.1 Required layout reference

The generated file **`open-notebook-team-access-prototype.html` is the required visual and interaction reference for the frontend modification**. Developers must use it as the target layout when updating the existing Open Notebook Next.js interface.

Implementation rules:

- Reproduce the prototype's overall shell: dark left navigation, top search/header, role-aware account area, content width, cards, tables, badges, tabs, banners, and responsive behaviour.
- Use the prototype's page hierarchy and navigation grouping for Knowledge, Create, and System features.
- Use its HR, Finance, Executive, Company Shared, CEO, and Admin access states as the expected UI behaviour.
- Implement its separate Users and Teams administration pages.
- Implement its Knowledge/Analytics switch and analytical-answer layout on the existing Ask & Search page.
- Convert the prototype into the repository's existing React components, hooks, routing, translation system, and API client. Do not embed or ship the standalone HTML file as an iframe.
- Reuse existing Open Notebook components and feature logic wherever possible, restyling or composing them to match the prototype.
- Where the prototype conflicts with working Open Notebook behaviour, preserve the working functionality and adapt the visual treatment without removing capability.
- The prototype contains simulated data and interactions only. Production data, permissions, search, chat, analytics, and mutations must come from the protected FastAPI endpoints.

The prototype should be stored in the project documentation or design-reference directory during implementation so developers and QA can compare the Next.js result against it.

| Existing page | Modification |
|---|---|
| Login | Individual email/password login using native sessions |
| Home | Team label, access summary, filtered recent notebooks |
| Notebooks | Team badges and permitted results only |
| Notebook detail | Keep Sources/Notes/Chat tabs; show team badge |
| All Sources | Add team filter; return permitted sources only |
| Source detail | Keep content, insights, and source chat |
| Ask & Search | Add Knowledge/Analytics switch |
| Podcasts | Show permitted episodes only |
| Transformations | Preserve functionality; enforce source scope |
| Advanced | Restrict dangerous/global operations |
| Models | Preserve page; Admin only |
| Settings | Preserve page; Admin only |

New pages:

```text
frontend/src/app/(dashboard)/users/page.tsx
frontend/src/app/(dashboard)/teams/page.tsx
```

### 11.2 Users page

- List/search/filter users.
- Invite a user.
- Assign one team and role.
- Resend invite.
- Disable/reactivate account.
- Display last activity.

### 11.3 Teams page

- Create/edit/archive a team.
- Assign team manager.
- Display member and notebook counts.
- Display inherited access rules.

Only Admin sees these navigation links. The API still enforces authorization if a URL is entered directly.

## 12. Migration of Existing Data

Do not create a new Open Notebook database.

1. Back up the existing SurrealDB database and uploaded files.
2. Apply additive migrations for `team`, `app_user`, dataset metadata, and new fields.
3. Create HR, Finance, Executive, and Company Shared teams.
4. Create one default organization and attach every team and existing record to it.
5. Create the first Admin user.
6. Provide an Admin migration screen or script listing every existing notebook.
7. Admin assigns each notebook to a team.
8. Backfill each linked source from its notebook.
9. Flag a source linked to notebooks assigned to different teams for manual resolution.
10. Until classified, existing content is Admin-only. Never default it to Company Shared.
11. Enable native per-user session authentication after classification and tests pass.

Existing record IDs, embeddings, files, notes, chat sessions, and podcast files remain unchanged.

## 13. File-Level Change List

### Modify

```text
api/auth.py
api/models.py
api/routers/notebooks.py
api/routers/sources.py
api/routers/notes.py
api/routers/chat.py
api/routers/source_chat.py
api/routers/search.py
api/routers/podcasts.py
api/routers/transformations.py
api/routers/embedding.py
api/routers/embedding_rebuild.py
api/routers/commands.py
api/routers/models.py
api/routers/credentials.py
api/routers/settings.py
open_notebook/domain/notebook.py
open_notebook/utils/context_builder.py
frontend/src/app/(auth)/login/page.tsx
frontend/src/app/(dashboard)/layout.tsx
frontend/src/app/(dashboard)/page.tsx
frontend/src/app/(dashboard)/notebooks/page.tsx
frontend/src/app/(dashboard)/notebooks/[id]/page.tsx
frontend/src/app/(dashboard)/sources/page.tsx
frontend/src/app/(dashboard)/sources/[id]/page.tsx
frontend/src/app/(dashboard)/search/page.tsx
frontend/src/app/(dashboard)/podcasts/page.tsx
frontend/src/app/(dashboard)/transformations/page.tsx
frontend/src/app/(dashboard)/advanced/page.tsx
frontend/src/app/(dashboard)/settings/models/page.tsx
frontend/src/app/(dashboard)/settings/page.tsx
```

### Add

```text
api/access.py
api/routers/users.py
api/routers/teams.py
api/routers/analytics.py
open_notebook/domain/user.py
open_notebook/domain/session.py
open_notebook/domain/team.py
open_notebook/analytics/__init__.py
open_notebook/analytics/service.py
open_notebook/analytics/metrics.py
open_notebook/analytics/query_templates.py
open_notebook/database/migrations/<next>.surrealql
frontend/src/app/(dashboard)/users/page.tsx
frontend/src/app/(dashboard)/teams/page.tsx
frontend/src/lib/hooks/use-current-user.ts
frontend/src/lib/hooks/use-users.ts
frontend/src/lib/hooks/use-teams.ts
frontend/src/lib/hooks/use-analytics.ts
tests/test_access_policy.py
tests/test_auth_sessions.py
tests/test_team_scoping.py
tests/test_analytics.py
```

Do not create a second frontend, second API, or replacement notebook domain.

## 14. Incremental Implementation Order

### Phase 1 - Authentication and teams

- Add database migration.
- Create the default organization and attach all application records to it.
- Add user/team domain models.
- Modify existing authentication.
- Add `GET /api/me` and central access helper.
- Add Users and Teams pages.

### Phase 2 - Protect existing content

- Add guards to notebook and source routes.
- Protect notes, chat, downloads, insights, and recently viewed.
- Modify existing text/vector search scope.
- Protect context builder, podcasts, transformations, and background jobs.

### Phase 3 - Apply new layout

- Update the existing dashboard layout and navigation.
- Add team badges and access indicators.
- Keep current components/hooks where possible.
- Restrict existing Models and Settings pages to Admin.

### Phase 4 - Add analytics

- Register Finance-owned sample sales dataset.
- Add metric registry and query templates.
- Add analytics router/service.
- Add Analytics mode to existing Ask & Search page.
- Add result KPIs, table/chart, freshness, filters, and View Query.

### Phase 5 - Verify and deploy

- Run current Open Notebook regression tests.
- Run new team-isolation tests.
- Run analytical correctness tests.
- Perform UAT using the generated test pack.
- Deploy the modified fork using the existing Docker strategy.

## 15. Testing

All current Open Notebook tests must continue passing.

Add negative authorization tests for every protected endpoint:

- HR cannot list or open Finance/Executive resources.
- Finance cannot list or open HR/Executive resources.
- CEO can read every team's business content.
- CEO cannot manage Users/Teams/credentials unless also Admin.
- Admin can manage the system.
- Direct IDs, downloads, session IDs, job IDs, and audio URLs cannot bypass permissions.
- Full-text/vector search, citations, context, and generated output contain no forbidden canary.
- Cache keys include the permission scope.

Analytics acceptance cases from the test pack:

| Test | Expected result |
|---|---|
| Finance asks highest spender YTD | Sarah Lim; MYR 8,460; 24 completed transactions |
| CEO asks the same | Same result with calculation scope |
| HR asks the same | No permitted dataset; no leaked values |
| Rank customers | Sarah, Amir, Michelle, Jason |
| Sarah average transaction | MYR 352.50 |
| Sarah most-used service | Full Groom; 11 transactions |
| Include refunded transaction explicitly | MYR 9,360 with changed scope disclosed |
| Ask for a future empty period | No data; no invented answer |
| Finance asks for HR salaries | Refuse/no permitted data |

## 16. MVP Acceptance Criteria

1. The project remains a fork of Open Notebook and retains its existing features.
2. No replacement frontend, API, database, RAG pipeline, or podcast system is introduced.
3. Users authenticate individually.
4. Every existing notebook and source is assigned to one team before non-Admin access is enabled.
5. HR, Finance, and Executive content is isolated in every existing workflow.
6. Company Shared content is visible to all active users.
7. CEO can read all business content.
8. Only Admin manages users, teams, models, credentials, and settings.
9. Search permission is applied inside existing full-text and vector queries.
10. Restricted text never reaches LLM context.
11. Existing notes, chat, transformations, podcasts, downloads, and jobs remain functional and protected.
12. Analytics mode answers the highest-spender test correctly from structured data.
13. The analytics database performs calculations; the LLM only explains validated results.
14. All existing regression tests and new test-pack cases pass.
15. The updated Next.js pages visually and functionally follow `open-notebook-team-access-prototype.html` as the approved layout reference.

## 17. Future Lago Subscription Readiness

### 17.1 Status and intended repository

Future billing target: <https://github.com/getlago/lago>.

The originally supplied `getlago/lagolater` URL is interpreted as `getlago/lago` followed by the word "later." Lago must **not** be installed, deployed, or called during the current MVP. No billing page, checkout, invoice, payment gateway, pricing plan, or subscription enforcement is required now.

Lago is intended to remain a separate billing service. Open Notebook remains the product and source of application identity; Lago later handles metering, subscriptions, pricing, entitlements, invoices, and payment orchestration.

### 17.2 Subscription ownership boundary

The future paid subscription belongs to an `organization`, not to an individual user or department team.

```text
Organization / customer
  -> one future Lago customer
  -> one or more future subscriptions
  -> many application users
  -> many teams
  -> notebooks, sources, datasets, and usage
```

Teams control data access. Subscription entitlements control which product capabilities the organization may use. These concerns must stay separate.

Examples:

- HR permission decides whether Aisha may read an HR notebook.
- A future `analytics_enabled` entitlement decides whether the organization's plan includes Analytics mode.
- A future seat limit decides whether another active user may be added.
- Lago must never decide whether HR can see Finance documents.

### 17.3 Preparation required in the current MVP

Implement only these subscription-readiness items now:

1. Add `organization` and `organization_id` as defined in this TDD.
2. Generate an immutable `organization.external_key` suitable for a future Lago external customer ID.
3. Keep user IDs stable and never use email as an external billing identity.
4. Centralize feature access behind an entitlement interface.
5. Define usage-event names and a durable outbox format.
6. Add configuration flags, but leave Lago disabled.
7. Keep all Lago-specific HTTP clients, webhooks, and billing UI out of the current MVP.

### 17.4 Entitlement interface now

Add a small provider-neutral interface:

```python
class EntitlementService(Protocol):
    async def enabled(
        self,
        organization_id: str,
        feature_code: str,
    ) -> bool: ...

    async def limit(
        self,
        organization_id: str,
        feature_code: str,
    ) -> int | None: ...
```

Current implementation:

```text
LocalEntitlementService
- returns enabled for all MVP features
- reads optional local configuration
- performs no network requests
- contains no Lago dependency
```

Future implementation:

```text
LagoEntitlementService
- reads synchronized Lago entitlement state
- uses a local cache/fallback
- does not replace team authorization
```

Initial stable feature codes:

```text
knowledge_chat
podcast_generation
custom_models
api_access
max_active_users
max_storage_bytes
max_monthly_ai_requests
```

Do not scatter plan names such as `free`, `pro`, or `enterprise` through feature code. Application code checks feature codes and limits; pricing plans remain a future Lago concern.

### 17.5 Usage-event outbox

Add an application-owned `usage_event_outbox` record. In the current MVP, events may be recorded locally for validation, but they are not sent to Lago.

```text
usage_event_outbox
- id
- transaction_id       # immutable UUID; unique
- organization_id
- user_id
- event_code
- quantity
- occurred_at
- properties           # bounded, non-secret metadata
- delivery_status      # pending | sent | failed | ignored
- provider              # none now; lago later
- provider_event_id    # nullable
- attempts
- created
- updated
```

Suggested stable event codes:

```text
ai_request
analytics_query
source_processed
embedding_tokens
podcast_audio_seconds
storage_bytes_snapshot
active_user_snapshot
```

Each event gets a unique `transaction_id`. Later retries must reuse that same ID so Lago can deduplicate usage instead of charging twice.

Example future-compatible event envelope:

```json
{
  "transaction_id": "01JXYZ...",
  "external_customer_id": "org_01JABC...",
  "code": "analytics_query",
  "timestamp": "2026-09-15T08:30:00Z",
  "properties": {
    "quantity": 1,
    "user_id": "user_01JDEF...",
    "mode": "analytics"
  }
}
```

Do not put document text, questions, answers, personal details, model secrets, or database credentials into billing events.

### 17.6 Configuration prepared now

```text
BILLING_PROVIDER=none
BILLING_EVENTS_ENABLED=false
ENTITLEMENT_PROVIDER=local
LAGO_API_URL=
LAGO_API_KEY_FILE=
LAGO_WEBHOOK_SECRET_FILE=
```

Only the first three are used in this MVP. Empty Lago settings must not prevent Open Notebook from starting.

### 17.7 Deferred future implementation

The following are explicitly deferred:

- Deploying Lago and its dependencies.
- Creating Lago customers, plans, billable metrics, subscriptions, wallets, coupons, or invoices.
- Connecting Stripe or another payment gateway.
- Checkout and billing-portal UI.
- Subscription lifecycle webhooks.
- Seat enforcement and plan upgrade prompts.
- Sending usage events to Lago.
- Invoice, tax, credit, payment, and dunning workflows.
- Billing analytics inside the application.

When the subscription phase begins, implement an adapter under a separate module such as:

```text
open_notebook/billing/base.py
open_notebook/billing/local.py
open_notebook/billing/lago.py
open_notebook/billing/events.py
api/routers/billing.py
```

The rest of Open Notebook must depend on `EntitlementService` and the usage-event interface, not on the Lago SDK or Lago API response objects directly.

### 17.8 Failure behaviour for the future integration

- Billing-event delivery is asynchronous and never blocks document chat or source processing.
- Use an outbox worker with retry and dead-letter handling.
- Subscription/entitlement state is synchronized locally through verified webhooks and periodic reconciliation.
- Webhook processing is idempotent.
- A temporary Lago outage does not erase access state or corrupt application data.
- Administrative billing changes require audit logs and explicit confirmation.

### 17.9 Licensing and deployment note

The Lago repository is AGPLv3. Before distributing a modified hosted product or combining repositories, review the license obligations for the intended deployment. Keep Lago as a separately deployed service connected through its API rather than copying its source into the Open Notebook repository.

## 18. Explicit Engineering Constraints

- Fork `lfnovo/open-notebook` and pin the starting upstream commit.
- Keep upstream code structure and naming conventions.
- Use the existing Next.js, FastAPI, and SurrealDB stack for users, sessions, teams, and access control; do not introduce an external authentication service or application database.
- Prefer wrappers, dependencies, and additive fields over large rewrites.
- Keep migrations additive and reversible where practical.
- Do not modify existing IDs or re-import working content.
- Do not duplicate existing source processing, RAG, chat, podcast, or model-provider logic.
- Maintain a small upstream-sync log for conflicts caused by authorization changes.
- Treat frontend hiding as usability only; FastAPI remains the security boundary.
- Treat the user question, documents, and LLM output as untrusted.
- Keep future Lago integration behind provider-neutral entitlement and event interfaces.
- Do not implement or deploy Lago during the current MVP.

## 19. Final Decision

Implement this project by modifying the existing Open Notebook repository. Add identity and a central authorization layer, then insert it into current data-access paths. Extend the current Ask & Search UI with an isolated analytics service. Preserve the application's existing features and database content throughout the migration.

Prepare for future Lago subscription management by introducing a stable organization boundary, provider-neutral entitlement checks, and idempotent usage-event records now. Leave all billing, pricing, payment, subscription, invoice, and Lago communication work disabled and deferred.
