"""Unit tests for the knowledge-only Home chat graph (open_notebook.graphs.home_chat).

Every question — including former analytics phrasing like "highest spender" —
flows through the knowledge pipeline (strategy → search → synthesis, reused
from ``open_notebook.graphs.ask``); the analytics routing hop was removed per
ADR-017. Covers the empty-scope honest answer, conversation-history
formatting, follow-up suggestion parsing, analytics-phrased questions flowing
through the knowledge pipeline, and an end-to-end two-turn run on an
in-memory checkpointer proving memory accumulation and per-turn metadata.
"""

from contextlib import contextmanager
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from open_notebook.graphs import home_chat as home
from open_notebook.graphs.ask import Strategy

EMPTY_CONFIG = cast(RunnableConfig, {"configurable": {}})


def _model_returning(content: str) -> MagicMock:
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return model


def _strategy_payload() -> dict:
    return {
        "strategy": Strategy(
            reasoning="look it up",
            searches=[home.ask_graph.Search(term="rag", instructions="extract")],
        )
    }


@contextmanager
def _patch_knowledge_pipeline(final_answer=None, suggestion: str = "Follow up?"):
    """Patch the full knowledge pipeline (strategy → search → final)."""
    final_side_effect = final_answer or (
        lambda state, config: {"final_answer": f"final for {state['question']}"}
    )
    patches = [
        patch.object(
            home.ask_graph,
            "call_model_with_messages",
            new=AsyncMock(return_value=_strategy_payload()),
        ),
        patch(
            "open_notebook.graphs.ask.vector_search",
            new=AsyncMock(return_value=[{"id": "source:1", "content": "x"}]),
        ),
        patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning("partial answer")),
        ),
        patch.object(
            home.ask_graph,
            "write_final_answer",
            new=AsyncMock(side_effect=final_side_effect),
        ),
        patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning(suggestion)),
        ),
    ]
    with patches[0], patches[1], patches[2], patches[3], patches[4]:
        yield


class TestKnowledgeFinal:
    @pytest.mark.asyncio
    async def test_empty_scope_answers_honestly_without_model(self):
        state = cast(
            home.HomeChatState,
            {"question": "q", "notebook_ids": [], "answers": ["partial"]},
        )
        with patch(
            "open_notebook.graphs.ask.write_final_answer",
            new=AsyncMock(side_effect=AssertionError("must not call a model")),
        ):
            result = await home.knowledge_final(state, EMPTY_CONFIG)
        assert result["final_answer"] == home.NO_SCOPE_ANSWER
        ai_msg = result["messages"][0]
        assert isinstance(ai_msg, AIMessage)
        assert ai_msg.content == home.NO_SCOPE_ANSWER

    @pytest.mark.asyncio
    async def test_non_empty_scope_delegates_to_ask(self):
        state = cast(
            home.HomeChatState,
            {"question": "q", "notebook_ids": ["notebook:1"], "answers": ["a"]},
        )
        with patch(
            "open_notebook.graphs.ask.write_final_answer",
            new=AsyncMock(return_value={"final_answer": "done"}),
        ) as delegate:
            result = await home.knowledge_final(state, EMPTY_CONFIG)
        assert result["final_answer"] == "done"
        assert isinstance(result["messages"][0], AIMessage)
        assert delegate.await_count == 1


class TestAskStateView:
    def test_carries_strategy_and_answers_into_final_stage(self):
        """Regression: dropping answers/strategy made the synthesis prompt's
        RESULTS section empty, so the model honestly claimed it had no
        retrieved text even after successful searches."""
        strategy = Strategy(reasoning="r", searches=[])
        state = cast(
            home.HomeChatState,
            {
                "question": "q",
                "strategy": strategy,
                "answers": [
                    {"question": "q", "content": "partial one"},
                    {"question": "older question", "content": "stale"},
                ],
                "messages": [HumanMessage(content="q")],
            },
        )
        view = home._ask_state_view(state)
        assert view["strategy"] is strategy
        assert view["answers"] == ["partial one"]
        assert view["question"] == "q"


class TestChatHistory:
    def test_excludes_current_question_and_limits_turns(self):
        messages = [
            HumanMessage(content=f"q{i} (older)") if i % 2 == 0 else AIMessage(content=f"a{i}")
            for i in range(12)
        ]
        messages.append(HumanMessage(content="current question"))
        state = cast(home.HomeChatState, {"messages": messages})
        history = home._format_chat_history(state)
        assert "current question" not in history
        # Only the last HISTORY_TURNS exchanges survive
        assert "q4 (older)" not in history
        assert "q6 (older)" in history
        assert history.count("User:") == home.HISTORY_TURNS

    def test_empty_when_single_message(self):
        state = cast(
            home.HomeChatState,
            {"messages": [HumanMessage(content="only question")]},
        )
        assert home._format_chat_history(state) == ""


class TestSuggestFollowups:
    @pytest.mark.asyncio
    async def test_parses_numbered_and_plain_lines(self):
        state = cast(home.HomeChatState, {"question": "q", "final_answer": "answer"})
        raw = "1. What about October?\n- Any top services?\nno prose\nAlso relevant?"
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning(raw)),
        ):
            result = await home.suggest_followups(state, EMPTY_CONFIG)
        assert result["suggestions"] == [
            "What about October?",
            "Any top services?",
            "Also relevant?",
        ]
        assert result["turn_metadata"] == [{"suggestions": result["suggestions"]}]

    @pytest.mark.asyncio
    async def test_failure_yields_empty_suggestions(self):
        state = cast(home.HomeChatState, {"question": "q", "final_answer": "answer"})
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await home.suggest_followups(state, EMPTY_CONFIG)
        assert result["suggestions"] == []
        assert result["turn_metadata"] == [{"suggestions": []}]


class TestKnowledgeOnlyPipeline:
    """Former analytics phrasing now flows through the knowledge pipeline —
    there is no classifier and no analytics node anywhere in the graph."""

    @pytest.mark.asyncio
    async def test_highest_spender_question_flows_through_knowledge(self):
        from langgraph.checkpoint.memory import MemorySaver

        graph = home._home_state.compile(checkpointer=MemorySaver())
        config = RunnableConfig(configurable={"thread_id": "spender-thread"})

        with _patch_knowledge_pipeline():
            result = await graph.ainvoke(  # type: ignore[call-overload]
                {
                    "messages": [
                        HumanMessage(content="Who is the highest spender this year?")
                    ],
                    "question": "Who is the highest spender this year?",
                    "notebook_ids": ["notebook:1"],
                },
                config=config,
            )

        assert result["final_answer"] == "final for Who is the highest spender this year?"
        assert result["suggestions"] == ["Follow up?"]
        # Turn metadata carries only the suggestions list (no analytics key).
        assert result["turn_metadata"] == [{"suggestions": ["Follow up?"]}]

    def test_graph_has_no_classify_or_analytics_nodes(self):
        assert "classify" not in home._home_state.nodes
        assert "analytics" not in home._home_state.nodes
        assert "knowledge_strategy" in home._home_state.nodes
        assert "knowledge_search" in home._home_state.nodes
        assert "knowledge_final" in home._home_state.nodes
        assert "suggest" in home._home_state.nodes


def _compiled_graph():
    from langgraph.checkpoint.memory import MemorySaver

    return home._home_state.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_production_graph_streams_with_async_checkpointer(tmp_path):
    """Regression: the lazily-built graph uses AsyncSqliteSaver and must
    stream via astream — a sync SqliteSaver raises NotImplementedError
    ("does not support async methods") mid-stream."""
    import aiosqlite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    home._home_chat_graph = None
    production_graph = await home.get_home_chat_graph()
    assert isinstance(production_graph.checkpointer, AsyncSqliteSaver)

    saver = AsyncSqliteSaver(aiosqlite.connect(str(tmp_path / "cp.sqlite")))
    graph = home._home_state.compile(checkpointer=saver)
    config = RunnableConfig(configurable={"thread_id": "async-test"})

    with _patch_knowledge_pipeline(final_answer=lambda state, config: {"final_answer": "done"}):
        chunks = [
            chunk
            async for chunk in graph.astream(  # type: ignore[call-overload]
                {
                    "messages": [HumanMessage(content="top spender?")],
                    "question": "top spender?",
                    "notebook_ids": ["notebook:1"],
                },
                config=config,
                stream_mode="updates",
            )
        ]

    node_names = {next(iter(c)) for c in chunks}
    assert node_names == {"knowledge_strategy", "knowledge_search", "knowledge_final", "suggest"}
    # The turn actually persisted to the async checkpointer.
    state = await graph.aget_state(config=config)
    assert state.values["messages"][-1].content == "done"


@pytest.mark.asyncio
class TestEndToEndMemory:
    async def test_two_turns_accumulate_messages_and_turn_metadata(self):
        graph = _compiled_graph()
        config = RunnableConfig(configurable={"thread_id": "test-thread"})
        final_stage_views: list = []

        async def run_turn(question: str) -> dict:
            def record_view(state, config):
                final_stage_views.append(dict(state))
                return {"final_answer": f"final for {state['question']}"}

            with _patch_knowledge_pipeline(final_answer=record_view):
                return await graph.ainvoke(
                    {
                        "messages": [HumanMessage(content=question)],
                        "question": question,
                        "notebook_ids": ["notebook:1"],
                    },
                    config=config,
                )

        first = await run_turn("first question")
        assert first["final_answer"] == "final for first question"
        assert first["suggestions"] == ["Follow up?"]

        second = await run_turn("second question")
        # Memory: the checkpoint carries the first turn's messages forward.
        human_turns = [
            m for m in second["messages"] if getattr(m, "type", None) == "human"
        ]
        assert len(human_turns) == 2
        # The strategy stage saw the prior turn as chat history.
        strategy_state = home._ask_state_view(cast(home.HomeChatState, second))
        assert "first question" in strategy_state["chat_history"]
        assert "final for first question" in strategy_state["chat_history"]
        # One turn_metadata entry per AI turn, appended not duplicated —
        # each entry carries only the suggestions list.
        assert len(second["turn_metadata"]) == 2
        assert second["turn_metadata"] == [
            {"suggestions": ["Follow up?"]},
            {"suggestions": ["Follow up?"]},
        ]
        # Turn isolation: the second turn's synthesis saw ONLY its own
        # search results — not turn 1's accumulated answers (that leak made
        # the new answer merge the previous one).
        assert final_stage_views[1]["question"] == "second question"
        assert final_stage_views[1]["answers"] == ["partial answer"]
