# ADR-010: Per-user cookie sessions replace the shared-password middleware

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: #1, PDR-001 (single-user first — this is its sanctioned evolution), ADR-001 (SurrealDB as the database)

## Context

Open Notebook authenticated with a single shared bearer password (`OPEN_NOTEBOOK_PASSWORD`) checked by a global Starlette middleware — every request had the same identity, so there was nothing to authorize against. The team-access MVP needs per-user identity (four roles, session revocation, invite lifecycle) and the frontend currently keeps the shared password in localStorage, which any XSS can exfiltrate.

## Decision

Replace `PasswordAuthMiddleware` with native sessions: `POST /api/auth/login` verifies an Argon2id hash, issues an opaque random token stored in SurrealDB only as a SHA-256 hash (`user_session.token_hash`), and returns it as an HTTP-only, SameSite=Lax cookie. Mutating requests carry a CSRF token. Login is rate-limited with a generic invalid-credentials error. Disabling a user or resetting a password revokes all sessions. The frontend drops the localStorage bearer store entirely; nothing token-shaped is readable from JavaScript.

## Alternatives considered

- **Per-user bearer tokens in localStorage** — keeps the existing proxy pattern verbatim, but XSS exfiltrates the token and gives the attacker a durable credential. Rejected.
- **JWTs** — stateless revocation is a contradiction with "disable revokes sessions now"; opaque tokens plus a DB row are simpler and already the shape of `user_session`. Rejected.
- **External identity provider** — solves a problem this deployment does not have and breaks the self-hostable, single-process posture. Rejected.

## Consequences

- Auth becomes stateful: every protected request loads the session + user from SurrealDB (indexed by `token_hash`; async-first rule applies).
- Excluded-path handling (`/health`, `/docs`) moves from the middleware into router-level dependencies.
- The shared password disappears; existing CI/tests that set `OPEN_NOTEBOOK_PASSWORD` must move to session-based test auth.
