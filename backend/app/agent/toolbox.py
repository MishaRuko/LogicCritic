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
    GRAPH_TOOLS,
    AbstainInput,
    LinkClaimsInput,
    CheckConclusionInput,
    FetchUrlInput,
    FinalizeConclusionInput,
    ReadPaperInput,
    ReadSourceInput,
    RecordClaimInput,
    RecordReasoningInput,
    ReviseClaimInput,
    SearchPapersInput,
)
from app.services import graph_query
from app.agent.web import FetchError, fetch_public, html_to_text
from app.config import get_settings
from app.models import (
    AgentRun,
    Excerpt,
    GraphEdge,
    GraphEvent,
    ResearchGoal,
    Source,
    Statement,
    StatementExcerpt,
)
from app.services.claude_call import ClaudeCallFailed
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
MAX_VERDICT_CHARS = 300
EXCERPT_PREVIEW_CHARS = 140
PROMPT_VERSION = "research_agent_v6"


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
        self._reads = 0  # read_source calls so far
        self._position_id: str | None = None  # the agent's current conclusion-role claim

    async def call(self, name: str, arguments: dict[str, Any], tool_use_id: str) -> dict:
        handlers = {
            "search_papers": (SearchPapersInput, self.search_papers),
            "read_paper": (ReadPaperInput, self.read_paper),
            "fetch_url": (FetchUrlInput, self.fetch_url),
            "read_source": (ReadSourceInput, self.read_source),
            "record_claim": (RecordClaimInput, self.record_claim),
            "record_reasoning": (RecordReasoningInput, self.record_reasoning),
            "revise_claim": (ReviseClaimInput, self.revise_claim),
            "check_conclusion": (CheckConclusionInput, self.check_conclusion),
            "finalize_conclusion": (FinalizeConclusionInput, self.finalize_conclusion),
            "abstain": (AbstainInput, self.abstain),
            "link_claims": (LinkClaimsInput, self.link_claims),
            **{tool: (model, self._graph_tool(tool)) for tool, (_, model) in GRAPH_TOOLS.items()},
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

    # -- the existing graph -------------------------------------------------------------------

    async def link_claims(self, args: LinkClaimsInput, tool_use_id: str) -> dict:
        """Append an audited relation between two claims. Nothing existing is changed."""
        source_id = _uuid(args.source_statement_id, "source_statement_id")
        target_id = _uuid(args.target_statement_id, "target_statement_id")
        if source_id == target_id:
            raise ToolError("A claim cannot be linked to itself.")
        if not args.rationale.strip():
            raise ToolError("Say why the relation holds.")
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            claims = {
                s.id: s
                for s in await session.scalars(
                    select(Statement).where(
                        Statement.workspace_id == run.workspace_id, Statement.id.in_([source_id, target_id])
                    )
                )
            }
            if len(claims) != 2:
                raise ToolError("Both ids must be claims in this workspace's graph.")
            if any(claim.lifecycle == "rejected" for claim in claims.values()):
                raise ToolError("One of these claims was withdrawn; link the claim that replaced it.")
            duplicate = await session.scalar(
                select(GraphEdge).where(
                    GraphEdge.workspace_id == run.workspace_id,
                    GraphEdge.source_node_id == source_id,
                    GraphEdge.target_node_id == target_id,
                    GraphEdge.relation == args.relation,
                )
            )
            if duplicate is not None:
                return {"linked": False, "already_linked": True, "relation_id": str(duplicate.id)}
            material = {i: await _claim_material(session, claims[i]) for i in (source_id, target_id)}
        verdict, audit_rationale = "needs_review", "No independent audit was available."
        if self._judge is not None and hasattr(self._judge, "audit_links"):
            try:
                audits = await self._judge.audit_links([{
                    "source_statement_id": str(source_id), "target_statement_id": str(target_id),
                    "relation": args.relation, "rationale": args.rationale.strip(),
                    "source": material[source_id], "target": material[target_id],
                }])
                if audits.audits:
                    verdict, audit_rationale = audits.audits[0].verdict, audits.audits[0].rationale
            except ClaudeCallFailed as error:
                audit_rationale = f"The audit could not run: {error}"
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            edge = GraphEdge(
                workspace_id=run.workspace_id,
                source_node_kind="statement",
                source_node_id=source_id,
                relation=args.relation,
                target_node_kind="statement",
                target_node_id=target_id,
                metadata_={
                    "lifecycle": "proposed",
                    "generator_rationale": args.rationale.strip(),
                    "audit_verdict": verdict,
                    "audit_rationale": audit_rationale,
                    "run_id": str(run.id),
                    "actor": "research-agent",
                },
            )
            session.add(edge)
            await session.commit()
            return {
                "linked": True,
                "relation_id": str(edge.id),
                "relation": args.relation,
                "audit_verdict": verdict,
                "audit_rationale": audit_rationale,
            }

    def _graph_tool(self, name: str):
        async def handler(args, _: str) -> dict:
            async with self._sessions() as session:
                run = await session.get(AgentRun, self._run_id)
                return await graph_query.run_tool(session, run.workspace_id, name, args.model_dump())

        return handler

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
        self._reads += 1
        page = {
            "source_id": args.source_id,
            "title": source.title,
            "excerpts": shown,
            "next_offset": following if len(shown) < len(excerpts) else None,
        }
        if run.mode == "guarded":
            page["note"] = self._recording_note()
        return page

    def _recording_note(self) -> str:
        """A reminder to build the graph while reading, not after. A hint, never a refusal."""
        note = (
            "Record the claims you will rely on from these excerpts with record_claim now, "
            "before you read further."
        )
        if self._position_id is None and self._reads >= 2:
            note += (
                " You have no working position yet: record your current best answer as a "
                "claim with role 'conclusion', and when later evidence changes your view, "
                "record the new position and call revise_claim on the old one."
            )
        return note

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
        statement_id = str(patch.id_map["claim"])
        note = "Recorded. Use this statement_id in record_reasoning and check_conclusion."
        if args.role == "conclusion":
            if self._position_id is not None:
                note += (
                    f" You already hold a conclusion ({self._position_id}). If this one replaces "
                    f"it, call revise_claim on {self._position_id} with replaced_by_id "
                    f"{statement_id} and say why; otherwise both will count as your conclusions."
                )
            self._position_id = statement_id
        return {"statement_id": statement_id, "note": note}

    async def record_reasoning(self, args: RecordReasoningInput, tool_use_id: str) -> dict:
        premise_ids = [_uuid(i, "premise_ids") for i in args.premise_ids]
        conclusion_id = _uuid(args.conclusion_id, "conclusion_id")
        revises = _uuid(args.revises_step_id, "revises_step_id") if args.revises_step_id else None
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            used = await session.scalars(
                select(Statement).where(Statement.id.in_([*premise_ids, conclusion_id]))
            )
            for statement in used:
                if statement.lifecycle == "rejected":
                    raise ToolError(
                        f"Claim {statement.id} was withdrawn. Record reasoning from the corrected "
                        "claim instead."
                    )
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
            if args.weighing and args.weighing.strip():
                operations.append(
                    AnnotationCreateOperation(
                        op="create_annotation",
                        subject_type="reasoning_step",
                        subject_id="step",
                        type="custom:weighing",
                        value={"principle": args.weighing.strip()},
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

    async def revise_claim(self, args: ReviseClaimInput, tool_use_id: str) -> dict:
        old_id = _uuid(args.statement_id, "statement_id")
        new_id = _uuid(args.replaced_by_id, "replaced_by_id") if args.replaced_by_id else None
        if not args.reason.strip():
            raise ToolError("Say why the claim is being revised.")
        key = f"agent:{self._run_id}:{tool_use_id}"
        async with self._sessions() as session:
            run = await session.get(AgentRun, self._run_id)
            if await session.scalar(
                select(GraphEvent.id).where(
                    GraphEvent.workspace_id == run.workspace_id, GraphEvent.idempotency_key == key
                )
            ):
                return {"withdrawn": str(old_id), "replaced_by": str(new_id) if new_id else None}
            old = await session.get(Statement, old_id)
            if old is None or old.workspace_id != run.workspace_id:
                raise ToolError(f"No recorded claim {old_id}. Use an id returned by record_claim.")
            if (old.provenance or {}).get("run_id") != str(run.id):
                raise ToolError("Only claims recorded in this run can be revised.")
            if old.lifecycle == "rejected":
                raise ToolError("That claim was already withdrawn.")
            if new_id is not None:
                new = await session.get(Statement, new_id)
                if (
                    new is None
                    or new.workspace_id != run.workspace_id
                    or new.lifecycle == "rejected"
                ):
                    raise ToolError(
                        f"The replacement {new_id} must be a claim you recorded and have not "
                        "withdrawn. Record it first with record_claim."
                    )
                if new_id == old_id:
                    raise ToolError("A claim cannot replace itself.")
                await GraphPatchExecutor(session, run.workspace_id).apply(
                    GraphPatchRequest(
                        idempotency_key=f"{key}:link",
                        operations=[
                            RelationCreateOperation(
                                op="create_relation",
                                source_node_kind="statement",
                                source_node_id=new_id,
                                relation="revises",
                                target_node_kind="statement",
                                target_node_id=old_id,
                                metadata={"reason": args.reason.strip()},
                            )
                        ],
                    )
                )
            old_role = old.role
            if self._position_id == str(old_id):
                self._position_id = str(new_id) if new_id else None
            old.lifecycle = "rejected"
            old.superseded_by = new_id
            session.add(
                GraphEvent(
                    workspace_id=run.workspace_id,
                    event_type="agent_revision",
                    idempotency_key=key,
                    payload={
                        "withdrawn": str(old_id),
                        "replaced_by": str(new_id) if new_id else None,
                        "reason": args.reason.strip(),
                    },
                    provenance=self._provenance(run).model_dump(mode="json", exclude_none=True),
                )
            )
            await session.commit()
        return {
            "withdrawn": str(old_id),
            "replaced_by": str(new_id) if new_id else None,
            "role": old_role,
            "next": "Re-record any reasoning that used this claim (record_reasoning with "
            "revises_step_id), then check your conclusion again.",
        }

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
        if len(args.verdict) > MAX_VERDICT_CHARS:
            raise ToolError(
                f"The verdict is the bottom line only: at most {MAX_VERDICT_CHARS} characters. "
                "Put the evidence and deductions in the conclusion."
            )
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
            "verdict": args.verdict.strip(),
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


async def _claim_material(session, statement: Statement) -> dict:
    rows = await session.scalars(
        select(Excerpt.text)
        .join(StatementExcerpt, StatementExcerpt.excerpt_id == Excerpt.id)
        .where(StatementExcerpt.statement_id == statement.id)
    )
    return {"statement_id": str(statement.id), "text": statement.text, "excerpts": [t[:1200] for t in rows]}
