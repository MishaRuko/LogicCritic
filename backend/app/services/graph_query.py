"""Read-only queries over a workspace's argument graph, for people and agents.

Every result carries the ids it mentions, so an answer built from it can point back at the
exact claims and reasoning steps in the graph. Search is lexical (Postgres full text, with a
substring fallback); there are no embeddings in this system.
"""

import re
import uuid
from collections import deque

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Excerpt,
    GraphEdge,
    ProofObligation,
    ReasoningPremise,
    ReasoningStep,
    Source,
    Statement,
    StatementExcerpt,
)

TEXT_CHARS = 600
EXCERPT_CHARS = 500
MAX_CHAIN_NODES = 40


class GraphQueryError(ValueError):
    """A bad query the caller (often a model) can read and correct."""


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as error:
        raise GraphQueryError(f"{value!r} is not an id from the graph.") from error


def _cut(text: str, limit: int = TEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _brief(statement: Statement) -> dict:
    return {
        "statement_id": str(statement.id),
        "text": _cut(statement.text),
        "role": statement.role,
        "lifecycle": statement.lifecycle,
        "salience": statement.salience,
        "superseded": statement.superseded_by is not None,
    }


async def overview(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    statements = list(
        await session.scalars(select(Statement).where(Statement.workspace_id == workspace_id))
    )
    steps = await session.scalar(
        select(func.count()).select_from(ReasoningStep).where(ReasoningStep.workspace_id == workspace_id)
    )
    open_obligations = await session.scalar(
        select(func.count())
        .select_from(ProofObligation)
        .where(ProofObligation.workspace_id == workspace_id, ProofObligation.status == "open")
    )
    sources = list(
        await session.scalars(select(Source).where(Source.workspace_id == workspace_id).order_by(Source.created_at))
    )
    live = [s for s in statements if s.superseded_by is None and s.lifecycle != "rejected"]
    return {
        "claims": len(live),
        "reasoning_steps": steps,
        "open_obligations": open_obligations,
        "sources": [{"source_id": str(s.id), "title": s.title or s.original_filename} for s in sources[:40]],
        "conclusions": [_brief(s) for s in live if s.role == "conclusion"][:30],
    }


async def search(session: AsyncSession, workspace_id: uuid.UUID, query: str, limit: int = 10) -> dict:
    query = query.strip()
    if not query:
        raise GraphQueryError("Give some words to search for.")
    limit = max(1, min(limit, 25))
    document = func.to_tsvector("english", Statement.text)
    terms = func.websearch_to_tsquery("english", query)
    rows = list(
        await session.scalars(
            select(Statement)
            .where(Statement.workspace_id == workspace_id, document.op("@@")(terms))
            .order_by(func.ts_rank(document, terms).desc(), Statement.created_at)
            .limit(limit)
        )
    )
    if not rows:
        # Full text needs every word; fall back to any meaningful word as a substring.
        words = [w for w in re.findall(r"[\w-]{3,}", query.lower())][:8]
        if words:
            rows = list(
                await session.scalars(
                    select(Statement)
                    .where(
                        Statement.workspace_id == workspace_id,
                        or_(*(Statement.text.ilike(f"%{w}%") for w in words)),
                    )
                    .limit(limit * 3)
                )
            )
            rows.sort(key=lambda s: -sum(w in s.text.lower() for w in words))
            rows = rows[:limit]
    steps = list(
        await session.scalars(
            select(ReasoningStep)
            .where(
                ReasoningStep.workspace_id == workspace_id,
                func.to_tsvector("english", ReasoningStep.explanation).op("@@")(terms),
            )
            .limit(5)
        )
    )
    return {
        "claims": [_brief(s) for s in rows],
        "reasoning_steps": [
            {"step_id": str(s.id), "conclusion_id": str(s.conclusion_id), "explanation": _cut(s.explanation)}
            for s in steps
        ],
    }


async def _premises(session: AsyncSession, step_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[uuid.UUID]]:
    found: dict[uuid.UUID, list[uuid.UUID]] = {i: [] for i in step_ids}
    if step_ids:
        for step_id, statement_id in await session.execute(
            select(ReasoningPremise.reasoning_step_id, ReasoningPremise.statement_id)
            .where(ReasoningPremise.reasoning_step_id.in_(step_ids))
            .order_by(ReasoningPremise.position)
        ):
            found[step_id].append(statement_id)
    return found


async def get_node(session: AsyncSession, workspace_id: uuid.UUID, node_id: str) -> dict:
    """A claim or a reasoning step with everything directly attached to it."""
    key = _id(node_id)
    statement = await session.get(Statement, key)
    if statement is not None and statement.workspace_id == workspace_id:
        return await _statement_detail(session, workspace_id, statement)
    step = await session.get(ReasoningStep, key)
    if step is not None and step.workspace_id == workspace_id:
        premises = (await _premises(session, [step.id]))[step.id]
        texts = {
            s.id: s for s in await session.scalars(select(Statement).where(Statement.id.in_([*premises, step.conclusion_id])))
        }
        return {
            "kind": "reasoning_step",
            "step_id": str(step.id),
            "explanation": _cut(step.explanation, 1200),
            "lifecycle": step.lifecycle,
            "premises": [_brief(texts[p]) for p in premises if p in texts],
            "conclusion": _brief(texts[step.conclusion_id]) if step.conclusion_id in texts else None,
            "open_obligations": await _obligations(session, workspace_id, [step.id]),
        }
    raise GraphQueryError("No claim or reasoning step with that id in this workspace.")


async def _obligations(session, workspace_id, node_ids) -> list[dict]:
    rows = await session.scalars(
        select(ProofObligation).where(
            ProofObligation.workspace_id == workspace_id,
            ProofObligation.status == "open",
            or_(ProofObligation.blocks_statement_id.in_(node_ids), ProofObligation.blocks_node_id.in_(node_ids)),
        )
    )
    return [{"kind": o.kind, "description": _cut(o.description, 400)} for o in rows]


async def _statement_detail(session, workspace_id, statement: Statement) -> dict:
    excerpts = await session.execute(
        select(Excerpt.id, Excerpt.text, Source.title, Source.original_filename)
        .join(StatementExcerpt, StatementExcerpt.excerpt_id == Excerpt.id)
        .join(Source, Source.id == Excerpt.source_id)
        .where(StatementExcerpt.statement_id == statement.id)
    )
    derived_by = list(
        await session.scalars(select(ReasoningStep).where(ReasoningStep.conclusion_id == statement.id))
    )
    used_in = list(
        await session.scalars(
            select(ReasoningStep)
            .join(ReasoningPremise, ReasoningPremise.reasoning_step_id == ReasoningStep.id)
            .where(ReasoningPremise.statement_id == statement.id)
        )
    )
    premises = await _premises(session, [s.id for s in derived_by])
    edges = list(
        await session.scalars(
            select(GraphEdge).where(
                GraphEdge.workspace_id == workspace_id,
                or_(GraphEdge.source_node_id == statement.id, GraphEdge.target_node_id == statement.id),
            )
        )
    )
    others = {e.target_node_id if e.source_node_id == statement.id else e.source_node_id for e in edges}
    related = {
        s.id: s
        for s in await session.scalars(
            select(Statement).where(
                Statement.id.in_({*others, *(c.conclusion_id for c in used_in), *(p for ps in premises.values() for p in ps)})
            )
        )
    }
    return {
        "kind": "claim",
        **_brief(statement),
        "text": statement.text,
        "assertion_mode": statement.assertion_mode,
        "evidence": [
            {"excerpt_id": str(i), "source": title or name, "text": _cut(text, EXCERPT_CHARS)}
            for i, text, title, name in excerpts
        ],
        "derived_by": [
            {
                "step_id": str(s.id),
                "explanation": _cut(s.explanation, 400),
                "premises": [_brief(related[p]) for p in premises[s.id] if p in related],
            }
            for s in derived_by
        ],
        "supports_conclusions": [
            {"step_id": str(s.id), "conclusion": _brief(related[s.conclusion_id])}
            for s in used_in
            if s.conclusion_id in related
        ],
        "links": [
            {
                "relation": e.relation,
                "direction": "outgoing" if e.source_node_id == statement.id else "incoming",
                "other": _brief(related[o]) if (o := e.target_node_id if e.source_node_id == statement.id else e.source_node_id) in related else {"id": str(o)},
                "audit": (e.metadata_ or {}).get("audit_verdict"),
            }
            for e in edges
        ],
        "open_obligations": await _obligations(session, workspace_id, [statement.id]),
    }


async def trace_chain(
    session: AsyncSession, workspace_id: uuid.UUID, statement_id: str, direction: str = "support", depth: int = 4
) -> dict:
    """The logical chain through a claim: what it rests on (support) or what rests on it."""
    start = _id(statement_id)
    root = await session.get(Statement, start)
    if root is None or root.workspace_id != workspace_id:
        raise GraphQueryError("No claim with that id in this workspace.")
    if direction not in ("support", "consequences"):
        raise GraphQueryError("direction must be 'support' or 'consequences'.")
    depth = max(1, min(depth, 6))
    seen_statements, steps_out = {start}, []
    queue = deque([(start, 0)])
    while queue and len(seen_statements) < MAX_CHAIN_NODES:
        current, level = queue.popleft()
        if level >= depth:
            continue
        if direction == "support":
            steps = list(await session.scalars(select(ReasoningStep).where(ReasoningStep.conclusion_id == current)))
        else:
            steps = list(
                await session.scalars(
                    select(ReasoningStep)
                    .join(ReasoningPremise, ReasoningPremise.reasoning_step_id == ReasoningStep.id)
                    .where(ReasoningPremise.statement_id == current)
                )
            )
        premises = await _premises(session, [s.id for s in steps])
        for step in steps:
            if any(s["step_id"] == str(step.id) for s in steps_out):
                continue
            steps_out.append(
                {
                    "step_id": str(step.id),
                    "conclusion_id": str(step.conclusion_id),
                    "premise_ids": [str(p) for p in premises[step.id]],
                    "explanation": _cut(step.explanation, 300),
                }
            )
            nxt = premises[step.id] if direction == "support" else [step.conclusion_id]
            for item in nxt:
                if item not in seen_statements:
                    seen_statements.add(item)
                    queue.append((item, level + 1))
    claims = list(await session.scalars(select(Statement).where(Statement.id.in_(seen_statements))))
    return {
        "root": str(start),
        "direction": direction,
        "claims": [_brief(s) for s in claims],
        "reasoning_steps": steps_out,
        "truncated": bool(queue),
    }


async def known_ids(session: AsyncSession, workspace_id: uuid.UUID, ids: list[str]) -> tuple[list[str], list[str]]:
    """Split ids into (claim ids, step ids) that exist in the workspace; drop the rest."""
    parsed = []
    for value in ids:
        try:
            parsed.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    if not parsed:
        return [], []
    claims = await session.scalars(
        select(Statement.id).where(Statement.workspace_id == workspace_id, Statement.id.in_(parsed))
    )
    steps = await session.scalars(
        select(ReasoningStep.id).where(ReasoningStep.workspace_id == workspace_id, ReasoningStep.id.in_(parsed))
    )
    return [str(i) for i in claims], [str(i) for i in steps]


async def run_tool(session: AsyncSession, workspace_id: uuid.UUID, name: str, args: dict) -> dict:
    """Dispatch one read-only graph tool. Shared by the Q&A loop and the research agent."""
    try:
        if name == "graph_overview":
            return await overview(session, workspace_id)
        if name == "search_graph":
            return await search(session, workspace_id, args["query"], int(args.get("limit") or 10))
        if name == "get_graph_node":
            return await get_node(session, workspace_id, args["node_id"])
        if name == "trace_chain":
            return await trace_chain(
                session, workspace_id, args["statement_id"], args.get("direction", "support"), int(args.get("depth") or 4)
            )
    except GraphQueryError as error:
        return {"error": str(error)}
    return {"error": f"Unknown tool {name}."}
