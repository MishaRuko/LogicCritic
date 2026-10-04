import re
import uuid

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Excerpt, GraphEvent, Source, Statement, StatementExcerpt
from app.schemas import (
    ExtractionOutput,
    GraphPatchRequest,
    ProvenanceInput,
    ReasoningStepCreateOperation,
    SourceExtractionRequest,
    StatementCreateOperation,
)
from app.services.claude_errors import describe_claude_failure, ensure_complete
from app.services.claude_tools import strict_input_schema, strict_tool
from app.services.graph_patches import GraphPatchExecutor, graph_patch_response_from_event

PROMPT_VERSION = "source_extraction_v8"
NON_CLAIM_SECTIONS = {"retraction notice"}

EXTRACTION_SYSTEM_PROMPT = (
    "Extract only statements directly supported by the supplied source excerpts and explicit "
    "reasoning steps between extracted statements. Every statement must cite one or more excerpt "
    "IDs from the supplied context. A reasoning step is allowed only when the source explicitly "
    "states the connection between its premises and conclusion. Do not create a step merely "
    "because "
    "the statements appear in the same source. Do not add calculations, mechanisms, causal claims, "
    "generalizations, implications, or background knowledge unless they are explicitly written in "
    "the "
    "cited excerpts. If the connection needs an unstated premise, omit the reasoning step. Keep "
    "each "
    "explanation limited to the source-stated connection; do not explain it with new facts. Do not "
    "invent sources, facts, citations, or certainty. Extract only consequential claims: primary "
    "outcomes and effect sizes, essential study design needed to interpret them, substantive "
    "limitations, directly tested mechanisms, and final conclusions. Exclude routine procedures, "
    "registry or administrative details, citation/background boilerplate, repeated wording, and "
    "retraction-process metadata. Prefer one precise claim over several sentence-level restatements. "
    "Use core only for the minimum set of claims needed to state the paper's central contribution: "
    "primary results, final conclusions, and limitations that materially constrain those conclusions. "
    "Do not mark a claim core merely because it is technically important or appears in Discussion. "
    "Classify consequential but noncentral findings, subgroup or safety results, scope qualifications, "
    "implications, and future-research conclusions as secondary. Classify essential design facts, "
    "measurements, and details used directly as evidence or premises as supporting. Exclude standalone "
    "background, prior-work summaries, routine methods, administrative facts, and procedural detail "
    "rather than preserving them as statements. "
    "Do not impose a numeric claim limit: include every consequential claim, but not sentence-level "
    "coverage. Use the supplied document overview to judge paper-level importance. Do not return an "
    "empty result when consequential claims are present. Use unique client_ref values. Call the "
    "submit_extraction tool with the result."
)


EXTRACTION_MAX_TOKENS = 16000
EXTRACTION_TIMEOUT_SECONDS = 300


def extraction_schema() -> dict:
    return strict_input_schema(ExtractionOutput)


def drop_unresolvable_steps(output: ExtractionOutput) -> ExtractionOutput:
    """Remove reasoning steps that cite statements this result does not contain.

    The patch would reject such a step and fail the whole chunk, taking its good statements with
    it. A step with an unknown reference cannot be grounded, so it is the only thing dropped.
    """
    known = {item.client_ref for item in output.statements}
    kept = [
        step
        for step in output.reasoning_steps
        if step.conclusion_ref in known and all(ref in known for ref in step.premise_refs)
    ]
    return output.model_copy(update={"reasoning_steps": kept})


def drop_orphan_supporting(output: ExtractionOutput) -> ExtractionOutput:
    """Keep a supporting statement only if something kept relies on it.

    Supporting means "evidence or a premise for another claim". One that names no such claim, or
    names one that is not kept, is background by definition, so no list of topics is needed.
    """
    anchors = {s.client_ref for s in output.statements if s.salience != "supporting"}
    used_as_premise = {
        ref
        for step in output.reasoning_steps
        if step.conclusion_ref in anchors
        for ref in step.premise_refs
    }
    kept = [
        s
        for s in output.statements
        if s.salience != "supporting"
        or s.supports_ref in anchors
        or s.client_ref in used_as_premise
    ]
    return output.model_copy(update={"statements": kept})


def drop_duplicate_statements(
    output: ExtractionOutput, existing_texts: list[str]
) -> ExtractionOutput:
    """Drop repeated claims within one source and any local steps that depended on them."""
    seen = {_normalized_claim(text) for text in existing_texts}
    kept = []
    kept_refs = set()
    for statement in output.statements:
        normalized = _normalized_claim(statement.text)
        if normalized in seen:
            continue
        seen.add(normalized)
        kept.append(statement)
        kept_refs.add(statement.client_ref)
    steps = [
        step
        for step in output.reasoning_steps
        if step.conclusion_ref in kept_refs and all(ref in kept_refs for ref in step.premise_refs)
    ]
    return output.model_copy(update={"statements": kept, "reasoning_steps": steps})


def _normalized_claim(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


async def extract_source_to_patch(
    session: AsyncSession,
    source: Source,
    request: SourceExtractionRequest,
    excerpt_ids: list[uuid.UUID] | None = None,
) -> tuple[str, object]:
    settings = get_settings()
    model = request.model or settings.claude_model
    existing = await session.scalar(
        select(GraphEvent).where(
            GraphEvent.workspace_id == source.workspace_id,
            GraphEvent.idempotency_key == request.idempotency_key,
        )
    )
    if existing is not None:
        if existing.event_type != "graph_patch":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key was already used by a different operation.",
            )
        return model, graph_patch_response_from_event(existing)

    if not settings.claude_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Claude extraction is unavailable until CLAUDE_API_KEY is configured.",
        )

    query = select(Excerpt).where(Excerpt.source_id == source.id)
    if excerpt_ids is not None:
        query = query.where(Excerpt.id.in_(excerpt_ids))
    excerpts = [
        excerpt
        for excerpt in await session.scalars(query.order_by(Excerpt.sequence))
        if is_extractable_excerpt(
            excerpt, fulltext_available=bool((source.metadata_ or {}).get("fulltext_imported"))
        )
    ]
    context = _excerpt_context(excerpts, settings.max_extraction_context_chars)
    if not context:
        raise HTTPException(status_code=422, detail="Source has no extractable excerpts")

    existing_claims = await _existing_source_statement_texts(session, source.id)
    prior_context = "\n".join(f"- {text}" for text in existing_claims)
    payload = {
        "model": model,
        "max_tokens": EXTRACTION_MAX_TOKENS,
        "system": EXTRACTION_SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Document overview (for importance only):\n\n"
                    f"{await _document_overview(session, source.id)}\n\n"
                    "Claims already extracted from earlier chunks of this source "
                    "(do not repeat or paraphrase them):\n\n"
                    f"{prior_context or '(none)'}\n\nSource excerpts to extract:\n\n{context}"
                ),
            },
        ],
        "tools": [
            strict_tool(
                "submit_extraction",
                "Submit source-grounded statements and explicit reasoning steps.",
                ExtractionOutput,
            )
        ],
        "tool_choice": {"type": "tool", "name": "submit_extraction"},
    }
    headers = {
        "x-api-key": settings.claude_api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=EXTRACTION_TIMEOUT_SECONDS) as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages", json=payload, headers=headers
            )
            response.raise_for_status()
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Claude extraction request failed; no graph update was applied. "
                f"{describe_claude_failure(error)}"
            ).strip(),
        ) from error

    ensure_complete(response.json(), "extraction")
    try:
        tool_use = next(
            item
            for item in response.json()["content"]
            if item["type"] == "tool_use" and item["name"] == "submit_extraction"
        )
        output = drop_duplicate_statements(
            ExtractionOutput.model_validate(tool_use["input"]), existing_claims
        )
        output = drop_unresolvable_steps(drop_orphan_supporting(output))
    except (KeyError, StopIteration, TypeError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Claude returned an invalid extraction; no graph update was applied.",
        ) from error

    if not output.statements and not output.reasoning_steps:
        return model, None
    available_excerpt_ids = {item.id for item in excerpts}
    cited_excerpt_ids = {
        excerpt_id for item in output.statements for excerpt_id in item.excerpt_ids
    }
    if not cited_excerpt_ids <= available_excerpt_ids:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Claude cited excerpts outside the source context; no graph update was applied.",
        )

    provenance = ProvenanceInput(
        actor_type="extractor",
        actor_id="anthropic",
        model=model,
        prompt_version=PROMPT_VERSION,
        run_id=str(uuid.uuid4()),
    )
    patch_request = GraphPatchRequest(
        idempotency_key=request.idempotency_key,
        operations=[
            *[
                StatementCreateOperation(
                    op="create_statement",
                    client_ref=item.client_ref,
                    text=item.text,
                    assertion_mode=item.assertion_mode,
                    role=item.role,
                    salience=item.salience,
                    excerpt_ids=item.excerpt_ids,
                    provenance=provenance,
                )
                for item in output.statements
            ],
            *[
                ReasoningStepCreateOperation(
                    op="create_reasoning_step",
                    client_ref=item.client_ref,
                    premise_ids=item.premise_refs,
                    conclusion_id=item.conclusion_ref,
                    explanation=item.explanation,
                    provenance=provenance,
                )
                for item in output.reasoning_steps
            ],
        ],
    )
    patch = await GraphPatchExecutor(session, source.workspace_id).apply(patch_request)
    return model, patch


def _excerpt_context(excerpts: list[Excerpt], limit: int) -> str:
    chunks: list[str] = []
    used = 0
    for excerpt in excerpts:
        chunk = f"Excerpt {excerpt.id}:\n{excerpt.text}\n"
        if used + len(chunk) > limit:
            break
        chunks.append(chunk)
        used += len(chunk)
    return "\n".join(chunks)


def is_extractable_excerpt(excerpt: Excerpt, *, fulltext_available: bool = False) -> bool:
    """Retain document metadata as context without turning it into graph claims."""
    section = str(excerpt.locator.get("section", "")).strip().casefold()
    path = str(excerpt.locator.get("jsonPath", "")).strip().casefold()
    return (
        section not in NON_CLAIM_SECTIONS
        and path != "title"
        and not (fulltext_available and path == "abstract")
    )


async def _document_overview(session: AsyncSession, source_id: uuid.UUID, limit: int = 6000) -> str:
    excerpts = list(
        await session.scalars(
            select(Excerpt).where(Excerpt.source_id == source_id).order_by(Excerpt.sequence)
        )
    )
    preferred = []
    for excerpt in excerpts:
        section = str(excerpt.locator.get("section", "")).casefold()
        path = str(excerpt.locator.get("jsonPath", "")).casefold()
        if path == "title" or section in {
            "abstract",
            "summary",
            "conclusion",
            "conclusions",
            "limitations",
        }:
            preferred.append(excerpt.text)
    if not preferred:
        preferred = [item.text for item in excerpts[:3]]
    overview = "\n\n".join(preferred)
    return overview[:limit]


async def _existing_source_statement_texts(
    session: AsyncSession, source_id: uuid.UUID
) -> list[str]:
    return list(
        await session.scalars(
            select(Statement.text)
            .join(StatementExcerpt, StatementExcerpt.statement_id == Statement.id)
            .join(Excerpt, Excerpt.id == StatementExcerpt.excerpt_id)
            .where(Excerpt.source_id == source_id)
            .distinct()
        )
    )
