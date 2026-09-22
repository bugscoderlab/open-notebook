"""Knowledge-only Home ask chat graph.

Every question flows through the Ask pipeline (``open_notebook.graphs.ask``)
**wholesale** — the compiled Ask graph runs as a subgraph inside this memory
wrapper:

    START → prepare_ask ──(non-empty notebook_ids)──▶ ask (subgraph)
              │                                          │  = ask.graph,
              └──(empty notebook_ids)──▶ no_scope ────────┘   compiled wholesale
                                         (bypass, T5)          (no node re-wiring)
                                            │                  ▼
                                            └─────── remember ───┘  (final_answer → AIMessage)
                                                          │
                                                        suggest ──▶ END

Historical notes:

- This graph used to re-wire the ask node functions into its own pipeline
  with a hand-rolled state projection (``_ask_state_view``, per-turn answer
  tagging). That layer was replaced by wholesale subgraph delegation
  (wayfinder map #56, spike #58) — the projection only projected DATA, and
  every turn-isolation problem it solved is solved free by keeping the ask
  channels subgraph-private (see below).
- An earlier analytics routing hop was removed per ADR-017: business-data
  phrasing is answered from documents like any other question.

State-channel mapping across the subgraph boundary (mapping is BY NAME):

- Shared (declared in both schemas, plain overwrite reducers): ``question``,
  ``notebook_ids``, ``chat_history``, ``clarify``, ``final_answer``.
  ``prepare_ask`` projects the outer ``messages`` channel into the
  ``chat_history`` string the ask prompts render — the only projection left.
- Deliberately NOT shared (turn isolation): ``strategy`` and ``answers``
  stay private to the subgraph's per-invocation checkpoint namespace, so
  every turn starts with empty values. Declaring ``answers`` in this schema
  would make ``operator.add`` accumulate across turns and leak turn N-1's
  partial answers into turn N's synthesis.
- Never shared: ``messages``/``turn_metadata``/``suggestions`` (outer only);
  ``term``/``instructions``/``results``/``ids`` (subgraph private).

Memory: compiled with the shared AsyncSqliteSaver checkpointer keyed by
``thread_id = session_id`` — the ``messages`` channel (``add_messages``)
accumulates Human/AI messages across turns, and ``turn_metadata``
accumulates one entry per AI turn (the suggestions list) so the GET
endpoint can re-attach them to the persisted messages.
"""

import asyncio
import operator
import re
from typing import Annotated, Any, AsyncGenerator, List, Optional, TypedDict

import aiosqlite
from ai_prompter import Prompter
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.graphs import ask as ask_graph
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.error_classifier import classify_error
from open_notebook.utils.text_utils import extract_text_content

NO_SCOPE_ANSWER = (
    "I don't have access to any notebooks that could answer this question."
)

HISTORY_TURNS = 3


class HomeChatState(TypedDict, total=False):
    # Memory wrapper (outer-only channels).
    messages: Annotated[list, add_messages]
    turn_metadata: Annotated[list, operator.add]
    suggestions: List[str]
    # Channels shared BY NAME with ask.ThreadState — values cross the
    # subgraph boundary in both directions. All have overwrite reducers on
    # both sides, so cross-turn behaviour is "current turn wins".
    question: str
    notebook_ids: List[str]
    chat_history: str
    clarify: bool
    final_answer: str


def _last_human_question(state: HomeChatState) -> str:
    """The current question — callers store it explicitly, but fall back
    to the last human message when the graph is invoked directly (tests)."""
    question = state.get("question")
    if question:
        return question
    for msg in reversed(state.get("messages", [])):
        if getattr(msg, "type", None) == "human":
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _format_chat_history(state: HomeChatState) -> str:
    """Render prior turns as 'User:/Assistant:' lines for the ask prompts.

    Excludes the current question (``prepare_ask`` sets state["question"] and
    the HumanMessage is appended separately by the caller).
    """
    turns: List[str] = []
    for msg in state.get("messages", []):
        msg_type = getattr(msg, "type", None)
        if msg_type not in ("human", "ai"):
            continue
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue
        label = "User" if msg_type == "human" else "Assistant"
        turns.append(f"{label}: {content}")
    # The last entry is the current question (already in state["question"]).
    if turns and turns[-1].startswith("User:"):
        turns = turns[:-1]
    return "\n\n".join(turns[-HISTORY_TURNS * 2 :])


async def prepare_ask(state: HomeChatState, config: RunnableConfig) -> dict:
    """Project the outer memory into the shared ask channels.

    This replaces the old ``_ask_state_view``: it copies DATA (question,
    rendered chat history, clarify flag) instead of re-wiring ask's node
    functions. The ask graph is invoked wholesale immediately after.
    """
    return {
        "question": _last_human_question(state),
        "chat_history": _format_chat_history(state),
        # Chat always opts into the clarification gate (wayfinder map #56,
        # Q3). Explicit bool so a missing channel defaults to False.
        "clarify": bool(state.get("clarify")),
    }


async def no_scope_answer(state: HomeChatState, config: RunnableConfig) -> dict:
    """T5: an explicitly-empty notebook scope answers honestly, no model call.

    With the wholesale subgraph this lives OUTSIDE the ask graph: the ask
    pipeline's empty-scope behaviour is per-search (provide_answer returns
    []), but write_final_answer would still call a model. The composition
    intercepts one conditional edge earlier.
    """
    return {
        "final_answer": NO_SCOPE_ANSWER,
        "messages": [AIMessage(content=NO_SCOPE_ANSWER)],
    }


def _route_after_prepare(state: HomeChatState) -> str:
    if "notebook_ids" in state and not state["notebook_ids"]:
        return "no_scope"
    return "ask"


async def remember(state: HomeChatState, config: RunnableConfig) -> dict:
    """Persist the ask synthesis into the memory wrapper's messages channel."""
    return {"messages": [AIMessage(content=state.get("final_answer", ""))]}


# Leading list markers / enumerators on model-produced suggestion lines.
_SUGGESTION_PREFIX_RE = re.compile(r"^\s*(?:[-*•]\s*)?(?:\d+[.)]\s*)?")


async def suggest_followups(state: HomeChatState, config: RunnableConfig) -> dict:
    """Generate 2-3 follow-up questions for the answer just produced.

    Best-effort by design: any failure yields no suggestions rather than
    failing the turn. The same list is persisted in turn_metadata so the
    GET endpoint can re-attach it to the stored AI message.
    """
    suggestions: List[str] = []
    try:
        answer_text = state.get("final_answer") or ""
        if answer_text:
            system_prompt = Prompter(prompt_template="home_chat/suggestions").render(  # type: ignore[arg-type]
                data={"question": _last_human_question(state), "answer": answer_text}
            )
            model = await provision_langchain_model(
                system_prompt, None, "chat", max_tokens=300
            )
            response = await model.ainvoke([HumanMessage(content=system_prompt)])
            raw = clean_thinking_content(extract_text_content(response.content))
            for line in raw.splitlines():
                question = _SUGGESTION_PREFIX_RE.sub("", line).strip()
                if question and question.endswith("?"):
                    suggestions.append(question)
            suggestions = suggestions[:3]
    except Exception:
        suggestions = []
    metadata = {"suggestions": suggestions}
    return {"suggestions": suggestions, "turn_metadata": [metadata]}


def _build_graph(checkpointer: Optional[Any] = None) -> Any:
    """Compile the composed home-chat graph.

    ``ask_graph.graph`` — the module-level compiled Ask graph — is added as a
    node wholesale; no ask node functions are re-wired.
    """
    builder = StateGraph(HomeChatState)
    builder.add_node("prepare_ask", prepare_ask)
    builder.add_node("ask", ask_graph.graph)
    builder.add_node("no_scope", no_scope_answer)
    builder.add_node("remember", remember)
    builder.add_node("suggest", suggest_followups)
    builder.add_edge(START, "prepare_ask")
    builder.add_conditional_edges("prepare_ask", _route_after_prepare)
    builder.add_edge("ask", "remember")
    builder.add_edge("no_scope", "suggest")
    builder.add_edge("remember", "suggest")
    builder.add_edge("suggest", END)
    return builder.compile(checkpointer=checkpointer)


# Async SQLite checkpointer — the home chat graph streams with `astream`,
# which requires an async checkpointer (the sync SqliteSaver raises
# "does not support async methods" mid-stream). Same file as the other chat
# graphs; the router reads state via the graph's async `aget_state`.
#
# AsyncSqliteSaver binds to the running event loop at construction, so the
# graph is built lazily on first use (the API imports modules outside a
# running loop) via `get_home_chat_graph()`.
_home_chat_graph: Optional[Any] = None


async def get_home_chat_graph():
    """Lazily compile (once) and return the checkpointed home chat graph."""
    global _home_chat_graph
    if _home_chat_graph is None:
        memory = AsyncSqliteSaver(aiosqlite.connect(LANGGRAPH_CHECKPOINT_FILE))
        _home_chat_graph = _build_graph(checkpointer=memory)
    return _home_chat_graph


# ---------------------------------------------------------------------------
# Shared turn generator (one brain, two mouths: the web SSE endpoint and the
# messenger gateway's internal streaming endpoint both consume this).
# ---------------------------------------------------------------------------


class HomeTurnEvent(TypedDict, total=False):
    """Typed events emitted by ``stream_home_turn``.

    Sequence for a normal turn: answer_delta* → final_answer → suggestions
    (only when present) → complete. Failure at any point: a single error
    event (never a bare stream end — the #57 silent-abort lesson).
    """

    type: str  # "answer_delta" | "final_answer" | "suggestions" | "complete" | "error"
    content: str
    suggestions: List[str]
    final_answer: str
    message: str


async def stream_home_turn(
    *,
    session_id: str,
    question: str,
    notebook_ids: List[str],
    strategy_model: Optional[str],
    answer_model: Optional[str],
    final_answer_model: Optional[str],
) -> AsyncGenerator[HomeTurnEvent, None]:
    """Stream one conversational home-chat turn as typed events.

    Runs the composed graph with the token-streaming recipe proven by the
    spike: ``stream_mode=["updates", "messages"], subgraphs=True`` — without
    ``subgraphs=True`` the ask subgraph's LLM tokens do not stream at all.

    The generator catches ``BaseException`` so a connection kill or
    cancellation surfaces as a typed error event instead of a bare EOF
    (absorbed hard requirement from the diagnosis #57 / superseded hotfix
    #61). ``GeneratorExit`` and ``CancelledError`` are re-raised after the
    error event so cancellation semantics stay intact.
    """
    graph = await get_home_chat_graph()
    thread_config = RunnableConfig(configurable={"thread_id": session_id})
    current_state = await graph.aget_state(config=thread_config)
    messages = list((current_state.values if current_state else {}).get("messages", []))
    messages.append(HumanMessage(content=question))

    final_answer: Optional[str] = None
    suggestions: List[str] = []
    streamed: List[str] = []

    try:
        async for namespace, mode, payload in graph.astream(  # type: ignore[call-overload]
            input={
                "messages": messages,
                "question": question,
                "notebook_ids": notebook_ids,
                # Per-turn output channels are plain (no reducer) — reset them
                # so a value checkpointed by a PREVIOUS turn can't leak.
                "final_answer": None,
                "suggestions": None,
                # Chat opts into the Ask clarification gate (map #56, Q3).
                "clarify": True,
            },
            config=RunnableConfig(
                configurable={
                    "thread_id": session_id,
                    "strategy_model": strategy_model,
                    "answer_model": answer_model,
                    "final_answer_model": final_answer_model,
                }
            ),
            stream_mode=["updates", "messages"],
            subgraphs=True,
        ):
            if mode == "messages":
                chunk, metadata = payload
                if (
                    metadata.get("langgraph_node") == "write_final_answer"
                    and chunk.content
                ):
                    streamed.append(chunk.content)
                    yield {"type": "answer_delta", "content": chunk.content}
                continue

            node = next(iter(payload), None)
            update = payload.get(node, {}) if node else {}
            if node == "suggest":
                suggestions = update.get("suggestions") or []
            elif node in ("ask", "no_scope"):
                value = update.get("final_answer")
                if value:
                    final_answer = value

        # The clarify and empty-scope paths produce no token stream — their
        # final answer arrives via the node update above.
        if final_answer is None:
            final_answer = "".join(streamed)
        yield {"type": "final_answer", "content": final_answer}
        if suggestions:
            yield {"type": "suggestions", "suggestions": suggestions}
        yield {
            "type": "complete",
            "final_answer": final_answer,
            "suggestions": suggestions,
        }
    except GeneratorExit:
        raise
    except asyncio.CancelledError:
        _, message = classify_error(asyncio.CancelledError())
        yield {"type": "error", "message": message}
        raise
    except BaseException as e:  # noqa: BLE001 - never bare-EOF a chat turn
        _, message = classify_error(e)
        yield {"type": "error", "message": message}
