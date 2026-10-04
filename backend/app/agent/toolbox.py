"""Carries out the tool calls of a research agent against the workspace.

Every tool returns a plain dict for the model to read. A failure is returned as
`{"error": ...}` (reported to the model as a tool error), never raised, so the agent can
correct itself instead of the run dying.
"""

import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent import guardrail
from app.agent.tool_models import (
    CAUSAL_DESIGNS,
    AbstainInput,
    CheckConclusionInput,
    FetchUrlInput,
    FinalizeConclusionInput,
    ReadPaperInput,
    ReadSourceInput,
    RecordClaimInput,
    RecordReasoningInput,
    SearchPapersInput,
)
from app.agent.web import FetchError, fetch_public, html_to_text
from app.config import get_settings
from app.models import AgentRun, Excerpt, ResearchGoal, Source
from app.schemas import (
    AnnotationCreateOperation,
    GraphPatchRequest,
    ProvenanceInput,
    ReasoningStepCreateOperation,
    RelationCreateOperation,
    StatementCreateOperation,
)
from app.services.amass import AmassClient, AmassError
from app.services.amass_import import (
    amass_http_error,
    import_biomed_record,
    split_block,
)
from app.services.graph_patches import GraphPatchExecutor
from app.services.judge import Judge
from app.services.pdf_ingestion import PdfIngestionError, parse_pdf
from app.services.source_store import DuplicateSource, NewSource, store_source
from app.services.text_ingestion import ParsedExcerpt, parse_structured_text

log = logging.getLogger(__name__)

READ_PAGE_CHARS = 14_000
EXCERPT_PREVIEW_CHARS = 140
PROMPT_VERSION = "research_agent_v2"


class ToolError(Exception):
    """A tool call that cannot be done as asked. The message is for the agent."""


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as error:
        raise ToolError(
            f"{what} must be an id returned by an earlier tool call, not {value!r}."
        ) from error


class Toolbox:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        run_id: uuid.UUID,
        amass: AmassClient | None,
        judge: Judge | None = None,
    ) -> None:
        self._sessions = sessions
        self._run_id = run_id
        self._amass = amass
        self._judge = judge

    async def call(self, name: str, arguments: dict[str, Any], tool_use_id: str) -> dict:
        handlers = {
            "search_papers": (SearchPapersInput, self.search_papers),
            "read_paper": (ReadPaperInput, self.read_paper),
            "fetch_url": (FetchUrlInput, self.fetch_url),
            "read_source": (ReadSourceInput, self.read_source),
            "record_claim": (RecordClaimInput, self.record_claim),
            "record_reasoning": (RecordReasoningInput, self.record_reasoning),
            "check_conclusion": (CheckConclusionInput, self.check_conclusion),
            "finalize_conclusion": (FinalizeConclusionInput, self.finalize_conclusion),
            "abstain": (AbstainInput, self.abstain),
        }
        if name not in handlers:
            return {"error": f"Unknown tool {name!r}."}
        model, handler = handlers[name]
        try:
            args = model.model_validate(arguments)
            return await handler(args, tool_use_id)
        except ValidationError as error:
            return {"error": f"Invalid arguments: {error.errors()[0]['msg']}"}
        except (ToolError, FetchError, guardrail.GuardrailError) as error:
            return {"error": str(error)}
        except AmassError as error:
            return {"error": amass_http_error(error).detail}
        except HTTPException as error:
            return {"error": str(error.detail)}

    # -- reading ------------------------------------------------------------------------------

    def _need_amass(self) -> AmassClient:
        if self._amass is None:
            raise ToolError("Paper search is unavailable: no Amass API key is configured.")
        return self._amass

    async def search_papers(self, args: SearchPapersInput, _: str) -> dict:
        records = await self._need_amass().search_biomedcore(
            args.query,
            max(1, min(args.limit, 15)),
            min_publication_date=args.published_after,
            is_retracted=False if args.exclude_retracted else None,
        )
        return {
            "results": [
                {
                    "amass_id": r.amass_id,
                    "pmid": r.pmid,
                    "doi": r.doi,
                    "title": r.title,
                    "journal": r.journal,
                    "published": r.publication_date,
                    "citations": r.citation_count,
                    "retracted": r.is_retracted,
                    "has_fulltext": r.has_fulltext,
                    "abstract": (r.abstract or "")[:700],
                }
                for r in records
            ]
        }

    async def read_paper(self, args: ReadPaperInput, _: str) -> dict:
        if sum(bool(v) for v in (args.amass_id, args.pmid, args.doi)) != 1:
            raise ToolError("Give exactly one of amass_id, pmid or doi.")
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            result = await import_biomed_record(
                session,
                self._need_amass(),
                run.workspace_id,
                amass_id=args.amass_id,
                pmid=args.pmid,
                doi=args.doi,
                include_fulltext=True,
            )
        source = result.source
        return {
            "source_id": str(source.id),
            "title": source.title,
            "ids": source.external_ids,
            "published": source.metadata_.get("publication_date"),
            "retracted": result.retracted,
            "retraction_warning": (
                "This paper has been retracted. Do not rely on it as evidence for a conclusion."
                if result.retracted
                else None
            ),
            "excerpts": len(result.excerpts),
            "index": _index(result.excerpts),
            "next": "Call read_source with this source_id to read the text.",
        }

    async def fetch_url(self, args: FetchUrlInput, _: str) -> dict:
        page = await fetch_public(args.url, user_agent=get_settings().agent_user_agent)
        new = _page_to_source(page)
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            try:
                source, excerpts = await store_source(session, run.workspace_id, new)
                again = False
            except DuplicateSource as duplicate:
                source = await session.get(Source, duplicate.source_id)
                excerpts = list(
                    await session.scalars(
                        select(Excerpt)
                        .where(Excerpt.source_id == source.id)
                        .order_by(Excerpt.sequence)
                    )
                )
                again = True
        return {
            "source_id": str(source.id),
            "title": source.title,
            "url": page.final_url,
            "already_fetched": again,
            "excerpts": len(excerpts),
            "index": _index(excerpts),
            "next": "Call read_source with this source_id to read the text.",
        }

    async def read_source(self, args: ReadSourceInput, _: str) -> dict:
        source_id = _uuid(args.source_id, "source_id")
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            source = await session.get(Source, source_id)
            if source is None or source.workspace_id != run.workspace_id:
                raise ToolError("No such source in this workspace.")
            excerpts = list(
                await session.scalars(
                    select(Excerpt)
                    .where(Excerpt.source_id == source_id, Excerpt.sequence >= max(0, args.offset))
                    .order_by(Excerpt.sequence)
                )
            )
        shown, used = [], 0
        for item in excerpts:
            if shown and used + len(item.text) > READ_PAGE_CHARS:
                break
            shown.append(
                {
                    "excerpt_id": str(item.id),
                    "n": item.sequence,
                    "section": item.locator.get("section"),
                    "text": item.text,
                }
            )
            used += len(item.text)
        following = shown[-1]["n"] + 1 if shown else args.offset
        return {
            "source_id": args.source_id,
            "title": source.title,
            "excerpts": shown,
            "next_offset": following if len(shown) < len(excerpts) else None,
        }

    # -- recording ----------------------------------------------------------------------------

    def _provenance(self, run: AgentRun) -> ProvenanceInput:
        return ProvenanceInput(
            actor_type="agent",
            actor_id="research-agent",
            model=run.model,
            prompt_version=PROMPT_VERSION,
            run_id=str(run.id),
        )

    async def record_claim(self, args: RecordClaimInput, tool_use_id: str) -> dict:
        excerpt_ids = [_uuid(i, "excerpt_ids") for i in args.excerpt_ids]
        if not args.text.strip():
            raise ToolError("A claim needs text.")
        if args.claim_strength == "causal" and args.causal_support is None:
            raise ToolError(
                "A causal claim needs causal_support: the study design that supports a causal "
                "reading and what in the cited text shows it. If the evidence is observational, "
                "record the claim as associative instead."
            )
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            goal = await session.get(ResearchGoal, run.goal_id)
            for index in args.criteria_satisfied:
                if not 0 <= index < len(goal.completion_criteria):
                    raise ToolError(f"There is no completion criterion {index}.")
            provenance = self._provenance(run)
            operations: list[Any] = [
                StatementCreateOperation(
                    op="create_statement",
                    client_ref="claim",
                    text=args.text.strip(),
                    assertion_mode=args.assertion_mode,
                    role=args.role,
                    excerpt_ids=excerpt_ids,
                    provenance=provenance,
                )
            ]

            def annotate(kind: str, value: dict) -> None:
                operations.append(
                    AnnotationCreateOperation(
                        op="create_annotation",
                        subject_type="statement",
                        subject_id="claim",
                        type=kind,
                        value=value,
                        provenance=provenance,
                    )
                )

            if args.claim_strength:
                annotate("claim_strength", {"value": args.claim_strength})
            if args.causal_support is not None:
                annotate(
                    "causal_support",
                    {
                        "supported": args.causal_support.design in CAUSAL_DESIGNS,
                        "design": args.causal_support.design,
                        "justification": args.causal_support.justification,
                    },
                )
            if args.scope and any(args.scope.model_dump().values()):
                annotate("custom:scope", args.scope.model_dump())
            if args.criteria_satisfied:
                annotate(
                    "custom:satisfies_criteria", {"indexes": sorted(set(args.criteria_satisfied))}
                )
            patch = await GraphPatchExecutor(session, run.workspace_id).apply(
                GraphPatchRequest(
                    idempotency_key=f"agent:{run.id}:{tool_use_id}", operations=operations
                )
            )
        return {
            "statement_id": str(patch.id_map["claim"]),
            "note": "Recorded. Use this statement_id in record_reasoning and check_conclusion.",
        }

    async def record_reasoning(self, args: RecordReasoningInput, tool_use_id: str) -> dict:
        premise_ids = [_uuid(i, "premise_ids") for i in args.premise_ids]
        conclusion_id = _uuid(args.conclusion_id, "conclusion_id")
        revises = _uuid(args.revises_step_id, "revises_step_id") if args.revises_step_id else None
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            provenance = self._provenance(run)
            operations: list[Any] = [
                ReasoningStepCreateOperation(
                    op="create_reasoning_step",
                    client_ref="step",
                    premise_ids=premise_ids,
                    conclusion_id=conclusion_id,
                    explanation=args.explanation.strip(),
                    provenance=provenance,
                )
            ]
            if args.scope_change:
                operations.append(
                    AnnotationCreateOperation(
                        op="create_annotation",
                        subject_type="reasoning_step",
                        subject_id="step",
                        type="scope_transition",
                        value={
                            "from": args.scope_change.from_scope,
                            "to": args.scope_change.to_scope,
                            "justified": args.scope_change.justified,
                        },
                        provenance=provenance,
                    )
                )
            if revises:
                operations.append(
                    RelationCreateOperation(
                        op="create_relation",
                        source_node_kind="reasoning_step",
                        source_node_id="step",
                        relation="revises",
                        target_node_kind="reasoning_step",
                        target_node_id=revises,
                    )
                )
            patch = await GraphPatchExecutor(session, run.workspace_id).apply(
                GraphPatchRequest(
                    idempotency_key=f"agent:{run.id}:{tool_use_id}", operations=operations
                )
            )
        return {"step_id": str(patch.id_map["step"])}

    # -- guardrail ----------------------------------------------------------------------------

    async def check_conclusion(self, args: CheckConclusionInput, _: str) -> dict:
        statement_id = _uuid(args.statement_id, "statement_id")
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            goal = await session.get(ResearchGoal, run.goal_id)
            result = await guardrail.check(session, run, goal, statement_id, self._judge)
        return result.packet

    async def finalize_conclusion(self, args: FinalizeConclusionInput, _: str) -> dict:
        statement_id = _uuid(args.statement_id, "statement_id")
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            goal = await session.get(ResearchGoal, run.goal_id)
            accepted, result = await guardrail.finalize(
                session, run, goal, statement_id, args.certainty, self._judge
            )
        if not accepted:
            return {
                "accepted": False,
                "reason": (
                    f"The evidence does not support a conclusion at certainty {args.certainty!r}. "
                    f"You may finalize as: {result.allowed_certainties()}."
                ),
                "obligations": [o.as_dict() for o in result.obligations],
            }
        return {
            "accepted": True,
            "certainty": args.certainty,
            "conclusion": result.statement_text,
            "caveats": [o.as_dict() for o in result.obligations],
            "next": "State your answer. Include every caveat above.",
        }

    async def abstain(self, args: AbstainInput, _: str) -> dict:
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            run.certainty = "abstained"
            run.final_report = f"No conclusion. {args.reason}"
            await session.commit()
        return {"accepted": True, "next": "Briefly explain why you are abstaining."}


def _index(excerpts: list[Excerpt]) -> list[dict]:
    """A compact map of a source: section and opening words of each excerpt."""
    return [
        {
            "n": item.sequence,
            "section": item.locator.get("section") or item.locator.get("jsonPath"),
            "starts": item.text[:EXCERPT_PREVIEW_CHARS].replace("\n", " "),
        }
        for item in excerpts[:60]
    ]


def _page_to_source(page) -> NewSource:
    """A fetched page as a source. HTML becomes stored text, so excerpt offsets index it."""
    host = re.sub(r"^https?://", "", page.final_url).split("/")[0]
    metadata = {
        "url": page.final_url,
        "fetched_at": datetime.now(UTC).isoformat(),
        "content_type": page.content_type,
        "bytes": len(page.content),
    }
    ids = {"url": page.final_url}
    if page.content_type == "application/pdf":
        try:
            parsed = parse_pdf(page.content)
        except PdfIngestionError as error:
            raise ToolError(error.message) from error
        return NewSource(
            kind="web_page",
            origin="agent",
            title=parsed.title or host,
            mime_type="application/pdf",
            filename=_pdf_name(page.final_url),
            content=page.content,
            excerpts=parsed.excerpts,
            external_ids=ids,
            metadata={**metadata, **parsed.metadata},
        )
    raw = page.content.decode("utf-8", errors="replace")
    title, text = (raw, raw) if page.content_type == "text/plain" else html_to_text(raw)
    if page.content_type == "text/plain":
        title = host
    if not text.strip():
        raise ToolError("The page has no readable text (it may need JavaScript to load).")
    excerpts: list[ParsedExcerpt] = []
    for item in parse_structured_text(text):
        section = item.locator.get("section")
        for body, start, label in split_block(item.text, int(item.locator["start"]), section):
            locator = {
                **item.locator,
                "start": start,
                "end": start + len(body),
                "sequence": len(excerpts),
            }
            if label is not None:
                locator["section"] = label
            excerpts.append(ParsedExcerpt(text=body, sequence=len(excerpts), locator=locator))
    return NewSource(
        kind="web_page",
        origin="agent",
        title=title or host,
        mime_type="text/plain",
        filename="page.txt",
        content=text.encode("utf-8"),
        excerpts=excerpts,
        external_ids=ids,
        metadata={**metadata, "parser": "web_text_v1"},
    )


def _pdf_name(url: str) -> str:
    name = Path(urlsplit(url).path).name or "document"
    return name if name.lower().endswith(".pdf") else f"{name}.pdf"


def tool_result_text(result: dict, limit: int = 60_000) -> str:
    """JSON for the model, cut off before it can swamp the context."""
    text = json.dumps(result, ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + '..."truncated"'
