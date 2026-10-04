import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Annotation,
    Excerpt,
    GraphEvent,
    ReasoningPremise,
    ReasoningStep,
    Statement,
    StatementExcerpt,
)
from app.schemas import ArgumentCheckRequest, ArgumentCheckResponse
from app.services.claude_call import ClaudeCallFailed, get_client
from app.services.judge import Judge


async def check_arguments(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    request: ArgumentCheckRequest,
    step_ids: set[uuid.UUID] | None = None,
    judge: Judge | None = None,
) -> ArgumentCheckResponse:
    """Audit reasoning steps for unstated premises. `step_ids` limits it to some of them."""
    existing = await session.scalar(
        select(GraphEvent).where(
            GraphEvent.workspace_id == workspace_id,
            GraphEvent.idempotency_key == request.idempotency_key,
        )
    )
    if existing is not None:
        if existing.event_type != "argument_check":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Idempotency key is already in use"
            )
        return ArgumentCheckResponse(event_id=existing.id, **existing.payload["result"])
    if not get_settings().claude_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Claude argument checking is unavailable",
        )
    query = select(ReasoningStep).where(ReasoningStep.workspace_id == workspace_id)
    if step_ids is not None:
        query = query.where(ReasoningStep.id.in_(step_ids))
    steps = list(await session.scalars(query))
    if not steps:
        raise HTTPException(status_code=422, detail="Workspace has no reasoning steps")
    premise_rows = list(
        await session.execute(
            select(ReasoningPremise.reasoning_step_id, ReasoningPremise.statement_id).where(
                ReasoningPremise.reasoning_step_id.in_([item.id for item in steps])
            )
        )
    )
    premises = {item.id: [] for item in steps}
    for step_id, statement_id in premise_rows:
        premises[step_id].append(statement_id)
    statement_ids = {item.conclusion_id for item in steps} | {item for _, item in premise_rows}
    statements = {
        item.id: item.text
        for item in await session.scalars(select(Statement).where(Statement.id.in_(statement_ids)))
    }
    excerpt_rows = await session.execute(
        select(StatementExcerpt.statement_id, Excerpt.text)
        .join(Excerpt)
        .where(StatementExcerpt.statement_id.in_(statement_ids))
    )
    excerpts = {item: [] for item in statement_ids}
    for statement_id, text in excerpt_rows:
        excerpts[statement_id].append(text[:2000])
    steps_payload = [
        {
            "reasoning_step_id": str(step.id),
            "premises": [
                {"statement": statements[item], "excerpts": excerpts[item]}
                for item in premises[step.id]
            ],
            "conclusion": {
                "statement": statements[step.conclusion_id],
                "excerpts": excerpts[step.conclusion_id],
            },
        }
        for step in steps
    ]
    model = request.model or get_settings().claude_model
    judge = judge or Judge(get_client())
    try:
        output = await judge.check_steps(steps_payload, model)
    except ClaudeCallFailed as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Claude argument check failed. {error}"
        ) from error
    assessments = {item.reasoning_step_id: item for item in output.assessments}
    step_ids = {item.id for item in steps}
    if set(assessments) != step_ids:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Claude did not assess every reasoning step",
        )
    flagged = [item for item in assessments.values() if item.verdict == "needs_support"]
    for item in flagged:
        session.add(
            Annotation(
                workspace_id=workspace_id,
                subject_type="reasoning_step",
                subject_id=item.reasoning_step_id,
                type="required_premise",
                value={"description": item.rationale, "satisfied": False},
                provenance={"actor_type": "critic", "actor_id": "anthropic", "model": model},
                confidence=None,
                status="proposed",
            )
        )
    result = {"checked_steps": len(steps), "flagged_steps": len(flagged)}
    event = GraphEvent(
        workspace_id=workspace_id,
        event_type="argument_check",
        idempotency_key=request.idempotency_key,
        payload={"result": result},
        provenance={"actor_type": "critic", "actor_id": "anthropic", "model": model},
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return ArgumentCheckResponse(event_id=event.id, **result)
