"""Conservative, append-only cross-source claim linking."""

import uuid
from typing import Literal

from fastapi import HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Excerpt, GraphEdge, GraphEvent, Source, Statement, StatementExcerpt
from app.schemas import SynthesisRequest, SynthesisResponse
from app.services.claude_call import ClaudeCallFailed, get_client, structured_call
from app.services.judge import Judge, LinkAudits


class ProposedLink(BaseModel):
    source_statement_id: uuid.UUID
    target_statement_id: uuid.UUID
    relation: Literal["supports", "rebuts", "qualifies"]
    rationale: str = Field(min_length=1)


class LinkProposals(BaseModel):
    links: list[ProposedLink] = Field(default_factory=list)


PROPOSE_LINKS_SYSTEM = (
    "Propose only high-confidence links between statements from different sources. supports "
    "means independently compatible evidence for substantially the same proposition; rebuts "
    "means direct incompatibility; qualifies means the second statement explicitly narrows a "
    "scope or condition. Do not link merely related topics. Return no link when uncertain."
)


def _failed(error: ClaudeCallFailed) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Claude synthesis request failed. {error}".strip(),
    )


async def synthesize_workspace(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    request: SynthesisRequest,
    judge: Judge | None = None,
) -> SynthesisResponse:
    existing = await session.scalar(
        select(GraphEvent).where(
            GraphEvent.workspace_id == workspace_id,
            GraphEvent.idempotency_key == request.idempotency_key,
        )
    )
    if existing is not None:
        if existing.event_type != "cross_source_synthesis":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Idempotency key is already in use"
            )
        return SynthesisResponse(event_id=existing.id, **existing.payload["result"])
    settings = get_settings()
    if not settings.claude_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Claude synthesis is unavailable",
        )
    rows = await session.execute(
        select(Statement.id, Statement.text, Source.id, Excerpt.text)
        .join(StatementExcerpt, StatementExcerpt.statement_id == Statement.id)
        .join(Excerpt, Excerpt.id == StatementExcerpt.excerpt_id)
        .join(Source, Source.id == Excerpt.source_id)
        .where(
            Statement.workspace_id == workspace_id,
            Statement.lifecycle != "rejected",
            Statement.salience == "core",
        )
    )
    candidates: dict[uuid.UUID, dict] = {}
    for statement_id, text, source_id, excerpt in rows:
        item = candidates.setdefault(
            statement_id,
            {"statement_id": str(statement_id), "text": text, "source_ids": set(), "excerpts": []},
        )
        item["source_ids"].add(str(source_id))
        item["excerpts"].append(excerpt[:1200])
    material = [{**item, "source_ids": sorted(item["source_ids"])} for item in candidates.values()]
    if len({source for item in material for source in item["source_ids"]}) < 2:
        raise HTTPException(
            status_code=422, detail="Workspace needs statements from at least two sources"
        )
    model = request.model or settings.claude_model
    judge = judge or Judge(get_client())
    try:
        proposals = await structured_call(
            judge.client,
            model=model,
            system=PROPOSE_LINKS_SYSTEM,
            content={"statements": material},
            tool_name="submit_link_proposals",
            description="Return the requested structured result.",
            schema=LinkProposals,
            task="link proposal",
            tally=judge.usage,
        )
    except ClaudeCallFailed as error:
        raise _failed(error) from error
    valid_ids = {uuid.UUID(item["statement_id"]): set(item["source_ids"]) for item in material}
    links = [
        item
        for item in proposals.links
        if item.source_statement_id in valid_ids
        and item.target_statement_id in valid_ids
        and item.source_statement_id != item.target_statement_id
        and valid_ids[item.source_statement_id].isdisjoint(valid_ids[item.target_statement_id])
    ]
    audit_input = [
        {
            "source_statement_id": str(item.source_statement_id),
            "target_statement_id": str(item.target_statement_id),
            "relation": item.relation,
            "rationale": item.rationale,
            "source": next(
                row for row in material if row["statement_id"] == str(item.source_statement_id)
            ),
            "target": next(
                row for row in material if row["statement_id"] == str(item.target_statement_id)
            ),
        }
        for item in links
    ]
    try:
        audits = await judge.audit_links(audit_input, model) if links else LinkAudits()
    except ClaudeCallFailed as error:
        raise _failed(error) from error
    audit_map = {
        (item.source_statement_id, item.target_statement_id, item.relation): item
        for item in audits.audits
    }
    for link in links:
        audit = audit_map.get((link.source_statement_id, link.target_statement_id, link.relation))
        if audit is None:
            continue
        session.add(
            GraphEdge(
                workspace_id=workspace_id,
                source_node_kind="statement",
                source_node_id=link.source_statement_id,
                relation=link.relation,
                target_node_kind="statement",
                target_node_id=link.target_statement_id,
                metadata_={
                    "lifecycle": "proposed",
                    "generator_rationale": link.rationale,
                    "audit_verdict": audit.verdict,
                    "audit_rationale": audit.rationale,
                    "model": model,
                },
            )
        )
    result = {
        "candidates_considered": len(material),
        "proposed_links": len(links),
        "audited_links": len(audit_map),
        "links_needing_review": sum(item.verdict == "needs_review" for item in audit_map.values()),
    }
    event = GraphEvent(
        workspace_id=workspace_id,
        event_type="cross_source_synthesis",
        idempotency_key=request.idempotency_key,
        payload={"result": result},
        provenance={"actor_type": "critic", "actor_id": "anthropic", "model": model},
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return SynthesisResponse(event_id=event.id, **result)
