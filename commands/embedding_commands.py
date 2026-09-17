import time
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
)

from loguru import logger
from surreal_commands import CommandInput, CommandOutput, command, submit_command

from open_notebook.ai.models import model_manager
from open_notebook.database.repository import ensure_record_id, repo_insert, repo_query
from open_notebook.domain.notebook import Note, Source, SourceInsight
from open_notebook.exceptions import ConfigurationError, ContextLengthExceededError
from open_notebook.utils.chunking import ContentType, chunk_text, detect_content_type
from open_notebook.utils.embedding import generate_embedding, generate_embeddings

# NOTE: `stop_on` below can never trigger in practice — each command catches
# ValueError internally and returns success=False instead of raising, so the
# retry layer never sees it. Kept as-is on purpose; to be revisited in a
# dedicated error-handling PR.
EMBED_RETRY_CONFIG = {
    "max_attempts": 5,
    "wait_strategy": "exponential_jitter",
    "wait_min": 1,
    "wait_max": 60,
    "stop_on": [
        ValueError,
        ConfigurationError,
        ContextLengthExceededError,
    ],  # Don't retry validation/config errors
    "retry_log_level": "warning",
}


def get_command_id(input_data: CommandInput) -> str:
    """Extract command_id from input_data's execution context, or return 'unknown'."""
    if input_data.execution_context:
        return str(input_data.execution_context.command_id)
    return "unknown"


async def _embed_record(
    input_data: CommandInput,
    *,
    kind: str,
    record_id: str,
    embed: Callable[[], Awaitable[Tuple[Dict[str, Any], str]]],
) -> Tuple[Optional[Dict[str, Any]], float, Optional[str]]:
    """
    Shared core for the embed_* commands: run the embedding work with the
    common logging and error-handling epilogue.

    Args:
        input_data: The command input (used for command_id logging).
        kind: Record kind for log messages ("note", "insight", "source").
        record_id: The record being embedded.
        embed: Async callable doing the actual load/validate/embed/write work.
            Returns (extra_output_fields, success_log_detail).

    Returns:
        (extra_output_fields, processing_time, error_message)
        extra_output_fields is None and error_message is set on permanent
        (ValueError) failure. Transient failures re-raise so the retry layer
        can handle them.
    """
    start_time = time.time()

    try:
        logger.info(f"Starting embedding for {kind}: {record_id}")

        extra_fields, log_detail = await embed()

        processing_time = time.time() - start_time
        logger.info(
            f"Successfully embedded {kind} {record_id}{log_detail} in {processing_time:.2f}s"
        )
        return extra_fields, processing_time, None

    except ValueError as e:
        # Permanent failure - don't retry
        processing_time = time.time() - start_time
        cmd_id = get_command_id(input_data)
        logger.error(f"Failed to embed {kind} {record_id} (command: {cmd_id}): {e}")
        return None, processing_time, str(e)
    except Exception as e:
        # Transient failure - will be retried (surreal-commands logs final failure)
        cmd_id = get_command_id(input_data)
        logger.debug(
            f"Transient error embedding {kind} {record_id} (command: {cmd_id}): {e}"
        )
        raise


async def _embed_markdown_record(
    input_data: CommandInput,
    *,
    label: str,
    record_id: str,
    loader: Callable[[str], Awaitable[Any]],
) -> Tuple[Dict[str, Any], str]:
    """
    Load a record, validate its content, embed it as markdown and UPSERT the
    embedding back onto the record. Shared by embed_note and embed_insight.
    """
    # 1. Load record
    record = await loader(record_id)
    if not record:
        raise ValueError(f"{label} '{record_id}' not found")

    if not record.content or not record.content.strip():
        raise ValueError(f"{label} '{record_id}' has no content to embed")

    # 2. Generate embedding (auto-chunks + mean pools if needed)
    # Notes and insights are typically markdown content
    cmd_id = get_command_id(input_data)
    embedding = await generate_embedding(
        record.content, content_type=ContentType.MARKDOWN, command_id=cmd_id
    )

    # 3. UPSERT embedding into the record
    await repo_query(
        "UPDATE $record_id SET embedding = $embedding",
        {
            "record_id": ensure_record_id(record_id),
            "embedding": embedding,
        },
    )

    return {}, ""


class RebuildEmbeddingsInput(CommandInput):
    mode: Literal["existing", "all"]
    include_sources: bool = True
    include_notes: bool = True
    include_insights: bool = True
    # T5: permitted notebook scope recorded at submit time. None = global
    # (admin-only). The worker filters every collection query with it and the
    # router-side counts use the same set. Note the payload is trusted beyond
    # existence/coherence checks: embed/ask re-check their own item scope at
    # run time, and the router is the only submit path.
    notebook_ids: Optional[List[str]] = None


class RebuildEmbeddingsOutput(CommandOutput):
    success: bool
    total_items: int
    jobs_submitted: int  # Count of embedding commands submitted
    failed_submissions: int  # Count of items that failed to submit
    sources_submitted: int = 0
    notes_submitted: int = 0
    insights_submitted: int = 0
    processing_time: float
    error_message: Optional[str] = None


class CreateInsightInput(CommandInput):
    """Input for creating a source insight with automatic retry on conflicts."""

    source_id: str
    insight_type: str
    content: str


class CreateInsightOutput(CommandOutput):
    """Output from insight creation command."""

    success: bool
    insight_id: Optional[str] = None
    processing_time: float
    error_message: Optional[str] = None


class EmbedNoteInput(CommandInput):
    """Input for embedding a single note."""

    note_id: str
    # T5: the caller's team recorded at submit time (API-gated paths only).
    # The worker revalidates the note's parent notebooks against it before
    # processing; absent = legacy/domain-submitted job, no revalidation.
    expected_team_id: Optional[str] = None


class EmbedNoteOutput(CommandOutput):
    """Output from note embedding command."""

    success: bool
    note_id: str
    processing_time: float
    error_message: Optional[str] = None


class EmbedInsightInput(CommandInput):
    """Input for embedding a single source insight."""

    insight_id: str


class EmbedInsightOutput(CommandOutput):
    """Output from insight embedding command."""

    success: bool
    insight_id: str
    processing_time: float
    error_message: Optional[str] = None


class EmbedSourceInput(CommandInput):
    """Input for embedding a source (creates multiple chunk embeddings)."""

    source_id: str
    # T5: the caller's team recorded at submit time (API-gated paths only);
    # the worker revalidates before processing. Absent = legacy job.
    expected_team_id: Optional[str] = None


class EmbedSourceOutput(CommandOutput):
    """Output from source embedding command."""

    success: bool
    source_id: str
    chunks_created: int
    processing_time: float
    error_message: Optional[str] = None


@command("embed_note", app="open_notebook", retry=EMBED_RETRY_CONFIG)
async def _revalidate_source_team(source_id: str, expected_team_id: Optional[str]) -> None:
    """T5 worker-side revalidation: the source's team must still match the
    caller's team recorded at submit time (raises ValueError -> permanent
    failure, no retry). Unclassified sources pass — they were admin-reachable
    at submit and stay admin-only at every read path."""
    if not expected_team_id:
        return
    rows = await repo_query("SELECT team FROM $id", {"id": ensure_record_id(source_id)})
    if not rows:
        raise ValueError(f"Source {source_id} no longer exists")
    team = str(rows[0]["team"]) if rows[0].get("team") else None
    if team and team != expected_team_id:
        raise ValueError(
            f"Source {source_id} changed team after the job was submitted; aborting"
        )


async def _revalidate_note_team(note_id: str, expected_team_id: Optional[str]) -> None:
    """T5 worker-side revalidation for notes (which carry no team field):
    every classified parent notebook must still belong to the caller's team."""
    if not expected_team_id:
        return
    rows = await repo_query(
        "SELECT out.team AS team FROM artifact WHERE in = $id AND out.team IS NOT NONE",
        {"id": ensure_record_id(note_id)},
    )
    if not rows:
        return  # unclassified note: nothing to revalidate against
    foreign = [str(r["team"]) for r in rows if str(r["team"]) != expected_team_id]
    if foreign:
        raise ValueError(
            f"Note {note_id} is linked to a notebook outside the caller's team "
            "after the job was submitted; aborting"
        )


async def embed_note_command(input_data: EmbedNoteInput) -> EmbedNoteOutput:
    """
    Generate and store embedding for a single note.

    Uses the unified embedding pipeline with automatic chunking and mean pooling
    for notes that exceed the chunk size limit.

    Flow:
    1. Load Note by ID
    2. Generate embedding via generate_embedding() (auto-chunks + mean pools if needed)
    3. UPSERT note embedding in database

    Retry Strategy:
    - Retries up to 5 times for transient failures (network, timeout, etc.)
    - Uses exponential-jitter backoff (1-60s)
    - Does NOT retry permanent failures (ValueError for validation errors)
    """
    # T5: revalidate the recorded caller scope before doing any work
    # (raises ValueError -> permanent failure, no retry).
    await _revalidate_note_team(input_data.note_id, input_data.expected_team_id)

    async def embed() -> Tuple[Dict[str, Any], str]:
        return await _embed_markdown_record(
            input_data,
            label="Note",
            record_id=input_data.note_id,
            loader=Note.get,
        )

    _, processing_time, error_message = await _embed_record(
        input_data,
        kind="note",
        record_id=input_data.note_id,
        embed=embed,
    )

    return EmbedNoteOutput(
        success=error_message is None,
        note_id=input_data.note_id,
        processing_time=processing_time,
        error_message=error_message,
    )


@command("embed_insight", app="open_notebook", retry=EMBED_RETRY_CONFIG)
async def embed_insight_command(input_data: EmbedInsightInput) -> EmbedInsightOutput:
    """
    Generate and store embedding for a single source insight.

    Uses the unified embedding pipeline with automatic chunking and mean pooling
    for insights that exceed the chunk size limit.

    Flow:
    1. Load SourceInsight by ID
    2. Generate embedding via generate_embedding() (auto-chunks + mean pools if needed)
    3. UPSERT insight embedding in database

    Retry Strategy:
    - Retries up to 5 times for transient failures (network, timeout, etc.)
    - Uses exponential-jitter backoff (1-60s)
    - Does NOT retry permanent failures (ValueError for validation errors)
    """

    async def embed() -> Tuple[Dict[str, Any], str]:
        return await _embed_markdown_record(
            input_data,
            label="Insight",
            record_id=input_data.insight_id,
            loader=SourceInsight.get,
        )

    _, processing_time, error_message = await _embed_record(
        input_data,
        kind="insight",
        record_id=input_data.insight_id,
        embed=embed,
    )

    return EmbedInsightOutput(
        success=error_message is None,
        insight_id=input_data.insight_id,
        processing_time=processing_time,
        error_message=error_message,
    )


@command("embed_source", app="open_notebook", retry=EMBED_RETRY_CONFIG)
async def embed_source_command(input_data: EmbedSourceInput) -> EmbedSourceOutput:
    """
    Generate and store embeddings for a source document.

    Creates multiple chunk embeddings stored in the source_embedding table.
    Uses content-type aware chunking based on file extension or content heuristics.

    Flow:
    1. Load Source by ID
    2. DELETE existing source_embedding records for this source
    3. Detect content type from file path or content
    4. Chunk text using appropriate splitter
    5. Generate embeddings for all chunks in batches
    6. Bulk INSERT source_embedding records

    Retry Strategy:
    - Retries up to 5 times for transient failures (network, timeout, etc.)
    - Uses exponential-jitter backoff (1-60s)
    - Does NOT retry permanent failures (ValueError for validation errors)
    """
    # T5: revalidate the recorded caller scope before doing any work
    # (raises ValueError -> permanent failure, no retry).
    await _revalidate_source_team(input_data.source_id, input_data.expected_team_id)

    async def embed() -> Tuple[Dict[str, Any], str]:
        # 1. Load source
        source = await Source.get(input_data.source_id)
        if not source:
            raise ValueError(f"Source '{input_data.source_id}' not found")

        if not source.full_text or not source.full_text.strip():
            raise ValueError(f"Source '{input_data.source_id}' has no text to embed")

        # 2. DELETE existing embeddings (idempotency)
        logger.debug(f"Deleting existing embeddings for source {input_data.source_id}")
        await repo_query(
            "DELETE source_embedding WHERE source = $source_id",
            {"source_id": ensure_record_id(input_data.source_id)},
        )

        # 3. Detect content type from file path if available
        file_path = source.asset.file_path if source.asset else None
        content_type = detect_content_type(source.full_text, file_path)
        logger.debug(f"Detected content type: {content_type.value}")

        # 4. Chunk text using appropriate splitter
        chunks = chunk_text(source.full_text, content_type=content_type)
        total_chunks = len(chunks)

        # Log chunk statistics for debugging
        chunk_sizes = [len(c) for c in chunks]
        logger.info(
            f"Created {total_chunks} chunks for source {input_data.source_id} "
            f"(sizes: min={min(chunk_sizes) if chunk_sizes else 0}, "
            f"max={max(chunk_sizes) if chunk_sizes else 0}, "
            f"avg={sum(chunk_sizes) // len(chunk_sizes) if chunk_sizes else 0} chars)"
        )

        if total_chunks == 0:
            raise ValueError("No chunks created after splitting text")

        # 5. Generate embeddings for all chunks in batches
        cmd_id = get_command_id(input_data)
        logger.debug(f"Generating embeddings for {total_chunks} chunks")
        embeddings = await generate_embeddings(chunks, command_id=cmd_id)

        # Verify we got embeddings for all chunks
        if len(embeddings) != len(chunks):
            raise ValueError(
                f"Embedding count mismatch: got {len(embeddings)} embeddings "
                f"for {len(chunks)} chunks"
            )

        # 6. Bulk INSERT source_embedding records
        records = [
            {
                "source": ensure_record_id(input_data.source_id),
                "order": idx,
                "content": chunk,
                "embedding": embedding,
            }
            for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings))
        ]

        logger.debug(f"Inserting {len(records)} source_embedding records")
        await repo_insert("source_embedding", records)

        return {"chunks_created": total_chunks}, f": {total_chunks} chunks"

    extra_fields, processing_time, error_message = await _embed_record(
        input_data,
        kind="source",
        record_id=input_data.source_id,
        embed=embed,
    )

    return EmbedSourceOutput(
        success=error_message is None,
        source_id=input_data.source_id,
        chunks_created=(extra_fields or {}).get("chunks_created", 0),
        processing_time=processing_time,
        error_message=error_message,
    )


@command("create_insight", app="open_notebook", retry=EMBED_RETRY_CONFIG)
async def create_insight_command(
    input_data: CreateInsightInput,
) -> CreateInsightOutput:
    """
    Create a source insight with automatic retry on transaction conflicts.

    This command wraps the CREATE source_insight operation with retry logic
    to handle SurrealDB transaction conflicts that occur during batch imports
    when multiple parallel transformations try to create insights concurrently.

    Flow:
    1. CREATE source_insight record in database
    2. Submit embed_insight command (fire-and-forget) for async embedding
    3. Return the insight_id

    Retry Strategy:
    - Retries up to 5 times for transient failures (network, timeout, etc.)
    - Uses exponential-jitter backoff (1-60s)
    - Does NOT retry permanent failures (ValueError for validation errors)
    """
    start_time = time.time()

    try:
        logger.info(
            f"Creating insight for source {input_data.source_id}: "
            f"type={input_data.insight_type}"
        )

        # 1. Create insight record in database
        result = await repo_query(
            """
            CREATE source_insight CONTENT {
                "source": $source_id,
                "insight_type": $insight_type,
                "content": $content
            };
            """,
            {
                "source_id": ensure_record_id(input_data.source_id),
                "insight_type": input_data.insight_type,
                "content": input_data.content,
            },
        )

        if not result or len(result) == 0:
            raise ValueError("Failed to create insight - no result returned")

        insight_id = str(result[0].get("id", ""))
        if not insight_id:
            raise ValueError("Failed to create insight - no ID in result")

        # 2. Submit embedding command (fire-and-forget)
        submit_command(
            "open_notebook",
            "embed_insight",
            {"insight_id": insight_id},
        )
        logger.debug(f"Submitted embed_insight command for {insight_id}")

        processing_time = time.time() - start_time
        logger.info(
            f"Successfully created insight {insight_id} for source "
            f"{input_data.source_id} in {processing_time:.2f}s"
        )

        return CreateInsightOutput(
            success=True,
            insight_id=insight_id,
            processing_time=processing_time,
        )

    except ValueError as e:
        # Permanent failure - don't retry
        processing_time = time.time() - start_time
        cmd_id = get_command_id(input_data)
        logger.error(
            f"Failed to create insight for source {input_data.source_id} "
            f"(command: {cmd_id}): {e}"
        )
        return CreateInsightOutput(
            success=False,
            processing_time=processing_time,
            error_message=str(e),
        )
    except Exception as e:
        # Transient failure - will be retried (surreal-commands logs final failure)
        cmd_id = get_command_id(input_data)
        logger.debug(
            f"Transient error creating insight for source {input_data.source_id} "
            f"(command: {cmd_id}): {e}"
        )
        raise


async def collect_items_for_rebuild(
    mode: str,
    include_sources: bool,
    include_notes: bool,
    include_insights: bool,
    notebook_ids: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """
    Collect items to rebuild based on mode and include flags.

    T5: when ``notebook_ids`` is given (non-admin permitted scope), only
    items linked to those notebooks are collected — sources/insights via the
    ``reference`` edge, notes via the ``artifact`` edge.

    Returns:
        Dict with keys: 'sources', 'notes', 'insights' containing lists of item IDs
    """
    items: Dict[str, List[str]] = {"sources": [], "notes": [], "insights": []}

    scoped_sources: Optional[List[str]] = None
    scoped_notes: Optional[List[str]] = None
    if notebook_ids is not None:
        if not notebook_ids:
            return items  # empty permitted scope: nothing to rebuild
        record_ids = [ensure_record_id(i) for i in notebook_ids]
        if include_sources or include_insights:
            rows = await repo_query(
                "SELECT VALUE in FROM reference WHERE out IN $ids", {"ids": record_ids}
            )
            scoped_sources = [str(row) for row in rows]
        if include_notes:
            rows = await repo_query(
                "SELECT VALUE in FROM artifact WHERE out IN $ids", {"ids": record_ids}
            )
            scoped_notes = [str(row) for row in rows]

    if include_sources:
        if mode == "existing":
            # Query sources with embeddings (via source_embedding table)
            if scoped_sources is None:
                result = await repo_query(
                    """
                    RETURN array::distinct(
                        SELECT VALUE source.id
                        FROM source_embedding
                        WHERE embedding != none AND array::len(embedding) > 0
                    )
                    """
                )
            else:
                result = await repo_query(
                    """
                    RETURN array::distinct(
                        SELECT VALUE source.id
                        FROM source_embedding
                        WHERE embedding != none AND array::len(embedding) > 0
                          AND source IN $scoped
                    )
                    """,
                    {"scoped": [ensure_record_id(i) for i in scoped_sources]},
                )
            # RETURN returns the array directly as the result (not nested)
            if result:
                items["sources"] = [str(item) for item in result]
            else:
                items["sources"] = []
        else:  # mode == "all"
            # Query all sources with non-empty content
            if scoped_sources is None:
                result = await repo_query(
                    "SELECT id FROM source WHERE full_text != none AND string::trim(full_text) != ''"
                )
            else:
                result = await repo_query(
                    "SELECT id FROM source WHERE full_text != none AND string::trim(full_text) != '' AND id IN $scoped",
                    {"scoped": [ensure_record_id(i) for i in scoped_sources]},
                )
            items["sources"] = [str(item["id"]) for item in result] if result else []

        logger.info(f"Collected {len(items['sources'])} sources for rebuild")

    if include_notes:
        if mode == "existing":
            base = "SELECT id FROM note WHERE embedding != none AND array::len(embedding) > 0"
        else:  # mode == "all"
            base = "SELECT id FROM note WHERE content != none AND string::trim(content) != ''"
        if scoped_notes is not None:
            result = await repo_query(
                f"{base} AND id IN $scoped",
                {"scoped": [ensure_record_id(i) for i in scoped_notes]},
            )
        else:
            result = await repo_query(base)

        items["notes"] = [str(item["id"]) for item in result] if result else []
        logger.info(f"Collected {len(items['notes'])} notes for rebuild")

    if include_insights:
        if mode == "existing":
            base = "SELECT id FROM source_insight WHERE embedding != none AND array::len(embedding) > 0"
        else:  # mode == "all"
            base = "SELECT id FROM source_insight WHERE content != none AND string::trim(content) != ''"
        if scoped_sources is not None:
            result = await repo_query(
                f"{base} AND source IN $scoped",
                {"scoped": [ensure_record_id(i) for i in scoped_sources]},
            )
        else:
            result = await repo_query(base)

        items["insights"] = [str(item["id"]) for item in result] if result else []
        logger.info(f"Collected {len(items['insights'])} insights for rebuild")

    return items


def _submit_embedding_jobs(
    kind: str, command_name: str, id_field: str, item_ids: List[str]
) -> Tuple[int, int]:
    """
    Submit one embedding command per item, logging progress every 50 items.

    Returns:
        (submitted_count, failed_count)
    """
    logger.info(f"\nSubmitting {len(item_ids)} {kind} embedding jobs...")
    submitted = 0
    failed = 0
    for idx, item_id in enumerate(item_ids, 1):
        try:
            submit_command(
                "open_notebook",
                command_name,
                {id_field: item_id},
            )
            submitted += 1

            if idx % 50 == 0 or idx == len(item_ids):
                logger.info(f"  Progress: {idx}/{len(item_ids)} {kind} jobs submitted")

        except Exception as e:
            logger.error(f"Failed to submit {command_name} for {item_id}: {e}")
            failed += 1

    return submitted, failed


@command("rebuild_embeddings", app="open_notebook", retry=None)
async def rebuild_embeddings_command(
    input_data: RebuildEmbeddingsInput,
) -> RebuildEmbeddingsOutput:
    """
    Rebuild embeddings for sources, notes, and/or insights.

    This command submits individual embedding jobs for each item:
    - embed_source for sources
    - embed_note for notes
    - embed_insight for insights

    The command returns after submitting all jobs. Actual embedding
    happens asynchronously via the individual commands (which have
    their own retry strategies).

    Retry Strategy:
    - Retries disabled (retry=None) for this coordinator command
    - Individual embed_* commands handle their own retries
    """
    start_time = time.time()

    try:
        logger.info("=" * 60)
        logger.info(f"Starting embedding rebuild with mode={input_data.mode}")
        logger.info(
            f"Include: sources={input_data.include_sources}, notes={input_data.include_notes}, insights={input_data.include_insights}"
        )
        logger.info("=" * 60)

        # Check embedding model availability (fail fast)
        EMBEDDING_MODEL = await model_manager.get_embedding_model()
        if not EMBEDDING_MODEL:
            raise ValueError(
                "No embedding model configured. Please configure one in the Models section."
            )

        logger.info(f"Embedding model configured: {EMBEDDING_MODEL}")

        # Collect items to process (returns IDs only) — scoped to the
        # permitted notebooks recorded at submit time (T5)
        items = await collect_items_for_rebuild(
            input_data.mode,
            input_data.include_sources,
            input_data.include_notes,
            input_data.include_insights,
            notebook_ids=input_data.notebook_ids,
        )

        total_items = (
            len(items["sources"]) + len(items["notes"]) + len(items["insights"])
        )
        logger.info(f"Total items to rebuild: {total_items}")

        if total_items == 0:
            logger.warning("No items found to rebuild")
            return RebuildEmbeddingsOutput(
                success=True,
                total_items=0,
                jobs_submitted=0,
                failed_submissions=0,
                processing_time=time.time() - start_time,
            )

        # Submit one embedding command per item, per kind
        sources_submitted, sources_failed = _submit_embedding_jobs(
            "source", "embed_source", "source_id", items["sources"]
        )
        notes_submitted, notes_failed = _submit_embedding_jobs(
            "note", "embed_note", "note_id", items["notes"]
        )
        insights_submitted, insights_failed = _submit_embedding_jobs(
            "insight", "embed_insight", "insight_id", items["insights"]
        )
        failed_submissions = sources_failed + notes_failed + insights_failed

        processing_time = time.time() - start_time
        jobs_submitted = sources_submitted + notes_submitted + insights_submitted

        logger.info("=" * 60)
        logger.info("REBUILD JOBS SUBMITTED")
        logger.info(f"  Total jobs submitted: {jobs_submitted}/{total_items}")
        logger.info(f"  Sources: {sources_submitted}")
        logger.info(f"  Notes: {notes_submitted}")
        logger.info(f"  Insights: {insights_submitted}")
        logger.info(f"  Failed submissions: {failed_submissions}")
        logger.info(f"  Submission time: {processing_time:.2f}s")
        logger.info("  Note: Actual embedding happens asynchronously")
        logger.info("=" * 60)

        return RebuildEmbeddingsOutput(
            success=True,
            total_items=total_items,
            jobs_submitted=jobs_submitted,
            failed_submissions=failed_submissions,
            sources_submitted=sources_submitted,
            notes_submitted=notes_submitted,
            insights_submitted=insights_submitted,
            processing_time=processing_time,
        )

    except Exception as e:
        processing_time = time.time() - start_time
        logger.error(f"Rebuild embeddings failed: {e}")
        logger.exception(e)

        return RebuildEmbeddingsOutput(
            success=False,
            total_items=0,
            jobs_submitted=0,
            failed_submissions=0,
            processing_time=processing_time,
            error_message=str(e),
        )
