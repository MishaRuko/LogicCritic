from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from lab_vision.models.common import TimeSpan, new_id, utcnow


class DeviationKind(StrEnum):
    WRONG_VALUE = "wrong_value"
    SKIPPED_STEP = "skipped_step"
    INCOMPLETE_STEP = "incomplete_step"
    OUT_OF_ORDER = "out_of_order"
    UNEXPECTED_EVENT = "unexpected_event"
    UNVERIFIED_CHECK = "unverified_check"


class Deviation(BaseModel):
    """A departure from the protocol, with the observations that caused it.

    `needs_review` marks findings that rest on missing or uncertain evidence rather than on a
    confident contradicting observation. A human decides whether they matter.
    """

    id: str = Field(default_factory=new_id)
    kind: DeviationKind
    rule: str
    step_id: str | None = None
    check_id: str | None = None
    expected: str | float | None = None
    observed: str | float | None = None
    message: str
    needs_review: bool = False
    observation_ids: list[str] = Field(default_factory=list)
    span: TimeSpan | None = None
    run_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
