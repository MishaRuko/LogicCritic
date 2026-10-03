from dataclasses import dataclass
from typing import Protocol

from lab_vision.models import Observation, ProtocolStep
from lab_vision.video import Frame, FrameWindow


class PerceptionError(RuntimeError):
    """A window could not be interpreted. No observations are produced for it."""


@dataclass(frozen=True)
class PerceptionRequest:
    """What the perceiver is asked about: one window, and only the steps currently relevant."""

    window: FrameWindow
    steps: tuple[ProtocolStep, ...]
    run_id: str


class Perceiver(Protocol):
    """Turns video into observations. Implementations propose; they never judge."""

    def observe(self, request: PerceptionRequest) -> list[Observation]: ...


class FrameProcessor(Protocol):
    """Optional preprocessing hook, for example cropping to a dial or tracking a tube."""

    def process(self, window: FrameWindow, steps: tuple[ProtocolStep, ...]) -> FrameWindow: ...


__all__ = ["Frame", "FrameProcessor", "PerceptionError", "PerceptionRequest", "Perceiver"]
