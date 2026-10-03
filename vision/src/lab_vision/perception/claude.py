import logging

from pydantic import ValidationError

from lab_vision.llm import ClaudeLLM, LLMError, StructuredLLM
from lab_vision.models import Observation, ObservedValue, Provenance
from lab_vision.perception.base import PerceptionError, PerceptionRequest
from lab_vision.perception.prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_content
from lab_vision.perception.schema import PerceptionOut

log = logging.getLogger(__name__)

__all__ = ["ClaudePerceiver", "ClaudeLLM"]


class ClaudePerceiver:
    """Asks Claude to report on the protocol steps in focus for one window of frames.

    If the answer cannot be obtained or does not validate, the whole window is dropped, so a
    bad response never yields partial observations.
    """

    def __init__(self, llm: StructuredLLM) -> None:
        self._llm = llm

    def observe(self, request: PerceptionRequest) -> list[Observation]:
        try:
            parsed = self._llm.generate(
                system=SYSTEM_PROMPT, content=build_user_content(request), output=PerceptionOut
            )
        except LLMError as exc:
            raise PerceptionError(str(exc)) from exc
        try:
            return self._to_observations(parsed, request)
        except ValidationError as exc:
            raise PerceptionError(f"invalid observation: {exc}") from exc

    def _to_observations(
        self, parsed: PerceptionOut, request: PerceptionRequest
    ) -> list[Observation]:
        provenance = Provenance(
            actor_type="extractor",
            actor_id="lab-vision.claude",
            model=self._llm.model,
            prompt_version=PROMPT_VERSION,
            run_id=request.run_id,
        )
        span = request.window.span
        frames = [f.index for f in request.window.frames]
        steps = {s.id: s for s in request.steps}
        observations: list[Observation] = []
        for item in parsed.observations:
            step = steps.get(item.step_id)
            if step is None:
                log.warning("dropping observation for unknown step %r", item.step_id)
                continue
            values = []
            for value in item.values:
                if step.check(value.check_id) is None:
                    log.warning("dropping unknown check %r on step %r", value.check_id, step.id)
                    continue
                values.append(ObservedValue(**value.model_dump()))
            observations.append(
                Observation(
                    step_id=step.id,
                    status=item.status,
                    values=values,
                    description=item.notes,
                    confidence=item.confidence,
                    span=span,
                    frame_indices=frames,
                    produced_by=provenance,
                )
            )
        for event in parsed.unexpected_events:
            observations.append(
                Observation(
                    description=event.description,
                    confidence=event.confidence,
                    span=span,
                    frame_indices=frames,
                    produced_by=provenance,
                )
            )
        return observations
