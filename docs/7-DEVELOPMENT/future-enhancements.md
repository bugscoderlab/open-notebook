# Future Enhancements: Ask/Home Chat Performance

Exploration notes for making **Ask** and **Home chat** faster and cheaper,
benchmarked against NotebookLM's perceived speed. Status: *proposal — nothing
here is committed work.* Grounded in the code as of the current `main`.

Related: [architecture](architecture.md) · [prompts](prompts.md) ·
[decisions/](decisions/README.md) (any work here should produce an ADR/PDR).

---

## 1. Problem statement

Users perceive NotebookLM answers as much faster than Open Notebook's. Two
cost dimensions matter:

- **Latency**: time-to-first-token and total time per question.
- **Token spend**: input tokens per question (what users pay for on hosted
  providers).

Both come from the same root cause: pipeline shape.

## 2. Current pipeline (verified in code)

### Ask (`open_notebook/graphs/ask.py`)

Per question, **three sequential LLM round-trips minimum**:

1. **Strategy** (`call_model_with_messages`) — one structured-output call
   planning searches + clarifications. May add a corrective retry
   (rare path).
2. **Fan-out** (`provide_answer`, via `trigger_queries`) — up to
   `ASK_MAX_SEARCHES` (default 3, ceiling 5, admin-editable) parallel
   branches. Each branch: embed the term → `vector_search(term, 10, …)`
   (top-10 chunks) → render 10 full chunks into the prompt → one extraction
   LLM call. The slowest branch gates the pipeline.
3. **Synthesis** (`write_final_answer`) — one call carrying every extraction
   answer.

Plus per-stage overhead: SurrealDB round-trips (each `repo_*` call opens and
closes a connection — no pooling), settings fetches, error classification.

### Home chat (`open_notebook/graphs/home_chat.py`)

Wraps the Ask graph **wholesale** as a subgraph, then adds:

- `prepare_ask` — permission scope resolution + history projection
  (history trimmed to `HISTORY_TURNS = 3`),
- `suggest` — **an additional LLM call per turn** generating follow-ups,
- checkpoint writes per stage (AsyncSqliteSaver).

So home chat pays the full Ask pipeline **plus** one more sequential model
call per turn.

### Upload-time precompute today

Only chunking + embeddings (`vectorize`, fire-and-forget command). **No LLM
ever reads a source until question time.** The opt-in insights transformation
(`add_insight`) is the closest existing hook for source-level digests.

### Caching today

None. Every question re-embeds each strategy term (3–5 embedding HTTP calls
before any LLM speaks), re-runs vector search, and re-runs the full pipeline.
An identical repeated question costs full price again.

## 3. Why NotebookLM is faster (observed/inferred)

NotebookLM is closed-source; this is inference from observable behavior:

- **Precompute at upload** — the "Processing source…" wait is where source
  understanding (embeddings, per-source summaries/index) is built. Question
  time reuses it.
- **Fewer calls per question** — appears to be one retrieve → answer pass,
  not a strategy → fan-out → synthesis chain.
- **Infrastructure** — Google serving, TPU inference, prefix/context caching,
  cross-user source caching.

The trade-off: NotebookLM answers from a shallower loop with bounded context.
Open Notebook's fan-out buys multi-angle decomposition, per-search extraction
with explicit source-ID citations (`payload["ids"]`), and the clarification
gate. Speed work should not silently delete those features.

## 4. Evaluated and rejected: classifier models ("System One": Laya / Jev)

A System One classifier (Laya is the concrete open-source representative)
replaces LLM calls used for *classification decisions* (model routing, risk
gating) with near-free typed questions (choice/score/noul) answered in a
single forward pass. Laya (`NandhaKishorM/laya`, Apache-2.0, self-hostable)
is the open-weights equivalent of TypeSafe's hosted Jev — same wire
protocol, ~33 ms vs Jev's ~236–276 ms. Assessment for Ask/Home chat:

- **Does not attack the main cost.** The dominant spend is context stuffing
  (chunks × N searches + synthesis), and a classifier cannot extract answers
  from retrieved chunks or synthesize text.
- **Marginal fits exist**: a noul gate for "can this follow-up be answered
  from chat history alone?", skip-conditions for the per-turn suggestions
  call, per-stage model routing. Each saves one small call occasionally.
- **Product-fit**: the original evaluation targeted Jev, a proprietary
  hosted API. Laya's open weights remove that objection, but adopting it
  still means torch + 300–420 MB checkpoints in the lean API/worker images.
  See §8 for the security-motivated revisit.

Verdict: revisit only if tool-use or multi-agent routing is added, or for
the security gate in §8. Not worth it for the current cost curve.

## 5. Levers that change the shape

### 5.1 Existing knobs (zero code)

| Knob | Effect |
|---|---|
| `ask_max_searches` → 1–2 (Ask settings) | Removes parallel branches, shrinks synthesis input. Biggest zero-code lever. |
| Per-stage models (`strategy_model` / `answer_model` / `final_answer_model` in Ask advanced options) | Small/fast model for strategy + extraction, capable model only for synthesis. |
| Trim retrieval depth | `vector_search(..., 10, …)` per branch → 30–50 chunks in flight per question. |

### 5.2 Caching (eliminates repeat work)

| Cache | Key | Win | Effort |
|---|---|---|---|
| Term embeddings | `hash(term)` + embedding model id | Kills 3–5 embedding HTTP RTTs at the top of every fan-out. Deterministic — no invalidation. | Trivial |
| Provider prefix cache (prompt layout) | prompt structure | On hosted providers, repeat questions skip cached prefix tokens. **Chunks-last rendering** makes the long stable prefix cacheable. | Nearly free (prompt-template edit) |
| Extraction/answer cache | normalized question + notebook scope + **content version** + model id + prompt version | Repeat/near-repeat questions near-instant. | Medium — needs a per-notebook content-version counter bumped on source add/remove/transform |

Limit: caching only helps the *second* time. First-question latency needs
precompute.

### 5.3 Precompute at upload (the NotebookLM move)

Add an LLM digest stage to ingest (worker, batched small model, alongside
`vectorize`):

1. **Per-source card** — one cheap call: summary, key claims, topics/entities.
   Stored on the source like insights (`add_insight` plumbing is the model).
2. **Per-chunk digest** — 2–3 sentence abstract + "what questions this chunk
   can answer," computed in batches at ingest.

Question-time payoff:

- **Retrieval pre-filter**: rank/trim the top-10 chunks per search by digest,
  feed only the top ~5 full chunks to extraction. Prompts shrink 40–60%;
  obvious-miss chunks are dropped without an LLM call.
- **Cheap answer tier**: "what does this source/notebook say overall"
  answered from source cards alone — no chunk fan-out.
- **Better strategy**: the strategy node plans blind today; a per-notebook
  index of source cards improves search-term quality.

### 5.4 Structural option: single-pass mode

Skip the per-search extraction stage entirely: retrieve top-K chunks once,
feed them straight to one answer call (classic RAG — structurally what
NotebookLM does). Loses per-search citation granularity unless a
chunk→citation mapping is kept. Combine with per-chunk digests (5.3) for the
full latency cut.

## 6. Sequenced plan

1. **Term-embedding cache + chunks-last prompt layout** — days, no schema
   change. Visible token/latency win.
2. **Per-source card at ingest** — small schema + one pipeline node, reuses
   insight plumbing. Add a setting knob (default pending product decision —
   ingest cost is user-visible on self-hosted billing).
3. **Answer cache with content versioning** — schema + counter bumps in
   source/notebook mutation paths.
4. **Per-chunk digests + retrieval pre-filter** — biggest lift, biggest cut;
   consider together with single-pass mode (5.4) as the end-state shape.

Each step is independently shippable; steps 1–2 are the smallest increment
with a visible difference.

## 7. Open questions

- **Ingest cost posture**: precompute moves token spend from question time to
  upload. NotebookLM absorbs this centrally; self-hosted users feel it
  directly. Default-on or default-off? (VISION.md decision.)
- **Invalidation**: digests regenerate on source reprocess (natural hook:
  the `vectorize`/reprocess command lifecycle); the answer cache needs the
  content-version counter or it serves stale answers.
- **Freshness vs depth**: per-chunk digests are navigation metadata, not
  truth — the extraction stage over full chunks must remain the citation
  ground to keep `ids`-based citations honest.

---

## 8. Addendum (2026-09): System-1 decisions revisited — security motive, borrowed pattern

Section 4 evaluated classifier models purely as a *cost* lever for Ask/Home
chat and rejected them. Two updates since:

- **The proprietary objection is gone.** Laya (`NandhaKishorM/laya`,
  Apache-2.0) is a self-hostable, non-autoregressive typed-decision engine
  (choice/score/noul, single forward pass, ~33 ms). Adopting it as a
  dependency was still rejected: torch + 300–420 MB checkpoints don't fit
  the lean API/worker images, and the dominant costs (context stuffing,
  synthesis, embeddings) are generative workloads it cannot touch.
- **A non-cost motive exists: prompt-injection screening.** Ingested source
  content (web pages, PDFs) flows into LLM context today with no gate.
  That is a security concern, not a latency one, and §4 never weighed it.

So: borrow the *pattern*, not the dependency. Any implementation goes
through the existing AI layer (`provision_langchain_model` + a small,
user-configurable model — e.g. a new `decision` default model type), never
through a second inference stack.

Candidate ideas, in priority order (all *proposal — nothing committed*):

1. **Injection screen on ingested content** — confidence-gated
   noul-style check ("does this content contain instructions directed at
   the AI?") in `graphs/source.py` (`content_process`) or as a filter on
   retrieved chunks before context assembly. Auto-block above threshold,
   flag below. Clear security win, self-contained, no UX risk.
2. **Calibration discipline** (companion to 1) — fit gate thresholds on a
   small labeled eval set (tests/testpack tiers) instead of hardcoding
   them; report confidence quality. Process requirement for any gated
   automation.
3. **Batched structured judgments at ingest** — complements 5.3's
   per-source card: one cheap call yields summary + topics + flags
   (`needs_ocr`, `is_tabular`, `language`, `has_injection`) instead of
   several. Pure prompt/schema design over existing
   `PydanticOutputParser` plumbing.
4. **Ask query router (auto small-vs-frontier model selection)** — still
   marginal per §4/§5.1 (manual per-stage knobs already exist); revisit
   only if Ask token spend becomes a measured problem.

Status: exploration notes only. Any work here should produce an ADR/PDR
per the decisions/ convention.

---

## 9. Addendum (2026-09-27): Sales quick-query lane — structured lookup, pinned scope, verified remarks

Exploration notes for a **salesperson quick-query** workload (primarily
WhatsApp via the gateway): factual, high-volume questions ("how much is
ladder X", "which ladder suits a 2.5m room"). Priorities in order:
**speed, cost, accuracy**; RBAC applies (e.g. costing visible only to
bosses/finance). Status: *proposal — nothing committed.* Full write-up also
in the Obsidian vault ("Open Notebook - Sales Quick-Query Enhancements").

Relationship to §5/§6: Lane 1 below is the domain-specific form of §5.3's
precompute-at-upload (a per-source *spec card* instead of a general digest),
and item 4 is §5.2's answer cache with **scope-aware keys** (RBAC-safe
cross-user sharing). Where they overlap, implement together.

### Target shape (two-lane cascade, permission-filtered)

```
WhatsApp message → linked user (role/team inherited)
  → per-link pinned notebook scope (admin-configured, ⊆ permitted)
  → Lane 1 FAST LOOKUP: structured specs pre-extracted at ingest
       permission filter → product/SKU match → deterministic answer + citation
       (~1–2s, near-zero marginal cost, exact prices)
  → cache hit? → return cached answer (scope-aware key)
  → no structured match → Lane 2: Ask pipeline, pinned scope, capped fan-out
       (clarify gate may offer to broaden — broadening stays within permitted)
```

Pattern names (for searchability): **evidence-based scope pinning with
clarify-gate fallback**, **cascade retrieval** (cheap-first, escalate on
failure), **selective search**.

### Evaluated and rejected for this workload

- **LlamaIndex** — duplicates the existing custom RAG (SurrealDB embeddings
  + LangChain/LangGraph + esperanto).
- **GraphRAG** — no entity-relationship reasoning in the query shape.
- **Reranking** — wrong lever; the problem is retrieval scope, not chunk
  ordering. Lane 1 filters deterministically.
- **Hybrid BM25+vector** — marginal for exact model names; revisit only if
  Lane 1 matching proves weak.

### Items (dependency order)

1. **WIP triage (prerequisite)** — land the uncommitted `ask.py`
   strategy-refactor / home-chat changes as a discrete commit first.
2. **Structured spec extraction** — new SurrealDB table (migration +
   `AsyncMigrationManager` edit); rows extracted at source-processing
   **completion** (embed success path, not upload — processing is async),
   each row tagged with source/notebook id so visibility derives from
   notebook permissions. Public vs restricted data separation (price list ≠
   cost sheet) is an **ingest discipline**, not code — chunk-level ACLs out
   of scope.
3. **Lane 1 endpoint** (e.g. `POST /api/lookup`) tried by integrations
   before home chat: permitted scope (`effective_notebook_scope`) →
   structured filters → template/minimal-LLM answer with citation.
4. **Per-link pinned scope** — replaces `effective_notebook_scope(user, [])`
   (search-all) for messenger links; pin ⊆ permitted by construction;
   escalation via the existing clarify gate broadens within permitted only.
5. **Shared answer cache** — key = `hash(effective_scope + normalized
   question + content_generation)`; generation counter bumped at processing
   completion and on deletions (same hook as §5.2/§6.3). Cache Lane 1 +
   stateless Ask answers, never chat turns; TTL backstop; cached answers
   show age + sources.
6. **Verified remarks** — `remark` field + save-time LLM verification
   (supported/contradicted/unverifiable) on notebooks/sources; **factual**
   remarks authoritative, **contextual** remarks ("user notes") advisory.
   Injection: Lane 1 metadata, Lane 2 context prefix, optionally embedded
   into source vectors. Re-verify on source change (same lifecycle hook as
   the cache). Open: remark author permissions.

### Later/optional

- **Ask-mode for notebook chat** — notebook chat is the expensive surface
  (no token budget, full-context resend, unbounded history); port the
  home-chat pattern (budgeted context via `context_builder.py`, and/or an
  "auto (search)" mode through the Ask subgraph scoped to the notebook).
- **Auto-pin from search provenance** — evidence-based refinement of item 4.
- **Profile facts** (durable personal attributes) — parked, undecided.
