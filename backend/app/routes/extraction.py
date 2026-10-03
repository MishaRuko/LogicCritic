import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Source
from app.schemas import ExtractionJobResponse, SourceExtractionRequest
from app.services.extraction_jobs import cancel_extraction_job, create_extraction_job

router = APIRouter(tags=["extraction"])


@router.post("/sources/{source_id}/extract", response_model=ExtractionJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def extract_source(
    source_id: uuid.UUID,
    payload: SourceExtractionRequest,
    session: AsyncSession = Depends(get_session),
) -> ExtractionJobResponse:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return await create_extraction_job(session, source, payload)


@router.get("/extraction-jobs/{job_id}", response_model=ExtractionJobResponse)
async def get_extraction_job(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ExtractionJobResponse:
    from app.models import ExtractionJob

    job = await session.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Extraction job not found")
    return job


@router.delete("/extraction-jobs/{job_id}", response_model=ExtractionJobResponse)
async def cancel_extraction(job_id: uuid.UUID, session: AsyncSession = Depends(get_session)) -> ExtractionJobResponse:
    from app.models import ExtractionJob

    job = await session.get(ExtractionJob, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Extraction job not found")
    return await cancel_extraction_job(session, job)
