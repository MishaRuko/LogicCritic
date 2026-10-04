import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runs import cancel_run, create_run
from app.config import get_settings
from app.database import get_session
from app.models import AgentEvent, AgentRun
from app.routes.workspaces import require_workspace
from app.schemas import AgentEventResponse, AgentRunCreate, AgentRunResponse

router = APIRouter(tags=["agent"])


async def _run_or_404(session: AsyncSession, run_id: uuid.UUID) -> AgentRun:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


@router.post(
    "/workspaces/{workspace_id}/agent-runs",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_agent_run(
    workspace_id: uuid.UUID,
    payload: AgentRunCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentRun:
    """Queue a research run. It costs real model tokens, so it only starts on this request."""
    await require_workspace(workspace_id, session)
    if not get_settings().claude_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The research agent is unavailable until CLAUDE_API_KEY is configured.",
        )
    return await create_run(session, workspace_id, payload)


@router.get("/workspaces/{workspace_id}/agent-runs", response_model=list[AgentRunResponse])
async def list_agent_runs(
    workspace_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[AgentRun]:
    await require_workspace(workspace_id, session)
    return list(
        await session.scalars(
            select(AgentRun)
            .where(AgentRun.workspace_id == workspace_id)
            .order_by(AgentRun.created_at.desc())
        )
    )


@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> AgentRun:
    return await _run_or_404(session, run_id)


@router.get("/agent-runs/{run_id}/events", response_model=list[AgentEventResponse])
async def list_agent_events(
    run_id: uuid.UUID,
    after: int = Query(default=0, ge=0, description="Return events with a higher seq than this."),
    limit: int = Query(default=500, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
) -> list[AgentEvent]:
    """The run's trace in order. Poll with `after` set to the last seq you have."""
    await _run_or_404(session, run_id)
    return list(
        await session.scalars(
            select(AgentEvent)
            .where(AgentEvent.run_id == run_id, AgentEvent.seq > after)
            .order_by(AgentEvent.seq)
            .limit(limit)
        )
    )


@router.delete("/agent-runs/{run_id}", response_model=AgentRunResponse)
async def cancel_agent_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> AgentRun:
    return await cancel_run(session, await _run_or_404(session, run_id))
