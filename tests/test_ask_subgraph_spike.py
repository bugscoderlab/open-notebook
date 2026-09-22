"""Spike tests for wayfinder ticket #58: Ask graph as a home-chat subgraph.

Proves, against the throwaway composition in
``research/ask_subgraph_spike/spike_graph.py``:

(a) the composed graph runs end-to-end and outer-``messages`` history reaches
    the ask pipeline (rendered into ThreadState.chat_history);
(b) ``astream`` exposes token-level synthesis output — the exact recipe:
    ``astream(..., stream_mode=["updates", "messages"], subgraphs=True)``
    yields 3-tuples ``(namespace, mode, payload)`` where synthesis chunks have
    ``metadata["langgraph_node"] == "write_final_answer"``; with the default
    ``subgraphs=False`` the subgraph's LLM tokens do NOT stream at all (the
    only messages event is the outer remember node's merged channel write);
(c) clarify=True + a strategy carrying clarifications blocks: no searches;
(d) channel mapping gotcha: declaring ``answers`` in the outer schema makes
    operator.add accumulate across turns and leak into the next synthesis;
    leaving it subgraph-private keeps turns isolated;
(e) a checkpoint written by the OLD home_chat graph loads under the new
    composition.

Runnable: ``uv run --env-file .env pytest tests/test_ask_subgraph_spike.py``
"""

import json
from contextlib import contextmanager
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver

from open_notebook.graphs import ask as ask_graph
from open_notebook.graphs import home_chat
from research.ask_subgraph_spike import spike_graph


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


def _config(thread_id: str) -> RunnableConfig:
    return RunnableConfig(
        configurable={
            "thread_id": thread_id,
            "strategy_model": "strategy_model",
            "answer_model": "answer_model",
            "final_answer_model": "final_answer_model",
        }
    )


def _input(question: str, **extra) -> dict:
    return {
        "messages": [HumanMessage(content=question)],
        "notebook_ids": ["notebook:1"],
        **extra,
    }


@pytest.fixture
def patched_ask():
    """Patch the ask pipeline's externals; yields (record, vector_search)."""
    record: list = []
    search = AsyncMock(return_value=[{"id": "source:1", "content": "doc about rag"}])

    def _enter(strategy=None, partial=None, final=None):
        @contextmanager
        def _cm():
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
            ):
                yield

        return _cm()

    return record, search, _enter


class TestCompositionEndToEnd:
    @pytest.mark.asyncio
    async def test_two_turns_history_reaches_ask_pipeline(self, patched_ask):
        """(a) Composed graph runs end-to-end; prior turns render into
        ThreadState.chat_history and reach the strategy + synthesis stages."""
        record, search, enter = patched_ask
        graph = spike_graph.build_graph(checkpointer=MemorySaver())
        config = _config("history-thread")

        with (
            enter(),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            turn1 = await graph.ainvoke(_input("first question"), config=config)
            turn2 = await graph.ainvoke(_input("second question"), config=config)

        assert turn1["final_answer"] == "final answer text"
        assert turn2["final_answer"] == "final answer text"
        assert turn2["suggestions"] == ["Follow up?"]
        assert turn2["turn_metadata"] == [{"suggestions": ["Follow up?"]}] * 2
        # Memory wrapper accumulated both turns.
        human_turns = [m for m in turn2["messages"] if m.type == "human"]
        assert [m.content for m in human_turns] == ["first question", "second question"]
        ai_turns = [m for m in turn2["messages"] if m.type == "ai"]
        assert [m.content for m in ai_turns] == ["final answer text"] * 2
        # The ask pipeline actually searched (both turns).
        assert search.await_count == 2
        # (a) History crossed into the ask pipeline: turn 2's strategy stage
        # saw turn 1 rendered as chat history.
        strategy_prompts = [
            r["prompt"] for r in record if r["model"] == "strategy_model"
        ]
        assert "User: first question" in strategy_prompts[1]
        assert "Assistant: final answer text" in strategy_prompts[1]
        # Turn 1 had no history.
        assert "CONVERSATION SO FAR" not in strategy_prompts[0]
        assert "CONVERSATION SO FAR" in strategy_prompts[1]

    @pytest.mark.asyncio
    async def test_turn_isolation_private_answers_channel(self, patched_ask):
        """(d, positive branch) With ``answers`` subgraph-private, turn 2's
        synthesis sees ONLY its own partial answer."""
        record, _, enter = patched_ask
        graph = spike_graph.build_graph(checkpointer=MemorySaver())
        config = _config("isolation-thread")

        with (
            enter(),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            await graph.ainvoke(_input("first question"), config=config)
            await graph.ainvoke(_input("second question"), config=config)

        final_prompts = [
            r["prompt"] for r in record if r["model"] == "final_answer_model"
        ]
        assert "partial answer" in final_prompts[1]
        # The synthesis prompt renders the raw answers list — with private
        # channels it contains exactly this turn's single entry.
        assert final_prompts[1].count("partial answer") == 1

    @pytest.mark.asyncio
    async def test_shared_answers_channel_leaks_across_turns(self, patched_ask):
        """(d, gotcha) Declaring ``answers`` in the outer schema (operator.add
        on both sides) accumulates: turn 2's synthesis sees turn 1's partial
        answers too — the leak _ask_state_view's per-turn tagging prevents."""
        record, _, enter = patched_ask
        graph = spike_graph.build_graph(checkpointer=MemorySaver(), share_answers=True)
        config = _config("leak-thread")

        with (
            enter(),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            await graph.ainvoke(_input("first question"), config=config)
            await graph.ainvoke(_input("second question"), config=config)

        final_prompts = [
            r["prompt"] for r in record if r["model"] == "final_answer_model"
        ]
        assert final_prompts[1].count("partial answer") == 2

    @pytest.mark.asyncio
    async def test_empty_scope_bypasses_subgraph_without_model(self, patched_ask):
        """T5 survives composition: empty notebook_ids routes around the ask
        subgraph and answers honestly without any model call."""
        record, search, enter = patched_ask
        graph = spike_graph.build_graph(checkpointer=MemorySaver())
        config = _config("noscope-thread")

        with (
            enter(),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            result = await graph.ainvoke(
                {
                    "messages": [HumanMessage(content="anything?")],
                    "notebook_ids": [],
                },
                config=config,
            )

        assert result["final_answer"] == home_chat.NO_SCOPE_ANSWER
        assert result["messages"][-1].content == home_chat.NO_SCOPE_ANSWER
        search.assert_not_called()
        assert record == []  # no ask-stage provision call at all


class TestTokenStreaming:
    @pytest.mark.asyncio
    async def test_subgraphs_true_attributes_tokens_to_inner_node(self, patched_ask):
        """(b) Exact recipe: stream_mode=["updates", "messages"] with
        subgraphs=True. Synthesis tokens carry the INNER node name in
        metadata; inner node-level updates still stream."""
        record, _, enter = patched_ask
        graph = spike_graph.build_graph(checkpointer=MemorySaver())
        config = _config("stream-thread")

        events = []
        with (
            enter(),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            async for item in graph.astream(
                _input("stream me"),
                config=config,
                stream_mode=["updates", "messages"],
                subgraphs=True,
            ):
                events.append(item)

        message_events = [
            payload for _ns, mode, payload in events if mode == "messages"
        ]
        synthesis = [
            chunk.content
            for chunk, metadata in message_events
            if metadata["langgraph_node"] == "write_final_answer"
        ]
        # BaseChatModel appends a trailing empty chunk_position="last" chunk.
        assert synthesis == ["final ", "answer ", "text", ""]

        # Node-level updates still stream, from both levels.
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
    async def test_default_streaming_does_not_propagate_subgraph_tokens(
        self, patched_ask
    ):
        """(b, gotcha) With the DEFAULT subgraphs=False, the ask subgraph's
        LLM token chunks do NOT stream at all — the only messages event is
        the outer remember node's messages-channel write, delivered as one
        merged chunk. Per-stage token SSE therefore REQUIRES
        subgraphs=True (see test above)."""
        record, _, enter = patched_ask
        graph = spike_graph.build_graph(checkpointer=MemorySaver())
        config = _config("stream-flat-thread")

        events = []
        with (
            enter(),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            async for item in graph.astream(
                _input("stream me"),
                config=config,
                stream_mode=["updates", "messages"],
            ):
                events.append(item)

        message_events = [payload for mode, payload in events if mode == "messages"]
        inner_nodes = {
            metadata["langgraph_node"] for _chunk, metadata in message_events
        }
        assert "write_final_answer" not in inner_nodes
        assert "agent" not in inner_nodes
        assert "provide_answer" not in inner_nodes
        # Sole messages event: the merged AIMessage the remember node wrote
        # into the outer messages channel — no per-stage tokens.
        assert inner_nodes == {"remember"}
        assert [chunk.content for chunk, _m in message_events] == ["final answer text"]
        # Node-level updates still stream at the outer level.
        outer_updates = {
            next(iter(payload)) for mode, payload in events if mode == "updates"
        }
        assert {"prepare_ask", "remember", "suggest", "ask"} <= outer_updates


class TestClarifyBranch:
    @pytest.mark.asyncio
    async def test_clarify_blocks_searches(self, patched_ask):
        """(c) clarify=True + strategy carrying clarifications → blocking
        clarify output, no searches, no synthesis model call."""
        record, search, enter = patched_ask
        graph = spike_graph.build_graph(checkpointer=MemorySaver())
        config = _config("clarify-thread")

        with (
            enter(
                strategy=_strategy_json(
                    searches=[], clarifications=["Which timeframe?"]
                ),
            ),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            result = await graph.ainvoke(
                _input("how much?", clarify=True), config=config
            )

        search.assert_not_called()
        assert result["final_answer"] == (
            "Before I can answer this well, I need a bit more information:"
            "\n\n1. Which timeframe?"
        )
        assert result["messages"][-1].content == result["final_answer"]
        # Only the strategy model was called inside the ask subgraph.
        assert [r["model"] for r in record] == ["strategy_model"]


class TestOldCheckpointCompatibility:
    @pytest.mark.asyncio
    async def test_checkpoint_written_by_old_home_chat_loads(self, patched_ask):
        """(e) A checkpoint written by the OLD home_chat state schema (which
        includes its tagged answers/strategy/suggestions channels) loads under
        the new composition: turn 2 sees turn 1 as chat history."""
        record, _, enter = patched_ask
        saver = MemorySaver()

        # Turn 1 via the OLD graph, patched like the existing home-chat tests.
        old_graph = home_chat._home_state.compile(checkpointer=saver)
        with (
            patch.object(
                home_chat.ask_graph,
                "call_model_with_messages",
                new=AsyncMock(
                    return_value={
                        "strategy": ask_graph.Strategy(
                            reasoning="r",
                            searches=[ask_graph.Search(term="rag", instructions="x")],
                        )
                    }
                ),
            ),
            patch(
                "open_notebook.graphs.ask.vector_search",
                new=AsyncMock(return_value=[{"id": "source:1", "content": "x"}]),
            ),
            patch(
                "open_notebook.graphs.ask.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model("partial answer")),
            ),
            patch.object(
                home_chat.ask_graph,
                "write_final_answer",
                new=AsyncMock(
                    side_effect=lambda state, config: {"final_answer": "old answer"}
                ),
            ),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            await old_graph.ainvoke(  # type: ignore[call-overload]
                {
                    "messages": [HumanMessage(content="first question")],
                    "question": "first question",
                    "notebook_ids": ["notebook:1"],
                },
                config=_config("compat-thread"),
            )

        # Turn 2 via the NEW composed graph on the same saver + thread.
        new_graph = spike_graph.build_graph(checkpointer=saver)
        with (
            enter(),
            patch(
                "open_notebook.graphs.home_chat.provision_langchain_model",
                new=AsyncMock(return_value=_suggest_model()),
            ),
        ):
            result = await new_graph.ainvoke(
                _input("second question"),
                config=_config("compat-thread"),
            )

        assert result["final_answer"] == "final answer text"
        human_turns = [m for m in result["messages"] if m.type == "human"]
        assert [m.content for m in human_turns] == ["first question", "second question"]
        strategy_prompts = [
            r["prompt"] for r in record if r["model"] == "strategy_model"
        ]
        assert "User: first question" in strategy_prompts[0]
        assert "Assistant: old answer" in strategy_prompts[0]
