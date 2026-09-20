# Open Notebook — Team Access

The multi-user evolution of Open Notebook: individual accounts organized into department teams with role-based, server-enforced content scope. This glossary governs the vocabulary of that work.

## Language

**Organization**:
The single tenant boundary for a deployment. All users, teams, and content belong to one organization; its immutable `external_key` is reserved as the future billing identity.
_Avoid_: Tenant, workspace, account

**Team**:
A department (HR, Finance, Executive) with members and exactly one manager. Every user has exactly one primary team in the MVP.
_Avoid_: Department (in code and schema), group, workspace

**Company-shared**:
A visibility value, not a team. Content marked company-shared is readable by every active user in the organization. Owned by the Executive team.
_Avoid_: Shared team, public, global

**Visibility**:
The access scope on a notebook or source: `team` (owning team only) or `company_shared` (whole organization).
_Avoid_: Access level, sharing mode, permission

**Member**:
A user with read/write access limited to their own team's content plus company-shared content. The default role.
_Avoid_: Regular user, standard user

**Team manager**:
A role that can create, edit, and link all content within their own team. No user management, no extra cross-team visibility.
_Avoid_: Manager (unqualified), lead

**CEO**:
A role that can read every team's business content but writes only in the Executive team. Does not manage users, teams, models, credentials, or settings.
_Avoid_: Superuser, executive (as a role name)

**Admin**:
The only role that manages users, teams, models, credentials, and settings, on top of full content access.

**Canary**:
A unique phrase embedded in a test document used to prove that forbidden content cannot leak through any retrieval path (search, citations, AI answers).

**Unclassified**:
Existing content whose filename carries no team token at migration time. Defaults to company-shared (a deliberate, recorded deviation from the source TDD §12.10).

**Gateway**:
The optional messenger-facing service that ferries text between chat platforms (Telegram, WhatsApp) and the API. It holds no user identities and makes no access decisions; it is a transport adapter, not a client.
_Avoid_: Bot service, chat backend, messenger API

**Integration link**:
The binding between one messenger identity (a Telegram chat or a WhatsApp number) and exactly one user account. Access scope always derives from the linked user, never from the platform or the link itself.
_Avoid_: Connection, pairing, device pairing (too easily confused with WhatsApp linked devices)

**Linking code**:
The short, short-lived code a user carries from the web UI to a messenger to establish their integration link. Proof of control of both sides of the binding.
_Avoid_: OTP, verification code, pairing code (that term is WhatsApp's own device-pairing feature)

**Internal caller**:
A service (today: the gateway) authenticated to the API by a service credential rather than a user session. The API resolves an internal caller to a user only through a live integration link — an internal caller can never assert an arbitrary user identity.
_Avoid_: Service account, system user, machine user
