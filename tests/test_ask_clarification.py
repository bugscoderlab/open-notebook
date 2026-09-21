"""
Unit tests for the Ask clarification gate (open_notebook.graphs.ask).

The gate is opt-in via the `clarify` thread flag (only the standalone Ask
endpoint sets it). When the strategy model returns clarifying questions, the
graph short-circuits to a deterministic clarify node — no vector search, no
answer-model calls — and the questions become the final answer (blocking).
Home chat reuses the strategy node without the flag; its behavior must be
provably unchanged.
"""

import json
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.runnables import RunnableConfig

from open_notebook.exceptions import ExternalServiceError
from open_notebook.graphs.ask import (
    MAX_CLARIFICATIONS,
    Search,
    Strategy,
    ThreadState,
    call_model_with_messages,
    clarify,
    graph,
    trigger_queries,
)

EMPTY_CONFIG = cast(RunnableConfig, {"configurable": {}})


def _model_returning(content: str) -> MagicMock:
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=MagicMock(content=content))
    return model


def _strategy_json(
    terms: list[str], clarifications: list[str] | None = None
) -> str:
    return json.dumps(
        {
            "reasoning": "look things up",
            "searches": [{"term": t, "instructions": "extract"} for t in terms],
            "clarifications": clarifications or [],
        }
    )


class TestClarificationGate:
    @pytest.mark.asyncio
    async def test_clarifications_clear_searches_when_gate_on(self):
        """Blocking: clarifications present means searches never run."""
        state = cast(ThreadState, {"question": "q", "clarify": True})
        with patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(
                return_value=_model_returning(
                    _strategy_json(
                        ["ladder"], ["How tall are you?", "Indoor or outdoor?"]
                    )
                )
            ),
        ):
            result = await call_model_with_messages(state, EMPTY_CONFIG)
        strategy = result["strategy"]
        assert strategy.clarifications == ["How tall are you?", "Indoor or outdoor?"]
        assert strategy.searches == []

    @pytest.mark.asyncio
    async def test_clarifications_capped_at_three(self):
        state = cast(ThreadState, {"question": "q", "clarify": True})
        questions = [f"q{i}" for i in range(5)]
        with patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(
                return_value=_model_returning(
                    _strategy_json([], questions)
                )
            ),
        ):
            result = await call_model_with_messages(state, EMPTY_CONFIG)
        assert len(result["strategy"].clarifications) == MAX_CLARIFICATIONS

    @pytest.mark.asyncio
    async def test_blank_clarifications_dropped(self):
        state = cast(ThreadState, {"question": "q", "clarify": True})
        with patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(
                return_value=_model_returning(
                    _strategy_json(["rag"], ["", "  ", "real question"])
                )
            ),
        ):
            result = await call_model_with_messages(state, EMPTY_CONFIG)
        # Blank entries dropped, real question kept — and because a real
        # clarification exists, the search is still blocked.
        assert result["strategy"].clarifications == ["real question"]
        assert result["strategy"].searches == []

    @pytest.mark.asyncio
    async def test_all_blank_clarifications_raise(self):
        state = cast(ThreadState, {"question": "q", "clarify": True})
        with patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(
                return_value=_model_returning(_strategy_json([], ["", "  "]))
            ),
        ):
            with pytest.raises(ExternalServiceError, match="no search terms"):
                await call_model_with_messages(state, EMPTY_CONFIG)

    @pytest.mark.asyncio
    async def test_gate_off_strips_clarifications(self):
        """Home-chat regression guard: without the flag, clarifications from
        the model are stripped and the normal pipeline runs."""
        state = cast(ThreadState, {"question": "q"})
        with patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(
                return_value=_model_returning(
                    _strategy_json(["rag"], ["How tall are you?"])
                )
            ),
        ):
            result = await call_model_with_messages(state, EMPTY_CONFIG)
        assert result["strategy"].clarifications == []
        assert [s.term for s in result["strategy"].searches] == ["rag"]

    @pytest.mark.asyncio
    async def test_gate_off_empty_strategy_still_raises(self):
        state = cast(ThreadState, {"question": "q"})
        with patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(return_value=_model_returning(_strategy_json([]))),
        ):
            with pytest.raises(ExternalServiceError):
                await call_model_with_messages(state, EMPTY_CONFIG)


class TestTriggerQueriesRouting:
    @pytest.mark.asyncio
    async def test_clarifications_route_to_clarify_node(self):
        state = cast(
            ThreadState,
            {
                "question": "q",
                "strategy": Strategy(
                    reasoning="r",
                    searches=[Search(term="one", instructions="x")],
                    clarifications=["How tall are you?"],
                ),
            },
        )
        sends = await trigger_queries(state, EMPTY_CONFIG)
        assert len(sends) == 1
        assert sends[0].node == "clarify"
        assert sends[0].arg["clarifications"] == ["How tall are you?"]

    @pytest.mark.asyncio
    async def test_searches_route_to_provide_answer(self):
        state = cast(
            ThreadState,
            {
                "question": "q",
                "strategy": Strategy(
                    reasoning="r",
                    searches=[Search(term="one", instructions="x")],
                ),
            },
        )
        sends = await trigger_queries(state, EMPTY_CONFIG)
        assert [s.node for s in sends] == ["provide_answer"]


class TestClarifyNode:
    @pytest.mark.asyncio
    async def test_renders_numbered_questions_without_model_call(self):
        state = {
            "question": "q",
            "clarifications": ["How tall are you?", "Indoor or outdoor?"],
        }
        with patch(
            "open_notebook.graphs.ask.provision_langchain_model",
            new=AsyncMock(side_effect=AssertionError("clarify must not call a model")),
        ):
            result = await clarify(state, EMPTY_CONFIG)  # type: ignore[arg-type]
        final_answer = result["final_answer"]
        assert "1. How tall are you?" in final_answer
        assert "2. Indoor or outdoor?" in final_answer


class TestGraphShortCircuit:
    @pytest.mark.asyncio
    async def test_underspecified_question_skips_search_entirely(self):
        """End to end through the compiled graph: a clarifying strategy
        produces the questions as the final answer with zero vector searches
        and exactly one model call (the strategy stage)."""
        searched: list = []

        async def _record_search(*args, **kwargs):
            searched.append(True)
            return []

        with (
            patch(
                "open_notebook.graphs.ask.vector_search",
                new=AsyncMock(side_effect=_record_search),
            ),
            patch(
                "open_notebook.graphs.ask.provision_langchain_model",
                new=AsyncMock(
                    return_value=_model_returning(
                        _strategy_json(
                            ["ladder"], ["How tall are you?", "Indoor or outdoor?"]
                        )
                    )
                ),
            ) as provision,
        ):
            result = await graph.ainvoke(
                input=cast(  # type: ignore[arg-type]
                    ThreadState,
                    {
                        "question": "my ceiling is 2.5m, what ladder is suitable?",
                        "notebook_ids": ["notebook:a"],
                        "clarify": True,
                    },
                ),
                config=EMPTY_CONFIG,
            )
        assert searched == []
        assert provision.await_count == 1
        final_answer = result["final_answer"]
        assert "1. How tall are you?" in final_answer
        assert "2. Indoor or outdoor?" in final_answer
