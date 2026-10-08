import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_session
from app.models import (
    AgentRun,
    Excerpt,
    ExperimentProtocol,
    ExperimentRun,
    ExtractionJob,
    GraphEvent,
    Source,
    SourceValidity,
)
from app.routes.workspaces import require_workspace
from app.services.experiments import extract_source_protocol, live_directory
from app.services.research_state import research_fingerprint, verification_state

router = APIRouter(tags=["experiments"])


class ProtocolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workspace_id: uuid.UUID
    source_id: uuid.UUID
    protocol: dict
    step_excerpts: dict
    extraction_method: str
    approved_at: datetime | None
    created_at: datetime


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    workspace_id: uuid.UUID
    protocol_id: uuid.UUID
    status: str
    mode: str
    filename: str
    result: dict
    error: str | None
    created_at: datetime
    completed_at: datetime | None


class ExtractRequest(BaseModel):
    source_id: uuid.UUID


async def agent_suggestion(
    session: AsyncSession, workspace_id: uuid.UUID, fingerprint: str
) -> dict | None:
    """The protocol of the most recent finished agent run, if that run handed one over.

    Only the latest run counts: an earlier run's protocol answers an earlier question, so it is
    never offered once a later run has finished without one.
    """
    run = await session.scalar(
        select(AgentRun)
        .where(AgentRun.workspace_id == workspace_id, AgentRun.status == "succeeded")
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
        .limit(1)
    )
    if run is None:
        return None
    source = await session.scalar(
        select(Source)
        .where(
            Source.workspace_id == workspace_id,
            Source.origin == "agent",
            Source.metadata_["parser"].astext == "agent_protocol_v1",
            Source.metadata_["run_id"].astext == str(run.id),
        )
        .order_by(Source.created_at.desc())
        .limit(1)
    )
    if source is None:
        return None
    prepared = await session.scalar(
        select(ExperimentProtocol)
        .where(ExperimentProtocol.source_id == source.id)
        .order_by(ExperimentProtocol.created_at.desc())
        .limit(1)
    )
    return {
        "run_id": str(run.id),
        "source_id": str(source.id),
        "protocol_id": str(prepared.id) if prepared else None,
        "current": bool(prepared and prepared.research_fingerprint == fingerprint),
    }


async def require_research_ready(session: AsyncSession, workspace_id: uuid.UUID) -> str:
    await require_workspace(workspace_id, session)
    fingerprint = await research_fingerprint(session, workspace_id)
    active = await session.scalar(
        select(ExtractionJob.id)
        .where(
            ExtractionJob.workspace_id == workspace_id,
            ExtractionJob.status.in_(["queued", "running"]),
        )
        .limit(1)
    )
    if active:
        raise HTTPException(
            409,
            "Wait for research extraction to finish before preparing or running an experiment.",
        )
    return fingerprint


@router.get("/workspaces/{workspace_id}/experiments")
async def list_experiments(workspace_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    await require_workspace(workspace_id, session)
    verified, fingerprint = await verification_state(session, workspace_id)
    check = await session.scalar(
        select(GraphEvent)
        .where(GraphEvent.workspace_id == workspace_id, GraphEvent.event_type == "verification_run")
        .order_by(GraphEvent.created_at.desc(), GraphEvent.id.desc())
        .limit(1)
    )
    protocols = list(
        await session.scalars(
            select(ExperimentProtocol)
            .where(ExperimentProtocol.workspace_id == workspace_id)
            .order_by(ExperimentProtocol.created_at.desc())
        )
    )
    runs = list(
        await session.scalars(
            select(ExperimentRun)
            .where(ExperimentRun.workspace_id == workspace_id)
            .order_by(ExperimentRun.created_at.desc())
        )
    )
    return {
        "verified": verified,
        "verification": {
            "verification_event_id": str(check.id),
            "rules_run": check.payload.get("rules", []),
            **{
                key: check.payload.get(key, 0)
                for key in (
                    "issues_opened",
                    "issues_resolved",
                    "obligations_opened",
                    "obligations_resolved",
                )
            },
        }
        if check and verified
        else None,
        "protocols": [
            {
                **ProtocolResponse.model_validate(p).model_dump(mode="json"),
                "current": p.research_fingerprint == fingerprint,
            }
            for p in protocols
        ],
        "runs": [RunResponse.model_validate(r) for r in runs],
        "suggested": await agent_suggestion(session, workspace_id, fingerprint),
    }


@router.post(
    "/workspaces/{workspace_id}/protocols", response_model=ProtocolResponse, status_code=201
)
async def prepare_protocol(
    workspace_id: uuid.UUID, payload: ExtractRequest, session: AsyncSession = Depends(get_session)
):
    await require_workspace(workspace_id, session)
    fingerprint = await require_research_ready(session, workspace_id)
    source = await session.get(Source, payload.source_id)
    if source is None or source.workspace_id != workspace_id or source.origin == "lab-vision":
        raise HTTPException(404, "Research source not found in this workspace")
    validity = await session.scalar(
        select(SourceValidity)
        .where(SourceValidity.source_id == source.id)
        .order_by(SourceValidity.created_at.desc())
        .limit(1)
    )
    if validity and validity.status == "invalidated":
        raise HTTPException(409, "An invalidated source cannot be used as an experiment protocol.")
    excerpts = list(
        await session.scalars(
            select(Excerpt).where(Excerpt.source_id == source.id).order_by(Excerpt.sequence)
        )
    )
    protocol_id = uuid.uuid4()
    try:
        protocol, method, citations = await asyncio.to_thread(
            extract_source_protocol,
            source,
            excerpts,
            str(protocol_id),
            source.title or source.original_filename,
        )
    except (ValueError, RuntimeError) as error:
        raise HTTPException(422, str(error)) from error
    # Model calls can take time; reject if the research changed during extraction.
    session.expire_all()
    if await require_research_ready(session, workspace_id) != fingerprint:
        raise HTTPException(
            409, "Research changed during protocol extraction. Retry with the current research."
        )
    row = ExperimentProtocol(
        id=protocol_id,
        workspace_id=workspace_id,
        source_id=payload.source_id,
        protocol=protocol.model_dump(mode="json"),
        step_excerpts=citations,
        research_fingerprint=fingerprint,
        extraction_method=method,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.post(
    "/workspaces/{workspace_id}/protocols/{protocol_id}/approve", response_model=ProtocolResponse
)
async def approve_protocol(
    workspace_id: uuid.UUID, protocol_id: uuid.UUID, session: AsyncSession = Depends(get_session)
):
    fingerprint = await require_research_ready(session, workspace_id)
    protocol = await session.get(ExperimentProtocol, protocol_id)
    if protocol is None or protocol.workspace_id != workspace_id:
        raise HTTPException(404, "Protocol not found")
    if fingerprint != protocol.research_fingerprint:
        raise HTTPException(409, "Research changed. Extract a new protocol before approving it.")
    protocol.approved_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(protocol)
    return protocol


@router.post(
    "/workspaces/{workspace_id}/experiment-runs", response_model=RunResponse, status_code=202
)
async def start_experiment(
    workspace_id: uuid.UUID,
    protocol_id: uuid.UUID | None = Form(None),
    source_id: uuid.UUID | None = Form(None),
    mode: Literal["demo", "replay", "video", "live"] = Form(...),
    file: UploadFile | None = File(None),
    partial_recording: bool = Form(False),
    session: AsyncSession = Depends(get_session),
):
    fingerprint = await require_research_ready(session, workspace_id)
    protocol = await session.get(ExperimentProtocol, protocol_id) if protocol_id else None
    if protocol_id and (protocol is None or protocol.workspace_id != workspace_id):
        raise HTTPException(404, "Protocol not found")
    if protocol is None and source_id is None:
        # Use the latest agent protocol, refreshing it automatically if research changed.
        suggestion = await agent_suggestion(session, workspace_id, fingerprint)
        if suggestion:
            source_id = uuid.UUID(suggestion["source_id"])
            if suggestion["current"]:
                protocol = await session.get(
                    ExperimentProtocol, uuid.UUID(suggestion["protocol_id"])
                )
    if protocol is None or protocol.research_fingerprint != fingerprint:
        selected_source = source_id or (protocol.source_id if protocol else None)
        if selected_source is None:
            raise HTTPException(422, "Choose the research source for this experiment.")
        protocol = await prepare_protocol(
            workspace_id, ExtractRequest(source_id=selected_source), session
        )
    # Starting a run authorizes using the grounded methodology; explicit review remains optional.
    protocol_id = protocol.id
    if mode in ("video", "live") and not get_settings().claude_api_key:
        raise HTTPException(
            503,
            "Video analysis requires CLAUDE_API_KEY. "
            "Use the sample run or replay saved observations for the demo.",
        )
    filename = "Synthetic sample observations"
    storage_key = None
    run_id = uuid.uuid4()
    if mode == "live":
        # Frames and the recording arrive later, from the camera page, while the run is live.
        filename = "Live session"
    elif mode != "demo":
        if file is None:
            raise HTTPException(422, "Attach a video or observations JSONL file.")
        filename = Path(file.filename or "recording").name
        allowed = {".mp4", ".mov", ".webm", ".avi", ".m4v"} if mode == "video" else {".jsonl"}
        if Path(filename).suffix.lower() not in allowed:
            raise HTTPException(
                415, "Use an MP4, MOV, WebM or AVI video, or a JSONL observations replay."
            )
        content = await file.read(get_settings().max_experiment_upload_bytes + 1)
        if len(content) > get_settings().max_experiment_upload_bytes:
            raise HTTPException(413, "Experiment uploads must be 100 MB or smaller.")
        if not content:
            raise HTTPException(422, "The attachment is empty.")
        storage_key = f"{workspace_id}/{run_id}/{filename}"
        path = Path(get_settings().upload_dir) / storage_key
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, content)
    run = ExperimentRun(
        id=run_id,
        workspace_id=workspace_id,
        protocol_id=protocol_id,
        mode=mode,
        filename=filename,
        storage_key=storage_key,
        result={
            "coverage": "excerpt" if partial_recording else "complete_recording",
            **({"live": {"observations": [], "deviations": []}} if mode == "live" else {}),
        },
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


RECORDING_TYPES = {"video/webm": ".webm", "video/mp4": ".mp4", "video/quicktime": ".mov"}


async def live_run(session: AsyncSession, run_id: uuid.UUID) -> ExperimentRun:
    """A live session that is still accepting frames and recording."""
    run = await session.get(ExperimentRun, run_id)
    if run is None or run.mode != "live":
        raise HTTPException(404, "Live session not found")
    if run.status not in ("queued", "running") or (live_directory(run) / "ended").exists():
        raise HTTPException(409, "This live session has ended.")
    return run


@router.get("/experiment-runs/{run_id}", response_model=RunResponse)
async def get_experiment_run(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(404, "Experiment run not found")
    return run


@router.post("/experiment-runs/{run_id}/frames")
async def receive_live_frames(
    run_id: uuid.UUID,
    frames: list[UploadFile] = File(...),
    timestamps: list[float] = Form(...),
    session: AsyncSession = Depends(get_session),
):
    """Still frames from the camera page, each with its time in seconds since the stream began."""
    run = await live_run(session, run_id)
    settings = get_settings()
    if len(frames) != len(timestamps):
        raise HTTPException(422, "Send one timestamp per frame.")
    directory = live_directory(run) / "frames"
    directory.mkdir(parents=True, exist_ok=True)
    saved = 0
    for frame, seconds in zip(frames, timestamps, strict=True):
        if not 0 <= seconds <= settings.live_max_seconds:
            continue  # past the session cap; the analysis stops there anyway
        content = await frame.read(settings.max_live_frame_bytes + 1)
        if not content or len(content) > settings.max_live_frame_bytes:
            raise HTTPException(413, "Each frame must be a JPEG of at most 2 MB.")
        # Write then rename, so the analysis never reads a half-written frame.
        target = directory / f"{round(seconds * 1000):09d}.jpg"
        partial = target.with_suffix(".part")
        await asyncio.to_thread(partial.write_bytes, content)
        partial.rename(target)
        saved += 1
    return {"received": saved, "max_seconds": settings.live_max_seconds}


@router.post("/experiment-runs/{run_id}/recording")
async def receive_recording_chunk(
    run_id: uuid.UUID,
    request: Request,
    index: int = Query(ge=0),
    session: AsyncSession = Depends(get_session),
):
    """One piece of the camera's own recording, appended in order to a single file.

    Pieces are small (each request stays far below Cloudflare's 100 MB limit) and streamed to
    disk, so a long session never sits in memory. Resending a piece already stored is harmless.
    """
    run = await live_run(session, run_id)
    settings = get_settings()
    kind = (request.headers.get("content-type") or "").split(";")[0].strip()
    if kind not in RECORDING_TYPES:
        raise HTTPException(415, "Send the recording as WebM or MP4.")
    directory = live_directory(run)
    directory.mkdir(parents=True, exist_ok=True)
    recording = directory / f"recording{RECORDING_TYPES[kind]}"
    count = directory / "recording.pieces"
    stored = int(count.read_text()) if count.exists() else 0
    if index < stored:
        return {"stored": stored}
    if index > stored:
        raise HTTPException(409, f"Recording piece {stored} is missing; send it first.")
    size = recording.stat().st_size if recording.exists() else 0
    received = 0
    with recording.open("ab") as file:
        async for block in request.stream():
            received += len(block)
            if received > settings.max_live_chunk_bytes or (
                size + received > settings.max_live_recording_bytes
            ):
                file.truncate(size)  # drop the partial piece; the file stays consistent
                raise HTTPException(413, "The recording piece or the whole recording is too large.")
            file.write(block)
    count.write_text(str(stored + 1))
    if run.storage_key is None:
        run.storage_key = str(recording.relative_to(get_settings().upload_dir))
        run.filename = f"Live session{recording.suffix}"
        await session.commit()
    return {"stored": stored + 1}


@router.post("/experiment-runs/{run_id}/stop", response_model=RunResponse)
async def stop_live_session(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """End the stream. The analysis finishes the frames it has, then completes the run."""
    run = await session.get(ExperimentRun, run_id)
    if run is None or run.mode != "live":
        raise HTTPException(404, "Live session not found")
    directory = live_directory(run)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "ended").touch()
    return run


@router.get("/experiment-runs/{run_id}/recording")
async def experiment_recording(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    run = await session.get(ExperimentRun, run_id)
    if run is None or run.mode not in ("video", "live") or not run.storage_key:
        raise HTTPException(404, "Experiment recording not found")
    path = Path(get_settings().upload_dir) / run.storage_key
    if not path.is_file():
        raise HTTPException(404, "Experiment recording is unavailable")
    return FileResponse(path, filename=run.filename, content_disposition_type="inline")


@router.post("/records", status_code=201)
async def publish_record(payload: dict, session: AsyncSession = Depends(get_session)):
    import json

    from sqlalchemy.dialects.postgresql import insert

    from app.models import ExperimentRecord
    from app.services.experiment_records import validate_record

    if len(json.dumps(payload).encode()) > 2_000_000:
        raise HTTPException(413, "Experiment records must be smaller than 2 MB.")
    try:
        run_id = uuid.UUID(payload.get("experimentId", ""))
    except (ValueError, TypeError, AttributeError) as error:
        raise HTTPException(422, "The report needs a valid experiment run id.") from error
    run = await session.get(ExperimentRun, run_id)
    if run is None:
        raise HTTPException(404, "Experiment run not found")
    if run.status != "succeeded":
        raise HTTPException(409, "Finish the experiment analysis before generating a record.")
    protocol = await session.get(ExperimentProtocol, run.protocol_id)
    try:
        validate_record(payload, run, protocol)
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise HTTPException(422, str(error)) from error
    record_hash = payload["recordHash"]
    await session.execute(
        insert(ExperimentRecord)
        .values(record_hash=record_hash, run_id=run_id, record=payload)
        .on_conflict_do_nothing()
    )
    await session.commit()
    return {"path": f"/r/{record_hash[:16]}"}


@router.get("/records/{record_id}")
async def fetch_record(record_id: str, session: AsyncSession = Depends(get_session)):
    import re

    from app.models import ExperimentRecord

    if not re.fullmatch(r"[a-f0-9]{16,64}", record_id):
        raise HTTPException(404, "Experiment record not found")
    row = await session.scalar(
        select(ExperimentRecord).where(ExperimentRecord.record_hash.startswith(record_id))
    )
    if row is None:
        raise HTTPException(404, "Experiment record not found")
    return row.record


@router.get("/experiment-runs/{run_id}/records")
async def run_records(run_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    from app.models import ExperimentRecord

    if await session.get(ExperimentRun, run_id) is None:
        raise HTTPException(404, "Experiment run not found")
    rows = await session.scalars(
        select(ExperimentRecord)
        .where(ExperimentRecord.run_id == run_id)
        .order_by(ExperimentRecord.created_at.desc())
    )
    return [row.record for row in rows]
