import logging
import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from lab_vision.models import Deviation, Observation, Protocol, ProtocolStep
from lab_vision.models.common import new_id
from lab_vision.perception import FrameProcessor, Perceiver, PerceptionError, PerceptionRequest
from lab_vision.sinks import RunSink, RunSummary
from lab_vision.verification import ProtocolVerifier
from lab_vision.video import FrameSource, FrameWindow, make_windows

log = logging.getLogger(__name__)


class WindowObserver(typing.Protocol):
    """Sees each window as the model will, after processing. For debugging and inspection."""

    def on_window(self, window: FrameWindow, steps: tuple[ProtocolStep, ...]) -> None: ...


@dataclass
class RunResult:
    summary: RunSummary
    observations: list[Observation] = field(default_factory=list)
    deviations: list[Deviation] = field(default_factory=list)


class Pipeline:
    """video -> windows -> perception -> deterministic verification -> sinks.

    Each stage sits behind an interface, so a live stream, a different model, preprocessing
    such as cropping, or the graph backend can be swapped in without touching the rest.
    """

    def __init__(
        self,
        protocol: Protocol,
        source: FrameSource,
        perceiver: Perceiver,
        sinks: Sequence[RunSink] = (),
        processors: Sequence[FrameProcessor] = (),
        window_observers: Sequence[WindowObserver] = (),
        window_seconds: float = 5.0,
        max_frames_per_window: int = 5,
        lookahead_steps: int = 3,
        min_confidence: float = 0.6,
        source_name: str = "unknown",
        run_id_factory: Callable[[], str] = new_id,
    ) -> None:
        self.protocol = protocol
        self.source = source
        self.perceiver = perceiver
        self.sinks = tuple(sinks)
        self.processors = tuple(processors)
        self.window_observers = tuple(window_observers)
        self.window_seconds = window_seconds
        self.max_frames_per_window = max_frames_per_window
        self.lookahead_steps = lookahead_steps
        self.min_confidence = min_confidence
        self.source_name = source_name
        self._run_id_factory = run_id_factory

    def run(self) -> RunResult:
        run_id = self._run_id_factory()
        verifier = ProtocolVerifier(self.protocol, self.min_confidence, run_id)
        observations: list[Observation] = []
        deviations: list[Deviation] = []
        windows = failed = 0
        last_span = None

        for window in make_windows(
            self.source.frames(), self.window_seconds, self.max_frames_per_window
        ):
            windows += 1
            last_span = window.span
            steps = verifier.pending(self.lookahead_steps)
            if not steps:
                break
            for processor in self.processors:
                try:
                    window = processor.process(window, steps)
                except Exception:  # noqa: BLE001 - processors only enhance; never lose a window
                    log.exception("processor %s failed; using the unprocessed window", processor)
            for observer in self.window_observers:
                observer.on_window(window, steps)
            try:
                found = self.perceiver.observe(PerceptionRequest(window, steps, run_id))
            except PerceptionError as exc:
                failed += 1
                log.warning("window %.1f-%.1fs skipped: %s", *_bounds(window.span), exc)
                continue
            for observation in found:
                observations.append(observation)
                self._emit(lambda s, o=observation: s.on_observation(o))
                for deviation in verifier.ingest(observation):
                    deviations.append(deviation)
                    self._emit(lambda s, d=deviation: s.on_deviation(d))

        for deviation in verifier.finalize(last_span):
            deviations.append(deviation)
            self._emit(lambda s, d=deviation: s.on_deviation(d))

        summary = RunSummary(
            run_id=run_id,
            protocol_id=self.protocol.id,
            protocol_version=self.protocol.version,
            source=self.source_name,
            windows=windows,
            failed_windows=failed,
            observations=len(observations),
            deviations=len(deviations),
            needs_review=sum(d.needs_review for d in deviations),
        )
        self._emit(lambda s: s.on_run_complete(summary))
        return RunResult(summary, observations, deviations)

    def _emit(self, call: Callable[[RunSink], None]) -> None:
        for sink in self.sinks:
            call(sink)


def _bounds(span) -> tuple[float, float]:
    return span.start_s, span.end_s
