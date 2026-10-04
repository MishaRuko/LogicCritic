"""A verification applies to the research version that was actually checked."""

import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Annotation,
    GraphEdge,
    GraphEvent,
    ReasoningStep,
    Source,
    SourceValidity,
    Statement,
)


async def research_fingerprint(session: AsyncSession, workspace_id: uuid.UUID) -> str:
    records = []
    for model in (Source, Statement, ReasoningStep, GraphEdge, Annotation):
        rows = await session.scalars(select(model).where(model.workspace_id == workspace_id))
        for row in rows:
            # Run artifacts do not change the research that the protocol was extracted from.
            if getattr(row, "origin", None) == "lab-vision":
                continue
            if getattr(row, "provenance", {}).get("actor_id") == "lab-vision":
                continue
            data = {c.key: getattr(row, c.key) for c in model.__mapper__.column_attrs}
            records.append((model.__tablename__, str(row.id), data))
    validity = await session.scalars(
        select(SourceValidity).join(Source).where(Source.workspace_id == workspace_id)
    )
    for row in validity:
        records.append(("validity", str(row.id), {"status": row.status, "source": row.source_id}))
    return hashlib.sha256(
        json.dumps(sorted(records), default=str, sort_keys=True).encode()
    ).hexdigest()


async def verification_state(session: AsyncSession, workspace_id: uuid.UUID) -> tuple[bool, str]:
    fingerprint = await research_fingerprint(session, workspace_id)
    event = await session.scalar(
        select(GraphEvent)
        .where(GraphEvent.workspace_id == workspace_id, GraphEvent.event_type == "verification_run")
        .order_by(GraphEvent.created_at.desc(), GraphEvent.id.desc())
        .limit(1)
    )
    return bool(event and event.payload.get("research_fingerprint") == fingerprint), fingerprint
