import json
import uuid

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Annotation, Excerpt, GraphEvent, ReasoningPremise, ReasoningStep, Statement, StatementExcerpt
from app.schemas import ArgumentCheckOutput, ArgumentCheckRequest, ArgumentCheckResponse


async def check_arguments(
    session: AsyncSession, workspace_id: uuid.UUID, request: ArgumentCheckRequest
) -> ArgumentCheckResponse:
    existing = await session.scalar(select(GraphEvent).where(
        GraphEvent.workspace_id == workspace_id, GraphEvent.idempotency_key == request.idempotency_key
    ))
    if existing is not None:
        if existing.event_type != "argument_check":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Idempotency key is already in use")
        return ArgumentCheckResponse(event_id=existing.id, **existing.payload["result"])
    if not get_settings().claude_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Claude argument checking is unavailable")
    steps = list(await session.scalars(select(ReasoningStep).where(ReasoningStep.workspace_id == workspace_id)))
    if not steps:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Workspace has no reasoning steps")
    premise_rows = list(await session.execute(select(ReasoningPremise.reasoning_step_id, ReasoningPremise.statement_id).where(
        ReasoningPremise.reasoning_step_id.in_([item.id for item in steps])
    )))
    premises = {item.id: [] for item in steps}
    for step_id, statement_id in premise_rows:
        premises[step_id].append(statement_id)
    statement_ids = {item.conclusion_id for item in steps} | {item for _, item in premise_rows}
    statements = {item.id: item.text for item in await session.scalars(select(Statement).where(Statement.id.in_(statement_ids)))}
    excerpt_rows = await session.execute(select(StatementExcerpt.statement_id, Excerpt.text).join(Excerpt).where(
        StatementExcerpt.statement_id.in_(statement_ids)
    ))
    excerpts = {item: [] for item in statement_ids}
    for statement_id, text in excerpt_rows:
        excerpts[statement_id].append(text[:2000])
    steps_payload = [{
        "reasoning_step_id": str(step.id),
        "premises": [{"statement": statements[item], "excerpts": excerpts[item]} for item in premises[step.id]],
        "conclusion": {"statement": statements[step.conclusion_id], "excerpts": excerpts[step.conclusion_id]},
    } for step in steps]
    model = request.model or get_settings().claude_model
    payload = {
        "model": model, "max_tokens": 4096,
        "system": "Audit each argument step conservatively. A conclusion being stated in its own excerpt is not support. Mark supported only if the premise excerpts independently establish the conclusion without an unstated calculation, mechanism, causal assumption, generalization, or background fact. If unsure, choose needs_support. Do not assess truth beyond these excerpts.",
        "messages": [{"role": "user", "content": json.dumps({"steps": steps_payload})}],
        "tools": [{"name": "submit_argument_check", "description": "Return exactly one assessment for every step.", "input_schema": ArgumentCheckOutput.model_json_schema()}],
        "tool_choice": {"type": "tool", "name": "submit_argument_check"},
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post("https://api.anthropic.com/v1/messages", json=payload, headers={"x-api-key": get_settings().claude_api_key, "anthropic-version": "2023-06-01"})
            response.raise_for_status()
        tool_use = next(item for item in response.json()["content"] if item["type"] == "tool_use")
        output = ArgumentCheckOutput.model_validate(tool_use["input"])
    except (httpx.HTTPError, KeyError, StopIteration, TypeError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Claude argument check failed") from error
    assessments = {item.reasoning_step_id: item for item in output.assessments}
    step_ids = {item.id for item in steps}
    if set(assessments) != step_ids:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Claude did not assess every reasoning step")
    flagged = [item for item in assessments.values() if item.verdict == "needs_support"]
    for item in flagged:
        session.add(Annotation(workspace_id=workspace_id, subject_type="reasoning_step", subject_id=item.reasoning_step_id,
            type="required_premise", value={"description": item.rationale, "satisfied": False},
            provenance={"actor_type": "critic", "actor_id": "anthropic", "model": model}, confidence=None, status="proposed"))
    result = {"checked_steps": len(steps), "flagged_steps": len(flagged)}
    event = GraphEvent(workspace_id=workspace_id, event_type="argument_check", idempotency_key=request.idempotency_key,
        payload={"result": result}, provenance={"actor_type": "critic", "actor_id": "anthropic", "model": model})
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return ArgumentCheckResponse(event_id=event.id, **result)
