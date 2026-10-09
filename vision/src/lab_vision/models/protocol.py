from enum import StrEnum

from pydantic import BaseModel, Field, model_validator


class CheckKind(StrEnum):
    EQUALS = "equals"
    NUMERIC = "numeric"


class Check(BaseModel):
    """A single value that can be read off the video and compared with an expectation.

    The expected value is never shown to the perception model. It is asked to read the
    value, and the comparison is done deterministically in verification.
    """

    id: str
    question: str
    kind: CheckKind
    expected: str | float
    unit: str | None = None
    target: str | None = Field(
        default=None,
        description=(
            "Short visual description of the object this value is read from, such as 'handheld "
            "pipette with digital display'. Used to find the object and send a close-up."
        ),
    )
    tolerance: float = Field(default=0.0, ge=0)
    accept: list[str] = Field(
        default_factory=list,
        description="Extra accepted spellings for an EQUALS check.",
    )

    @model_validator(mode="after")
    def _expected_matches_kind(self) -> "Check":
        if self.kind is CheckKind.NUMERIC and isinstance(self.expected, str):
            try:
                self.expected = float(self.expected)
            except ValueError as exc:
                raise ValueError(f"check {self.id!r}: numeric check needs a numeric value") from exc
        if self.kind is CheckKind.EQUALS:
            self.expected = str(self.expected)
        return self


class ProtocolStep(BaseModel):
    id: str
    description: str
    source_text: str | None = Field(
        default=None,
        description="The exact sentence in the written protocol this step was derived from.",
    )
    objects: list[str] = Field(
        default_factory=list,
        description="Apparatus and materials expected to be involved in this step.",
    )
    checks: list[Check] = Field(default_factory=list)
    optional: bool = False
    variant: str | None = Field(
        default=None,
        description=(
            "When the source describes alternative procedures (two device types, two sample "
            "preparations), the one this step belongs to. None for steps every procedure shares."
        ),
    )
    obligation_ids: list[str] = Field(
        default_factory=list,
        description="Graph proof obligations this step exists to resolve.",
    )

    def check(self, check_id: str) -> Check | None:
        return next((c for c in self.checks if c.id == check_id), None)


class Protocol(BaseModel):
    """An ordered experimental procedure. List order is the required execution order."""

    id: str
    title: str
    version: str = "1"
    steps: list[ProtocolStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> "Protocol":
        step_ids = [s.id for s in self.steps]
        if len(set(step_ids)) != len(step_ids):
            raise ValueError("step ids must be unique")
        for step in self.steps:
            check_ids = [c.id for c in step.checks]
            if len(set(check_ids)) != len(check_ids):
                raise ValueError(f"step {step.id!r}: check ids must be unique")
        return self

    @property
    def variants(self) -> list[str]:
        """The alternative procedures the source describes, in order of first appearance."""
        return list(dict.fromkeys(s.variant for s in self.steps if s.variant))

    def only(self, variant: str | None) -> "Protocol":
        """The procedure a recording follows: the shared steps plus one variant's (the first
        when none is named). A protocol without variants is returned unchanged."""
        if not self.variants:
            return self
        chosen = variant if variant in self.variants else self.variants[0]
        steps = [s for s in self.steps if s.variant in (None, chosen)]
        return self.model_copy(update={"steps": steps})

    def step(self, step_id: str) -> ProtocolStep | None:
        return next((s for s in self.steps if s.id == step_id), None)

    def index_of(self, step_id: str) -> int:
        return next(i for i, s in enumerate(self.steps) if s.id == step_id)
