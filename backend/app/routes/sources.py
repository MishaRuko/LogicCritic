import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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
from app.services.text_ingestion import parse_structured_text

router = APIRouter(tags=["sources"])

TEXT_EXTENSIONS = {".md", ".markdown", ".txt"}
TEXT_MIME_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}


def is_supported_text_upload(upload: UploadFile, filename: str) -> bool:
    return upload.content_type in TEXT_MIME_TYPES or Path(filename).suffix.lower() in TEXT_EXTENSIONS


@router.post(
    "/workspaces/{workspace_id}/sources",
    response_model=SourceWithExcerptsResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_text_source(
    workspace_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> SourceWithExcerptsResponse:
    await require_workspace(workspace_id, session)
    filename = Path(file.filename or "upload.txt").name

    if not is_supported_text_upload(file, filename):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only UTF-8 .txt and Markdown uploads are implemented. PDF ingestion is not implemented yet.",
        )

    content = await file.read(get_settings().max_upload_bytes + 1)
    if len(content) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Upload is too large")

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Text uploads must use UTF-8 encoding",
        ) from error

    if not text.strip():
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Source is empty")

    parsed_excerpts = parse_structured_text(text)
    if not parsed_excerpts:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="No text excerpts found")

    source_id = uuid.uuid4()
    content_hash = hashlib.sha256(content).hexdigest()
    storage_key = f"{workspace_id}/{source_id}/{filename}"
    storage_path = Path(get_settings().upload_dir) / storage_key
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(content)

    source = Source(
        id=source_id,
        workspace_id=workspace_id,
        kind="document",
        title=Path(filename).stem,
        origin="upload",
        mime_type=file.content_type or "text/plain",
        original_filename=filename,
        storage_key=storage_key,
        content_hash=content_hash,
        metadata_={"parser": "structured_text_v1"},
    )
    excerpts = [
        Excerpt(source_id=source_id, text=item.text, locator=item.locator, sequence=item.sequence)
        for item in parsed_excerpts
    ]

    try:
        session.add(source)
        # Excerpts are constructed by the parser rather than an ORM relationship,
        # so persist their immutable parent before bulk-inserting children.
        await session.flush()
        session.add_all(excerpts)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        storage_path.unlink(missing_ok=True)
        existing = await session.scalar(
            select(Source).where(
                Source.workspace_id == workspace_id,
                Source.content_hash == content_hash,
            )
        )
        if existing is None:
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"message": "This source already exists in the workspace", "source_id": str(existing.id)},
        ) from error

    await session.refresh(source)
    for excerpt in excerpts:
        await session.refresh(excerpt)
    source_payload = SourceResponse.model_validate(source).model_dump()
    source_payload["metadata_"] = source_payload.pop("metadata")
    return SourceWithExcerptsResponse(
        **source_payload,
        excerpts=[ExcerptResponse.model_validate(excerpt) for excerpt in excerpts],
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only a user can change source validity")

    existing_event = await session.scalar(
        select(GraphEvent).where(
            GraphEvent.workspace_id == source.workspace_id,
            GraphEvent.idempotency_key == payload.idempotency_key,
        )
    )
    if existing_event is not None:
        if existing_event.event_type != "source_validity_recorded":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key is already in use")
        validity = await session.get(SourceValidity, uuid.UUID(existing_event.payload["validity_id"]))
        if validity is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Recorded validity is unavailable")
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
            payload={"source_id": str(source.id), "validity_id": str(validity.id), "status": payload.status},
            provenance=payload.provenance.model_dump(mode="json", exclude_none=True),
        )
    )
    await session.commit()
    await session.refresh(validity)
    return validity
