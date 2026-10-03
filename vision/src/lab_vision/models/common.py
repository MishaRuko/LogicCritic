from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Provenance(BaseModel):
    """Who produced an object. Mirrors the graph's Provenance contract (ARCHITECTURE.md 5.7)."""

    actor_type: Literal["user", "agent", "extractor", "rule_engine", "integration"]
    actor_id: str
    model: str | None = None
    prompt_version: str | None = None
    run_id: str | None = None


class TimeSpan(BaseModel):
    """A stretch of the recording, in seconds from the start of the video."""

    start_s: float = Field(ge=0)
    end_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "TimeSpan":
        if self.end_s < self.start_s:
            raise ValueError("end_s must not be before start_s")
        return self

    def overlaps(self, other: "TimeSpan", tolerance_s: float = 0.0) -> bool:
        return (
            self.start_s <= other.end_s + tolerance_s and other.start_s <= self.end_s + tolerance_s
        )
