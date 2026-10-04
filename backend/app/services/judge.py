"""The judge: the model that reviews work instead of producing it.

Generators (extraction, link proposals, the research agent) propose; the judge audits. Every
review in the backend goes through this one class, so they share a client, a model setting and a
token tally:

- `check_steps`: does a reasoning step need a premise the source does not state?
- `audit_links`: does the evidence establish a proposed cross-source link?
- `assess`: do an agent run's claims meet its completion criteria, and do the cited passages show
  the study design the agent declared?
- `propose_criteria`: what must the evidence show before a question can be answered?

A judge that cannot answer raises ClaudeCallFailed; callers fail closed. The agent's own labels
are only hints to it: it reads the cited text itself.
"""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import get_settings
from app.schemas import ArgumentCheckOutput
from app.services.claude_call import new_tally, structured_call

# Used when criteria cannot be generated. Deliberately generic, so any question can be held to them.
DEFAULT_CRITERIA = [
    "Direct evidence of the right kind (for an intervention: a randomised trial) for the "
    "population and outcome the question asks about",
    "Evidence from more than one independent source",
    "Evidence against the claim was actively searched for and is accounted for",
]


class CriterionVerdict(BaseModel):
    index: int = Field(description="The criterion's number, from 0.")
    met: bool = Field(description="True only if the evidence fully meets the criterion.")
    gap: str = Field(
        default="",
        description="What the evidence still lacks for this criterion. Empty only if it is "
        "fully met; a criterion that is only partly met is not met.",
    )
    rationale: str = Field(
        description="One or two sentences citing the claim or search that decides it."
    )
    supporting_statement_ids: list[str] = Field(
        description="statement_ids of the claims that meet it. Empty if none do."
    )


class DesignVerdict(BaseModel):
    statement_id: str
    design_shown: bool = Field(
        description="True only if the cited text itself shows the study design the agent declared."
    )
    rationale: str


class JudgeOutput(BaseModel):
    criteria: list[CriterionVerdict]
    designs: list[DesignVerdict]


class ProposedCriteria(BaseModel):
    completion_criteria: list[str] = Field(
        description="3 to 5 conditions the evidence must meet before the question can be answered."
    )
    falsifiers: list[str] = Field(
        description="1 to 3 findings that would show a tentative answer is wrong."
    )


ASSESS_SYSTEM = """\
You are an independent, sceptical reviewer of a research agent's work. You are given the question, \
the completion criteria, the agent's claims with the exact source text each cites, the study \
design the agent says each causal claim rests on, and the searches the agent ran.

Decide each criterion strictly:
- A criterion is met only if at least one listed claim, read together with the source text it \
cites, directly provides the evidence the criterion asks for: the right kind of evidence, for the \
population and outcome in question. A claim that is merely on the same topic does not meet it.
- The agent's own tags (criteria_hint) are hints, not evidence. Ignore them where the text does \
not bear them out.
- A criterion about opposing or disconfirming evidence is met if the claims include evidence that \
argues against the conclusion, OR if the agent's searches were clearly aimed at finding such \
evidence (queries about negative results, failures, harms, criticism or contrary findings) and the \
claims honestly report what they found. A search that only looks for supporting studies does not \
meet it.
- A criterion about independent sources needs claims from different sources, not one source \
repeated.

For each causal claim with a declared design, say whether the cited text itself shows that \
design (for example 'randomly assigned', 'randomised'). A design that is only asserted, or that \
the text contradicts, is not shown.

A criterion that is only partly met is not met: say in `gap` what is still missing, and leave \
`gap` empty only when nothing is. Be concise. If you are unsure, say the criterion is not met."""

PROPOSE_SYSTEM = """\
You set the standard of evidence for a research question before any research is done. Given a \
question, claim or hypothesis, state what the evidence must show before an answer could be \
justified, as 3 to 5 criteria. Each criterion must be something a reviewer can check from the \
text of a cited paper:
- the kind of evidence needed: study design, population, outcome measured;
- agreement across more than one independent study, or replication;
- that evidence against the answer was looked for and is accounted for.
Criteria must be neutral about the answer: they say what evidence would settle the question \
either way, so a conclusion that refutes the claim can meet them as well as one that confirms \
it. Never phrase one as 'consistent evidence of benefit' or any other direction; say 'consistent \
findings, in whichever direction'. Do not ask for things a paper's text cannot show: who \
funded or conducted the work, the researchers' motives or affiliations. Do not name \
particular trials or papers. Be specific to this topic, not generic. If the question asks \
about a specific study, paper or supplied source, the criteria concern what that source's \
design, data and reporting must show; do not demand replication in other studies or evidence \
outside it. Keep each criterion under 25 words. Also state 1 to 3 \
findings that would show a tentative answer wrong. Do \
not guess the answer."""


class LinkAudit(BaseModel):
    source_statement_id: uuid.UUID
    target_statement_id: uuid.UUID
    relation: Literal["supports", "rebuts", "qualifies"]
    verdict: Literal["supported", "needs_review"]
    rationale: str = Field(min_length=1)


class LinkAudits(BaseModel):
    audits: list[LinkAudit] = Field(default_factory=list)


STEP_AUDIT_SYSTEM = (
    "Audit each argument step conservatively. A conclusion being stated in its own "
    "excerpt is not support. Mark supported only if the premise excerpts "
    "independently establish the conclusion without an unstated calculation, "
    "mechanism, causal assumption, generalization, or background fact. If unsure, "
    "choose needs_support. Do not assess truth beyond these excerpts. A step may carry a "
    "declared_weighing: the principle its author uses to weigh the evidence (for example, "
    "randomised trials outrank observational studies for causal effects; larger, more precise or "
    "lower-bias studies outrank smaller or weaker ones). Accept a declared weighing that is a "
    "recognised principle of evidence appraisal, that fits the premises as the excerpts describe "
    "them, and that is not tailored to favour the conclusion: it then does not count as an "
    "unstated premise. If the conclusion depends on weighing and none is declared, or the one "
    "declared is ad hoc or does not fit this evidence, choose needs_support and say what "
    "weighing is needed."
)

LINK_AUDIT_SYSTEM = (
    "Audit each proposed cross-source link using only the included statements and "
    "excerpts. Mark supported only when the evidence establishes the exact relation. A "
    "shared topic, author assertion, or unstated mechanism is insufficient; otherwise "
    "choose needs_review."
)


class Judge:
    """Reviews work with a model. Counts its own token usage in `usage`."""

    def __init__(self, client: Any, model: str | None = None) -> None:
        self.client = client
        self.model = model or get_settings().agent_judge_model
        self.usage = new_tally()

    async def _ask(self, system, content, name, description, schema, task, model=None, **extra):
        return await structured_call(
            self.client,
            model=model or self.model,
            system=system,
            content=content,
            tool_name=name,
            description=description,
            schema=schema,
            task=task,
            tally=self.usage,
            **extra,
        )

    async def check_steps(self, steps: list[dict], model: str | None = None) -> ArgumentCheckOutput:
        return await self._ask(
            STEP_AUDIT_SYSTEM,
            {"steps": steps},
            "submit_argument_check",
            "Return exactly one assessment for every step.",
            ArgumentCheckOutput,
            "argument check",
            model,
        )

    async def audit_links(self, links: list[dict], model: str | None = None) -> LinkAudits:
        return await self._ask(
            LINK_AUDIT_SYSTEM,
            {"links": links},
            "submit_link_audits",
            "Return the requested structured result.",
            LinkAudits,
            "link audit",
            model,
        )

    async def assess(self, material: dict) -> JudgeOutput:
        output = await self._ask(
            ASSESS_SYSTEM,
            material,
            "submit_assessment",
            "Return a verdict for every criterion.",
            JudgeOutput,
            "assessment",
            max_tokens=4096,
        )
        return _tidy(output, material)

    async def propose_criteria(self, question: str, kind: str) -> ProposedCriteria:
        return await self._ask(
            PROPOSE_SYSTEM,
            {"kind": kind, "text": question},
            "submit_criteria",
            "Return the completion criteria and falsifiers.",
            ProposedCriteria,
            "criteria proposal",
            max_tokens=2048,
        )


def _tidy(output: JudgeOutput, material: dict) -> JudgeOutput:
    """Make the verdict complete: every criterion judged, no invented ids."""
    count = len(material.get("criteria", []))
    known = {claim["statement_id"] for claim in material.get("claims", [])}
    seen: dict[int, CriterionVerdict] = {}
    for verdict in output.criteria:
        if 0 <= verdict.index < count and verdict.index not in seen:
            verdict.supporting_statement_ids = [
                s for s in verdict.supporting_statement_ids if s in known
            ]
            if verdict.met and verdict.gap.strip():
                verdict.met = False  # "partly met" is not met
                verdict.rationale += f" Still missing: {verdict.gap.strip()}"
            if verdict.met and not verdict.supporting_statement_ids and not _search_based(verdict):
                verdict.met = False  # "met" with nothing to point at is not met
                verdict.rationale += " (No supporting claim was identified.)"
            seen[verdict.index] = verdict
    for index in range(count):
        seen.setdefault(
            index,
            CriterionVerdict(
                index=index,
                met=False,
                rationale="The judge did not assess this criterion.",
                supporting_statement_ids=[],
            ),
        )
    # A design verdict only means something for a claim that declared a design: the judge
    # sometimes rules on descriptive claims too, which would raise a false obligation.
    declared = {c["statement_id"] for c in material.get("claims", []) if c.get("declared_design")}
    designs = [d for d in output.designs if d.statement_id in declared]
    return JudgeOutput(criteria=[seen[i] for i in sorted(seen)], designs=designs)


def _search_based(verdict: CriterionVerdict) -> bool:
    """A criterion about the search itself can be met by what the agent searched for."""
    text = verdict.rationale.lower()
    return "search" in text or "quer" in text
