"""Unit tests for the unified Home chat graph (open_notebook.graphs.home_chat).

Covers the auto-routing classifier (LLM + keyword fallback), the empty-scope
honest answer, conversation-history formatting, follow-up suggestion
parsing, and an end-to-end two-turn run on an in-memory checkpointer proving
memory accumulation and per-turn metadata.
"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig

from open_notebook.analytics.service import AnalyticsAnswer
from open_notebook.graphs import home_chat as home
from open_notebook.graphs.ask import Strategy

EMPTY_CONFIG = cast(RunnableConfig, {"configurable": {}})


def _model_returning(content: str) -> MagicMock:
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return model


class TestClassifyKeywords:
    def test_strong_analytics_signals_route_analytics(self):
        assert home.classify_keywords("Who spent the most this year?") == "analytics"
        assert home.classify_keywords("Rank customers by revenue") == "analytics"
        assert home.classify_keywords("average transaction value") == "analytics"

    def test_knowledge_questions_return_none(self):
        assert home.classify_keywords("What is RAG?") is None
        assert home.classify_keywords("Summarize the quarterly report") is None


class TestClassifyWithModel:
    @pytest.mark.asyncio
    async def test_analytics_keyword_reply(self):
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning("ANALYTICS")),
        ):
            assert await home._classify_with_model("top spender?") == "analytics"

    @pytest.mark.asyncio
    async def test_knowledge_keyword_reply(self):
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning("KNOWLEDGE")),
        ):
            assert await home._classify_with_model("what is rag?") == "knowledge"

    @pytest.mark.asyncio
    async def test_unparseable_reply_returns_none(self):
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning("I cannot say")),
        ):
            assert await home._classify_with_model("hello?") is None

    @pytest.mark.asyncio
    async def test_model_failure_returns_none(self):
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(side_effect=RuntimeError("no model")),
        ):
            assert await home._classify_with_model("hello?") is None


class TestClassifyMessage:
    @pytest.mark.asyncio
    async def test_falls_back_to_keywords_when_model_missing(self):
        state = cast(home.HomeChatState, {"question": "Who is the top spender?"})
        with patch(
            "open_notebook.graphs.home_chat._classify_with_model",
            new=AsyncMock(return_value=None),
        ):
            result = await home.classify_message(state, EMPTY_CONFIG)
        assert result["route"] == "analytics"

    @pytest.mark.asyncio
    async def test_defaults_to_knowledge(self):
        state = cast(home.HomeChatState, {"question": "Explain vector search"})
        with patch(
            "open_notebook.graphs.home_chat._classify_with_model",
            new=AsyncMock(return_value=None),
        ):
            result = await home.classify_message(state, EMPTY_CONFIG)
        assert result["route"] == "knowledge"


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
        assert result["turn_metadata"] == [
            {"analytics_answer": None, "suggestions": result["suggestions"]}
        ]

    @pytest.mark.asyncio
    async def test_failure_yields_empty_suggestions(self):
        state = cast(home.HomeChatState, {"question": "q", "final_answer": "answer"})
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ):
            result = await home.suggest_followups(state, EMPTY_CONFIG)
        assert result["suggestions"] == []
        assert result["turn_metadata"][0]["suggestions"] == []

    @pytest.mark.asyncio
    async def test_analytics_answer_is_carried_into_metadata(self):
        payload = {"status": "ok", "answer_text": "Sarah spent MYR 100."}
        state = cast(
            home.HomeChatState,
            {
                "question": "q",
                "route": "analytics",
                "final_answer": "",
                "analytics_answer": payload,
            },
        )
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning("Follow up?")),
        ):
            result = await home.suggest_followups(state, EMPTY_CONFIG)
        assert result["turn_metadata"][0]["analytics_answer"] == payload


class TestRunAnalytics:
    @pytest.mark.asyncio
    async def test_calls_service_with_caller_from_state(self):
        answer = AnalyticsAnswer(status="ok", answer_text="Sarah spent MYR 100.")
        state = cast(
            home.HomeChatState,
            {
                "question": "top spender?",
                "user_id": "user:1",
                "team_id": "team:1",
                "role": "ceo",
            },
        )
        with patch(
            "open_notebook.graphs.home_chat.ask_analytics_question",
            new=AsyncMock(return_value=answer),
        ) as service:
            result = await home.run_analytics(state, EMPTY_CONFIG)
        assert service.await_count == 1
        assert service.await_args is not None
        caller = service.await_args.kwargs["caller"]
        assert caller.id == "user:1" and caller.team_id == "team:1" and caller.role == "ceo"
        assert result["analytics_answer"]["status"] == "ok"
        assert result["messages"][0].content == "Sarah spent MYR 100."


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

    answer = AnalyticsAnswer(status="ok", answer_text="Sarah spent MYR 100.")
    with (
        patch.object(
            home, "_classify_with_model", new=AsyncMock(return_value="analytics")
        ),
        patch.object(
            home, "ask_analytics_question", new=AsyncMock(return_value=answer)
        ),
        patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning("Follow up?")),
        ),
    ):
        chunks = [
            chunk
            async for chunk in graph.astream(  # type: ignore[call-overload]
                {
                    "messages": [HumanMessage(content="top spender?")],
                    "question": "top spender?",
                    "user_id": "user:1",
                    "team_id": "team:1",
                    "role": "member",
                },
                config=config,
                stream_mode="updates",
            )
        ]

    node_names = {next(iter(c)) for c in chunks}
    assert "classify" in node_names
    assert "analytics" in node_names
    assert "suggest" in node_names
    # The turn actually persisted to the async checkpointer.
    state = await graph.aget_state(config=config)
    assert state.values["messages"][-1].content == "Sarah spent MYR 100."


def _strategy_payload() -> dict:
    return {
        "strategy": Strategy(
            reasoning="look it up",
            searches=[home.ask_graph.Search(term="rag", instructions="extract")],
        )
    }


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

            with (
                patch.object(
                    home, "_classify_with_model", new=AsyncMock(return_value="knowledge")
                ),
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
                    new=AsyncMock(side_effect=record_view),
                ),
                patch(
                    "open_notebook.graphs.home_chat.provision_langchain_model",
                    new=AsyncMock(return_value=_model_returning("Tell me more?")),
                ),
            ):
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
        assert first["suggestions"] == ["Tell me more?"]

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
        # One turn_metadata entry per AI turn, appended not duplicated.
        assert len(second["turn_metadata"]) == 2
        # Turn isolation: the second turn's synthesis saw ONLY its own
        # search results — not turn 1's accumulated answers (that leak made
        # the new answer merge the previous one).
        assert final_stage_views[1]["question"] == "second question"
        assert final_stage_views[1]["answers"] == ["partial answer"]

    async def test_analytics_then_knowledge_does_not_replay_old_chart(self):
        """Regression: an analytics payload lives in the checkpointed state
        across turns, so a following knowledge turn must not attach it to
        its own metadata (the UI would render the old chart in the new
        answer)."""
        graph = _compiled_graph()
        config = RunnableConfig(configurable={"thread_id": "mixed-thread"})
        answer = AnalyticsAnswer(status="ok", answer_text="Sarah spent MYR 100.")

        async def run_turn(question: str, route: str) -> dict:
            with (
                patch.object(
                    home, "_classify_with_model", new=AsyncMock(return_value=route)
                ),
                patch.object(
                    home, "ask_analytics_question", new=AsyncMock(return_value=answer)
                ),
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
                    new=AsyncMock(return_value=_model_returning("partial")),
                ),
                patch.object(
                    home.ask_graph,
                    "write_final_answer",
                    new=AsyncMock(
                        side_effect=lambda state, config: {
                            "final_answer": f"final for {state['question']}"
                        }
                    ),
                ),
                patch(
                    "open_notebook.graphs.home_chat.provision_langchain_model",
                    new=AsyncMock(return_value=_model_returning("Follow up?")),
                ),
            ):
                return await graph.ainvoke(
                    {"messages": [HumanMessage(content=question)], "question": question},
                    config=config,
                )

        first = await run_turn("top spender?", "analytics")
        assert first["analytics_answer"]["status"] == "ok"
        assert first["turn_metadata"][0]["analytics_answer"] is not None

        second = await run_turn("what is rag?", "knowledge")
        # The knowledge turn's metadata must NOT carry the analytics payload
        # checkpointed from the previous turn.
        assert second["turn_metadata"][1]["analytics_answer"] is None
        assert second["turn_metadata"][1]["suggestions"] == ["Follow up?"]
