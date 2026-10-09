"""Everything the workspace screen shows, in one request.

The screen used to assemble this from one request per claim and three per source, every few
seconds, which saturated the API with a handful of viewers. Here it is built from a fixed number
of queries, and an ETag lets an unchanged workspace answer 304 with no body.
"""

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Excerpt, ExtractionJob, Issue, ProofObligation, Source, SourceValidity
from app.routes.etag import etag_response
from app.routes.graph import get_graph
from app.routes.workspaces import require_workspace
from app.schemas import (
    ExcerptResponse,
    ExtractionJobResponse,
    GraphContextResponse,
    GraphResponse,
    IssueResponse,
    ObligationResponse,
    SourceResponse,
    SourceValidityResponse,
    WorkspaceResponse,
)

router = APIRouter(tags=["graph"])


def statement_contexts(
    graph: GraphResponse, obligations: list[ProofObligation], issues: list[Issue]
) -> list[dict]:
    """Each claim's neighbourhood, as `/statements/{id}/context` returns it, for every claim."""
    statements = {s.id: s for s in graph.statements}
    concluding = defaultdict(list)  # claim id -> steps that conclude it
    using = defaultdict(list)  # claim id -> steps that use it as a premise
    for step in graph.reasoning_steps:
        concluding[step.conclusion_id].append(step)
        for premise in step.premise_ids:
            using[premise].append(step)
    blocking = defaultdict(list)
    for obligation in obligations:
        blocking[obligation.blocks_statement_id].append(obligation)
    flagged = defaultdict(list)
    for issue in issues:
        flagged[issue.node_id].append(issue)

    contexts = []
    for statement in graph.statements:
        incoming, outgoing = concluding[statement.id], using[statement.id]
        upstream = {p for step in incoming for p in step.premise_ids}
        downstream = {step.conclusion_id for step in outgoing}
        steps = {step.id: step for step in [*incoming, *outgoing]}
        contexts.append(
            GraphContextResponse(
                focus_statement=statement,
                upstream_statements=[statements[i] for i in upstream if i in statements],
                downstream_statements=[statements[i] for i in downstream if i in statements],
                reasoning_steps=list(steps.values()),
                obligations=[ObligationResponse.model_validate(o) for o in blocking[statement.id]],
                issues=[IssueResponse.model_validate(i) for i in flagged[statement.id]],
            ).model_dump(mode="json")
        )
    return contexts


@router.get("/workspaces/{workspace_id}/snapshot")
async def get_snapshot(
    workspace_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_session)
) -> Response:
    workspace = await require_workspace(workspace_id, session)
    graph = await get_graph(workspace_id, session)
    obligations = list(
        await session.scalars(
            select(ProofObligation).where(
                ProofObligation.workspace_id == workspace_id,
                ProofObligation.blocks_statement_id.is_not(None),
            )
        )
    )
    issues = list(
        await session.scalars(
            select(Issue).where(Issue.workspace_id == workspace_id, Issue.node_type == "statement")
        )
    )
    sources = list(
        await session.scalars(
            select(Source).where(Source.workspace_id == workspace_id).order_by(Source.created_at)
        )
    )
    source_ids = [source.id for source in sources]
    excerpts = defaultdict(list)
    for excerpt in await session.scalars(
        select(Excerpt)
        .where(Excerpt.source_id.in_(source_ids))
        .order_by(Excerpt.source_id, Excerpt.sequence)
    ):
        excerpts[excerpt.source_id].append(ExcerptResponse.model_validate(excerpt))
    # The latest validity decision for each source.
    validity = {}
    for decision in await session.scalars(
        select(SourceValidity)
        .where(SourceValidity.source_id.in_(source_ids))
        .order_by(SourceValidity.source_id, SourceValidity.created_at)
    ):
        validity[str(decision.source_id)] = SourceValidityResponse.model_validate(
            decision
        ).model_dump(mode="json")
    jobs = list(
        await session.scalars(
            select(ExtractionJob)
            .where(ExtractionJob.workspace_id == workspace_id)
            .order_by(ExtractionJob.created_at)
        )
    )

    payload = {
        "workspace": WorkspaceResponse.model_validate(workspace).model_dump(mode="json"),
        "graph": graph.model_dump(mode="json"),
        "contexts": statement_contexts(graph, obligations, issues),
        "sources": [
            {
                **SourceResponse.model_validate(source).model_dump(mode="json"),
                "excerpts": [e.model_dump(mode="json") for e in excerpts[source.id]],
            }
            for source in sources
        ],
        "validity": validity,
        "jobs": [ExtractionJobResponse.model_validate(job).model_dump(mode="json") for job in jobs],
    }
    return etag_response(request, payload)
