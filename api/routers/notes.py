from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from api.access import (
    CurrentUser,
    check_note_read,
    check_note_write,
    check_notebook_read,
    check_notebook_write,
    get_current_user,
    permitted_notebook_ids,
)
from api.models import NoteCreate, NoteResponse, NoteUpdate
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Note
from open_notebook.exceptions import (
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)

router = APIRouter()


@router.get("/notes", response_model=List[NoteResponse])
async def get_notes(
    user: CurrentUser = Depends(get_current_user),
    notebook_id: Optional[str] = Query(None, description="Filter by notebook ID"),
):
    """Get all notes the caller may read, with optional notebook filtering."""
    try:
        if notebook_id:
            # T5: reading a notebook's notes requires read access to it.
            notebook = await check_notebook_read(user, notebook_id)
            notes = await notebook.get_notes()
        else:
            # T5: the global list is filtered inside the query — only notes
            # linked (via artifact) to a permitted notebook.
            permitted = await permitted_notebook_ids(user)
            if not permitted:
                return []
            rows = await repo_query(
                """
                SELECT * FROM note
                WHERE id IN (SELECT VALUE in FROM artifact WHERE out IN $ids)
                ORDER BY updated DESC
                """,
                {"ids": [ensure_record_id(i) for i in permitted]},
            )
            notes = [Note(**row) for row in rows]

        return [
            NoteResponse(
                id=note.id or "",
                title=note.title,
                content=note.content,
                note_type=note.note_type,
                created=str(note.created),
                updated=str(note.updated),
            )
            for note in notes
        ]
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching notes: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching notes: {str(e)}")


@router.post("/notes", response_model=NoteResponse)
async def create_note(
    note_data: NoteCreate, user: CurrentUser = Depends(get_current_user)
):
    """Create a new note."""
    try:
        # T5: adding a note to a notebook requires write access to it.
        if note_data.notebook_id:
            await check_notebook_write(user, note_data.notebook_id)

        # Auto-generate title if not provided and it's an AI note
        title = note_data.title
        if not title and note_data.note_type == "ai" and note_data.content:
            from open_notebook.graphs.prompt import graph as prompt_graph

            prompt = "Based on the Note below, please provide a Title for this content, with max 15 words"
            # LangGraph accepts a partial state dict at runtime, but its typed
            # overloads require the full state type (langgraph typing limitation).
            result = await prompt_graph.ainvoke(  # type: ignore[call-overload]
                {
                    "input_text": note_data.content,
                    "prompt": prompt,
                }
            )
            title = result.get("output", "Untitled Note")

        # Validate note_type
        note_type: Optional[Literal["human", "ai"]] = None
        if note_data.note_type in ("human", "ai"):
            note_type = note_data.note_type  # type: ignore[assignment]
        elif note_data.note_type is not None:
            raise HTTPException(
                status_code=400, detail="note_type must be 'human' or 'ai'"
            )

        new_note = Note(
            title=title,
            content=note_data.content,
            note_type=note_type,
        )
        command_id = await new_note.save()

        # Add to notebook if specified
        if note_data.notebook_id:
            await new_note.add_to_notebook(note_data.notebook_id)

        return NoteResponse(
            id=new_note.id or "",
            title=new_note.title,
            content=new_note.content,
            note_type=new_note.note_type,
            created=str(new_note.created),
            updated=str(new_note.updated),
            command_id=str(command_id) if command_id else None,
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Notebook not found")
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error creating note: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error creating note: {str(e)}")


@router.get("/notes/{note_id}", response_model=NoteResponse)
async def get_note(
    note_id: str, user: CurrentUser = Depends(get_current_user)
):
    """Get a specific note by ID."""
    try:
        note = await check_note_read(user, note_id)

        return NoteResponse(
            id=note.id or "",
            title=note.title,
            content=note.content,
            note_type=note.note_type,
            created=str(note.created),
            updated=str(note.updated),
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Note not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching note {note_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching note: {str(e)}")


@router.put("/notes/{note_id}", response_model=NoteResponse)
async def update_note(
    note_id: str,
    note_update: NoteUpdate,
    user: CurrentUser = Depends(get_current_user),
):
    """Update a note."""
    try:
        note = await check_note_write(user, note_id)

        # Update only provided fields
        if note_update.title is not None:
            note.title = note_update.title
        if note_update.content is not None:
            note.content = note_update.content
        if note_update.note_type is not None:
            if note_update.note_type in ("human", "ai"):
                note.note_type = note_update.note_type  # type: ignore[assignment]
            else:
                raise HTTPException(
                    status_code=400, detail="note_type must be 'human' or 'ai'"
                )

        command_id = await note.save()

        return NoteResponse(
            id=note.id or "",
            title=note.title,
            content=note.content,
            note_type=note.note_type,
            created=str(note.created),
            updated=str(note.updated),
            command_id=str(command_id) if command_id else None,
        )
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Note not found")
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error updating note {note_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error updating note: {str(e)}")


@router.delete("/notes/{note_id}")
async def delete_note(
    note_id: str, user: CurrentUser = Depends(get_current_user)
):
    """Delete a note."""
    try:
        note = await check_note_write(user, note_id)

        await note.delete()

        return {"message": "Note deleted successfully"}
    except HTTPException:
        raise
    except NotFoundError:
        raise HTTPException(status_code=404, detail="Note not found")
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting note {note_id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error deleting note: {str(e)}")
