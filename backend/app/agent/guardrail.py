"""The guardrail: what stands between a conclusion and a justified answer.

`check` looks at one recorded conclusion and everything it rests on (its upstream cone), runs
the verifier, and returns the open obligations. It says what must be established, never which
action to take: the agent chooses. `finalize` is the hard gate: it refuses a conclusion the
evidence graph does not support at the certainty claimed.

Models only propose here (the critic audits steps, synthesis proposes links between sources).
Whether an obligation exists is decided by deterministic rules over the accepted graph.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import assurance
from app.config import get_settings
from app.models import (
    AgentEvent,
    AgentRun,
    Annotation,
    Excerpt,
    GraphEdge,
    Issue,
    JudgeVerdict,
    ProofObligation,
    ReasoningPremise,
    ReasoningStep,
    ResearchGoal,
    Source,
    SourceValidity,
    Statement,
    StatementExcerpt,
)
from app.schemas import ArgumentCheckRequest, SynthesisRequest
from app.services.argument_check import check_arguments
from app.services.claude_call import ClaudeCallFailed
from app.services.judge import Judge, JudgeOutput
from app.services.synthesis import synthesize_workspace
from app.services.verification import run_verification

log = logging.getLogger(__name__)

CRITICAL_RULES = {
    "ungrounded_statement",
    "missing_premise",
    "invalidated_source",
    "scope_leap",
    "causality_overclaim",
    "direct_conflict",
    "unresolved_conflict",
    "unmet_criteria",
    "causal_design_not_shown",
    "judge_unavailable",
    "withdrawn_premise",
    "unreasoned_conclusion",
}
# Even a conditional conclusion cannot rest on these.
HARD_BLOCKERS = {"invalidated_source", "ungrounded_statement", "withdrawn_premise"}
AGENT_RULES = [
    "unresolved_conflict",
    "unmet_criteria",
    "causal_design_not_shown",
    "judge_unavailable",
]
MAX_CONE_STATEMENTS = 40
MAX_GAP_CHARS = 900


@dataclass
class Obligation:
    kind: str
    severity: str  # "critical" or "advisory"
    description: str
    required_condition: str
    statement_id: uuid.UUID | None = None
    step_id: uuid.UUID | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "description": self.description,
            "required_condition": self.required_condition,
            "applies_to": str(self.statement_id or self.step_id or ""),
        }


@dataclass
class CheckResult:
    statement_id: uuid.UUID
    statement_text: str
    obligations: list[Obligation] = field(default_factory=list)
    packet: dict = field(default_factory=dict)

    @property
    def critical(self) -> list[Obligation]:
        return [item for item in self.obligations if item.severity == "critical"]

    def allowed_certainties(self) -> list[str]:
        allowed = ["hypothesis"]
        if not any(item.kind in HARD_BLOCKERS for item in self.obligations):
            allowed.insert(0, "conditional")
        if not self.critical:
            allowed.insert(0, "established")
        return allowed


class GuardrailError(Exception):
    """The request cannot be checked (unknown statement and the like). Written for the agent."""


async def _active_steps(session: AsyncSession, workspace_id: uuid.UUID) -> list[ReasoningStep]:
    """Reasoning steps that count: not rejected, and not replaced by a later revision."""
    steps = list(
        await session.scalars(
            select(ReasoningStep).where(
                ReasoningStep.workspace_id == workspace_id, ReasoningStep.lifecycle != "rejected"
            )
        )
    )
    revised = set(
        await session.scalars(
            select(GraphEdge.target_node_id).where(
                GraphEdge.workspace_id == workspace_id,
                GraphEdge.relation == "revises",
                GraphEdge.target_node_kind == "reasoning_step",
            )
        )
    )
    return [step for step in steps if step.id not in revised]


async def upstream_cone(
    session: AsyncSession, workspace_id: uuid.UUID, statement_id: uuid.UUID
) -> tuple[set[uuid.UUID], list[ReasoningStep], dict[uuid.UUID, list[uuid.UUID]]]:
    """The statement, the active steps concluding it, and, recursively, their premises.

    A withdrawn (rejected) claim is not part of the argument, so the walk does not enter it. The
    step that still lists it as a premise stays in the cone: `check` reports that step as stale.
    """
    steps = await _active_steps(session, workspace_id)
    withdrawn = set(
        await session.scalars(
            select(Statement.id).where(
                Statement.workspace_id == workspace_id, Statement.lifecycle == "rejected"
            )
        )
    )
    premise_rows = await session.execute(
        select(ReasoningPremise.reasoning_step_id, ReasoningPremise.statement_id).where(
            ReasoningPremise.reasoning_step_id.in_([step.id for step in steps])
        )
    )
    premises: dict[uuid.UUID, list[uuid.UUID]] = {step.id: [] for step in steps}
    for step_id, premise_id in premise_rows:
        premises[step_id].append(premise_id)

    by_conclusion: dict[uuid.UUID, list[ReasoningStep]] = {}
    for step in steps:
        by_conclusion.setdefault(step.conclusion_id, []).append(step)

    statements, cone_steps, queue = {statement_id}, [], [statement_id]
    while queue:
        current = queue.pop()
        for step in by_conclusion.get(current, []):
            if step not in cone_steps:
                cone_steps.append(step)
            for premise_id in premises[step.id]:
                if premise_id not in statements and premise_id not in withdrawn:
                    statements.add(premise_id)
                    queue.append(premise_id)
    return statements, cone_steps, premises


async def check(
    session: AsyncSession,
    run: AgentRun,
    goal: ResearchGoal,
    statement_id: uuid.UUID,
    judge: Judge | None = None,
) -> CheckResult:
    workspace_id = run.workspace_id
    target = await session.get(Statement, statement_id)
    if target is None or target.workspace_id != workspace_id:
        raise GuardrailError(
            f"No recorded statement {statement_id}. Use an id returned by record_claim."
        )
    if target.lifecycle == "rejected":
        raise GuardrailError("That statement was rejected and cannot be a conclusion.")

    notes: list[str] = []
    cone, cone_steps, premises = await upstream_cone(session, workspace_id, statement_id)
    await run_verification(session, workspace_id)
    notes += await _audit_new_steps(session, run, cone_steps, judge)
    await run_verification(session, workspace_id)
    notes += await _link_sources(session, run, judge)

    obligations = await _withdrawn_obligations(session, workspace_id, cone_steps, premises)
    if target.assertion_mode == "asserted" and not any(
        step.conclusion_id == statement_id for step in cone_steps
    ):
        obligations.append(
            Obligation(
                kind="unreasoned_conclusion",
                severity="critical",
                description=(
                    "This conclusion is your own assertion, but no recorded reasoning leads to it, "
                    "so nothing it rests on can be checked."
                ),
                required_condition=(
                    "Record the claims it rests on, then record_reasoning from them to this "
                    "conclusion. Citing excerpts directly does not replace the reasoning."
                ),
                statement_id=statement_id,
            )
        )
    obligations += await _rule_obligations(session, workspace_id, cone, {s.id for s in cone_steps})
    obligations += await _conflict_obligations(session, workspace_id, cone)
    if judge is None:
        obligations += await _criteria_obligations(session, goal, cone, obligations)
    else:
        obligations += await _judged_obligations(session, run, goal, cone, obligations, judge)
    await _persist_agent_obligations(session, workspace_id, statement_id, obligations)

    result = CheckResult(statement_id, target.text, obligations)
    result.packet = await _packet(session, result, cone, cone_steps, premises, goal, notes)
    return result


# -- model-assisted proposals ----------------------------------------------------------------


async def _audit_new_steps(
    session: AsyncSession, run: AgentRun, steps: list[ReasoningStep], judge: Judge | None
) -> list[str]:
    """Have the critic audit steps it has not seen in this run. A failure is noted, not fatal."""
    audited = set(run.usage.get("audited_steps", []))
    fresh = {step.id for step in steps if str(step.id) not in audited}
    if not fresh or not get_settings().claude_api_key:
        return []
    key = f"agent-critic:{run.id}:{len(audited)}:{len(fresh)}"
    try:
        await check_arguments(
            session,
            run.workspace_id,
            ArgumentCheckRequest(idempotency_key=key, model=judge.model if judge else None),
            step_ids=fresh,
            judge=judge,
        )
    except HTTPException as error:
        # Nothing was written (these fail before they save), and a rollback would expire the
        # objects this check is still using.
        return [f"The argument critic could not run ({error.detail}); premise gaps may be missed."]
    run.usage = {**run.usage, "audited_steps": sorted(audited | {str(i) for i in fresh})}
    await session.commit()
    return []


async def _link_sources(session: AsyncSession, run: AgentRun, judge: Judge | None) -> list[str]:
    """Propose supports/rebuts links between sources, once per change in the set of claims."""
    count = len(
        list(
            await session.scalars(
                select(Statement.id).where(
                    Statement.workspace_id == run.workspace_id, Statement.lifecycle != "rejected"
                )
            )
        )
    )
    if count < 2 or not get_settings().claude_api_key:
        return []
    try:
        await synthesize_workspace(
            session,
            run.workspace_id,
            SynthesisRequest(
                idempotency_key=f"agent-synth:{run.id}:{count}",
                model=judge.model if judge else None,
            ),
            judge=judge,
        )
    except HTTPException as error:
        if error.status_code == 422:  # fewer than two sources have claims yet
            return []
        return [f"Cross-source linking could not run ({error.detail}); conflicts may be missed."]
    return []


# -- deterministic obligations ----------------------------------------------------------------


async def _rule_obligations(
    session: AsyncSession, workspace_id: uuid.UUID, cone: set[uuid.UUID], step_ids: set[uuid.UUID]
) -> list[Obligation]:
    """Open verifier issues on the cone's statements and active steps.

    An issue on a source (a retraction) is not on any statement, so it is carried to every
    statement in the cone that cites the source.
    """
    cited: dict[uuid.UUID, list[uuid.UUID]] = {}
    for statement_id, source_id in await session.execute(
        select(StatementExcerpt.statement_id, Excerpt.source_id)
        .join(Excerpt, Excerpt.id == StatementExcerpt.excerpt_id)
        .where(StatementExcerpt.statement_id.in_(cone))
    ):
        cited.setdefault(source_id, []).append(statement_id)
    nodes = cone | step_ids | set(cited)
    issues = list(
        await session.scalars(
            select(Issue).where(
                Issue.workspace_id == workspace_id, Issue.status == "open", Issue.node_id.in_(nodes)
            )
        )
    )
    obligations = []
    for issue in issues:
        pending = await session.scalar(
            select(ProofObligation).where(
                ProofObligation.workspace_id == workspace_id,
                ProofObligation.generated_by_rule == issue.rule_code,
                ProofObligation.blocks_node_id == issue.node_id,
                ProofObligation.status == "open",
            )
        )
        if issue.node_type == "source":
            owners: list[uuid.UUID | None] = sorted(set(cited.get(issue.node_id, [])), key=str)
        else:
            owners = [issue.node_id if issue.node_type == "statement" else None]
        description = (
            pending.description if pending else issue.details.get("message", issue.rule_code)
        )
        condition = pending.required_condition if pending else ""
        if issue.rule_code == "missing_premise" and issue.node_type == "reasoning_step":
            gap = await _critic_gap(session, issue.node_id)
            if gap:
                description = f"The critic says this step needs a premise it does not state: {gap}"
                condition = (
                    "State that premise as a claim you can support (record_claim, citing "
                    "evidence), then re-record the reasoning with it, using revises_step_id to "
                    "replace this step. If the gap is how you weigh the evidence (for example, "
                    "randomised over observational), state that principle in the weighing field "
                    "of record_reasoning instead. Or narrow the conclusion so the premises "
                    "already establish it."
                )
        for owner in owners:
            obligations.append(
                Obligation(
                    kind=issue.rule_code,
                    severity="critical" if issue.rule_code in CRITICAL_RULES else "advisory",
                    description=description,
                    required_condition=condition,
                    statement_id=owner,
                    step_id=issue.node_id if issue.node_type == "reasoning_step" else None,
                )
            )
    return obligations


async def _critic_gap(session: AsyncSession, step_id: uuid.UUID) -> str:
    """What the critic said was missing from a step, so the agent knows what to supply."""
    rows = await session.scalars(
        select(Annotation).where(
            Annotation.subject_type == "reasoning_step",
            Annotation.subject_id == step_id,
            Annotation.type == "required_premise",
        )
    )
    said = [
        str(row.value.get("description", "")).strip()
        for row in rows
        if not row.value.get("satisfied")
    ]
    return " ".join(part for part in said if part)[:MAX_GAP_CHARS]


async def _withdrawn_obligations(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    cone_steps: list[ReasoningStep],
    premises: dict[uuid.UUID, list[uuid.UUID]],
) -> list[Obligation]:
    """Steps that still rely on a claim the agent withdrew: the reasoning is stale."""
    ids = {p for step in cone_steps for p in premises.get(step.id, [])}
    withdrawn = {
        s.id: s
        for s in await session.scalars(
            select(Statement).where(Statement.id.in_(ids), Statement.lifecycle == "rejected")
        )
    }
    replacements = {
        edge.target_node_id: edge.source_node_id
        for edge in await session.scalars(
            select(GraphEdge).where(
                GraphEdge.workspace_id == workspace_id,
                GraphEdge.relation == "revises",
                GraphEdge.source_node_kind == "statement",
                GraphEdge.target_node_id.in_(withdrawn),
            )
        )
    }
    found = []
    for step in cone_steps:
        for premise_id in premises.get(step.id, []):
            if premise_id not in withdrawn:
                continue
            better = replacements.get(premise_id)
            found.append(
                Obligation(
                    kind="withdrawn_premise",
                    severity="critical",
                    description=(
                        f"This step relies on a claim you withdrew: "
                        f"'{withdrawn[premise_id].text[:200]}'."
                    ),
                    required_condition=(
                        "Record the reasoning again without that claim"
                        + (f" (its replacement is {better})" if better else "")
                        + ", using revises_step_id to replace this step."
                    ),
                    step_id=step.id,
                )
            )
    return found


async def _conflict_obligations(
    session: AsyncSession, workspace_id: uuid.UUID, cone: set[uuid.UUID]
) -> list[Obligation]:
    """Opposing evidence the argument has not taken into account.

    A `rebuts` link between a statement in the cone and one outside it means the agent holds a
    claim that other evidence contradicts without having engaged with it. Engaging means
    recording the opposing claim and using it in the reasoning, which brings it into the cone.
    """
    edges = list(
        await session.scalars(
            select(GraphEdge).where(
                GraphEdge.workspace_id == workspace_id,
                GraphEdge.relation == "rebuts",
                GraphEdge.source_node_kind == "statement",
                GraphEdge.target_node_kind == "statement",
            )
        )
    )
    found, seen = [], set()
    for edge in edges:
        inside = [n for n in (edge.source_node_id, edge.target_node_id) if n in cone]
        outside = [n for n in (edge.source_node_id, edge.target_node_id) if n not in cone]
        if len(inside) != 1 or not outside or (inside[0], outside[0]) in seen:
            continue
        seen.add((inside[0], outside[0]))
        verdict = (edge.metadata_ or {}).get("audit_verdict")
        other = await session.get(Statement, outside[0])
        if other is None or other.lifecycle == "rejected":
            continue
        confirmed = verdict == "supported"
        found.append(
            Obligation(
                kind="unresolved_conflict" if confirmed else "possible_conflict",
                severity="critical" if confirmed else "advisory",
                description=(
                    f"Evidence not accounted for contradicts a claim you rely on: "
                    f"'{other.text[:220]}'"
                    + ("" if confirmed else " (the audit asks for review: the link is uncertain)")
                ),
                required_condition=(
                    "Record that opposing claim and use it in your reasoning: explain why it does "
                    "or does not change the conclusion, or narrow the conclusion to what survives."
                ),
                statement_id=inside[0],
            )
        )
    return found


async def _criteria_obligations(
    session: AsyncSession, goal: ResearchGoal, cone: set[uuid.UUID], existing: list[Obligation]
) -> list[Obligation]:
    """Completion criteria with no sound claim in the cone providing evidence for them."""
    if not goal.completion_criteria:
        return []
    blocked = {o.statement_id for o in existing if o.kind in HARD_BLOCKERS}
    rows = list(
        await session.scalars(
            select(Annotation).where(
                Annotation.subject_type == "statement",
                Annotation.subject_id.in_(cone),
                Annotation.type == "custom:satisfies_criteria",
            )
        )
    )
    covered: set[int] = set()
    for row in rows:
        if row.subject_id not in blocked:
            covered |= {int(i) for i in row.value.get("indexes", [])}
    return [
        Obligation(
            kind="unmet_criteria",
            severity="critical",
            description=f"Completion criterion {index} has no sound supporting claim: '{text}'",
            required_condition=(
                "Find and record evidence that meets this criterion (tagging the claim with "
                "criteria_satisfied), or narrow the conclusion to what the evidence does cover."
            ),
        )
        for index, text in enumerate(goal.completion_criteria)
        if index not in covered
    ]


async def _persist_agent_obligations(
    session: AsyncSession, workspace_id: uuid.UUID, statement_id: uuid.UUID, found: list[Obligation]
) -> None:
    """Keep the agent-rule obligations visible in the graph, and close those that are met."""
    wanted = {(o.kind, o.description) for o in found if o.kind in AGENT_RULES}
    existing = list(
        await session.scalars(
            select(ProofObligation).where(
                ProofObligation.workspace_id == workspace_id,
                ProofObligation.generated_by_rule.in_(AGENT_RULES),
                ProofObligation.blocks_node_id == statement_id,
            )
        )
    )
    open_now = {(o.generated_by_rule, o.description): o for o in existing if o.status == "open"}
    for key, row in open_now.items():
        if key not in wanted:
            row.status = "resolved"
    for kind, description in wanted - set(open_now):
        match = next(o for o in found if (o.kind, o.description) == (kind, description))
        session.add(
            ProofObligation(
                workspace_id=workspace_id,
                kind=kind,
                description=description,
                required_condition=match.required_condition,
                blocks_statement_id=statement_id,
                blocks_node_type="statement",
                blocks_node_id=statement_id,
                generated_by_rule=kind,
                status="open",
            )
        )
    await session.commit()


# -- the independent judge ---------------------------------------------------------------------

EXCERPT_CHARS = 1500


async def _judged_obligations(
    session: AsyncSession,
    run: AgentRun,
    goal: ResearchGoal,
    cone: set[uuid.UUID],
    existing: list[Obligation],
    judge: Judge,
) -> list[Obligation]:
    """Criteria and causal designs, decided by a model that reads the cited text.

    The agent's own tags are passed along as hints only. If the judge cannot answer, the
    criteria are treated as unmet: a gate that opens when its checker is down is no gate.
    """
    material = await _judge_material(session, run, goal, cone, existing)
    if not material["criteria"] and not any(c["declared_design"] for c in material["claims"]):
        return []
    key = hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()
    try:
        saved = await session.scalar(
            select(JudgeVerdict).where(
                JudgeVerdict.run_id == run.id, JudgeVerdict.input_hash == key
            )
        )
        if saved is not None:
            verdict = JudgeOutput.model_validate(saved.verdict)
        else:
            verdict = await judge.assess(material)
            session.add(
                JudgeVerdict(
                    run_id=run.id,
                    input_hash=key,
                    material=material,
                    verdict=verdict.model_dump(mode="json"),
                )
            )
            await session.commit()
    except ClaudeCallFailed as error:
        return [
            Obligation(
                kind="judge_unavailable",
                severity="critical",
                description=f"The independent check could not run: {error}.",
                required_condition="Call check_conclusion again. If it keeps failing, conclude "
                "only as a hypothesis.",
            )
        ]
    found = []
    for item in verdict.criteria:
        if not item.met:
            found.append(
                Obligation(
                    kind="unmet_criteria",
                    severity="critical",
                    description=f"Completion criterion {item.index} is not met: "
                    f"'{goal.completion_criteria[item.index]}'. Reviewer: {item.rationale}",
                    required_condition=(
                        "Find and record evidence that meets this criterion, or narrow the "
                        "conclusion to what the evidence does cover."
                    ),
                )
            )
    for item in verdict.designs:
        if not item.design_shown:
            found.append(
                Obligation(
                    kind="causal_design_not_shown",
                    severity="critical",
                    description=f"The cited text does not show the study design you declared "
                    f"for this claim. Reviewer: {item.rationale}",
                    required_condition=(
                        "Cite the passage that shows the design, correct the declared design, or "
                        "word the claim as an association."
                    ),
                    statement_id=uuid.UUID(item.statement_id),
                )
            )
    return found


async def _judge_material(
    session: AsyncSession,
    run: AgentRun,
    goal: ResearchGoal,
    cone: set[uuid.UUID],
    existing: list[Obligation],
) -> dict:
    blocked = {o.statement_id for o in existing if o.kind in HARD_BLOCKERS}
    statements = list(
        await session.scalars(
            select(Statement).where(Statement.id.in_(cone)).order_by(Statement.id)
        )
    )
    annotations: dict[uuid.UUID, dict[str, dict]] = {}
    for row in await session.scalars(
        select(Annotation).where(
            Annotation.subject_type == "statement", Annotation.subject_id.in_(cone)
        )
    ):
        annotations.setdefault(row.subject_id, {})[row.type] = row.value
    claims = []
    for statement in statements:
        if statement.id in blocked:
            continue  # a retracted or ungrounded claim cannot meet a criterion
        rows = await session.execute(
            select(Source.title, Excerpt.text)
            .join(Excerpt, Excerpt.source_id == Source.id)
            .join(StatementExcerpt, StatementExcerpt.excerpt_id == Excerpt.id)
            .where(StatementExcerpt.statement_id == statement.id)
            .order_by(Excerpt.id)
        )
        have = annotations.get(statement.id, {})
        design = have.get("causal_support")
        claims.append(
            {
                "statement_id": str(statement.id),
                "role": statement.role,
                "text": statement.text,
                "claim_strength": (have.get("claim_strength") or {}).get("value"),
                "declared_design": design.get("design") if design else None,
                "declared_design_reason": design.get("justification") if design else None,
                "criteria_hint": (have.get("custom:satisfies_criteria") or {}).get("indexes", []),
                "cited": [{"source": title, "text": body[:EXCERPT_CHARS]} for title, body in rows],
            }
        )
    queries = []
    for event in await session.scalars(
        select(AgentEvent).where(AgentEvent.run_id == run.id).order_by(AgentEvent.seq)
    ):
        if event.type == "tool_call" and event.payload.get("name") == "search_papers":
            queries.append(str(event.payload.get("input", {}).get("query", ""))[:300])
        elif event.type == "web_search" and event.payload.get("query"):
            queries.append(str(event.payload["query"])[:300])
    return {
        "question": goal.question,
        "kind": goal.kind,
        "criteria": list(goal.completion_criteria),
        "claims": claims,
        "searches": queries[:40],
    }


# -- what the agent is shown ------------------------------------------------------------------


async def _packet(
    session: AsyncSession,
    result: CheckResult,
    cone: set[uuid.UUID],
    cone_steps: list[ReasoningStep],
    premises: dict[uuid.UUID, list[uuid.UUID]],
    goal: ResearchGoal,
    notes: list[str],
) -> dict:
    statements = {
        s.id: s for s in await session.scalars(select(Statement).where(Statement.id.in_(cone)))
    }
    rows = await session.execute(
        select(StatementExcerpt.statement_id, Source.id, Source.title, Source.external_ids)
        .join(Excerpt, Excerpt.id == StatementExcerpt.excerpt_id)
        .join(Source, Source.id == Excerpt.source_id)
        .where(StatementExcerpt.statement_id.in_(cone))
    )
    sources: dict[uuid.UUID, dict[uuid.UUID, dict]] = {}
    for statement_id, source_id, title, ids in rows:
        sources.setdefault(statement_id, {})[source_id] = {
            "source_id": str(source_id),
            "title": title,
            "retracted": await _invalidated(session, source_id),
            **{k: v for k, v in (ids or {}).items() if k in ("doi", "pmid", "url")},
        }
    chain = [
        {
            "statement_id": str(i),
            "text": statements[i].text[:300],
            "role": statements[i].role,
            "from_sources": list(sources.get(i, {}).values()),
        }
        for i in list(cone)[:MAX_CONE_STATEMENTS]
        if i in statements
    ]
    return {
        "conclusion": {"statement_id": str(result.statement_id), "text": result.statement_text},
        "evidence_chain": chain,
        "reasoning_steps": [
            {
                "step_id": str(s.id),
                "premises": [str(p) for p in premises.get(s.id, []) if p in cone],
                "explanation": s.explanation[:240],
            }
            for s in cone_steps
        ],
        "obligations": [o.as_dict() for o in result.obligations],
        "completion_criteria": list(enumerate(goal.completion_criteria)),
        "can_finalize_as": result.allowed_certainties(),
        "assurance": assurance.from_check(result.obligations).as_dict(),
        "notes": notes,
        "you_may": [
            "read or fetch more evidence and record it",
            "record opposing evidence and reason about it",
            "narrow the conclusion (record a more modest claim and check that)",
            "finalize as conditional or hypothesis, with the caveats stated",
            "abstain",
        ],
    }


async def _invalidated(session: AsyncSession, source_id: uuid.UUID) -> bool:
    latest = await session.scalar(
        select(SourceValidity)
        .where(SourceValidity.source_id == source_id)
        .order_by(SourceValidity.created_at.desc())
        .limit(1)
    )
    return latest is not None and latest.status == "invalidated"


async def finalize(
    session: AsyncSession,
    run: AgentRun,
    goal: ResearchGoal,
    statement_id: uuid.UUID,
    certainty: str,
    judge: Judge | None = None,
) -> tuple[bool, CheckResult]:
    """Accept the conclusion if the evidence graph supports it at this certainty."""
    result = await check(session, run, goal, statement_id, judge)
    if certainty not in result.allowed_certainties():
        return False, result
    run.final_statement_id = statement_id
    run.certainty = certainty
    goal.status = {"established": "answered", "conditional": "conditionally_answered"}.get(
        certainty, "open"
    )
    await session.commit()
    return True, result
