from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from surreal_commands import get_command_status

from api.access import (
    CurrentUser,
    get_current_user,
    permitted_notebook_ids,
)
from api.command_service import CommandService
from api.models import (
    RebuildProgress,
    RebuildRequest,
    RebuildResponse,
    RebuildStats,
    RebuildStatusResponse,
)
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.exceptions import OpenNotebookError

router = APIRouter()


async def _scoped_item_ids(
    permitted_ids: list, kind: str
) -> list:
    """Source/note ids linked (reference/artifact) to a permitted notebook (T5)."""
    edge = "reference" if kind == "source" else "artifact"
    rows = await repo_query(
        f"SELECT VALUE in FROM {edge} WHERE out IN $ids",
        {"ids": [ensure_record_id(i) for i in permitted_ids]},
    )
    return [str(row) for row in rows]


@router.post("/rebuild", response_model=RebuildResponse)
async def start_rebuild(
    request: RebuildRequest, user: CurrentUser = Depends(get_current_user)
):
    """
    Start a background job to rebuild embeddings.

    - **mode**: "existing" (re-embed items with embeddings) or "all" (embed everything)
    - **include_sources**: Include sources in rebuild (default: true)
    - **include_notes**: Include notes in rebuild (default: true)
    - **include_insights**: Include insights in rebuild (default: true)

    T5: non-admin callers rebuild only items in their permitted notebook scope;
    admins keep the global scope. The scope rides the job payload and the
    worker revalidates it.

    Returns command ID to track progress and estimated item count.
    """
    try:
        logger.info(f"Starting rebuild request: mode={request.mode}")

        # Import commands to ensure they're registered
        import commands.embedding_commands  # noqa: F401

        # T5 scope: admins get the global scope (None); everyone else is
        # limited to their permitted notebooks.
        permitted = None if user.role == "admin" else await permitted_notebook_ids(user)
        scoped_sources: list = []
        scoped_notes: list = []
        if permitted is not None:
            if not permitted:
                scoped_sources, scoped_notes = [], []
            else:
                if request.include_sources or request.include_insights:
                    scoped_sources = await _scoped_item_ids(permitted, "source")
                if request.include_notes:
                    scoped_notes = await _scoped_item_ids(permitted, "note")

        # Estimate total items (quick count query)
        # This is a rough estimate before the command runs
        total_estimate = 0

        if request.include_sources:
            if request.mode == "existing":
                # Count sources with embeddings
                if permitted is None:
                    result = await repo_query(
                        """
                        SELECT VALUE count(array::distinct(
                            SELECT VALUE source.id
                            FROM source_embedding
                            WHERE embedding != none AND array::len(embedding) > 0
                        )) as count FROM {}
                        """
                    )
                else:
                    result = await repo_query(
                        """
                        SELECT VALUE count(array::distinct(
                            SELECT VALUE source.id
                            FROM source_embedding
                            WHERE embedding != none AND array::len(embedding) > 0
                              AND source IN $scoped
                        )) as count FROM {}
                        """,
                        {"scoped": [ensure_record_id(i) for i in scoped_sources]},
                    )
            else:
                # Count all sources with content
                if permitted is None:
                    result = await repo_query(
                        "SELECT VALUE count() as count FROM source WHERE full_text != none GROUP ALL"
                    )
                else:
                    result = await repo_query(
                        "SELECT VALUE count() as count FROM source WHERE full_text != none AND id IN $scoped GROUP ALL",
                        {"scoped": [ensure_record_id(i) for i in scoped_sources]},
                    )

            if result and isinstance(result[0], dict):
                total_estimate += result[0].get("count", 0)
            elif result:
                total_estimate += result[0] if isinstance(result[0], int) else 0

        if request.include_notes:
            if request.mode == "existing":
                base = "SELECT VALUE count() as count FROM note WHERE embedding != none AND array::len(embedding) > 0"
            else:
                base = "SELECT VALUE count() as count FROM note WHERE content != none"
            if permitted is not None:
                result = await repo_query(
                    f"{base} AND id IN $scoped GROUP ALL",
                    {"scoped": [ensure_record_id(i) for i in scoped_notes]},
                )
            else:
                result = await repo_query(f"{base} GROUP ALL")

            if result and isinstance(result[0], dict):
                total_estimate += result[0].get("count", 0)
            elif result:
                total_estimate += result[0] if isinstance(result[0], int) else 0

        if request.include_insights:
            if request.mode == "existing":
                base = "SELECT VALUE count() as count FROM source_insight WHERE embedding != none AND array::len(embedding) > 0"
            else:
                base = "SELECT VALUE count() as count FROM source_insight"
            if permitted is not None:
                result = await repo_query(
                    f"{base} AND source IN $scoped GROUP ALL",
                    {"scoped": [ensure_record_id(i) for i in scoped_sources]},
                )
            else:
                result = await repo_query(f"{base} GROUP ALL")

            if result and isinstance(result[0], dict):
                total_estimate += result[0].get("count", 0)
            elif result:
                total_estimate += result[0] if isinstance(result[0], int) else 0

        logger.info(f"Estimated {total_estimate} items to process")

        # Submit command — the permitted scope rides the payload (T5)
        command_id = await CommandService.submit_command_job(
            "open_notebook",
            "rebuild_embeddings",
            {
                "mode": request.mode,
                "include_sources": request.include_sources,
                "include_notes": request.include_notes,
                "include_insights": request.include_insights,
                "notebook_ids": permitted,
            },
        )

        logger.info(f"Submitted rebuild command: {command_id}")

        return RebuildResponse(
            command_id=command_id,
            total_items=total_estimate,
            message=f"Rebuild operation started. Estimated {total_estimate} items to process.",
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Failed to start rebuild: {e}")
        logger.exception(e)
        raise HTTPException(
            status_code=500, detail=f"Failed to start rebuild operation: {str(e)}"
        )


@router.get("/rebuild/{command_id}/status", response_model=RebuildStatusResponse)
async def get_rebuild_status(
    command_id: str, user: CurrentUser = Depends(get_current_user)
):
    """
    Get the status of a rebuild operation.

    T5: authenticated — progress counts contain no content.

    Returns:
    - **status**: queued, running, completed, failed
    - **progress**: processed count, total count, percentage
    - **stats**: breakdown by type (sources, notes, insights, failed)
    - **timestamps**: started_at, completed_at
    """
    try:
        # Get command status from surreal_commands
        status = await get_command_status(command_id)

        if not status:
            raise HTTPException(status_code=404, detail="Rebuild command not found")

        # Build response based on status
        response = RebuildStatusResponse(
            command_id=command_id,
            status=status.status,
        )

        # Extract metadata from command result
        if status.result and isinstance(status.result, dict):
            result = status.result

            # Build progress info
            if "total_items" in result and "jobs_submitted" in result:
                total = result["total_items"]
                submitted = result["jobs_submitted"]
                response.progress = RebuildProgress(
                    processed=submitted,
                    total=total,
                    percentage=round((submitted / total * 100) if total > 0 else 0, 2),
                )

            # Build stats
            response.stats = RebuildStats(
                sources=result.get("sources_submitted", 0),
                notes=result.get("notes_submitted", 0),
                insights=result.get("insights_submitted", 0),
                failed=result.get("failed_submissions", 0),
            )

        # Add timestamps
        if hasattr(status, "created") and status.created:
            response.started_at = str(status.created)
        if hasattr(status, "updated") and status.updated:
            response.completed_at = str(status.updated)

        # Add error message if failed
        if (
            status.status == "failed"
            and status.result
            and isinstance(status.result, dict)
        ):
            response.error_message = status.result.get("error_message", "Unknown error")

        return response

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Failed to get rebuild status: {e}")
        logger.exception(e)
        raise HTTPException(
            status_code=500, detail=f"Failed to get rebuild status: {str(e)}"
        )
