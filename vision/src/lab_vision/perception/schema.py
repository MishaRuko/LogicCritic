from pydantic import BaseModel, Field

from lab_vision.models import StepStatus


class ValueOut(BaseModel):
    check_id: str
    value: str | None = Field(description="The reading, as seen. Null if it cannot be read.")
    confidence: float = Field(description="0 to 1: how clearly the frames show this reading.")


class StepOut(BaseModel):
    step_id: str
    status: StepStatus
    confidence: float = Field(description="0 to 1: how clearly the frames show this status.")
    values: list[ValueOut]
    notes: str | None = Field(description="What was seen, in one sentence. Null if nothing to add.")


class EventOut(BaseModel):
    description: str
    confidence: float = Field(description="0 to 1.")


class PerceptionOut(BaseModel):
    """What the model returns for one window. One entry per step it was asked about."""

    observations: list[StepOut]
    unexpected_events: list[EventOut]
