# ADR-013: Content classification — flags as teamlessness, company-shared ownership, and the login gate

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: #1 (spec), #7 (T6), ADR-012 (unclassified = admin-only — the mechanism this reuses), `_jobbrief/tdd.md` §12, decision-log §7 (the recorded §12.10 deviation)

## Context

T6 classifies pre-team-access content into teams. The ticket pins the what (filename tokens, sources first, notebooks inherit, company-shared default, flagged items Admin-only, member login gated) but not the how, and three design points needed a decision:

1. **Where flags live.** "Mixed-team notebooks and cross-team source links flagged Admin-only" suggests a flag field, but ADR-012 already makes *teamless* rows admin-only (`can_read_team` returns False without a team). A separate flag column would duplicate that state and risk drift.
2. **What team company-shared content carries.** `visibility = company_shared` is not a team; but the T5 permitted-scope SQL requires `team IS NOT NONE AND (team = $team OR visibility = 'company_shared')` — a company-shared row with no team is invisible to members anyway.
3. **Who the login gate blocks, and what "complete" means.** TDD §12.11 says enable member auth "after classification and tests pass"; the ticket says "member login is enabled only after the classification pass completes".

## Decision

1. **A flag is a row that still has no team.** The pass only looks at teamless rows, so "flagged" and "unclassified" are the same storage state the enforcement layer already understands. The migration screen lists teamless rows the pass cannot auto-place (ambiguous filename tokens, mixed-team source sets, cross-team links) with a reason computed on demand. No flag column, no drift. The cross-team rule applies the T5 same-team link rule strictly: **any** linked notebook owned by a different team flags the source — one matching notebook does not excuse another cross-team link.
2. **Company-shared content is stamped `team = executive`, `visibility = company_shared`.** Executive owns company-shared content per the CONTEXT.md vocabulary; the team stamp is what makes the row reachable in the SQL-side scope queries. Same rule for the manual-assign endpoint.
3. **The gate blocks `member` and `team_manager` login with a 403 until completion; `admin` and `ceo` are exempt.** The admin must log in to run the pass; the CEO reads classified content either way, and blocking them adds no safety. `POST /auth/login` therefore has one deliberate non-contract status: 403 + `MEMBER_LOGIN_BLOCKED` (the frozen contract's 401/429 still cover credential failures). The frontend maps 403-on-login to a dedicated `migration_pending` error code.
4. **Completion is `organization.classification_completed_at` (migration 29), one-way.** It is set by the pass (or by an assignment) only when zero flagged rows remain, and never cleared — a later ambiguous upload must not lock members out again. Resolving the last flagged item from the migration screen flips it, which enables member sign-in without a re-run.
5. **Idempotency by construction.** The pass never rewrites rows that already have a team, so a second run classifies nothing and cannot undo human resolutions. Cross-team-flagged sources are un-classified (team reset) each pass and re-flagged to the same end state.
6. **Token combination and sourceless notebooks.** A filename with one team token plus a company token (`hr_company_shared_guide.pdf`) classifies as that team with `company_shared` visibility — team ownership wins, sharing follows. A notebook with no classifiable sources (empty, or all linked sources ambiguous/flagged) takes the same company-shared default as a no-token source: the approved §12.10 deviation is "unclassified ⇒ company-shared", not "unclassified ⇒ admin-only", and its linked flagged sources stay admin-only regardless, so no document content leaks through it.

## Alternatives considered

- **`migration_flag` column on notebook/source** — explicit reasons in storage, but duplicates the teamless state and needs a second write path kept in sync with enforcement. Rejected; reasons are cheap to recompute and listed per item.
- **Blocking only `member`** — the ticket's literal wording. Rejected: a team_manager signing in early gets write access to partially classified content, which is strictly worse than a member peeking.
- **Gate in `authenticate_user`** — would also block `resolve_session`, locking admins out of their own migration screen when the app requires auth. Router-level gate on login only; existing sessions are unaffected (in the UAT flow, members simply cannot log in before completion).
- **Completion requiring an explicit admin "finalize" click** — one more UI concept to explain; auto-completion when the flag set is empty is self-maintaining and matches "run the pass → members log in".

## Consequences

- Migration 29 is a single field on `organization`; the `Organization` domain model gained `classification_completed_at` plus `get_organization_by_id`/`update_organization`.
- The pass logs every assignment and flag via loguru (the "logged" acceptance criterion), and returns the full action list to the caller.
- `tests/test_migration_classification.py` runs the service against a fake in-memory repo; numeric-looking record ids round-trip through `RecordID.parse` as `source:⟨02⟩` (angle-bracket escaping), so tests use realistic alphanumeric ids.
- T10's UAT checklist should verify: 7 test-pack PDFs land in filename-matched teams, the no-token guide becomes company-shared (Executive-owned), mixed notebooks are admin-only, member login is blocked before / enabled after completion.
