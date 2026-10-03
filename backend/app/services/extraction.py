import uuid

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Excerpt, GraphEvent, Source
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

PROMPT_VERSION = "source_extraction_v2"

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
    "invent sources, facts, citations, or certainty. Extract the central explicitly stated "
    "methods, "
    "results, and conclusions from each excerpt set; do not return an empty result when those are "
    "present. Use unique client_ref values. Call the submit_extraction tool with the result."
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
    excerpts = list(await session.scalars(query.order_by(Excerpt.sequence)))
    context = _excerpt_context(excerpts, settings.max_extraction_context_chars)
    if not context:
        raise HTTPException(status_code=422, detail="Source has no extractable excerpts")

    payload = {
        "model": model,
        "max_tokens": EXTRACTION_MAX_TOKENS,
        "system": EXTRACTION_SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": f"Source excerpts:\n\n{context}"},
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
        output = drop_unresolvable_steps(ExtractionOutput.model_validate(tool_use["input"]))
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
