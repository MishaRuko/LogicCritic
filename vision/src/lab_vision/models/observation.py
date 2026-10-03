from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator

from lab_vision.models.common import Provenance, TimeSpan, new_id, utcnow


class StepStatus(StrEnum):
    PERFORMED = "performed"
    IN_PROGRESS = "in_progress"
    NOT_OBSERVED = "not_observed"
    UNCLEAR = "unclear"


class ObservedValue(BaseModel):
    check_id: str
    value: str | float | None = None
    confidence: float = Field(ge=0, le=1)


class Observation(BaseModel):
    """A claim about what the recording shows. It is a `reported` statement, not a fact.

    With a `step_id` it describes progress on that protocol step. Without one it describes an
    event outside the protocol and needs a `description`.
    """

    id: str = Field(default_factory=new_id)
    step_id: str | None = None
    status: StepStatus = StepStatus.UNCLEAR
    values: list[ObservedValue] = Field(default_factory=list)
    description: str | None = None
    confidence: float = Field(ge=0, le=1)
    span: TimeSpan
    frame_indices: list[int] = Field(default_factory=list)
    produced_by: Provenance
    created_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _outside_protocol_needs_description(self) -> "Observation":
        if self.step_id is None and not self.description:
            raise ValueError("an observation outside the protocol needs a description")
        return self
