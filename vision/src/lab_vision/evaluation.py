import json
from pathlib import Path

from pydantic import BaseModel, Field

from lab_vision.models import Deviation, DeviationKind, TimeSpan


class ExpectedDeviation(BaseModel):
    """A mistake deliberately placed in a clip. Time is optional."""

    kind: DeviationKind
    step_id: str | None = None
    check_id: str | None = None
    span: TimeSpan | None = None


class GroundTruth(BaseModel):
    """Labels for one clip. An empty list means the clip is a correct run."""

    clip_id: str
    deviations: list[ExpectedDeviation] = Field(default_factory=list)


class EvalReport(BaseModel):
    clip_id: str
    expected: int
    detected: int
    missed: list[ExpectedDeviation]
    false_alarms: list[Deviation]
    review_flags: list[Deviation]

    @property
    def recall(self) -> float | None:
        return self.detected / self.expected if self.expected else None


def load_truth(path: Path) -> GroundTruth:
    return GroundTruth.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_deviations(path: Path) -> list[Deviation]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [Deviation.model_validate_json(line) for line in lines if line.strip()]


def _matches(expected: ExpectedDeviation, found: Deviation, tolerance_s: float) -> bool:
    if expected.kind is not found.kind or expected.step_id != found.step_id:
        return False
    if expected.check_id is not None and expected.check_id != found.check_id:
        return False
    if expected.span is not None and found.span is not None:
        return expected.span.overlaps(found.span, tolerance_s)
    return True


def evaluate(
    truth: GroundTruth, deviations: list[Deviation], tolerance_s: float = 5.0
) -> EvalReport:
    """Score a run against labels.

    Confident findings that match nothing are false alarms. Findings marked `needs_review` are
    reported separately: they ask a human to look rather than assert a mistake.
    """
    unmatched = list(deviations)
    missed: list[ExpectedDeviation] = []
    detected = 0
    for expected in truth.deviations:
        hit = next((d for d in unmatched if _matches(expected, d, tolerance_s)), None)
        if hit is None:
            missed.append(expected)
        else:
            detected += 1
            unmatched.remove(hit)
    return EvalReport(
        clip_id=truth.clip_id,
        expected=len(truth.deviations),
        detected=detected,
        missed=missed,
        false_alarms=[d for d in unmatched if not d.needs_review],
        review_flags=[d for d in unmatched if d.needs_review],
    )
