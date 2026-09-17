import asyncio
import traceback
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from langchain_core.runnables import RunnableConfig
from loguru import logger
from pydantic import BaseModel, Field

from api.access import (
    CurrentUser,
    check_chat_session_write,
    check_notebook_read,
    filter_context_to_permitted,
    get_current_user,
    permitted_notebook_ids,
)
from api.routers._chat_shared import (
    ChatMessage,
    SuccessResponse,
    extract_chat_messages,
    get_session_or_404,
)
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import ChatSession, Notebook
from open_notebook.exceptions import (
    NotFoundError,
    OpenNotebookError,
)
from open_notebook.graphs.chat import graph as chat_graph
from open_notebook.utils import token_count
from open_notebook.utils.context_builder import build_notebook_context
from open_notebook.utils.graph_utils import get_session_message_count

router = APIRouter()


# Request/Response models
class CreateSessionRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID to create session for")
    title: Optional[str] = Field(None, description="Optional session title")
    model_override: Optional[str] = Field(
        None, description="Optional model override for this session"
    )


class UpdateSessionRequest(BaseModel):
    title: Optional[str] = Field(None, description="New session title")
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatSessionResponse(BaseModel):
    id: str = Field(..., description="Session ID")
    title: str = Field(..., description="Session title")
    notebook_id: Optional[str] = Field(None, description="Notebook ID")
    created: str = Field(..., description="Creation timestamp")
    updated: str = Field(..., description="Last update timestamp")
    message_count: Optional[int] = Field(
        None, description="Number of messages in session"
    )
    model_override: Optional[str] = Field(
        None, description="Model override for this session"
    )


class ChatSessionWithMessagesResponse(ChatSessionResponse):
    messages: List[ChatMessage] = Field(
        default_factory=list, description="Session messages"
    )


class ExecuteChatRequest(BaseModel):
    session_id: str = Field(..., description="Chat session ID")
    message: str = Field(..., description="User message content")
    context: Dict[str, Any] = Field(
        ..., description="Chat context with sources and notes"
    )
    model_override: Optional[str] = Field(
        None, description="Optional model override for this message"
    )


class ExecuteChatResponse(BaseModel):
    session_id: str = Field(..., description="Session ID")
    messages: List[ChatMessage] = Field(..., description="Updated message list")


class BuildContextRequest(BaseModel):
    notebook_id: str = Field(..., description="Notebook ID")
    context_config: Dict[str, Any] = Field(..., description="Context configuration")


class BuildContextResponse(BaseModel):
    context: Dict[str, Any] = Field(..., description="Built context data")
    token_count: int = Field(..., description="Estimated token count")
    char_count: int = Field(..., description="Character count")


@router.get("/chat/sessions", response_model=List[ChatSessionResponse])
async def get_sessions(
    user: CurrentUser = Depends(get_current_user),
    notebook_id: str = Query(..., description="Notebook ID"),
):
    """Get all chat sessions for a notebook."""
    try:
        # T5: reading a notebook's sessions requires read access to it.
        notebook = await check_notebook_read(user, notebook_id)

        # Get sessions for this notebook
        sessions_list = await notebook.get_chat_sessions()

        results = []
        for session in sessions_list:
            session_id = str(session.id)

            # Get message count from LangGraph state
            msg_count = await get_session_message_count(chat_graph, session_id)

            results.append(
                ChatSessionResponse(
                    id=session.id or "",
                    title=session.title or "Untitled Session",
                    notebook_id=notebook_id,
                    created=str(session.created),
                    updated=str(session.updated),
                    message_count=msg_count,
                    model_override=getattr(session, "model_override", None),
                )
            )

        return results
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching chat sessions: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error fetching chat sessions: {str(e)}"
        )


@router.post("/chat/sessions", response_model=ChatSessionResponse)
async def create_session(
    request: CreateSessionRequest, user: CurrentUser = Depends(get_current_user)
):
    """Create a new chat session."""
    try:
        # T5: creating a session in a notebook requires read access to it.
        await check_notebook_read(user, request.notebook_id)

        # Create new session
        session = ChatSession(
            title=request.title
            or f"Chat Session {asyncio.get_event_loop().time():.0f}",
            model_override=request.model_override,
        )
        await session.save()

        # Relate session to notebook
        await session.relate_to_notebook(request.notebook_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=request.notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=0,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating chat session: {str(e)}")
        raise HTTPException(
            status_code=500, detail=f"Error creating chat session: {str(e)}"
        )


@router.get(
    "/chat/sessions/{session_id}", response_model=ChatSessionWithMessagesResponse
)
async def get_session(
    session_id: str, user: CurrentUser = Depends(get_current_user)
):
    """Get a specific session with its messages."""
    try:
        # Get session (normalizes the ID; T5: the single fetch doubles as the
        # permission check — out-of-scope sessions 404)
        full_session_id, session = await get_session_or_404(session_id, user)

        # Get session state from LangGraph to retrieve messages
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        thread_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Extract messages from state
        messages: list[ChatMessage] = []
        if thread_state and thread_state.values and "messages" in thread_state.values:
            messages = extract_chat_messages(thread_state.values["messages"])

        # Find notebook_id (we need to query the relationship)
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )

        notebook_id = notebook_query[0]["out"] if notebook_query else None

        if not notebook_id:
            # This might be an old session created before API migration
            logger.warning(
                f"No notebook relationship found for session {session_id} - may be an orphaned session"
            )

        return ChatSessionWithMessagesResponse(
            id=session.id or "",
            title=session.title or "Untitled Session",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=len(messages),
            messages=messages,
            model_override=getattr(session, "model_override", None),
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching session: {str(e)}")


@router.put("/chat/sessions/{session_id}", response_model=ChatSessionResponse)
async def update_session(
    session_id: str,
    request: UpdateSessionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Update session title."""
    try:
        # Get session (normalizes the ID; T5: write check re-checks via parent)
        full_session_id, _session = await get_session_or_404(session_id)
        session = await check_chat_session_write(user, full_session_id)

        update_data = request.model_dump(exclude_unset=True)

        if "title" in update_data:
            session.title = update_data["title"]

        if "model_override" in update_data:
            session.model_override = update_data["model_override"]

        await session.save()

        # Find notebook_id
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook_id = notebook_query[0]["out"] if notebook_query else None

        # Get message count from LangGraph state
        msg_count = await get_session_message_count(chat_graph, full_session_id)

        return ChatSessionResponse(
            id=session.id or "",
            title=session.title or "",
            notebook_id=notebook_id,
            created=str(session.created),
            updated=str(session.updated),
            message_count=msg_count,
            model_override=session.model_override,
        )
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating session: {str(e)}")


@router.delete("/chat/sessions/{session_id}", response_model=SuccessResponse)
async def delete_session(
    session_id: str, user: CurrentUser = Depends(get_current_user)
):
    """Delete a chat session."""
    try:
        # Get session (normalizes the ID; T5: write check re-checks via parent)
        full_session_id, _session = await get_session_or_404(session_id)
        session = await check_chat_session_write(user, full_session_id)

        await session.delete()

        return SuccessResponse(success=True, message="Session deleted successfully")
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting session: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting session: {str(e)}")


@router.post("/chat/execute", response_model=ExecuteChatResponse)
async def execute_chat(
    request: ExecuteChatRequest, user: CurrentUser = Depends(get_current_user)
):
    """Execute a chat request and get AI response."""
    try:
        # Verify session exists (normalizes the ID; T5: 404 when the session's
        # parent notebook/source is outside the caller's permitted scope)
        full_session_id, session = await get_session_or_404(request.session_id, user)

        # Fetch notebook linked to this session
        notebook_query = await repo_query(
            "SELECT out FROM refers_to WHERE in = $session_id",
            {"session_id": ensure_record_id(full_session_id)},
        )
        notebook = None
        if notebook_query:
            notebook = await Notebook.get(notebook_query[0]["out"])

        # T5: the client-supplied context is display state from the frontend,
        # but it still ends up in the LLM prompt — drop anything outside the
        # caller's permitted scope before it can leak foreign content.
        context = await filter_context_to_permitted(user, request.context)

        # Determine model override (per-request override takes precedence over session-level)
        model_override = (
            request.model_override
            if request.model_override is not None
            else getattr(session, "model_override", None)
        )

        # Get current state
        # Use sync get_state() in a thread since SqliteSaver doesn't support async
        current_state = await asyncio.to_thread(
            chat_graph.get_state,
            config=RunnableConfig(configurable={"thread_id": full_session_id}),
        )

        # Prepare state for execution
        state_values = current_state.values if current_state else {}
        state_values["messages"] = state_values.get("messages", [])
        state_values["context"] = context
        state_values["notebook"] = notebook
        state_values["model_override"] = model_override

        # Add user message to state
        from langchain_core.messages import HumanMessage

        user_message = HumanMessage(content=request.message)
        state_values["messages"].append(user_message)

        # Execute chat graph in a thread so the synchronous LangGraph invoke
        # (SqliteSaver checkpoints are sync) doesn't block the event loop and
        # freeze the rest of the API while the LLM responds. Mirrors the
        # get_state() calls above.
        # The lambda pins down which `invoke` overload is used; asyncio.to_thread
        # can't resolve overloaded callables on its own. The ignore is a langgraph
        # typing limitation: it accepts a partial state dict at runtime, but the
        # signature requires the full state type.
        result = await asyncio.to_thread(
            lambda: chat_graph.invoke(
                input=state_values,  # type: ignore[arg-type]
                config=RunnableConfig(
                    configurable={
                        "thread_id": full_session_id,
                        "model_id": model_override,
                    }
                ),
            )
        )

        # Update session timestamp
        await session.save()

        # Convert messages to response format
        messages = extract_chat_messages(result.get("messages", []))

        return ExecuteChatResponse(session_id=request.session_id, messages=messages)
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Session not found")
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        # Log detailed error with context for debugging
        logger.error(
            f"Error executing chat: {str(e)}\n"
            f"  Session ID: {request.session_id}\n"
            f"  Model override: {request.model_override}\n"
            f"  Traceback:\n{traceback.format_exc()}"
        )
        raise HTTPException(status_code=500, detail=f"Error executing chat: {str(e)}")


@router.post("/chat/context", response_model=BuildContextResponse)
async def build_context(
    request: BuildContextRequest, user: CurrentUser = Depends(get_current_user)
):
    """Build context for a notebook based on context configuration."""
    try:
        # Verify the notebook exists and is readable (T5)
        notebook = await check_notebook_read(user, request.notebook_id)

        # T5: only items linked to a notebook the caller may read may enter
        # the context — the builder rejects out-of-scope ids before fetching.
        context_data, total_content = await build_notebook_context(
            notebook,
            request.context_config,
            permitted_notebook_ids=set(await permitted_notebook_ids(user)),
        )

        char_count = len(total_content)
        estimated_tokens = token_count(total_content) if total_content else 0

        return BuildContextResponse(
            context=context_data, token_count=estimated_tokens, char_count=char_count
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error building context: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error building context: {str(e)}")
