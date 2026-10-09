import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from lab_vision.llm import StructuredLLM
from lab_vision.models import Check, CheckKind, Protocol, ProtocolStep

log = logging.getLogger(__name__)


class StructuringError(RuntimeError):
    """The model's structured protocol did not hold up against the source text."""


# The schema below is what the model fills in. It is deliberately flatter and looser than
# `Protocol`: the model proposes, and `_problems` plus `Protocol` validation decide.


class CheckDraft(BaseModel):
    id: str = Field(description="Short kebab-case id, unique within the step, e.g. 'volume'.")
    question: str = Field(
        description=(
            "A neutral question answerable by looking at the video, such as 'What volume is "
            "set on the pipette?'. Never include the expected value."
        )
    )
    kind: Literal["numeric", "equals"] = Field(
        description="'numeric' for a measured quantity, 'equals' for a label or named item."
    )
    expected: str = Field(
        description="The value the protocol requires. For numeric, digits only: '5', '42'."
    )
    unit: str | None = Field(description="Unit exactly as written, e.g. 'μL' or '°C'. Else null.")
    target: str | None = Field(
        description=(
            "Where the value is read from, described by how it looks in five words or fewer, "
            "e.g. 'handheld pipette with digital display' or 'water bath with temperature "
            "display'. Null if not applicable."
        )
    )
    tolerance: float | None = Field(
        description="Allowed deviation. Null unless the protocol text itself states one."
    )
    accept: list[str] = Field(
        description="Other spellings of the expected value for 'equals' checks. Else empty."
    )


class StepDraft(BaseModel):
    id: str = Field(description="'s' followed by the step number: s1, s2, ...")
    source_text: str = Field(
        description="The step's sentence copied character for character from the protocol."
    )
    description: str = Field(
        description="The step as one clear instruction. Keep every quantity and time limit."
    )
    objects: list[str] = Field(
        description=(
            "Apparatus a person would see in use, each described by how it looks in five words "
            "or fewer, e.g. 'green box of crushed ice', not just 'ice bucket'."
        )
    )
    checks: list[CheckDraft]
    optional: bool = Field(description="True only if the protocol marks the step optional.")
    variant: str | None = Field(
        default=None,
        description=(
            "Only when the text describes alternative procedures (for example two device types or "
            "two sample preparations): a short name for the one this step belongs to, the same "
            "name for all its steps. Null for steps all of them share, and when there is only one."
        ),
    )
    measurement: bool = Field(
        default=False,
        description=(
            "True for characterisation or measurement done on the result (XRD, microscopy, "
            "spectroscopy, electrical testing): it often happens later or elsewhere."
        ),
    )


class ProtocolDraft(BaseModel):
    steps: list[StepDraft]


SYSTEM_PROMPT = """\
You convert a written laboratory procedure into a structured checklist that a camera system \
will verify against video of the experiment.

The text is either a numbered protocol or a paper's methods written as prose.

For a numbered protocol:
- One step per numbered item, in the original order. Never rewrite, merge or drop steps.

For prose methods (a paper's methods or experimental section):
- Take only the physical actions someone performs at the bench, in the order they are \
performed, one action per step: preparing, mixing, depositing, heating, measuring with an \
instrument and the like.
- Leave out everything else: results, discussion and comparisons, participant recruitment, \
ethics, study design, statistics and data analysis, and descriptions of what was measured \
rather than how.
- If the text describes alternative procedures (for example two device types made in \
different ways), name each in `variant` on its own steps; a step one procedure says is \
"identical to" the other's belongs to that procedure too. Mark characterisation and \
measurement steps with `measurement`.

Rules for both:
- Use ids s1, s2, ... by position.
- Copy `source_text` exactly, character for character, from the sentence or clause that states \
the step.
- Do not invent anything. Every quantity, temperature, label and time limit must come from \
the text.
- Add a check only for a value that can be read off video: a volume set on a pipette or \
dispensed, a temperature on a display, a labelled tube or plate. Each check has a neutral \
question and the required value.
- Do not make checks for durations or timing. Keep them in the description.
- Prefer few, reliable checks over many speculative ones. A step with nothing readable has \
no checks.
- Never put the expected value in a question.
- Objects and targets are used to find things in video with a detector that matches visual \
descriptions. Describe how the thing looks, in five words or fewer. Do not invent equipment \
the text does not imply."""


def structure_protocol(
    text: str,
    llm: StructuredLLM,
    protocol_id: str,
    title: str,
    max_attempts: int = 2,
) -> Protocol:
    """Turn prose into a validated `Protocol`.

    The draft is checked against the source text, so the model cannot quietly rewrite a step
    or invent a quantity. Problems are fed back once. The result is still a draft for a
    person to review before it is used as ground truth.
    """
    feedback = ""
    problems: list[str] = []
    for attempt in range(1, max_attempts + 1):
        prompt = f"Protocol text:\n\n{text.strip()}\n{feedback}"
        draft = llm.generate(
            system=SYSTEM_PROMPT,
            content=[{"type": "text", "text": prompt}],
            output=ProtocolDraft,
        )
        problems = _problems(draft, text)
        if not problems:
            return _to_protocol(draft, protocol_id, title)
        log.warning("structuring attempt %d had %d problems", attempt, len(problems))
        feedback = "\n\nYour previous answer had these problems. Fix them:\n" + "\n".join(
            f"- {p}" for p in problems
        )
    raise StructuringError("; ".join(problems))


def _squash(text: str) -> str:
    return " ".join(text.split())


def _problems(draft: ProtocolDraft, text: str) -> list[str]:
    problems: list[str] = []
    source = _squash(text)
    if not draft.steps:
        return ["no steps were produced"]
    ids = [s.id for s in draft.steps]
    if len(set(ids)) != len(ids):
        problems.append("step ids are not unique")
    last_position = -1
    for step in draft.steps:
        quote = _squash(step.source_text)
        position = source.find(quote)
        if not quote or position < 0:
            problems.append(f"{step.id}: source_text does not appear verbatim in the protocol")
            continue
        if position < last_position:
            problems.append(f"{step.id}: steps are out of the protocol's order")
        last_position = position
        check_ids = [c.id for c in step.checks]
        if len(set(check_ids)) != len(check_ids):
            problems.append(f"{step.id}: check ids are not unique")
        for check in step.checks:
            if check.kind == "numeric" and not _numeric_in(check.expected, quote):
                problems.append(
                    f"{step.id}/{check.id}: expected value {check.expected!r} is not in the step"
                )
            if check.expected.casefold() in check.question.casefold():
                problems.append(f"{step.id}/{check.id}: the question gives away the answer")
    return problems


def _numeric_in(expected: str, quote: str) -> bool:
    try:
        wanted = float(expected)
    except ValueError:
        return False
    return any(float(n) == wanted for n in re.findall(r"\d+(?:\.\d+)?", quote))


# PDFs set the degree sign as a superscript "o" or spell it out.
UNITS = {"oc": "°C", "ºc": "°C", "degc": "°C", "deg c": "°C", "c": "°C"}


def _unit(unit: str | None) -> str | None:
    if unit is None:
        return None
    return UNITS.get(unit.strip().lower(), unit.strip())


def _to_protocol(draft: ProtocolDraft, protocol_id: str, title: str) -> Protocol:
    steps = [
        ProtocolStep(
            id=s.id,
            description=s.description,
            source_text=_squash(s.source_text),
            objects=s.objects,
            # Characterisation often happens later or in another lab: not watched for by default.
            optional=s.optional or s.measurement,
            variant=(s.variant or "").strip() or None,
            checks=[
                Check(
                    id=c.id,
                    question=c.question,
                    kind=CheckKind(c.kind),
                    expected=c.expected,
                    unit=_unit(c.unit),
                    target=c.target,
                    tolerance=c.tolerance or 0.0,
                    accept=c.accept,
                )
                for c in s.checks
            ],
        )
        for s in draft.steps
    ]
    return Protocol(id=protocol_id, title=title, steps=steps)
