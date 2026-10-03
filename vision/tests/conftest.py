from pathlib import Path

import pytest

from lab_vision.models import Observation, ObservedValue, Provenance, StepStatus, TimeSpan
from lab_vision.protocol import load_protocol

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def protocol():
    return load_protocol(FIXTURES / "protocol.yaml")


def observe(
    step_id: str | None,
    status: StepStatus = StepStatus.PERFORMED,
    values: dict[str, tuple[str | float, float]] | None = None,
    confidence: float = 0.9,
    start: float = 0,
    description: str | None = None,
) -> Observation:
    return Observation(
        step_id=step_id,
        status=status,
        values=[
            ObservedValue(check_id=k, value=v, confidence=c) for k, (v, c) in (values or {}).items()
        ],
        description=description,
        confidence=confidence,
        span=TimeSpan(start_s=start, end_s=start + 5),
        produced_by=Provenance(actor_type="extractor", actor_id="test"),
    )
