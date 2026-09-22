"""Unit tests for the home chat graph and the shared turn generator
(open_notebook.graphs.home_chat).

Home chat delegates to the Ask pipeline wholesale: the compiled Ask graph
runs as a subgraph inside the memory wrapper (wayfinder map #56, spike #58).
Covers:

- composition: history reaches the ask pipeline, turns stay isolated, the
  empty scope answers honestly without a model call;
- the token-streaming recipe: ``stream_mode=["updates", "messages"],
  subgraphs=True`` attributes synthesis tokens to the inner node — with the
  default ``subgraphs=False`` they do not stream at all (the gotcha);
- the clarify gate (chat opts in): blocking clarifications, zero searches;
- old-schema checkpoint compatibility (a checkpoint carrying the removed
  channels loads under the new composition);
- the shared turn generator's external event sequence, including the
  typed-error-never-bare-EOF contract (#57);
- the preserved helpers (history formatting, suggestion parsing).
"""

import json
from contextlib import contextmanager
from typing import List, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.base import Checkpoint
from langgraph.checkpoint.memory import MemorySaver

from open_notebook.graphs import home_chat as home


class _FakeStreamingChatModel(BaseChatModel):
    """Scripted LLM that emits ``chunks`` through the real LangChain streaming
    machinery, so LangGraph's stream_mode="messages" can observe tokens even
    though the ask nodes call ``ainvoke`` (BaseChatModel routes ainvoke
    through _astream whenever a _StreamingCallbackHandler is attached)."""

    chunks: List[str]

    @property
    def _llm_type(self) -> str:
        return "fake-streaming"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content="".join(self.chunks)))
            ]
        )

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for chunk in self.chunks:
            yield ChatGenerationChunk(message=AIMessageChunk(content=chunk))


def _provision_fake(record, strategy, partial, final):
    """Fake ``provision_langchain_model`` dispatching per model key, recording
    every call's system prompt for assertions."""

    async def fake(system_prompt, model, context_type, **kwargs):
        record.append({"model": model, "prompt": system_prompt})
        if kwargs.get("structured"):
            return _FakeStreamingChatModel(chunks=[strategy])
        if model == "answer_model":
            return _FakeStreamingChatModel(chunks=partial)
        if model == "final_answer_model":
            return _FakeStreamingChatModel(chunks=final)
        raise AssertionError(f"unexpected provision call for {model!r}")

    return fake


def _suggest_model(content: str = "1. Follow up?") -> MagicMock:
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return model


def _strategy_json(searches=None, clarifications=None) -> str:
    return json.dumps(
        {
            "reasoning": "look things up",
            "searches": searches or [{"term": "rag", "instructions": "extract"}],
            "clarifications": clarifications or [],
        }
    )


def _config(thread_id: str) -> dict:
    return {
        "configurable": {
            "thread_id": thread_id,
            "strategy_model": "strategy_model",
            "answer_model": "answer_model",
            "final_answer_model": "final_answer_model",
        }
    }


def _input(question: str, **extra) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "notebook_ids": ["notebook:1"],
        **extra,
    }


@contextmanager
def _patched_pipeline(record, search, strategy=None, partial=None, final=None):
    with (
        patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=_provision_fake(
                record,
                strategy or _strategy_json(),
                partial or ["partial ", "answer"],
                final or ["final ", "answer ", "text"],
            ),
        ),
        patch("open_notebook.graphs.ask.vector_search", new=search),
        patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_suggest_model()),
        ),
    ):
        yield


@pytest.fixture
def patched_pipeline():
    """Patch the ask pipeline's externals; yields (record, vector_search)."""
    record: list = []
    search = AsyncMock(return_value=[{"id": "source:1", "content": "doc about rag"}])
    return record, search


def _build_memory_graph():
    return home._build_graph(checkpointer=MemorySaver())


class TestCompositionEndToEnd:
    @pytest.mark.asyncio
    async def test_two_turns_history_reaches_ask_pipeline(self, patched_pipeline):
        """The composed graph runs end-to-end; prior turns render into
        ThreadState.chat_history and reach the strategy + synthesis stages."""
        record, search = patched_pipeline
        graph = _build_memory_graph()

        with _patched_pipeline(record, search):
            turn1 = await graph.ainvoke(_input("first question"), config=_config("history-thread"))
            turn2 = await graph.ainvoke(_input("second question"), config=_config("history-thread"))

        assert turn1["final_answer"] == "final answer text"
        assert turn2["final_answer"] == "final answer text"
        assert turn2["suggestions"] == ["Follow up?"]
        assert turn2["turn_metadata"] == [{"suggestions": ["Follow up?"]}] * 2
        human_turns = [m for m in turn2["messages"] if m.type == "human"]
        assert [m.content for m in human_turns] == ["first question", "second question"]
        ai_turns = [m for m in turn2["messages"] if m.type == "ai"]
        assert [m.content for m in ai_turns] == ["final answer text"] * 2
        assert search.await_count == 2
        strategy_prompts = [r["prompt"] for r in record if r["model"] == "strategy_model"]
        assert "User: first question" in strategy_prompts[1]
        assert "Assistant: final answer text" in strategy_prompts[1]
        assert "CONVERSATION SO FAR" not in strategy_prompts[0]
        assert "CONVERSATION SO FAR" in strategy_prompts[1]

    @pytest.mark.asyncio
    async def test_turn_isolation_with_private_answers_channel(self, patched_pipeline):
        """With ``answers`` subgraph-private, turn 2's synthesis sees ONLY its
        own partial answer — the isolation the removed answer-tagging used to
        provide."""
        record, search = patched_pipeline
        graph = _build_memory_graph()

        with _patched_pipeline(record, search):
            await graph.ainvoke(_input("first question"), config=_config("isolation-thread"))
            await graph.ainvoke(_input("second question"), config=_config("isolation-thread"))

        final_prompts = [r["prompt"] for r in record if r["model"] == "final_answer_model"]
        assert "partial answer" in final_prompts[1]
        assert final_prompts[1].count("partial answer") == 1

    @pytest.mark.asyncio
    async def test_empty_scope_bypasses_subgraph_without_model(self, patched_pipeline):
        """T5: empty notebook_ids routes around the ask subgraph and answers
        honestly without any model call."""
        record, search = patched_pipeline
        graph = _build_memory_graph()

        with _patched_pipeline(record, search):
            result = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content="anything?")],
                    "notebook_ids": [],
                },
                config=_config("noscope-thread"),
            )

        assert result["final_answer"] == home.NO_SCOPE_ANSWER
        assert result["messages"][-1].content == home.NO_SCOPE_ANSWER
        search.assert_not_called()
        assert record == []  # no ask-stage provision call at all


class TestTokenStreaming:
    @pytest.mark.asyncio
    async def test_subgraphs_true_attributes_tokens_to_inner_node(self, patched_pipeline):
        """The exact recipe consumers must use: stream_mode=["updates",
        "messages"] with subgraphs=True. Synthesis tokens carry the INNER
        node name in metadata; inner node-level updates still stream."""
        record, search = patched_pipeline
        graph = _build_memory_graph()

        events = []
        with _patched_pipeline(record, search):
            async for item in graph.astream(
                _input("stream me"),
                config=_config("stream-thread"),
                stream_mode=["updates", "messages"],
                subgraphs=True,
            ):
                events.append(item)

        message_events = [payload for _ns, mode, payload in events if mode == "messages"]
        synthesis = [
            chunk.content
            for chunk, metadata in message_events
            if metadata["langgraph_node"] == "write_final_answer"
        ]
        # BaseChatModel appends a trailing empty chunk_position="last" chunk.
        assert synthesis == ["final ", "answer ", "text", ""]

        inner_updates = {
            next(iter(payload))
            for ns, mode, payload in events
            if mode == "updates" and ns
        }
        assert {"agent", "provide_answer", "write_final_answer"} <= inner_updates
        outer_updates = {
            next(iter(payload))
            for ns, mode, payload in events
            if mode == "updates" and not ns
        }
        assert {"prepare_ask", "remember", "suggest", "ask"} <= outer_updates

    @pytest.mark.asyncio
    async def test_default_streaming_does_not_propagate_subgraph_tokens(self, patched_pipeline):
        """Gotcha guard: with the DEFAULT subgraphs=False, the ask subgraph's
        LLM token chunks do NOT stream at all. Any future refactor of
        stream_home_turn must keep subgraphs=True or this test fires."""
        record, search = patched_pipeline
        graph = _build_memory_graph()

        events = []
        with _patched_pipeline(record, search):
            async for item in graph.astream(
                _input("stream me"),
                config=_config("stream-flat-thread"),
                stream_mode=["updates", "messages"],
            ):
                events.append(item)

        message_events = [payload for mode, payload in events if mode == "messages"]
        inner_nodes = {metadata["langgraph_node"] for _chunk, metadata in message_events}
        assert "write_final_answer" not in inner_nodes
        assert "agent" not in inner_nodes
        assert "provide_answer" not in inner_nodes
        assert inner_nodes == {"remember"}


class TestClarifyBranch:
    @pytest.mark.asyncio
    async def test_clarify_blocks_searches(self, patched_pipeline):
        """clarify=True + strategy carrying clarifications → blocking clarify
        output, no searches, no synthesis model call. Chat always opts in
        (the turn generator sets it; direct invocations pass it explicitly)."""
        record, search = patched_pipeline
        graph = _build_memory_graph()

        with _patched_pipeline(
            record,
            search,
            strategy=_strategy_json(searches=[], clarifications=["Which timeframe?"]),
        ):
            result = await graph.ainvoke(
                _input("how much?", clarify=True), config=_config("clarify-thread")
            )

        search.assert_not_called()
        assert result["final_answer"] == (
            "Before I can answer this well, I need a bit more information:"
            "\n\n1. Which timeframe?"
        )
        assert result["messages"][-1].content == result["final_answer"]
        assert [r["model"] for r in record] == ["strategy_model"]


class TestOldCheckpointCompatibility:
    @pytest.mark.asyncio
    async def test_old_schema_checkpoint_loads_under_new_composition(self, patched_pipeline):
        """A checkpoint carrying the REMOVED channels of the old home-chat
        schema (strategy/answers/question) loads under the new composition:
        the junk keys are ignored and the next turn still sees the history."""
        record, search = patched_pipeline
        saver = MemorySaver()
        graph = home._build_graph(checkpointer=saver)

        with _patched_pipeline(record, search):
            await graph.ainvoke(
                _input("first question"), config=_config("fixture-thread")
            )
        record.clear()  # only the reloaded thread's prompts matter below

        # Re-write the checkpoint under a new thread, adding old-schema keys.
        from langchain_core.runnables import RunnableConfig

        tuple_ = saver.get_tuple(
            RunnableConfig(
                configurable={"thread_id": "fixture-thread", "checkpoint_ns": ""}
            )
        )
        assert tuple_ is not None
        old_style = cast(Checkpoint, dict(tuple_.checkpoint))
        old_style["channel_values"] = {
            **tuple_.checkpoint["channel_values"],
            "question": "first question",
            "strategy": {"reasoning": "old", "searches": []},
            "answers": [{"question": "first question", "content": "stale partial"}],
        }
        saver.put(
            RunnableConfig(
                configurable={"thread_id": "old-schema-thread", "checkpoint_ns": ""}
            ),
            old_style,
            tuple_.metadata,
            tuple_.checkpoint["channel_versions"],
        )

        with _patched_pipeline(record, search):
            result = await graph.ainvoke(
                _input("second question"), config=_config("old-schema-thread")
            )

        assert result["final_answer"] == "final answer text"
        human_turns = [m for m in result["messages"] if m.type == "human"]
        assert [m.content for m in human_turns] == ["first question", "second question"]
        strategy_prompts = [r["prompt"] for r in record if r["model"] == "strategy_model"]
        assert "User: first question" in strategy_prompts[0]
        # The stale old-schema partial must NOT leak into the new synthesis.
        final_prompts = [r["prompt"] for r in record if r["model"] == "final_answer_model"]
        assert "stale partial" not in final_prompts[0]


class TestStreamHomeTurn:
    """The shared generator's external contract: event sequence and the
    typed-error-never-bare-EOF rule (#57)."""

    @pytest.mark.asyncio
    async def test_normal_turn_event_sequence(self, patched_pipeline):
        record, search = patched_pipeline
        graph = _build_memory_graph()

        with (
            patch.object(home, "get_home_chat_graph", new=AsyncMock(return_value=graph)),
            _patched_pipeline(record, search),
        ):
            events = [
                event
                async for event in home.stream_home_turn(
                    session_id="gen-thread",
                    question="hello?",
                    notebook_ids=["notebook:1"],
                    strategy_model="strategy_model",
                    answer_model="answer_model",
                    final_answer_model="final_answer_model",
                )
            ]

        kinds = [e["type"] for e in events]
        assert kinds == [
            "answer_delta",
            "answer_delta",
            "answer_delta",
            "final_answer",
            "suggestions",
            "complete",
        ]
        assert "".join(e["content"] for e in events if e["type"] == "answer_delta") == (
            "final answer text"
        )
        assert events[-2]["suggestions"] == ["Follow up?"]
        assert events[-1]["final_answer"] == "final answer text"

    @pytest.mark.asyncio
    async def test_model_failure_yields_error_event_not_exception(self, patched_pipeline):
        record, search = patched_pipeline
        graph = _build_memory_graph()

        async def _boom(*args, **kwargs):
            raise RuntimeError("model exploded")

        with (
            patch.object(home, "get_home_chat_graph", new=AsyncMock(return_value=graph)),
            patch(
                "open_notebook.graphs.ask.provision_langchain_model",
                new=_boom,
            ),
            patch("open_notebook.graphs.ask.vector_search", new=search),
        ):
            events = [
                event
                async for event in home.stream_home_turn(
                    session_id="gen-error-thread",
                    question="hello?",
                    notebook_ids=["notebook:1"],
                    strategy_model="s",
                    answer_model="a",
                    final_answer_model="f",
                )
            ]

        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert events[0]["message"]

    @pytest.mark.asyncio
    async def test_empty_scope_final_answer_without_deltas(self, patched_pipeline):
        record, search = patched_pipeline
        graph = _build_memory_graph()

        with (
            patch.object(home, "get_home_chat_graph", new=AsyncMock(return_value=graph)),
            _patched_pipeline(record, search),
        ):
            events = [
                event
                async for event in home.stream_home_turn(
                    session_id="gen-noscope-thread",
                    question="anything?",
                    notebook_ids=[],
                    strategy_model="s",
                    answer_model="a",
                    final_answer_model="f",
                )
            ]

        # The suggest node runs on every path — clarify/empty answers also get
        # follow-up suggestions.
        assert [e["type"] for e in events] == ["final_answer", "suggestions", "complete"]
        assert events[0]["content"] == home.NO_SCOPE_ANSWER

    @pytest.mark.asyncio
    async def test_clarify_final_answer_without_deltas(self, patched_pipeline):
        record, search = patched_pipeline
        graph = _build_memory_graph()

        with (
            patch.object(home, "get_home_chat_graph", new=AsyncMock(return_value=graph)),
            _patched_pipeline(
                record,
                search,
                strategy=_strategy_json(searches=[], clarifications=["Which timeframe?"]),
            ),
        ):
            events = [
                event
                async for event in home.stream_home_turn(
                    session_id="gen-clarify-thread",
                    question="how much?",
                    notebook_ids=["notebook:1"],
                    strategy_model="s",
                    answer_model="a",
                    final_answer_model="f",
                )
            ]

        assert [e["type"] for e in events] == ["final_answer", "suggestions", "complete"]
        assert "Which timeframe?" in events[0]["content"]


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
        assert "q4 (older)" not in history
        assert "q6 (older)" in history
        assert history.count("User:") == home.HISTORY_TURNS

    def test_empty_when_single_message(self):
        state = cast(home.HomeChatState, {"messages": [HumanMessage(content="only question")]})
        assert home._format_chat_history(state) == ""


class TestSuggestFollowups:
    @pytest.mark.asyncio
    async def test_parses_numbered_and_plain_lines(self):
        state = cast(home.HomeChatState, {"question": "q", "final_answer": "answer"})
        raw = "1. What about October?\n- Any top services?\nno prose\nAlso relevant?"
        with patch(
            "open_notebook.graphs.home_chat.provision_langchain_model",
            new=AsyncMock(return_value=_suggest_model(raw)),
        ):
            result = await home.suggest_followups(state, {"configurable": {}})
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
            result = await home.suggest_followups(state, {"configurable": {}})
        assert result["suggestions"] == []
        assert result["turn_metadata"] == [{"suggestions": []}]
