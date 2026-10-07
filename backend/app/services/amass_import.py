import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import AmassCacheEntry, Excerpt, GraphEvent, Source, SourceValidity
from app.services.amass import BIOMEDCORE, AmassClient, AmassError, BiomedRecord
from app.services.source_store import DuplicateSource, NewSource, store_source
from app.services.text_ingestion import ParsedExcerpt, _bounded_spans, parse_structured_text

PARSER = "amass_biomedcore_v3"
MAX_EXCERPT_CHARS = 1500
# A structured-abstract heading on its own line: "BACKGROUND:", "PATIENTS AND METHODS:".
ABSTRACT_HEADING = re.compile(r"^[A-Z][A-Z &/,-]{2,50}:$")
RETRACTION_NOTICE = re.compile(
    r"^(?:this (?:article|publication) has been retracted|retraction(?: notice)?\b|"
    r"the (?:article|paper) (?:has been|was) retracted)",
    re.IGNORECASE,
)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
INTEGRATION_PROVENANCE = {"actor_type": "integration", "actor_id": "amass"}
RETRACTION_REASON = (
    "Amass reports this publication as retracted, based on PubMed retraction notices."
)


@dataclass(frozen=True)
class ImportResult:
    source: Source
    excerpts: list[Excerpt]
    already_imported: bool
    retracted: bool


def amass_http_error(error: AmassError) -> HTTPException:
    """Translate an Amass failure into the API's own response, keeping Amass's message."""
    if error.status == 429:
        headers = {"Retry-After": str(int(error.retry_after))} if error.retry_after else None
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, error.message, headers)
    if error.status == 404:
        return HTTPException(status.HTTP_404_NOT_FOUND, error.message)
    if error.status == 400:
        return HTTPException(422, error.message)
    if error.status == 503:
        return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, error.message)
    if error.status in (401, 403):
        return HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "Amass rejected the configured API key; no source was imported.",
        )
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"Amass request failed: {error.message}")


def record_to_excerpts(record: BiomedRecord) -> list[ParsedExcerpt]:
    """Title, abstract and any full text as excerpts located by `jsonPath` and offsets."""
    excerpts: list[ParsedExcerpt] = []

    def add(text: str, json_path: str, start: int = 0, section: str | None = None) -> None:
        locator: dict[str, int | str] = {
            "jsonPath": json_path,
            "start": start,
            "end": start + len(text),
            "sequence": len(excerpts),
        }
        if section is not None:
            locator["section"] = section
        excerpts.append(ParsedExcerpt(text=text, sequence=len(excerpts), locator=locator))

    if record.title and record.title.strip():
        add(record.title.strip(), "title")
    for field in ("abstract", "fulltext"):
        value = getattr(record, field)
        if not value or not value.strip():
            continue
        for item in parse_structured_text(value, bounded=False):
            section = item.locator.get("section")
            for text, start, label in split_block(item.text, int(item.locator["start"]), section):
                add(text, field, start, label)
    return excerpts


def split_block(text: str, start: int, section: str | None) -> list[tuple[str, int, str | None]]:
    """Cut a long block into excerpts of at most MAX_EXCERPT_CHARS, keeping exact offsets.

    Abstracts use single line breaks, not blank lines, so a whole structured abstract (and any
    retraction notice appended to it) would otherwise be one excerpt that every claim cites.
    Splits fall on a heading line, then a line break, then a sentence boundary. A heading such
    as "RESULTS:" becomes the section label of the excerpts under it.
    """
    lines = text.split("\n")
    if len(text) <= MAX_EXCERPT_CHARS and not any(
        ABSTRACT_HEADING.match(line.strip()) or RETRACTION_NOTICE.match(line.strip())
        for line in lines
    ):
        return [(text, start, section)]

    pieces: list[tuple[int, int, str | None]] = []  # (offset, end, section) within `text`
    offset = 0
    for line in lines:
        line_end = offset + len(line)
        stripped = line.strip()
        if ABSTRACT_HEADING.match(stripped):
            section = stripped.rstrip(":").title()
            pieces.append((offset, line_end, section))  # a heading line, merged below
        elif RETRACTION_NOTICE.match(stripped):
            section = "Retraction Notice"
            for a, b in _sentence_spans(line, offset):
                pieces.append((a, b, section))
        elif stripped:
            for a, b in _sentence_spans(line, offset):
                pieces.append((a, b, section))
        offset = line_end + 1

    excerpts: list[tuple[str, int, str | None]] = []
    group: list[tuple[int, int, str | None]] = []

    def flush() -> None:
        if group:
            first, last = group[0][0], group[-1][1]
            body = text[first:last]
            # drop a heading that is the group's only content
            if body.strip() and not ABSTRACT_HEADING.match(body.strip()):
                excerpts.append((body, start + first, group[-1][2]))
            group.clear()

    for piece in pieces:
        heading = ABSTRACT_HEADING.match(text[piece[0] : piece[1]].strip())
        if group and (
            heading or piece[2] != group[-1][2] or piece[1] - group[0][0] > MAX_EXCERPT_CHARS
        ):
            flush()
        group.append(piece)
    flush()
    return excerpts


def _sentence_spans(line: str, offset: int) -> list[tuple[int, int]]:
    """A line, or sentence-sized pieces of it when it is longer than one excerpt."""
    if len(line) <= MAX_EXCERPT_CHARS:
        return [(offset, offset + len(line))]
    spans, cursor = [], 0
    for match in SENTENCE_BREAK.finditer(line):
        spans.append((offset + cursor, offset + match.start()))
        cursor = match.end()
    spans.append((offset + cursor, offset + len(line)))
    # A single sentence can still exceed the cap; split it at whitespace or hard-cut it.
    return [
        (offset + a, offset + b)
        for start, end in spans
        for a, b in _bounded_spans(line, start - offset, end - offset)
    ]


def content_fingerprint(record: BiomedRecord) -> str:
    """Hash of what makes a record the same paper, ignoring counts and dates that drift."""
    stable = {
        field: getattr(record, field)
        for field in ("amass_id", "pmid", "pmcid", "doi", "title", "abstract", "fulltext")
    }
    return hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()


async def find_imported(
    session: AsyncSession, workspace_id: uuid.UUID, amass_id: str
) -> Source | None:
    return await session.scalar(
        select(Source).where(
            Source.workspace_id == workspace_id,
            Source.origin == "amass",
            Source.external_ids["amass_id"].astext == amass_id,
        )
    )


async def fetch_record(
    session: AsyncSession,
    client: AmassClient,
    amass_id: str,
    *,
    include_fulltext: bool,
    refresh: bool = False,
) -> dict:
    """The raw record, from the cache when fresh, so a repeat import spends no API credits."""
    entry = None if refresh else await session.get(AmassCacheEntry, (BIOMEDCORE, amass_id))
    max_age = timedelta(hours=get_settings().amass_cache_ttl_hours)
    if (
        entry is not None
        and datetime.now(UTC) - entry.fetched_at < max_age
        and (entry.includes_fulltext or not include_fulltext)
    ):
        return entry.record

    raw = await client.get_biomedcore(amass_id, fulltext=include_fulltext)
    # One atomic upsert: a refresh, or two imports of the same paper at once, must not collide.
    values = {
        "record": raw,
        "includes_fulltext": include_fulltext,
        "fetched_at": datetime.now(UTC),
    }
    await session.execute(
        insert(AmassCacheEntry)
        .values(core=BIOMEDCORE, amass_id=amass_id, **values)
        .on_conflict_do_update(index_elements=["core", "amass_id"], set_=values)
    )
    await session.commit()
    return raw


async def resolve_amass_id(
    client: AmassClient, *, amass_id: str | None, pmid: str | None, doi: str | None
) -> str:
    if amass_id:
        return amass_id
    ids = await client.lookup_biomedcore(pmid=pmid, doi=doi)
    if not ids:
        wanted = f"PMID {pmid}" if pmid else f"DOI {doi}"
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Amass has no record for {wanted}.")
    return ids[0]


async def import_biomed_record(
    session: AsyncSession,
    client: AmassClient,
    workspace_id: uuid.UUID,
    *,
    amass_id: str | None = None,
    pmid: str | None = None,
    doi: str | None = None,
    include_fulltext: bool = True,
) -> ImportResult:
    try:
        amass_id = await resolve_amass_id(client, amass_id=amass_id, pmid=pmid, doi=doi)
        existing = await find_imported(session, workspace_id, amass_id)
        if existing is not None:
            return await _existing_result(session, existing)
        raw = await fetch_record(session, client, amass_id, include_fulltext=include_fulltext)
    except AmassError as error:
        raise amass_http_error(error) from error

    record = BiomedRecord.model_validate(raw)
    excerpts = record_to_excerpts(record)
    if not excerpts:
        raise HTTPException(
            422,
            "This Amass record has no title, abstract or full text to import.",
        )

    retrieved_at = datetime.now(UTC).isoformat()
    snapshot = json.dumps(
        {"core": BIOMEDCORE, "retrieved_at": retrieved_at, "record": raw}, indent=2, sort_keys=True
    )
    new = NewSource(
        kind="amass_record",
        origin="amass",
        title=record.title,
        mime_type="application/json",
        filename=f"{record.amass_id}.json",
        content=snapshot.encode("utf-8"),
        excerpts=excerpts,
        external_ids={
            key: value
            for key, value in {
                "amass_id": record.amass_id,
                "pmid": record.pmid,
                "pmcid": record.pmcid,
                "doi": record.doi,
            }.items()
            if value
        },
        metadata={
            "parser": PARSER,
            "core": BIOMEDCORE,
            "retrieved_at": retrieved_at,
            "journal": record.journal,
            "publication_date": record.publication_date,
            "amass_last_update": record.last_update_date,
            "citation_count": record.citation_count,
            "is_retracted": record.is_retracted,
            "has_fulltext": record.has_fulltext,
            "fulltext_imported": bool(record.fulltext),
            "url": record.url,
        },
        content_hash=content_fingerprint(record),
    )
    try:
        source, stored = await store_source(session, workspace_id, new)
    except DuplicateSource as duplicate:
        # The same paper under a different Amass ID, or a concurrent import: return the original.
        existing = await session.get(Source, duplicate.source_id)
        return await _existing_result(session, existing)

    if record.is_retracted:
        await record_retraction(session, source)
    return ImportResult(source, stored, already_imported=False, retracted=bool(record.is_retracted))


async def _existing_result(session: AsyncSession, source: Source) -> ImportResult:
    excerpts = list(
        await session.scalars(
            select(Excerpt).where(Excerpt.source_id == source.id).order_by(Excerpt.sequence)
        )
    )
    latest = await latest_validity(session, source.id)
    return ImportResult(
        source,
        excerpts,
        already_imported=True,
        retracted=latest is not None and latest.status == "invalidated",
    )


async def latest_validity(session: AsyncSession, source_id: uuid.UUID) -> SourceValidity | None:
    return await session.scalar(
        select(SourceValidity)
        .where(SourceValidity.source_id == source_id)
        .order_by(SourceValidity.created_at.desc())
        .limit(1)
    )


async def record_retraction(session: AsyncSession, source: Source) -> bool:
    """Mark the source invalidated once. Returns whether anything was recorded."""
    latest = await latest_validity(session, source.id)
    if latest is not None and latest.status == "invalidated":
        return False
    key = f"amass-retraction:{source.id}"
    already_logged = await session.scalar(
        select(GraphEvent.id).where(
            GraphEvent.workspace_id == source.workspace_id, GraphEvent.idempotency_key == key
        )
    )
    if already_logged is not None:
        return False
    validity = SourceValidity(
        source_id=source.id,
        status="invalidated",
        reason=RETRACTION_REASON,
        provenance=INTEGRATION_PROVENANCE,
    )
    session.add(validity)
    await session.flush()
    session.add(
        GraphEvent(
            workspace_id=source.workspace_id,
            event_type="source_validity_recorded",
            idempotency_key=key,
            payload={
                "source_id": str(source.id),
                "validity_id": str(validity.id),
                "status": "invalidated",
            },
            provenance=INTEGRATION_PROVENANCE,
        )
    )
    await session.commit()
    return True


async def refresh_retraction_status(
    session: AsyncSession, client: AmassClient, source: Source
) -> tuple[bool, bool]:
    """Re-read the record from Amass. Returns (retracted now, newly recorded as invalidated)."""
    amass_id = (source.external_ids or {}).get("amass_id")
    if source.origin != "amass" or not amass_id:
        raise HTTPException(422, "This source did not come from Amass.")
    try:
        raw = await fetch_record(session, client, amass_id, include_fulltext=False, refresh=True)
    except AmassError as error:
        raise amass_http_error(error) from error
    retracted = bool(BiomedRecord.model_validate(raw).is_retracted)
    recorded = await record_retraction(session, source) if retracted else False
    return retracted, recorded
