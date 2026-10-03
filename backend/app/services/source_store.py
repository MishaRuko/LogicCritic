import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Excerpt, Source
from app.services.text_ingestion import ParsedExcerpt


class DuplicateSource(Exception):
    """The workspace already holds a source with identical content."""

    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(f"Source already exists: {source_id}")
        self.source_id = source_id


@dataclass(frozen=True)
class NewSource:
    """Everything needed to persist a source, whatever it was ingested from."""

    kind: str
    origin: str
    title: str | None
    mime_type: str
    filename: str
    content: bytes
    excerpts: list[ParsedExcerpt]
    external_ids: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    # Set when the stored bytes include volatile fields (retrieval time, citation counts) that
    # must not make the same content look new.
    content_hash: str | None = None


async def store_source(
    session: AsyncSession, workspace_id: uuid.UUID, new: NewSource
) -> tuple[Source, list[Excerpt]]:
    """Save the original bytes, the source row and its immutable excerpts.

    Raises `DuplicateSource` when the workspace already has this exact content.
    """
    source_id = uuid.uuid4()
    content_hash = new.content_hash or hashlib.sha256(new.content).hexdigest()
    storage_key = f"{workspace_id}/{source_id}/{new.filename}"
    storage_path = Path(get_settings().upload_dir) / storage_key
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path.write_bytes(new.content)

    source = Source(
        id=source_id,
        workspace_id=workspace_id,
        kind=new.kind,
        title=new.title,
        origin=new.origin,
        mime_type=new.mime_type,
        original_filename=new.filename,
        storage_key=storage_key,
        content_hash=content_hash,
        external_ids=new.external_ids,
        metadata_=new.metadata,
    )
    excerpts = [
        Excerpt(source_id=source_id, text=item.text, locator=item.locator, sequence=item.sequence)
        for item in new.excerpts
    ]
    try:
        session.add(source)
        # Excerpts are built by a parser rather than an ORM relationship, so persist their
        # immutable parent before bulk-inserting the children.
        await session.flush()
        session.add_all(excerpts)
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        storage_path.unlink(missing_ok=True)
        existing = await session.scalar(
            select(Source).where(
                Source.workspace_id == workspace_id, Source.content_hash == content_hash
            )
        )
        if existing is None:
            raise
        raise DuplicateSource(existing.id) from error

    await session.refresh(source)
    for excerpt in excerpts:
        await session.refresh(excerpt)
    return source, excerpts
