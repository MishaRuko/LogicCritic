import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import session_factory
from app.models import Excerpt, ExtractionJob, Source
from app.schemas import SourceExtractionRequest
from app.services.extraction import extract_source_to_patch, is_extractable_excerpt


async def create_extraction_job(
    session: AsyncSession,
    source: Source,
    request: SourceExtractionRequest,
) -> ExtractionJob:
    existing = await session.scalar(
        select(ExtractionJob).where(
            ExtractionJob.workspace_id == source.workspace_id,
            ExtractionJob.idempotency_key == request.idempotency_key,
        )
    )
    if existing is not None:
        return existing

    job = ExtractionJob(
        workspace_id=source.workspace_id,
        source_id=source.id,
        idempotency_key=request.idempotency_key,
        model=request.model or get_settings().claude_model,
    )
    session.add(job)
    try:
        await session.commit()
        await session.refresh(job)
    except IntegrityError:
        await session.rollback()
        existing = await session.scalar(
            select(ExtractionJob).where(
                ExtractionJob.workspace_id == source.workspace_id,
                ExtractionJob.idempotency_key == request.idempotency_key,
            )
        )
        if existing is None:
            raise
        return existing
    return job


async def claim_next_extraction_job() -> uuid.UUID | None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        async with session.begin():
            job = await session.scalar(
                select(ExtractionJob)
                .where(
                    ExtractionJob.status == "queued",
                    or_(
                        ExtractionJob.next_attempt_at.is_(None),
                        ExtractionJob.next_attempt_at <= now,
                    ),
                )
                .order_by(ExtractionJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            job.status = "running"
            job.attempts += 1
            job.error = None
            job.started_at = now
            job.heartbeat_at = now
            job.next_attempt_at = None
            return job.id


async def process_extraction_job(job_id: uuid.UUID) -> None:
    settings = get_settings()
    async with session_factory() as session:
        job = await session.get(ExtractionJob, job_id)
        if job is None or job.status != "running":
            return
        source = await session.get(Source, job.source_id)
        if source is None:
            await _finish_failed_job(session, job, "Source no longer exists")
            return

        try:
            excerpts = list(
                await session.scalars(
                    select(Excerpt).where(Excerpt.source_id == source.id).order_by(Excerpt.sequence)
                )
            )
            chunks = chunk_excerpt_ids(
                [
                    excerpt
                    for excerpt in excerpts
                    if is_extractable_excerpt(
                        excerpt,
                        fulltext_available=bool(
                            (source.metadata_ or {}).get("fulltext_imported")
                        ),
                    )
                ],
                settings.max_extraction_context_chars,
            )
            if not chunks:
                raise ValueError("Source has no extractable excerpts")

            job.total_chunks = len(chunks)
            job.completed_chunks = 0
            job.output_patch_ids = []
            await session.commit()

            patch_ids: list[str] = []
            for index, excerpt_ids in enumerate(chunks):
                _, patch = await extract_source_to_patch(
                    session,
                    source,
                    SourceExtractionRequest(
                        idempotency_key=f"extraction-job:{job.id}:chunk:{index}",
                        model=job.model,
                    ),
                    excerpt_ids,
                )
                await session.refresh(job)
                if job.status == "cancelled":
                    return
                if patch is not None:
                    patch_ids.append(str(patch.patch_id))
                job.completed_chunks = index + 1
                job.output_patch_ids = patch_ids.copy()
                job.heartbeat_at = datetime.now(UTC)
                await session.commit()

            if not patch_ids:
                raise ValueError(
                    "Claude extraction produced no graph operations for any source chunk"
                )
            job.status = "succeeded"
            job.completed_at = datetime.now(UTC)
            job.heartbeat_at = job.completed_at
            await session.commit()
        except Exception as error:
            await session.rollback()
            job = await session.get(ExtractionJob, job_id)
            if job is not None:
                await _finish_failed_job(session, job, str(error))


async def _finish_failed_job(session: AsyncSession, job: ExtractionJob, error: str) -> None:
    job.error = error[:4000]
    if job.attempts >= get_settings().extraction_max_attempts:
        job.status = "failed"
        job.completed_at = datetime.now(UTC)
    else:
        job.status = "queued"
        job.next_attempt_at = datetime.now(UTC) + timedelta(
            seconds=get_settings().extraction_retry_base_seconds * 2 ** (job.attempts - 1)
        )
    await session.commit()


async def recover_stale_extraction_jobs() -> int:
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().extraction_stale_after_seconds)
    async with session_factory() as session:
        stale_jobs = list(
            await session.scalars(
                select(ExtractionJob)
                .where(
                    ExtractionJob.status == "running",
                    or_(ExtractionJob.heartbeat_at < cutoff, ExtractionJob.heartbeat_at.is_(None)),
                )
                .with_for_update(skip_locked=True)
            )
        )
        for job in stale_jobs:
            job.status = "queued"
            job.error = "Worker lease expired; job returned to the queue"
            job.next_attempt_at = datetime.now(UTC)
        if stale_jobs:
            await session.commit()
        return len(stale_jobs)


async def cancel_extraction_job(session: AsyncSession, job: ExtractionJob) -> ExtractionJob:
    if job.status in {"succeeded", "failed", "cancelled"}:
        return job
    job.status = "cancelled"
    job.completed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(job)
    return job


def chunk_excerpt_ids(excerpts: list[Excerpt], limit: int) -> list[list[uuid.UUID]]:
    chunks: list[list[uuid.UUID]] = []
    current: list[uuid.UUID] = []
    used = 0
    for excerpt in excerpts:
        length = len(excerpt.text) + 64
        if length > limit:
            raise ValueError(f"Excerpt {excerpt.id} exceeds the extraction context limit")
        if current and used + length > limit:
            chunks.append(current)
            current = []
            used = 0
        current.append(excerpt.id)
        used += length
    if current:
        chunks.append(current)
    return chunks
