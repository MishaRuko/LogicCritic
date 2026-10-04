import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session, session_factory
from app.models import (
    GraphEdge,
    GraphEvent,
    Issue,
    ProofObligation,
    ReasoningPremise,
    ReasoningStep,
    Statement,
    StatementExcerpt,
)
from app.routes.workspaces import require_workspace
from app.schemas import (
    ArgumentCheckRequest,
    ArgumentCheckResponse,
    GraphContextResponse,
    GraphEdgeResponse,
    GraphPatchRequest,
    GraphPatchResponse,
    GraphQuestionRequest,
    GraphQuestionResponse,
    GraphResponse,
    IssueResponse,
    ObligationResponse,
    ReasoningStepResponse,
    ReviewDecisionRequest,
    ReviewDecisionResponse,
    StatementResponse,
    SynthesisRequest,
    SynthesisResponse,
    VerificationResponse,
)
from app.services import graph_qa
from app.services.argument_check import check_arguments
from app.services.claude_call import get_client
from app.config import get_settings
from app.services.graph_patches import GraphPatchExecutor
from app.services.synthesis import synthesize_workspace
from app.services.verification import run_verification

router = APIRouter(tags=["graph"])


@router.post("/workspaces/{workspace_id}/graph-patches", response_model=GraphPatchResponse)
async def apply_graph_patch(
    workspace_id: uuid.UUID,
    payload: GraphPatchRequest,
    session: AsyncSession = Depends(get_session),
) -> GraphPatchResponse:
    await require_workspace(workspace_id, session)
    return await GraphPatchExecutor(session, workspace_id).apply(payload)


async def statement_responses(
    session: AsyncSession, statements: list[Statement]
) -> list[StatementResponse]:
    ids = [item.id for item in statements]
    if not ids:
        return []
    excerpt_map: dict[uuid.UUID, list[uuid.UUID]] = {item: [] for item in ids}
    for statement_id, excerpt_id in await session.execute(
        select(StatementExcerpt.statement_id, StatementExcerpt.excerpt_id).where(
            StatementExcerpt.statement_id.in_(ids)
        )
    ):
        excerpt_map[statement_id].append(excerpt_id)
    responses = []
    for item in statements:
        payload = StatementResponse.model_validate(item).model_dump()
        payload["excerpt_ids"] = excerpt_map[item.id]
        responses.append(StatementResponse(**payload))
    return responses


async def reasoning_step_responses(
    session: AsyncSession, steps: list[ReasoningStep]
) -> list[ReasoningStepResponse]:
    ids = [item.id for item in steps]
    if not ids:
        return []
    premise_map: dict[uuid.UUID, list[uuid.UUID]] = {item: [] for item in ids}
    for step_id, statement_id in await session.execute(
        select(ReasoningPremise.reasoning_step_id, ReasoningPremise.statement_id)
        .where(ReasoningPremise.reasoning_step_id.in_(ids))
        .order_by(ReasoningPremise.position)
    ):
        premise_map[step_id].append(statement_id)
    responses = []
    for item in steps:
        payload = ReasoningStepResponse.model_validate(item).model_dump()
        payload["premise_ids"] = premise_map[item.id]
        responses.append(ReasoningStepResponse(**payload))
    return responses


@router.get("/workspaces/{workspace_id}/graph", response_model=GraphResponse)
async def get_graph(
    workspace_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> GraphResponse:
    await require_workspace(workspace_id, session)
    statements = list(
        await session.scalars(select(Statement).where(Statement.workspace_id == workspace_id))
    )
    steps = list(
        await session.scalars(
            select(ReasoningStep).where(ReasoningStep.workspace_id == workspace_id)
        )
    )
    edges = list(
        await session.scalars(select(GraphEdge).where(GraphEdge.workspace_id == workspace_id))
    )
    return GraphResponse(
        statements=await statement_responses(session, statements),
        reasoning_steps=await reasoning_step_responses(session, steps),
        relations=[GraphEdgeResponse.model_validate(item) for item in edges],
    )


@router.get(
    "/workspaces/{workspace_id}/statements/{statement_id}/context",
    response_model=GraphContextResponse,
)
async def get_statement_context(
    workspace_id: uuid.UUID, statement_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> GraphContextResponse:
    await require_workspace(workspace_id, session)
    focus = await session.get(Statement, statement_id)
    if focus is None or focus.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Statement not found")

    incoming_steps = list(
        await session.scalars(
            select(ReasoningStep).where(
                ReasoningStep.workspace_id == workspace_id,
                ReasoningStep.conclusion_id == statement_id,
            )
        )
    )
    outgoing_steps = list(
        await session.scalars(
            select(ReasoningStep)
            .join(ReasoningPremise, ReasoningPremise.reasoning_step_id == ReasoningStep.id)
            .where(
                ReasoningStep.workspace_id == workspace_id,
                ReasoningPremise.statement_id == statement_id,
            )
        )
    )
    all_steps = {item.id: item for item in [*incoming_steps, *outgoing_steps]}
    premise_ids = set(
        await session.scalars(
            select(ReasoningPremise.statement_id).where(
                ReasoningPremise.reasoning_step_id.in_([item.id for item in incoming_steps])
            )
        )
    )
    downstream_ids = {item.conclusion_id for item in outgoing_steps}
    upstream = (
        list(await session.scalars(select(Statement).where(Statement.id.in_(premise_ids))))
        if premise_ids
        else []
    )
    downstream = (
        list(await session.scalars(select(Statement).where(Statement.id.in_(downstream_ids))))
        if downstream_ids
        else []
    )
    obligations = list(
        await session.scalars(
            select(ProofObligation).where(
                ProofObligation.workspace_id == workspace_id,
                ProofObligation.blocks_statement_id == statement_id,
            )
        )
    )
    issues = list(
        await session.scalars(
            select(Issue).where(
                Issue.workspace_id == workspace_id,
                Issue.node_type == "statement",
                Issue.node_id == statement_id,
            )
        )
    )
    return GraphContextResponse(
        focus_statement=(await statement_responses(session, [focus]))[0],
        upstream_statements=await statement_responses(session, upstream),
        downstream_statements=await statement_responses(session, downstream),
        reasoning_steps=await reasoning_step_responses(session, list(all_steps.values())),
        obligations=[ObligationResponse.model_validate(item) for item in obligations],
        issues=[IssueResponse.model_validate(item) for item in issues],
    )


@router.post("/workspaces/{workspace_id}/verify", response_model=VerificationResponse)
async def verify_workspace(
    workspace_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> VerificationResponse:
    await require_workspace(workspace_id, session)
    return await run_verification(session, workspace_id)


@router.post("/workspaces/{workspace_id}/verify/stream")
async def stream_verification(
    workspace_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> StreamingResponse:
    await require_workspace(workspace_id, session)

    async def events():
        queue = asyncio.Queue()

        async def verify():
            try:
                async with session_factory() as verification_session:
                    result = await run_verification(verification_session, workspace_id, queue.put)
                    await queue.put({"type": "completed", "result": result.model_dump(mode="json")})
            except Exception:
                logging.getLogger(__name__).exception("Live verification failed")
                await queue.put({"type": "error", "message": "Verification failed. Please retry."})
            finally:
                await queue.put(None)

        task = asyncio.create_task(verify())
        try:
            while (event := await queue.get()) is not None:
                yield json.dumps(event) + "\n"
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        events(), media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workspaces/{workspace_id}/argument-check", response_model=ArgumentCheckResponse)
async def argument_check_workspace(
    workspace_id: uuid.UUID,
    payload: ArgumentCheckRequest,
    session: AsyncSession = Depends(get_session),
) -> ArgumentCheckResponse:
    await require_workspace(workspace_id, session)
    return await check_arguments(session, workspace_id, payload)


@router.post("/workspaces/{workspace_id}/graph-questions", response_model=GraphQuestionResponse)
async def ask_graph(
    workspace_id: uuid.UUID, payload: GraphQuestionRequest, session: AsyncSession = Depends(get_session)
) -> GraphQuestionResponse:
    """Answer a question about the argument graph by querying it; returns the nodes to highlight."""
    await require_workspace(workspace_id, session)
    if not get_settings().claude_api_key:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Claude is not configured")
    try:
        result = await graph_qa.ask(
            session_factory, workspace_id, payload.question, [t.model_dump() for t in payload.history], get_client()
        )
    except Exception as error:  # noqa: BLE001 - report model failures as a clear gateway error
        logging.getLogger(__name__).exception("graph question failed")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"The graph question failed: {error}") from error
    return GraphQuestionResponse(**result)


@router.post("/workspaces/{workspace_id}/synthesize", response_model=SynthesisResponse)
async def synthesize_workspace_graph(
    workspace_id: uuid.UUID, payload: SynthesisRequest, session: AsyncSession = Depends(get_session)
) -> SynthesisResponse:
    await require_workspace(workspace_id, session)
    return await synthesize_workspace(session, workspace_id, payload)


@router.post("/workspaces/{workspace_id}/review", response_model=ReviewDecisionResponse)
async def review_node(
    workspace_id: uuid.UUID,
    payload: ReviewDecisionRequest,
    session: AsyncSession = Depends(get_session),
) -> ReviewDecisionResponse:
    await require_workspace(workspace_id, session)
    if payload.provenance.actor_type != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only a user can review graph nodes"
        )
    existing = await session.scalar(
        select(GraphEvent).where(
            GraphEvent.workspace_id == workspace_id,
            GraphEvent.idempotency_key == payload.idempotency_key,
        )
    )
    if existing is not None:
        if existing.event_type != "review_decision":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Idempotency key is already in use"
            )
        return ReviewDecisionResponse(event_id=existing.id, **existing.payload)

    model = Statement if payload.node_type == "statement" else ReasoningStep
    node = await session.get(model, payload.node_id)
    if node is None or node.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Graph node not found")
    node.lifecycle = payload.decision
    event = GraphEvent(
        workspace_id=workspace_id,
        event_type="review_decision",
        idempotency_key=payload.idempotency_key,
        payload={
            "node_type": payload.node_type,
            "node_id": str(payload.node_id),
            "lifecycle": payload.decision,
        },
        provenance=payload.provenance.model_dump(mode="json", exclude_none=True),
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return ReviewDecisionResponse(event_id=event.id, **event.payload)
