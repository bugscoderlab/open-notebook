"""Unified Home ask chat router.

Session CRUD (per-user, owned via ``home_chat_session.user_id``) plus one SSE
endpoint that streams a full assistant turn: auto-routing (knowledge vs
analytics), the staged knowledge answer (strategy → per-search answers →
final answer) or the analytics payload, and follow-up suggestions.

Conversation memory is server-side: the home chat graph is checkpointed
(SqliteSaver) keyed by the session id, so messages, analytics payloads and
suggestions survive reloads and are shared across devices.
"""

import json
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from api.access import CurrentUser, effective_notebook_scope, get_current_user
from api.routers._chat_shared import (
    ChatMessage,
    SuccessResponse,
    extract_chat_messages,
    normalize_record_id,
)
from open_notebook.ai.models import DefaultModels
from open_notebook.database.repository import repo_query
from open_notebook.domain.home_chat import HomeChatSession
from open_notebook.exceptions import NotFoundError, OpenNotebookError
from open_notebook.graphs.home_chat import get_home_chat_graph
from open_notebook.utils.error_classifier import classify_error

router = APIRouter()

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class CreateHomeChatSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )


class UpdateHomeChatSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class HomeChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )


class HomeChatSessionWithMessagesResponse(HomeChatSessionResponse):
    messages: List[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )
    # One entry per AI message, in order: analytics payload + suggestions.
    turns: List[Dict[str, Any]] = Field(default_factory=list)


class SendHomeMessageRequest(BaseModel):
    message: str = Field(..., description="User message content")
    notebook_ids: Optional[List[str]] = Field(
        None, description="Optional notebook scope for the knowledge pipeline"
    )
    include_refunds: bool = Field(
        False, description="Include refunds in analytics answers"
    )
    strategy_model: Optional[str] = Field(None, description="Strategy model ID")
    answer_model: Optional[str] = Field(None, description="Answer model ID")
    final_answer_model: Optional[str] = Field(
        None, description="Final answer model ID"
    )
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )


async def _get_owned_session(session_id: str, user: CurrentUser) -> HomeChatSession:
    """Fetch a home chat session owned by the caller (T5: 404 otherwise)."""
    full_session_id = normalize_record_id("home_chat_session", session_id)
    session = await HomeChatSession.get(full_session_id)
    if not session or session.user_id != user.id:
        raise NotFoundError("Session not found")
    return session


async def _session_message_count(session_id: str) -> int:
    """Message count from the async-checkpointed graph state (0 on error).

    Not `graph_utils.get_session_message_count`: that helper uses the sync
    `get_state`, which the home chat graph's AsyncSqliteSaver rejects.
    """
    try:
        graph = await get_home_chat_graph()
        thread_state = await graph.aget_state(
            config=RunnableConfig(configurable={"thread_id": session_id})
        )
        if thread_state and thread_state.values:
            return len(thread_state.values.get("messages", []))
    except Exception as e:
        logger.warning(f"Could not fetch message count for session {session_id}: {e}")
    return 0


async def _session_response(
    session: HomeChatSession, user: CurrentUser
) -> HomeChatSessionResponse:
    return HomeChatSessionResponse(
        id=session.id or "",
        title=session.title or "Untitled Session",
        model_override=session.model_override,
        created=str(session.created),
        updated=str(session.updated),
        message_count=await _session_message_count(session.id or ""),
    )


@router.post("/home-chat/sessions", response_model=HomeChatSessionResponse)
async def create_home_chat_session(
    request: CreateHomeChatSessionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Create a new home chat session for the current user."""
    try:
        session = HomeChatSession(
            title=request.title or "New Conversation",
            model_override=request.model_override,
            user_id=user.id,
        )
        await session.save()
        return await _session_response(session, user)
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating home chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating home chat session: {str(e)}"
        )


@router.get("/home-chat/sessions", response_model=List[HomeChatSessionResponse])
async def get_home_chat_sessions(user: CurrentUser = Depends(get_current_user)):
    """List the current user's home chat sessions (newest first)."""
    try:
        records = await repo_query(
            "SELECT * FROM home_chat_session WHERE user_id = $user_id",
            {"user_id": user.id},
        )
        sessions: List[HomeChatSessionResponse] = []
        for record in records or []:
            session = HomeChatSession(**record)
            sessions.append(await _session_response(session, user))
        sessions.sort(key=lambda x: x.created, reverse=True)
        return sessions
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching home chat sessions: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching home chat sessions: {str(e)}"
        )


@router.get(
    "/home-chat/sessions/{session_id}",
    response_model=HomeChatSessionWithMessagesResponse,
)
async def get_home_chat_session(
    session_id: str = Path(..., description="Session ID"),
    user: CurrentUser = Depends(get_current_user),
):
    """Get a session with its persisted messages and per-turn metadata."""
    try:
        session = await _get_owned_session(session_id, user)
        full_session_id = session.id or ""

        graph = await get_home_chat_graph()
        thread_state = await graph.aget_state(
            config=RunnableConfig(configurable={"thread_id": full_session_id})
        )

        messages: List[ChatMessage] = []
        turns: List[Dict[str, Any]] = []
        if thread_state and thread_state.values:
            if "messages" in thread_state.values:
                messages = extract_chat_messages(thread_state.values["messages"])
            raw_turns = thread_state.values.get("turn_metadata") or []
            turns = [t for t in raw_turns if isinstance(t, dict)]

        return HomeChatSessionWithMessagesResponse(
            id=full_session_id,
            title=session.title or "Untitled Session",
            model_override=session.model_override,
            created=str(session.created),
            updated=str(session.updated),
            message_count=len(messages),
            messages=messages,
            turns=turns,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching home chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching home chat session: {str(e)}"
        )


@router.put(
    "/home-chat/sessions/{session_id}", response_model=HomeChatSessionResponse
)
async def update_home_chat_session(
    request: UpdateHomeChatSessionRequest,
    session_id: str = Path(..., description="Session ID"),
    user: CurrentUser = Depends(get_current_user),
):
    """Update session title and/or model override."""
    try:
        session = await _get_owned_session(session_id, user)
        if request.title is not None:
            session.title = request.title
        if request.model_override is not None:
            session.model_override = request.model_override
        await session.save()
        return await _session_response(session, user)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating home chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error updating home chat session: {str(e)}"
        )


@router.delete("/home-chat/sessions/{session_id}", response_model=SuccessResponse)
async def delete_home_chat_session(
    session_id: str = Path(..., description="Session ID"),
    user: CurrentUser = Depends(get_current_user),
):
    """Delete a home chat session."""
    try:
        session = await _get_owned_session(session_id, user)
        await session.delete()
        return SuccessResponse(
            success=True, message="Home chat session deleted successfully"
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting home chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error deleting home chat session: {str(e)}"
        )


async def _resolve_model_id(requested: Optional[str]) -> Optional[str]:
    """Requested model ID wins; otherwise the default chat model."""
    if requested:
        return requested
    defaults = await DefaultModels.get_instance()
    return defaults.default_chat_model


async def stream_home_chat_response(
    *,
    session_id: str,
    question: str,
    notebook_ids: List[str],
    user: CurrentUser,
    strategy_model: str,
    answer_model: str,
    final_answer_model: str,
    include_refunds: bool,
) -> AsyncGenerator[str, None]:
    """Stream one assistant turn as Server-Sent Events."""
    try:
        # Conversation memory: load checkpointed state, append the new human
        # message. add_messages dedupes by id, so replaying the checkpointed
        # list is safe. turn_metadata/suggestions are NOT replayed — their
        # operator.add reducer would duplicate entries.
        graph = await get_home_chat_graph()
        current_state = await graph.aget_state(
            config=RunnableConfig(configurable={"thread_id": session_id})
        )
        messages = list(
            (current_state.values if current_state else {}).get("messages", [])
        )
        messages.append(HumanMessage(content=question))

        yield f"data: {json.dumps({'type': 'user_message', 'content': question, 'timestamp': None})}\n\n"

        final_answer: Optional[str] = None
        analytics_answer: Optional[Dict[str, Any]] = None
        suggestions: List[str] = []

        input_state: Dict[str, Any] = {
            "messages": messages,
            "question": question,
            "notebook_ids": notebook_ids,
            "user_id": user.id,
            "team_id": user.team_id,
            "role": user.role,
            "include_refunds": include_refunds,
            # Per-turn output channels are plain (no reducer) — reset them
            # so a value checkpointed by a PREVIOUS turn (e.g. an analytics
            # payload) can't leak into this turn's events and metadata.
            "route": None,
            "final_answer": None,
            "analytics_answer": None,
            "suggestions": None,
        }

        async for chunk in graph.astream(  # type: ignore[call-overload]
            input=input_state,
            config=RunnableConfig(
                configurable={
                    "thread_id": session_id,
                    "strategy_model": strategy_model,
                    "answer_model": answer_model,
                    "final_answer_model": final_answer_model,
                }
            ),
            stream_mode="updates",
        ):
            if "classify" in chunk:
                yield f"data: {json.dumps({'type': 'route', 'route': chunk['classify'].get('route')})}\n\n"
            elif "knowledge_strategy" in chunk:
                strategy = chunk["knowledge_strategy"]["strategy"]
                yield f"data: {json.dumps({'type': 'strategy', 'reasoning': strategy.reasoning, 'searches': [{'term': s.term, 'instructions': s.instructions} for s in strategy.searches]})}\n\n"
            elif "knowledge_search" in chunk:
                for answer in chunk["knowledge_search"]["answers"]:
                    content = (
                        answer.get("content", "") if isinstance(answer, dict) else answer
                    )
                    yield f"data: {json.dumps({'type': 'answer', 'content': content})}\n\n"
            elif "knowledge_final" in chunk:
                final_answer = chunk["knowledge_final"].get("final_answer")
                yield f"data: {json.dumps({'type': 'final_answer', 'content': final_answer})}\n\n"
            elif "analytics" in chunk:
                analytics_answer = chunk["analytics"].get("analytics_answer")
                yield f"data: {json.dumps({'type': 'analytics_answer', 'data': analytics_answer})}\n\n"
            elif "suggest" in chunk:
                suggestions = chunk["suggest"].get("suggestions") or []
                yield f"data: {json.dumps({'type': 'suggestions', 'suggestions': suggestions})}\n\n"

        completion = {
            "type": "complete",
            "final_answer": final_answer,
            "analytics_answer": analytics_answer,
            "suggestions": suggestions,
        }
        yield f"data: {json.dumps(completion)}\n\n"

    except Exception as e:
        _, error_message = classify_error(e)
        logger.error(f"Error in home chat streaming: {str(e)}")
        yield f"data: {json.dumps({'type': 'error', 'message': error_message})}\n\n"


@router.post("/home-chat/sessions/{session_id}/messages")
async def send_message_to_home_chat(
    request: SendHomeMessageRequest,
    session_id: str = Path(..., description="Session ID"),
    user: CurrentUser = Depends(get_current_user),
):
    """Send a message and stream the assistant's answer (SSE)."""
    try:
        session = await _get_owned_session(session_id, user)
        if not request.message:
            raise HTTPException(status_code=400, detail="Message content is required")

        # T5: permitted ∩ requested scope. Empty effective scope is passed
        # through to the knowledge branch, which answers honestly without
        # searching (never a whole-KB fallback).
        notebook_ids = await effective_notebook_scope(
            user, request.notebook_ids or []
        )

        strategy_model = await _resolve_model_id(request.strategy_model)
        answer_model = await _resolve_model_id(request.answer_model)
        final_answer_model = await _resolve_model_id(request.final_answer_model)
        if not strategy_model or not answer_model or not final_answer_model:
            raise HTTPException(
                status_code=400,
                detail="No default chat model configured. Set one in Models settings.",
            )

        # Touch updated timestamp
        await session.save()

        return StreamingResponse(
            stream_home_chat_response(
                session_id=session.id or "",
                question=request.message,
                notebook_ids=notebook_ids,
                user=user,
                strategy_model=strategy_model,
                answer_model=answer_model,
                final_answer_model=final_answer_model,
                include_refunds=request.include_refunds,
            ),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error sending message to home chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error sending message: {str(e)}")
