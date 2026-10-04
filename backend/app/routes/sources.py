import asyncio
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import Excerpt, GraphEvent, Source, SourceValidity
from app.routes.workspaces import require_workspace
from app.schemas import (
    ExcerptResponse,
    SourceResponse,
    SourceValidityRequest,
    SourceValidityResponse,
    SourceWithExcerptsResponse,
)
from app.services.pdf_ingestion import PdfIngestionError, parse_pdf
from app.services.source_store import DuplicateSource, NewSource, store_source
from app.services.text_ingestion import parse_structured_text

router = APIRouter(tags=["sources"])

TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
TEXT_MIME_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}
PDF_MIME_TYPES = {"application/pdf"}
UNSUPPORTED_UPLOAD = "Only UTF-8 .txt and Markdown files and text-based PDFs are supported."


def source_with_excerpts(source: Source, excerpts: list[Excerpt]) -> SourceWithExcerptsResponse:
    payload = SourceResponse.model_validate(source).model_dump()
    payload["metadata_"] = payload.pop("metadata")
    return SourceWithExcerptsResponse(
        **payload, excerpts=[ExcerptResponse.model_validate(excerpt) for excerpt in excerpts]
    )


def is_supported_text_upload(upload: UploadFile, filename: str) -> bool:
    return (
        upload.content_type in TEXT_MIME_TYPES or Path(filename).suffix.lower() in TEXT_EXTENSIONS
    )


def is_pdf_upload(upload: UploadFile, filename: str) -> bool:
    return upload.content_type in PDF_MIME_TYPES or Path(filename).suffix.lower() == ".pdf"


def new_text_source(filename: str, content_type: str | None, content: bytes) -> NewSource:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=422,
            detail="Text uploads must use UTF-8 encoding",
        ) from error
    if not text.strip():
        raise HTTPException(status_code=422, detail="Source is empty")
    excerpts = parse_structured_text(text)
    if not excerpts:
        raise HTTPException(status_code=422, detail="No text excerpts found")
    return NewSource(
        kind="document",
        origin="upload",
        title=Path(filename).stem,
        mime_type=content_type or "text/plain",
        filename=filename,
        content=content,
        excerpts=excerpts,
        metadata={"parser": "structured_text_v1"},
    )


async def new_pdf_source(filename: str, content: bytes) -> NewSource:
    try:
        # Parsing is CPU-bound, so keep it off the event loop.
        parsed = await asyncio.to_thread(parse_pdf, content)
    except PdfIngestionError as error:
        code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE if error.code == "too_many_pages" else 422
        raise HTTPException(status_code=code, detail=error.message) from error
    return NewSource(
        kind="document",
        origin="upload",
        title=parsed.title or Path(filename).stem,
        mime_type="application/pdf",
        filename=filename,
        content=content,
        excerpts=parsed.excerpts,
        metadata=parsed.metadata,
    )


@router.post(
    "/workspaces/{workspace_id}/sources",
    response_model=SourceWithExcerptsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source(
    workspace_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> SourceWithExcerptsResponse:
    await require_workspace(workspace_id, session)
    filename = Path(file.filename or "upload.txt").name
    is_pdf = is_pdf_upload(file, filename)

    if not is_pdf and not is_supported_text_upload(file, filename):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=UNSUPPORTED_UPLOAD
        )

    content = await file.read(get_settings().max_upload_bytes + 1)
    if len(content) > get_settings().max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large"
        )

    new = (
        await new_pdf_source(filename, content)
        if is_pdf
        else new_text_source(filename, file.content_type, content)
    )
    try:
        source, excerpts = await store_source(session, workspace_id, new)
    except DuplicateSource as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "This source already exists in the workspace",
                "source_id": str(error.source_id),
            },
        ) from error

    return source_with_excerpts(source, excerpts)


@router.get("/workspaces/{workspace_id}/sources", response_model=list[SourceResponse])
async def list_workspace_sources(
    workspace_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[Source]:
    """Every source in the workspace: upload, Amass import or an agent run."""
    await require_workspace(workspace_id, session)
    return list(
        await session.scalars(
            select(Source)
            .where(Source.workspace_id == workspace_id)
            .order_by(Source.created_at, Source.id)
        )
    )


@router.get("/sources/{source_id}", response_model=SourceResponse)
async def get_source(source_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> Source:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return source


@router.get("/sources/{source_id}/excerpts", response_model=list[ExcerptResponse])
async def list_source_excerpts(
    source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[Excerpt]:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return list(
        await session.scalars(
            select(Excerpt).where(Excerpt.source_id == source_id).order_by(Excerpt.sequence)
        )
    )


@router.post("/sources/{source_id}/validity", response_model=SourceValidityResponse)
async def record_source_validity(
    source_id: uuid.UUID,
    payload: SourceValidityRequest,
    session: AsyncSession = Depends(get_session),
) -> SourceValidity:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if payload.provenance.actor_type != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only a user can change source validity"
        )

    existing_event = await session.scalar(
        select(GraphEvent).where(
            GraphEvent.workspace_id == source.workspace_id,
            GraphEvent.idempotency_key == payload.idempotency_key,
        )
    )
    if existing_event is not None:
        if existing_event.event_type != "source_validity_recorded":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Idempotency key is already in use"
            )
        validity = await session.get(
            SourceValidity, uuid.UUID(existing_event.payload["validity_id"])
        )
        if validity is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Recorded validity is unavailable"
            )
        return validity

    validity = SourceValidity(
        source_id=source.id,
        status=payload.status,
        reason=payload.reason,
        provenance=payload.provenance.model_dump(mode="json", exclude_none=True),
    )
    session.add(validity)
    await session.flush()
    session.add(
        GraphEvent(
            workspace_id=source.workspace_id,
            event_type="source_validity_recorded",
            idempotency_key=payload.idempotency_key,
            payload={
                "source_id": str(source.id),
                "validity_id": str(validity.id),
                "status": payload.status,
            },
            provenance=payload.provenance.model_dump(mode="json", exclude_none=True),
        )
    )
    await session.commit()
    await session.refresh(validity)
    return validity


@router.get("/sources/{source_id}/validity", response_model=SourceValidityResponse)
async def get_source_validity(
    source_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> SourceValidity:
    """The latest validity decision, whoever made it (a user, or an integration such as Amass)."""
    latest = await session.scalar(
        select(SourceValidity)
        .where(SourceValidity.source_id == source_id)
        .order_by(SourceValidity.created_at.desc())
        .limit(1)
    )
    if latest is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No validity decision recorded"
        )
    return latest
