# ADR-018: Internal-caller auth for messenger gateway

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: #40, map #37

## Context

The messenger gateway (Telegram/WhatsApp adapters) is a server, not a browser, so the cookie-session auth of ADR-010 cannot serve it. It must call search/ask on behalf of users, which raises two questions: how the service authenticates, and how a chat identity (a phone number, a chat id) maps to a user without weakening team enforcement.

## Decision

A single **internal token** (`OPEN_NOTEBOOK_INTERNAL_TOKEN`, auto-generated into `data/` when unset, treated like the encryption key) authenticates a dedicated `/api/integrations/*` router only; the token is valid nowhere else and never appears in logs (only a SHA-256 prefix). Internal endpoints resolve the caller through an **integration link** looked up server-side by `(platform, external_id)` — the gateway sends no user identity and can never impersonate an arbitrary user. Resolution yields the same `CurrentUser` every endpoint consumes, so `effective_notebook_scope` and the whole team-enforcement seam apply unchanged (empty scope = all permitted, never the whole knowledge base). Failure modes are all fail-closed: gateway refuses to start adapters on auth failure; unlinked senders get how-to-link instructions; disabled users resolve as unauthenticated. No network-address binding; the token is non-ambient, so CSRF does not apply to internal endpoints.

## Alternatives considered

- **Per-user API tokens (hashed in SurrealDB)** — the right choice if/when a real mobile app needs device-scoped credentials, but a second auth system to build, rotate, and document now for a use case that doesn't need it. The seam resolves through `CurrentUser` so this can be added later without rework.
- **Shared secret + act-as header on existing endpoints** — rejected: one leaked secret becomes full impersonation of any user.
- **Address binding (loopback/compose-network check)** — rejected: breaks legitimate self-hosted topologies (reverse proxies, podman, remote docker); the token already defeats CSRF-style attacks.

## Consequences

- Team guardrails for chat users are inherited, not re-implemented — the canary/access-matrix test patterns apply to the integrations router directly.
- The gateway is a dumb pipe by construction: all routing, scoping, and audit logging live in FastAPI.
- Rotating the internal token is a file delete + restart; a stale gateway fails loudly, never silently.
- Watch: per ADR-010's CORS posture, `/api/integrations/*` inherits the deployment's CORS config — fine for server-to-server, but don't later expose these endpoints to browsers.
