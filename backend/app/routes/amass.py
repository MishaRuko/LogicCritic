import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Source
from app.routes.sources import source_with_excerpts
from app.routes.workspaces import require_workspace
from app.schemas import (
    AmassImportRequest,
    AmassImportResponse,
    AmassRefreshResponse,
    AmassSearchRequest,
    AmassSearchResponse,
    AmassSearchResult,
)
from app.services.amass import AmassClient, AmassError, AmassNotConfigured, get_amass_client
from app.services.amass_import import (
    amass_http_error,
    import_biomed_record,
    refresh_retraction_status,
)

router = APIRouter(tags=["amass"])

ABSTRACT_PREVIEW_CHARS = 600
MAX_AUTHORS_SHOWN = 8


def amass_client() -> AmassClient:
    """The shared Amass client, or a 503 when no API key is configured."""
    try:
        return get_amass_client()
    except AmassNotConfigured as error:
        raise amass_http_error(error) from error


@router.post("/integrations/amass/search", response_model=AmassSearchResponse)
async def search_amass(
    payload: AmassSearchRequest, client: AmassClient = Depends(amass_client)
) -> AmassSearchResponse:
    """Search BiomedCore. Nothing is saved: pick a result and import it into a workspace."""
    try:
        records = await client.search_biomedcore(
            payload.query,
            payload.limit,
            min_publication_date=payload.min_publication_date,
            max_publication_date=payload.max_publication_date,
            min_citation_count=payload.min_citation_count,
            is_retracted=payload.is_retracted,
        )
    except AmassError as error:
        raise amass_http_error(error) from error
    return AmassSearchResponse(
        results=[
            AmassSearchResult(
                amass_id=record.amass_id,
                pmid=record.pmid,
                pmcid=record.pmcid,
                doi=record.doi,
                url=record.url,
                title=record.title,
                abstract_preview=_preview(record.abstract),
                authors=record.authors[:MAX_AUTHORS_SHOWN],
                journal=record.journal,
                publication_date=record.publication_date,
                citation_count=record.citation_count,
                is_retracted=record.is_retracted,
                has_fulltext=record.has_fulltext,
            )
            for record in records
        ]
    )


@router.post(
    "/workspaces/{workspace_id}/integrations/amass/import", response_model=AmassImportResponse
)
async def import_from_amass(
    workspace_id: uuid.UUID,
    payload: AmassImportRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    client: AmassClient = Depends(amass_client),
) -> AmassImportResponse:
    """Import one BiomedCore record as a source. 201 when new, 200 when already in the workspace."""
    await require_workspace(workspace_id, session)
    result = await import_biomed_record(
        session,
        client,
        workspace_id,
        amass_id=payload.amass_id,
        pmid=payload.pmid,
        doi=payload.doi,
        include_fulltext=payload.include_fulltext,
    )
    response.status_code = (
        status.HTTP_200_OK if result.already_imported else status.HTTP_201_CREATED
    )
    return AmassImportResponse(
        source=source_with_excerpts(result.source, result.excerpts),
        already_imported=result.already_imported,
        retracted=result.retracted,
    )


@router.post("/sources/{source_id}/amass/refresh", response_model=AmassRefreshResponse)
async def refresh_amass_source(
    source_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    client: AmassClient = Depends(amass_client),
) -> AmassRefreshResponse:
    """Re-read an imported record from Amass and invalidate the source if it was retracted."""
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    retracted, recorded = await refresh_retraction_status(session, client, source)
    return AmassRefreshResponse(retracted=retracted, newly_invalidated=recorded)


def _preview(text: str | None) -> str | None:
    if not text:
        return None
    return (
        text
        if len(text) <= ABSTRACT_PREVIEW_CHARS
        else text[:ABSTRACT_PREVIEW_CHARS].rstrip() + "..."
    )
