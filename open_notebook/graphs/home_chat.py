"""Knowledge-only Home ask chat graph.

Every question flows through the knowledge pipeline (the Ask strategy →
search → synthesis stages, reused from ``open_notebook.graphs.ask``),
followed by follow-up question suggestions. There is no routing hop:
business-data phrasing is answered from documents like any other question.

Historical note: this graph used to auto-route each message to either the
knowledge pipeline or a dedicated analytics pipeline (LLM router with a
deterministic keyword fallback). That routing — together with the analytics
node, the caller-role adapter, and the route/refunds/analytics-payload state
fields — was removed per ADR-017.

Memory: compiled with the shared SqliteSaver checkpointer keyed by
``thread_id = session_id`` — the ``messages`` channel (``add_messages``)
accumulates Human/AI messages across turns, and ``turn_metadata``
accumulates one entry per AI turn (the suggestions list) so the GET
endpoint can re-attach them to the persisted messages.
"""

import operator
import re
from typing import Annotated, Any, List, Optional, cast

import aiosqlite
from ai_prompter import Prompter
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send
from typing_extensions import TypedDict

from open_notebook.ai.provision import provision_langchain_model
from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE
from open_notebook.graphs import ask as ask_graph
from open_notebook.graphs.ask import ThreadState
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.text_utils import extract_text_content

NO_SCOPE_ANSWER = (
    "I don't have access to any notebooks that could answer this question."
)

HISTORY_TURNS = 3


class HomeChatState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    # Current-turn inputs (set by the router on every invoke).
    question: str
    notebook_ids: List[str]
    # Current-turn outputs, streamed to the client as SSE events.
    strategy: Any
    answers: Annotated[list, operator.add]
    final_answer: str
    suggestions: List[str]
    # One entry per AI turn, persisted for the GET endpoint (aligned with AI
    # messages in order). operator.add appends — the router must NOT replay
    # the checkpointed list back into the input.
    turn_metadata: Annotated[list, operator.add]


def _last_human_question(state: HomeChatState) -> str:
    """The current question — the router stores it explicitly, but fall back
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

    Excludes the current question (the router sets state["question"] and
    appends the HumanMessage separately).
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


def _ask_state_view(state: HomeChatState) -> ThreadState:
    """Project the home state into the shape the ask graph nodes expect.

    Must carry ``strategy`` and ``answers`` — the ask final-answer prompt
    renders both, and without them the synthesis stage sees an empty
    RESULTS section and (honestly) claims it has no retrieved text.

    ``answers`` accumulates across turns via the operator.add reducer, so
    only the entries tagged with the CURRENT question are forwarded —
    otherwise turn 2's synthesis would also see turn 1's search results
    and merge the previous answer into the new one.
    """
    question = _last_human_question(state)
    turn_answers = [
        entry.get("content", "")
        for entry in state.get("answers") or []
        if isinstance(entry, dict) and entry.get("question") == question
    ]
    return cast(
        ThreadState,
        {
            "question": question,
            "chat_history": _format_chat_history(state),
            "notebook_ids": state.get("notebook_ids") or [],
            "strategy": state.get("strategy"),
            "answers": turn_answers,
        },
    )


async def knowledge_strategy(state: HomeChatState, config: RunnableConfig) -> dict:
    return await ask_graph.call_model_with_messages(_ask_state_view(state), config)


async def knowledge_search_trigger(state: HomeChatState, config: RunnableConfig) -> list:
    """Fan out one search per strategy term (mirrors ask.trigger_queries)."""
    view = _ask_state_view(state)
    strategy = state.get("strategy")
    if not strategy:
        return []
    return [
        Send(
            "knowledge_search",
            {
                "question": view["question"],
                "instructions": s.instructions,
                "term": s.term,
                "notebook_ids": view["notebook_ids"],
            },
        )
        for s in strategy.searches
    ]


async def knowledge_final(state: HomeChatState, config: RunnableConfig) -> dict:
    """Synthesize the final knowledge answer, with conversation context.

    T5: an explicitly-empty notebook scope means "this caller may read
    nothing" — answer honestly without calling a model (mirrors the ask
    endpoint's empty-scope short-circuit).
    """
    if "notebook_ids" in state and not state["notebook_ids"]:
        return {
            "final_answer": NO_SCOPE_ANSWER,
            "messages": [AIMessage(content=NO_SCOPE_ANSWER)],
        }
    result = await ask_graph.write_final_answer(_ask_state_view(state), config)
    # The ask stage is single-shot; memory lives in our messages channel.
    return {**result, "messages": [AIMessage(content=result.get("final_answer", ""))]}


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


# Reuse the ask per-search node, wrapped: each partial answer is tagged
# with its question so a later turn's synthesis can tell its own results
# apart from the ones the operator.add channel accumulated before (the
# reducer never resets, and untagged answers would leak across turns).
async def knowledge_search(state: "ask_graph.SubGraphState", config: RunnableConfig) -> dict:
    result = await ask_graph.provide_answer(state, config)
    return {
        "answers": [
            {"question": state.get("question"), "content": content}
            for content in result.get("answers", [])
        ]
    }



_home_state = StateGraph(HomeChatState)
_home_state.add_node("knowledge_strategy", knowledge_strategy)
_home_state.add_node("knowledge_search", knowledge_search)
_home_state.add_node("knowledge_final", knowledge_final)
_home_state.add_node("suggest", suggest_followups)
_home_state.add_edge(START, "knowledge_strategy")
_home_state.add_conditional_edges(
    "knowledge_strategy", knowledge_search_trigger, ["knowledge_search"]
)
_home_state.add_edge("knowledge_search", "knowledge_final")
_home_state.add_edge("knowledge_final", "suggest")
_home_state.add_edge("suggest", END)


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
        _home_chat_graph = _home_state.compile(checkpointer=memory)
    return _home_chat_graph
