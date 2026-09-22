# ADR-019: Home chat delegates to the Ask graph wholesale, with streamed delivery

- **Status**: Accepted
- **Date**: 2026-09
- **Related**: map #56, tickets #57–#66, spike #58

## Context

Home chat (the home-page widget and the WhatsApp/Telegram bot asks, which share one pipeline) had two problems. It was **broken**: typed questions returned nothing at all — a mid-stream SSE transport abort, made total by two blind spots (the backend generator caught only `Exception`, so connection kills escaped as bare EOF; the frontend only toasted thrown errors, so EOF-before-`complete` fell through to a session refetch that rendered a question with no reply). And it was **fragile**: the graph re-wired the Ask pipeline's node functions into its own graph through a hand-rolled state projection (`_ask_state_view`, per-turn answer tagging to stop search results leaking across turns), while the web router and the messenger service each maintained their own copy of the graph plumbing. The user trusts the Ask (beta) tab — same questions, better behavior, streaming — and asked why chat couldn't simply *be* that.

## Decision

Home chat runs the **compiled Ask graph as a LangGraph subgraph**, wholesale — no node re-wiring, no projection layer. The home-chat graph is a thin memory wrapper: `prepare_ask` (the only surviving projection, and it projects *data* — question, rendered history, the clarify flag) → the Ask subgraph → `remember` (AI message into the checkpointed `messages` channel) → `suggest` (unchanged). One **shared turn generator** (`stream_home_turn`) emits typed events (answer deltas → final answer → suggestions → complete/error) consumed by both the web SSE endpoint and a new internal `POST /api/integrations/message/stream`; the gateway buffers deltas into paced platform segments (≥400 chars at a sentence boundary, hard-flush at 1,200, ~1 msg/sec, first segment quoting, no interim text — typing presence only). The clarification gate is **on** for chat: an ambiguous question gets blocking clarifications instead of a 30–90s wrong guess. Delivery failures follow one rule everywhere: **typed error, never bare EOF** — before the first byte the gateway falls back to the one-shot endpoint; after partial content it sends an honest apology tail.

## Alternatives considered

- **Slim single-call redesign** (direct retrieval → one LLM call, ~2–5s TTFB) — rejected by the user: Ask's multi-stage answers are the quality bar they want; streaming fixes *delivery*, and TTFB stays a known, accepted tradeoff.
- **Node-level reuse** (the status quo: re-wiring ask functions with a projection layer) — rejected: that layer is where the drift and the cross-turn leakage bugs lived; the spike (#58) proved subgraph delegation removes it entirely.
- **Hotfix-first** (patch the two blind spots on main before the redo) — rejected: it patched code the redo deletes; its durable value (the error-event contract, acceptance cases) was folded into this work as requirements instead.
- **Forking the Ask graph into chat** — rejected silently by choosing the subgraph: Ask improvements now reach chat for free, and there is one pipeline to fix.

## Consequences

- One turn pipeline for web + both messengers; the web/router and messenger/service duplicates are gone. Chat behavior tracks the Ask tab by construction.
- Turn isolation is free: `strategy`/`answers` stay subgraph-private (per-invocation checkpoint namespace), so turn N can never see turn N−1's partial answers — the exact bug the deleted tagging prevented.
- Token streaming requires `stream_mode=["updates","messages"], subgraphs=True`; with the default `subgraphs=False` subgraph LLM tokens do not stream at all (spike finding, pinned by a gotcha test).
- Per-stage model knobs (`strategy/answer/final_answer_model`) collapse to the default chat model on this path; request fields stay accepted-but-ignored.
- Old home-chat checkpoints (pre-redo schema) load unchanged; the removed channels are ignored (fixture test).
- Watch: the Ask graph is now load-bearing for chat — an Ask regression is a chat regression, so the acceptance cases from the diagnosis (#57 comment) should be run after any Ask change.
