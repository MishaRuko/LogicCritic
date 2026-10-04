import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.runs import cancel_run, create_run
from app.config import get_settings
from app.database import get_session
from app.models import (
    AgentEvent,
    AgentRun,
    ExperimentProtocol,
    JudgeVerdict,
    ResearchGoal,
    Source,
)
from app.routes.workspaces import require_workspace
from app.schemas import (
    AgentEventResponse,
    AgentGoalResponse,
    AgentProtocol,
    AgentRunCreate,
    AgentRunResponse,
    AgentVerdictResponse,
)

router = APIRouter(tags=["agent"])


async def _run_or_404(session: AsyncSession, run_id: uuid.UUID) -> AgentRun:
    run = await session.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent run not found")
    return run


def _response(
    run: AgentRun, goal: ResearchGoal, protocol: AgentProtocol | None = None
) -> AgentRunResponse:
    return AgentRunResponse(
        **{
            name: getattr(run, name)
            for name in AgentRunResponse.model_fields
            if name not in {"question", "kind", "goal", "protocol"}
        },
        goal=AgentGoalResponse.model_validate(goal),
        protocol=protocol,
        question=goal.question,
        kind=goal.kind,
    )


async def _protocols(session: AsyncSession, run_ids: list[uuid.UUID]) -> dict[str, AgentProtocol]:
    """The latest protocol each run recorded (the agent may refine it), keyed by run id."""
    if not run_ids:
        return {}
    rows = await session.scalars(
        select(Source)
        .where(
            Source.origin == "agent",
            Source.metadata_["parser"].astext == "agent_protocol_v1",
            Source.metadata_["run_id"].astext.in_([str(i) for i in run_ids]),
        )
        .order_by(Source.created_at)
    )
    found = {}
    for source in rows:
        meta = source.metadata_
        found[meta["run_id"]] = AgentProtocol(
            source_id=source.id,
            title=source.title,
            basis=meta.get("basis", ""),
            steps=meta.get("steps", []),
        )
    prepared = {
        row.source_id: row.id
        for row in await session.scalars(
            select(ExperimentProtocol)
            .where(ExperimentProtocol.source_id.in_([p.source_id for p in found.values()]))
            .order_by(ExperimentProtocol.created_at)
        )
    }
    for protocol in found.values():
        protocol.experiment_protocol_id = prepared.get(protocol.source_id)
    return found


async def _with_goal(session: AsyncSession, run: AgentRun) -> AgentRunResponse:
    goal = await session.get(ResearchGoal, run.goal_id)
    return _response(run, goal, (await _protocols(session, [run.id])).get(str(run.id)))


@router.post(
    "/workspaces/{workspace_id}/agent-runs",
    response_model=AgentRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_agent_run(
    workspace_id: uuid.UUID,
    payload: AgentRunCreate,
    session: AsyncSession = Depends(get_session),
) -> AgentRunResponse:
    """Queue a research run. It costs real model tokens, so it only starts on this request."""
    await require_workspace(workspace_id, session)
    if not get_settings().claude_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The research agent is unavailable until CLAUDE_API_KEY is configured.",
        )
    return await _with_goal(session, await create_run(session, workspace_id, payload))


@router.get("/workspaces/{workspace_id}/agent-runs", response_model=list[AgentRunResponse])
async def list_agent_runs(
    workspace_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[AgentRunResponse]:
    await require_workspace(workspace_id, session)
    rows = await session.execute(
        select(AgentRun, ResearchGoal)
        .join(ResearchGoal, AgentRun.goal_id == ResearchGoal.id)
        .where(AgentRun.workspace_id == workspace_id)
        .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
    )
    rows = list(rows)
    protocols = await _protocols(session, [run.id for run, _ in rows])
    return [_response(run, goal, protocols.get(str(run.id))) for run, goal in rows]


@router.get("/agent-runs/{run_id}", response_model=AgentRunResponse)
async def get_agent_run(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> AgentRunResponse:
    return await _with_goal(session, await _run_or_404(session, run_id))


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
) -> AgentRunResponse:
    return await _with_goal(session, await cancel_run(session, await _run_or_404(session, run_id)))


@router.get("/agent-runs/{run_id}/verdicts", response_model=list[AgentVerdictResponse])
async def list_agent_verdicts(
    run_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[AgentVerdictResponse]:
    """The independent reviewer's judgements of the run's evidence, oldest first.

    Each is one review of the claims then in the argument against the completion criteria. The
    last one is the current state; earlier ones show how the evidence grew.
    """
    await _run_or_404(session, run_id)
    rows = await session.scalars(
        select(JudgeVerdict).where(JudgeVerdict.run_id == run_id).order_by(JudgeVerdict.created_at)
    )
    out = []
    for row in rows:
        criteria = row.material.get("criteria", [])
        out.append(
            AgentVerdictResponse(
                id=row.id,
                created_at=row.created_at,
                criteria=[
                    {
                        "index": v["index"],
                        "criterion": criteria[v["index"]] if v["index"] < len(criteria) else "",
                        "met": v["met"],
                        "rationale": v["rationale"],
                        "supporting_statement_ids": v["supporting_statement_ids"],
                    }
                    for v in row.verdict.get("criteria", [])
                ],
                designs=row.verdict.get("designs", []),
                searches=row.material.get("searches", []),
            )
        )
    return out
