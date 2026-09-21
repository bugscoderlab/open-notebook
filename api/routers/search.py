import json
from typing import AsyncGenerator, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger

from api.access import CurrentUser, effective_notebook_scope, get_current_user
from api.models import AskRequest, AskResponse, SearchRequest, SearchResponse
from open_notebook.ai.models import Model, model_manager
from open_notebook.domain.notebook import (
    text_search,
    vector_search,
)
from open_notebook.exceptions import (
    DatabaseOperationError,
    InvalidInputError,
    OpenNotebookError,
)
from open_notebook.graphs.ask import graph as ask_graph

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
async def search_knowledge_base(
    search_request: SearchRequest, user: CurrentUser = Depends(get_current_user)
):
    """Search the knowledge base using text or vector search.

    T5: the server's authorized scope is authoritative — the effective scope
    is permitted ∩ requested, and an empty effective scope short-circuits to
    "no results" (the SurrealQL search functions treat an empty array as
    unscoped, so it must never reach them).
    """
    try:
        notebook_ids = await effective_notebook_scope(
            user, search_request.scope_notebook_ids
        )
        if not notebook_ids:
            return SearchResponse(
                results=[], total_count=0, search_type=search_request.type
            )

        if search_request.type == "vector":
            # Check if embedding model is available for vector search
            if not await model_manager.get_embedding_model():
                raise HTTPException(
                    status_code=400,
                    detail="Vector search requires an embedding model. Please configure one in the Models section.",
                )

            results = await vector_search(
                keyword=search_request.query,
                results=search_request.limit,
                source=search_request.search_sources,
                note=search_request.search_notes,
                minimum_score=search_request.minimum_score,
                notebook_ids=notebook_ids,
            )
        else:
            # Text search
            results = await text_search(
                keyword=search_request.query,
                results=search_request.limit,
                source=search_request.search_sources,
                note=search_request.search_notes,
                notebook_ids=notebook_ids,
            )

        return SearchResponse(
            results=results or [],
            total_count=len(results) if results else 0,
            search_type=search_request.type,
        )

    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except DatabaseOperationError as e:
        logger.error(f"Database error during search: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during search: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


def _ask_graph_input(question: str, notebook_ids: List[str], clarify: bool) -> dict:
    """Build the ask graph input.

    clarify opts the request into the clarification gate (#52): an
    underspecified question stops at clarifying questions instead of
    searching. Default off = legacy behavior.
    """
    return dict(question=question, notebook_ids=notebook_ids, clarify=clarify)


def _final_answer_from_chunk(chunk: dict) -> str | None:
    """Pull the final answer out of whichever terminal node emitted it.

    The clarify node and the synthesis node both terminate the graph with a
    final_answer — SSE consumers don't care which.
    """
    if "clarify" in chunk:
        return chunk["clarify"]["final_answer"]
    if "write_final_answer" in chunk:
        return chunk["write_final_answer"]["final_answer"]
    return None


async def stream_ask_response(
    question: str,
    strategy_model: Model,
    answer_model: Model,
    final_answer_model: Model,
    notebook_ids: List[str],
    clarify: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream the ask response as Server-Sent Events."""
    try:
        final_answer = None

        # LangGraph accepts a partial state dict at runtime, but its typed
        # overloads require the full state type (langgraph typing limitation).
        async for chunk in ask_graph.astream(  # type: ignore[call-overload]
            input=_ask_graph_input(question, notebook_ids, clarify),
            config=dict(
                configurable=dict(
                    strategy_model=strategy_model.id,
                    answer_model=answer_model.id,
                    final_answer_model=final_answer_model.id,
                )
            ),
            stream_mode="updates",
        ):
            if "agent" in chunk:
                strategy_data = {
                    "type": "strategy",
                    "reasoning": chunk["agent"]["strategy"].reasoning,
                    "searches": [
                        {"term": search.term, "instructions": search.instructions}
                        for search in chunk["agent"]["strategy"].searches
                    ],
                }
                yield f"data: {json.dumps(strategy_data)}\n\n"

            elif "provide_answer" in chunk:
                for answer in chunk["provide_answer"]["answers"]:
                    answer_data = {"type": "answer", "content": answer}
                    yield f"data: {json.dumps(answer_data)}\n\n"

            else:
                answer = _final_answer_from_chunk(chunk)
                if answer is not None:
                    final_answer = answer
                    final_data = {"type": "final_answer", "content": answer}
                    yield f"data: {json.dumps(final_data)}\n\n"

        # Send completion signal
        completion_data = {"type": "complete", "final_answer": final_answer}
        yield f"data: {json.dumps(completion_data)}\n\n"

    except Exception as e:
        from open_notebook.utils.error_classifier import classify_error

        _, user_message = classify_error(e)
        logger.error(f"Error in ask streaming: {str(e)}")
        error_data = {"type": "error", "message": user_message}
        yield f"data: {json.dumps(error_data)}\n\n"


async def _empty_ask_stream() -> AsyncGenerator[str, None]:
    """Honest no-data answer when the caller's permitted scope is empty (T5).

    Emits the same SSE event shapes as a real run so clients don't special-case it.
    """
    answer = (
        "I don't have access to any notebooks that could answer this question."
    )
    for payload in (
        {"type": "answer", "content": answer},
        {"type": "final_answer", "content": answer},
        {"type": "complete", "final_answer": answer},
    ):
        yield f"data: {json.dumps(payload)}\n\n"


@router.post("/search/ask")
async def ask_knowledge_base(
    ask_request: AskRequest, user: CurrentUser = Depends(get_current_user)
):
    """Ask the knowledge base a question using AI models."""
    try:
        # Cheapest check first: a malformed or unknown scope fails before any
        # model lookup or embedding check can mask it. T5: the effective scope
        # is permitted ∩ requested — an empty permitted set yields an honest
        # "no data" answer stream, never a whole-KB search.
        notebook_ids = await effective_notebook_scope(
            user, ask_request.scope_notebook_ids
        )
        if not notebook_ids:
            return StreamingResponse(
                _empty_ask_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )

        # Validate models exist
        strategy_model = await Model.get(ask_request.strategy_model)
        answer_model = await Model.get(ask_request.answer_model)
        final_answer_model = await Model.get(ask_request.final_answer_model)

        if not strategy_model:
            raise HTTPException(
                status_code=400,
                detail=f"Strategy model {ask_request.strategy_model} not found",
            )
        if not answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Answer model {ask_request.answer_model} not found",
            )
        if not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Final answer model {ask_request.final_answer_model} not found",
            )

        # Check if embedding model is available
        if not await model_manager.get_embedding_model():
            raise HTTPException(
                status_code=400,
                detail="Ask feature requires an embedding model. Please configure one in the Models section.",
            )

        # For streaming response
        return StreamingResponse(
            stream_ask_response(
                ask_request.question,
                strategy_model,
                answer_model,
                final_answer_model,
                notebook_ids,
                ask_request.clarify,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error in ask endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ask operation failed: {str(e)}")


@router.post("/search/ask/simple", response_model=AskResponse)
async def ask_knowledge_base_simple(
    ask_request: AskRequest, user: CurrentUser = Depends(get_current_user)
):
    """Ask the knowledge base a question and return a simple response (non-streaming)."""
    try:
        # Cheapest check first: a malformed or unknown scope fails before any
        # model lookup or embedding check can mask it. T5: permitted ∩
        # requested; empty permitted → honest "no data" instead of a whole-KB
        # search or an invented answer.
        notebook_ids = await effective_notebook_scope(
            user, ask_request.scope_notebook_ids
        )
        if not notebook_ids:
            return AskResponse(
                answer=(
                    "I don't have access to any notebooks that could answer "
                    "this question."
                ),
                question=ask_request.question,
            )

        # Validate models exist
        strategy_model = await Model.get(ask_request.strategy_model)
        answer_model = await Model.get(ask_request.answer_model)
        final_answer_model = await Model.get(ask_request.final_answer_model)

        if not strategy_model:
            raise HTTPException(
                status_code=400,
                detail=f"Strategy model {ask_request.strategy_model} not found",
            )
        if not answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Answer model {ask_request.answer_model} not found",
            )
        if not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail=f"Final answer model {ask_request.final_answer_model} not found",
            )

        # Check if embedding model is available
        if not await model_manager.get_embedding_model():
            raise HTTPException(
                status_code=400,
                detail="Ask feature requires an embedding model. Please configure one in the Models section.",
            )

        # Run the ask graph and get final result
        final_answer = None
        # LangGraph accepts a partial state dict at runtime, but its typed
        # overloads require the full state type (langgraph typing limitation).
        async for chunk in ask_graph.astream(  # type: ignore[call-overload]
            input=_ask_graph_input(
                ask_request.question, notebook_ids, ask_request.clarify
            ),
            config=dict(
                configurable=dict(
                    strategy_model=strategy_model.id,
                    answer_model=answer_model.id,
                    final_answer_model=final_answer_model.id,
                )
            ),
            stream_mode="updates",
        ):
            answer = _final_answer_from_chunk(chunk)
            if answer is not None:
                final_answer = answer

        if not final_answer:
            raise HTTPException(status_code=500, detail="No answer generated")

        return AskResponse(answer=final_answer, question=ask_request.question)

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error in ask simple endpoint: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Ask operation failed: {str(e)}")
