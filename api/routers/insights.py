from fastapi import APIRouter, Depends, HTTPException
from loguru import logger

from api.access import (
    CurrentUser,
    check_insight_read,
    check_insight_write,
    check_notebook_write,
    get_current_user,
)
from api.models import NoteResponse, SaveAsNoteRequest, SourceInsightResponse
from open_notebook.exceptions import (
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)

router = APIRouter()


@router.get("/insights/{insight_id}", response_model=SourceInsightResponse)
async def get_insight(
    insight_id: str, user: CurrentUser = Depends(get_current_user)
):
    """Get a specific insight by ID."""
    try:
        # T5: readable only when the parent source is.
        insight = await check_insight_read(user, insight_id)

        # Get source ID from the insight relationship
        source = await insight.get_source()

        return SourceInsightResponse(
            id=insight.id or "",
            source_id=source.id or "",
            insight_type=insight.insight_type,
            content=insight.content,
            created=insight.created.isoformat() if insight.created else None,
            updated=insight.updated.isoformat() if insight.updated else None,
        )
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching insight {insight_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error fetching insight")


@router.delete("/insights/{insight_id}")
async def delete_insight(
    insight_id: str, user: CurrentUser = Depends(get_current_user)
):
    """Delete a specific insight."""
    try:
        # T5: writable only when the parent source is.
        insight = await check_insight_write(user, insight_id)

        await insight.delete()

        return {"message": "Insight deleted successfully"}
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting insight {insight_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Error deleting insight")


@router.post("/insights/{insight_id}/save-as-note", response_model=NoteResponse)
async def save_insight_as_note(
    insight_id: str,
    request: SaveAsNoteRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """Convert an insight to a note."""
    try:
        # T5: the insight must be readable and the target notebook writable.
        insight = await check_insight_read(user, insight_id)
        if request.notebook_id:
            await check_notebook_write(user, request.notebook_id)

        # Use the existing save_as_note method from the domain model
        note = await insight.save_as_note(request.notebook_id)

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
        raise HTTPException(status_code=404, detail="Notebook not found")
    except InvalidInputError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error saving insight {insight_id} as note: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Error saving insight as note"
        )
