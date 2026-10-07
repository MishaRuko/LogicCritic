"""Length-neutral scoring: factual precision, verdict calibration and required deductions.

Each answer is scored alone. The scorer never sees the other arm's answer, so neither position
nor relative length can enter a score, and an omission costs nothing unless it is one of the few
deductions the question cannot be answered without.
"""

import random
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.claude_call import ClaudeCallFailed, structured_call

SCORER = "v3"
ATTEMPTS = 3
PAPER_VERDICTS = [
    "supported",
    "partially_supported",
    "not_supported",
    "contradicted",
    "cannot_determine",
]
SCIFACT_VERDICTS = ["SUPPORTS", "CONTRADICTS", "NOT ENOUGH INFO"]


class RequiredDeduction(BaseModel):
    deduction: str = Field(description="The inference a correct answer must draw, in one sentence.")
    evidence: str = Field(description="What in the source it rests on.")


class ScoringKey(BaseModel):
    expected_verdict: str
    verdict_rationale: str
    required_deductions: list[RequiredDeduction] = Field(min_length=1, max_length=3)


class ClaimCheck(BaseModel):
    claim: str
    status: Literal["supported", "contradicted", "unsupported"]
    note: str = ""


class DeductionCheck(BaseModel):
    index: int
    status: Literal["valid", "flawed", "absent"]
    note: str = ""


class AnswerScore(BaseModel):
    claims: list[ClaimCheck] = Field(max_length=80)
    verdict_given: str
    deductions: list[DeductionCheck] = Field(default_factory=list)
    invalid_inferences: list[str] = Field(default_factory=list)


KEY_SYSTEM = """You prepare the scoring key for one question in a scientific-reasoning evaluation.
You get the source packet, the question, and a draft rubric. Return:
- expected_verdict: one label from verdict_options for what the source actually supports.
- verdict_rationale: one or two sentences.
- required_deductions: the 1 to 3 inferences an answer cannot be correct without: the critical
  thinking the question tests (for example 'a non-significant difference in an underpowered trial
  does not show equivalence'). Not details, numbers to quote or background to mention: an answer
  that omits a detail but draws the inference is correct. Each must follow from the packet.
Return only JSON without Markdown:
{"expected_verdict":string,"verdict_rationale":string,"required_deductions":[{"deduction":string,"evidence":string}]}"""

SCORE_SYSTEM = """You audit one answer to a scientific-reasoning question for accuracy and
reasoning, using only the supplied source packet. You score this answer alone. Length is not a
merit: do not reward detail or penalize brevity, and never count an omission as an error.

1. claims: list every factual claim the answer makes about the source or the study: numbers,
   design, methods, results, what the authors state, and what the source does or does not
   contain or report. Mark each:
   - supported: the packet states it or it follows directly from what the packet states;
   - contradicted: the packet says otherwise, including claiming the source lacks something it
     contains or misstating a number;
   - unsupported: the packet neither states nor implies it.
   Do not list the answer's own evaluations or inferences as claims; they belong in 3.
   Statements about the answerer's own process ('I read only the abstract') are not claims.
2. verdict_given: the label from verdict_options the answer commits to, or "none" if it does not
   commit to one.
3. invalid_inferences: each inference the answer draws that does not follow from the evidence it
   relies on (for example treating non-significance as equivalence, association as causation,
   generalizing beyond the population). Only actual errors, quoted briefly; not omissions.
4. deductions: for each required deduction (by index), "valid" if the answer draws it with sound
   reasoning, "flawed" if it gestures at it but the reasoning is wrong or confused, "absent" if
   it does not draw it. Wording need not match.
Return only JSON without Markdown:
{"claims":[{"claim":string,"status":"supported"|"contradicted"|"unsupported","note":string}],"verdict_given":string,"invalid_inferences":[string],"deductions":[{"index":integer,"status":"valid"|"flawed"|"absent","note":string}]}"""


async def _ask(client: Any, model: str, system: str, content: dict, schema, task: str, tally: dict):
    for attempt in range(ATTEMPTS):
        try:
            return await structured_call(
                client,
                model=model,
                system=system,
                content=content,
                tool_name="submit",
                description="Return the requested JSON.",
                schema=schema,
                task=task,
                tally=tally,
                max_tokens=16_000,
                force_tool=False,
                allow_text_json=True,
                use_tools=False,
            )
        except ClaudeCallFailed:
            if attempt == ATTEMPTS - 1:
                raise


async def derive_key(client: Any, model: str, rubric: dict, packet: dict, tally: dict) -> dict:
    """The expected verdict and required deductions; fixed by the case for SciFact."""
    if rubric.get("gold_label"):
        return {
            "verdict_options": SCIFACT_VERDICTS,
            "expected_verdict": rubric["gold_label"],
            "verdict_rationale": "SciFact expert label.",
            "required_deductions": [],
        }
    draft = {k: rubric.get(k) for k in ("completion_criteria", "rubric", "trap")}
    key = await _ask(
        client,
        model,
        KEY_SYSTEM,
        {
            "packet": packet,
            "question": rubric["question"],
            "draft_rubric": draft,
            "verdict_options": PAPER_VERDICTS,
        },
        ScoringKey,
        "scoring-key derivation",
        tally,
    )
    out = key.model_dump()
    if out["expected_verdict"] not in PAPER_VERDICTS:
        raise ClaudeCallFailed(f"invalid expected verdict {out['expected_verdict']!r}")
    return {"verdict_options": PAPER_VERDICTS, **out}


async def score_answer(
    client: Any, model: str, question: str, packet: dict, key: dict, answer: str, tally: dict
) -> tuple[dict, dict]:
    """Return (material sent, metrics). The expected verdict is withheld from the scorer."""
    deductions = [d["deduction"] for d in key["required_deductions"]]
    material = {
        "packet": packet,
        "question": question,
        "verdict_options": key["verdict_options"],
        "required_deductions": [{"index": i, "deduction": d} for i, d in enumerate(deductions)],
        "answer": answer,
    }
    raw = await _ask(client, model, SCORE_SYSTEM, material, AnswerScore, "answer scoring", tally)
    # Only SciFact asks for a fixed 'Verdict: LABEL' line; free-text paper verdicts such as
    # 'supported only in part' must be read by the scorer, not matched by a pattern.
    stated = (
        explicit_verdict(answer, SCIFACT_VERDICTS)
        if key["verdict_options"] == SCIFACT_VERDICTS
        else None
    )
    return material, metrics(raw.model_dump(), key, stated)


def explicit_verdict(answer: str, options: list[str]) -> str | None:
    """A deterministic reading of an explicit 'Verdict: X' line, preferred over the scorer's."""
    pattern = "|".join(re.escape(option) for option in sorted(options, key=len, reverse=True))
    match = re.search(rf"verdict\W{{0,6}}({pattern})", answer, flags=re.IGNORECASE)
    if not match:
        return None
    return next(option for option in options if option.lower() == match.group(1).lower())


def metrics(raw: dict, key: dict, stated: str | None = None) -> dict:
    claims = raw["claims"]
    counts = {
        s: sum(c["status"] == s for c in claims)
        for s in ("supported", "contradicted", "unsupported")
    }
    total = len(claims)
    verdict = stated or raw["verdict_given"]
    required = len(key["required_deductions"])
    by_index = {d["index"]: d["status"] for d in raw.get("deductions", [])}
    statuses = [by_index.get(i, "absent") for i in range(required)]
    return {
        **raw,
        "verdict_given": verdict,
        "expected_verdict": key["expected_verdict"],
        "verdict_correct": verdict == key["expected_verdict"],
        "claims_total": total,
        "claims_supported": counts["supported"],
        "claims_contradicted": counts["contradicted"],
        "claims_unsupported": counts["unsupported"],
        "precision": counts["supported"] / total if total else None,
        "invalid_inference_count": len(raw.get("invalid_inferences", [])),
        "deductions_required": required,
        "deductions_valid": statuses.count("valid"),
        "deductions_flawed": statuses.count("flawed"),
        "deduction_recall": statuses.count("valid") / required if required else None,
        "errors": counts["contradicted"] + len(raw.get("invalid_inferences", [])),
    }


# -- aggregation -------------------------------------------------------------------------------

METRICS = [
    ("errors", "Errors per answer (contradicted claims + invalid inferences)", "lower"),
    ("claims_contradicted", "Contradicted claims per answer", "lower"),
    ("claims_unsupported", "Unsupported claims per answer", "lower"),
    ("invalid_inference_count", "Invalid inferences per answer", "lower"),
    ("precision", "Claim precision (supported / all factual claims)", "higher"),
    ("verdict_correct", "Verdict matches the expected verdict", "higher"),
    ("deduction_recall", "Required deductions drawn validly", "higher"),
    ("claims_total", "Factual claims per answer (descriptive)", None),
]


def paired_summary(pairs: list[tuple[dict, dict]], seed: int = 0) -> list[dict]:
    """Per metric: arm means and the guarded-minus-baseline mean with a bootstrap 95% CI."""
    rng = random.Random(seed)
    rows = []
    for name, label, better in METRICS:
        values = [
            (float(b[name]), float(g[name]))
            for b, g in pairs
            if b.get(name) is not None and g.get(name) is not None
        ]
        if not values:
            continue
        diffs = [g - b for b, g in values]
        means = []
        for _ in range(2000):
            sample = [diffs[rng.randrange(len(diffs))] for _ in diffs]
            means.append(sum(sample) / len(sample))
        means.sort()
        rows.append(
            {
                "metric": name,
                "label": label,
                "better": better,
                "n": len(values),
                "baseline": sum(b for b, _ in values) / len(values),
                "guarded": sum(g for _, g in values) / len(values),
                "diff": sum(diffs) / len(diffs),
                "ci_low": means[int(0.025 * len(means))],
                "ci_high": means[int(0.975 * len(means)) - 1],
            }
        )
    return rows
