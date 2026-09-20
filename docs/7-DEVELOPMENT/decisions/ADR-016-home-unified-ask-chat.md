# ADR-016: Home as a unified ask chat — auto-routing, server memory, LLM follow-ups

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: Home page (`/home`), ADR-010 (cookie sessions), ADR-011/015 (analytics pipelines), ADR-012 (access seam), #574/#87 (notebook scope)

## Context

The Home page was a placeholder. The product direction for it: one chat that answers both knowledge questions (the Ask pipeline: strategy → vector search → synthesis) and analytics questions (the frozen `AnalyticsAnswer` contract), with real conversation memory and suggested follow-ups. The existing surfaces are all single-shot: `useAsk` (one Q&A in component state, no history) and `useAnalyticsAsk` (one mutation, no threading). The only persisted multi-turn chat in the codebase is source/notebook chat (LangGraph `SqliteSaver` checkpoints keyed by `thread_id = session_id`), which became the template.

Three decisions were made up front: routing between knowledge/analytics is **auto-detected** (no manual mode toggle), memory is **server-side** (survives reloads, works cross-device), and follow-up suggestions are **LLM-generated per answer**.

## Decision

- **Auto-routing with a safe default.** A new graph (`open_notebook/graphs/home_chat.py`) classifies each message with a cheap LLM call (`max_tokens=16`, one word: ANALYTICS/KNOWLEDGE). On any failure — no model, error, unparseable reply — a deterministic keyword fallback fires on *strong* analytics signals only ("spent", "revenue", "ranking", "average transaction", …). Everything else routes to **knowledge**, the safe default: a misrouted knowledge question still gets a useful answer, while a misrouted analytics question would surface a confusing refusal.
- **Reuse, don't fork.** The knowledge branch reuses the ask graph's nodes verbatim (`call_model_with_messages`, `provide_answer`) via thin state-projection wrappers; the analytics branch calls `ask_analytics_question` unchanged, so both the template path with the user's richer period parsing and the ADR-015 text-to-SQL path flow into Home automatically. Memory is injected through a new optional `chat_history` field on the ask `ThreadState` — the ask/final-answer prompts render it only when non-empty, so the frozen `/api/search/ask` contract is untouched.
- **Server-side memory via checkpoints.** `home_chat_graph` is compiled with the shared `SqliteSaver` (`thread_id = session_id`). The `messages` channel (`add_messages`) accumulates Human/AI turns; a parallel `turn_metadata` channel (`operator.add`, one entry per AI turn: analytics payload + suggestions) lets the GET endpoint re-attach per-turn data to persisted messages. Sessions are `home_chat_session` records (migration 32) owned by a plain `user_id` field; every query filters by the authenticated caller (T5: foreign sessions are 404).
- **Follow-ups are best-effort.** After the final answer, one small LLM call (`prompts/home_chat/suggestions.jinja`) proposes 2–3 questions. Any failure yields zero suggestions — the turn never fails because suggestions failed.
- **Scope and honesty follow existing seams.** The message endpoint resolves `effective_notebook_scope` (permitted ∩ requested) exactly like the ask endpoint; an explicitly-empty scope short-circuits in `knowledge_final` to the same honest "no notebooks" answer, never a whole-KB fallback.
- **v1 limitation, accepted deliberately**: analytics parsing is single-question. A fragmentary follow-up ("and in September?") parses standalone and returns `no_data`; threading history into `parse_period` is a later increment.

## Alternatives considered

- **Client-side memory (history replay + localStorage)** — much smaller change, but not cross-device, grows token cost per turn, and diverges from the existing persisted-chat pattern. Rejected.
- **Manual mode toggle (knowledge/analytics)** — predictable but puts routing burden on the user; the product call was auto-detect. The route is still emitted as an SSE event so the UI can show which source answered.
- **Subgraph invoke of the ask graph** — loses per-stage streaming granularity (strategy/search/final events) and makes history injection awkward. Rejected in favour of node reuse inside one graph.
- **Follow-ups hardcoded** — zero cost but low value outside the analytics templates; rejected in favour of LLM-generated.

## Consequences

- Analytics misclassification (knowledge question routed to analytics) surfaces the analytics refusal text; this is the accepted cost of auto-routing — the LLM classifier plus strong-signal-only keyword fallback keeps it rare.
- Suggestions cost one extra small model call per turn and are not regenerated on session reload (they are persisted, not re-requested).
- The search page's Ask/Analytics modebars remain untouched; Home is a separate surface that may eventually replace them.
