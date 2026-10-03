from pathlib import Path

from lab_vision.models import Observation
from lab_vision.perception.base import PerceptionRequest


class ReplayPerceiver:
    """Serves previously recorded observations, so verification can be re-run offline.

    Re-running with changed protocol thresholds or rules costs no model calls.
    """

    def __init__(self, observations: list[Observation]) -> None:
        self._observations = sorted(observations, key=lambda o: o.span.start_s)

    @classmethod
    def from_jsonl(cls, path: Path) -> "ReplayPerceiver":
        lines = path.read_text(encoding="utf-8").splitlines()
        return cls([Observation.model_validate_json(line) for line in lines if line.strip()])

    def observe(self, request: PerceptionRequest) -> list[Observation]:
        span = request.window.span
        focus = {s.id for s in request.steps}
        return [
            o
            for o in self._observations
            if span.start_s <= o.span.start_s <= span.end_s
            and (o.step_id is None or o.step_id in focus)
        ]
