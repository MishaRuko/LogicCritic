from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from lab_vision.models import Deviation, Observation


class RunSummary(BaseModel):
    run_id: str
    protocol_id: str
    protocol_version: str
    source: str
    windows: int
    failed_windows: int
    observations: int
    deviations: int
    needs_review: int


class RunSink(Protocol):
    """Where results go. The graph integration is another implementation of this interface."""

    def on_observation(self, observation: Observation) -> None: ...

    def on_deviation(self, deviation: Deviation) -> None: ...

    def on_run_complete(self, summary: RunSummary) -> None: ...


class JsonlSink:
    """Writes observations.jsonl, deviations.jsonl and summary.json into a directory."""

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._directory = directory
        self._observations = (directory / "observations.jsonl").open("w", encoding="utf-8")
        self._deviations = (directory / "deviations.jsonl").open("w", encoding="utf-8")

    def on_observation(self, observation: Observation) -> None:
        self._write(self._observations, observation)

    def on_deviation(self, deviation: Deviation) -> None:
        self._write(self._deviations, deviation)

    def on_run_complete(self, summary: RunSummary) -> None:
        (self._directory / "summary.json").write_text(summary.model_dump_json(indent=2))
        self._observations.close()
        self._deviations.close()

    @staticmethod
    def _write(handle, model: BaseModel) -> None:
        handle.write(model.model_dump_json() + "\n")
        handle.flush()
