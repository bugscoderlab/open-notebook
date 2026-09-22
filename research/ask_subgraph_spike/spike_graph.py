"""Throwaway spike for wayfinder ticket #58.

Can the compiled Ask graph (``open_notebook.graphs.ask.graph``) run as a
subgraph of the home-chat memory wrapper, with token-level streaming of the
synthesis node? This module is the feasibility proof — it is NOT the
implementation (that is ticket #60).

Composition (see also the findings posted on issue #58):

    START → prepare_ask ──(non-empty notebook_ids)──▶ ask (subgraph)
              │                                          │  = ask.graph,
              └──(empty notebook_ids)──▶ no_scope ────────┘   compiled wholesale
                                       (bypass, T5)          (no node re-wiring)
                                          │                  ▼
                                          └─────── remember ───┘  (final_answer → AIMessage)
                                                        │
                                                      suggest ──▶ END  (reused from home_chat)

State-channel mapping across the subgraph boundary (mapping is BY NAME):

Cleanly shared (declared in both schemas, plain overwrite reducers):
  ``question``, ``notebook_ids``, ``chat_history``, ``clarify``, ``final_answer``.
  ``prepare_ask`` projects the outer ``messages`` channel into the
  ``chat_history`` string the ask prompts render — the only projection left,
  and it projects DATA, not node functions.

Deliberately NOT shared (turn isolation, finding d):
  ``strategy`` and ``answers`` are declared in ask's ThreadState but NOT in
  the outer state. Channels the parent does not declare stay private to the
  subgraph's per-invocation checkpoint namespace (``ask:<uuid>``), so every
  turn starts with empty ``strategy``/``answers`` — the cross-turn leakage
  today's ``_ask_state_view`` + per-turn answer tagging exists to prevent.
  If the outer graph ever declares ``answers`` (e.g. to fan per-search
  results into the outer update stream), operator.add makes it accumulate
  across turns and turn N's synthesis sees turns 1..N-1 partial answers.
  Asserted in tests/test_ask_subgraph_spike.py.

Never shared: ``messages``/``turn_metadata``/``suggestions`` (outer only);
``term``/``instructions``/``results``/``ids`` (subgraph private, discarded
after the invocation).
"""

import operator
from typing import Annotated, Any, List, Optional, cast

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from open_notebook.graphs import ask as ask_graph
from open_notebook.graphs import home_chat


class SpikeHomeState(TypedDict, total=False):
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


class SpikeHomeStateSharedAnswers(SpikeHomeState, total=False):
    """Gotcha variant (finding d): outer also declares ``answers``.

    operator.add on both sides means the shared channel accumulates across
    turns, and turn N's ``write_final_answer`` sees turns 1..N-1 partial
    answers. Built only so the test can demonstrate the leak.
    """

    answers: Annotated[list, operator.add]


async def prepare_ask(state: SpikeHomeState, config: RunnableConfig) -> dict:
    """Project the outer memory into the shared ask channels.

    This replaces ``home_chat._ask_state_view``: it copies DATA (question,
    rendered chat history) instead of re-wiring ask's node functions. The
    ask graph is invoked wholesale immediately after.
    """
    # The home-chat helpers only read ``messages``/``question`` — both
    # present here (TypedDicts don't subtype across declarations).
    home_view = cast(home_chat.HomeChatState, state)
    return {
        "question": home_chat._last_human_question(home_view),
        "chat_history": home_chat._format_chat_history(home_view),
        # Home chat never opts into the clarification gate; pass-through so
        # the spike can still exercise the clarify branch (assertion c) and
        # old checkpoints without the channel default to False.
        "clarify": bool(state.get("clarify")),
    }


async def no_scope_answer(state: SpikeHomeState, config: RunnableConfig) -> dict:
    """T5: explicitly-empty notebook scope answers honestly, no model call.

    With the wholesale subgraph this has to live OUTSIDE the ask graph: the
    ask pipeline's empty-scope behaviour is per-search (provide_answer
    returns []), but write_final_answer would still call a model. Today's
    home_chat.knowledge_final intercepts; the composition intercepts here,
    one conditional edge earlier.
    """
    return {
        "final_answer": home_chat.NO_SCOPE_ANSWER,
        "messages": [AIMessage(content=home_chat.NO_SCOPE_ANSWER)],
    }


def _route_after_prepare(state: SpikeHomeState) -> str:
    if "notebook_ids" in state and not state["notebook_ids"]:
        return "no_scope"
    return "ask"


async def remember(state: SpikeHomeState, config: RunnableConfig) -> dict:
    """Persist the ask synthesis into the memory wrapper's messages channel."""
    return {"messages": [AIMessage(content=state.get("final_answer", ""))]}


def build_graph(checkpointer: Optional[Any] = None, share_answers: bool = False) -> Any:
    """Compile the composed spike graph.

    ``ask_graph.graph`` — the module-level compiled Ask graph — is added as a
    node wholesale; no ask node functions are re-wired. ``suggest`` is reused
    unmodified from home_chat (it only needs question/final_answer).
    """
    schema = SpikeHomeStateSharedAnswers if share_answers else SpikeHomeState
    builder = StateGraph(schema)
    builder.add_node("prepare_ask", prepare_ask)
    builder.add_node("ask", ask_graph.graph)
    builder.add_node("no_scope", no_scope_answer)
    builder.add_node("remember", remember)
    builder.add_node("suggest", home_chat.suggest_followups)
    builder.add_edge(START, "prepare_ask")
    builder.add_conditional_edges("prepare_ask", _route_after_prepare)
    builder.add_edge("ask", "remember")
    builder.add_edge("no_scope", "suggest")
    builder.add_edge("remember", "suggest")
    builder.add_edge("suggest", END)
    return builder.compile(checkpointer=checkpointer)
