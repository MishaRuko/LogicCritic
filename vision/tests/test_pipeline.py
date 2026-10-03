from collections.abc import Iterator

from conftest import observe

from lab_vision.evaluation import ExpectedDeviation, GroundTruth, evaluate
from lab_vision.models import DeviationKind, StepStatus
from lab_vision.perception import PerceptionError, PerceptionRequest, ReplayPerceiver
from lab_vision.pipeline import Pipeline
from lab_vision.sinks import JsonlSink
from lab_vision.video import Frame


class FakeSource:
    def __init__(self, seconds: int) -> None:
        self.seconds = seconds

    def frames(self) -> Iterator[Frame]:
        for s in range(self.seconds):
            yield Frame(s, float(s), b"")


class ScriptedPerceiver:
    """Returns a prepared list of observations per window, in order."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[PerceptionRequest] = []

    def observe(self, request):
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Recorder:
    def __init__(self):
        self.observations, self.deviations, self.summary = [], [], None

    def on_observation(self, o):
        self.observations.append(o)

    def on_deviation(self, d):
        self.deviations.append(d)

    def on_run_complete(self, s):
        self.summary = s


def make(protocol, script, seconds=15, sinks=()):
    perceiver = ScriptedPerceiver(script)
    pipeline = Pipeline(
        protocol, FakeSource(seconds), perceiver, sinks=sinks, window_seconds=5, source_name="t"
    )
    return pipeline, perceiver


def test_end_to_end_flags_wrong_volume_and_reports_to_sinks(protocol):
    script = [
        [observe("add-diluent", values={"volume": (500, 0.9)})],
        [observe("transfer-sample", values={"destination": ("tube B", 0.9)}, start=5)],
        [observe("vortex", start=10)],
    ]
    recorder = Recorder()
    pipeline, _ = make(protocol, script, sinks=[recorder])
    result = pipeline.run()

    assert [d.kind for d in result.deviations] == [DeviationKind.WRONG_VALUE]
    assert result.deviations == recorder.deviations
    assert recorder.summary.deviations == 1 and recorder.summary.windows == 3
    assert result.deviations[0].run_id == result.summary.run_id


def test_focus_moves_forward_and_stops_when_protocol_is_done(protocol):
    script = [
        [observe("add-diluent", values={"volume": (900, 0.9)})],
        [observe("transfer-sample", values={"destination": ("tube B", 0.9)}, start=5)],
        [observe("vortex", start=10)],
    ]
    pipeline, perceiver = make(protocol, script, seconds=30)
    pipeline.run()
    assert [s.id for s in perceiver.requests[0].steps] == [
        "add-diluent",
        "transfer-sample",
        "vortex",
    ]
    assert [s.id for s in perceiver.requests[2].steps] == ["vortex"]
    assert len(perceiver.requests) == 3  # stops once the protocol is complete


def test_failed_window_is_skipped_not_fatal(protocol):
    script = [PerceptionError("boom"), [observe("add-diluent", values={"volume": (900, 0.9)})]]
    result = make(protocol, script, seconds=10)[0].run()
    assert result.summary.failed_windows == 1
    assert result.summary.observations == 1
    assert {d.step_id for d in result.deviations} == {"transfer-sample", "vortex"}  # unobserved


def test_jsonl_sink_and_replay_reproduce_the_run(protocol, tmp_path):
    script = [
        [observe("add-diluent", StepStatus.PERFORMED, {"volume": (500, 0.9)})],
        [observe("transfer-sample", values={"destination": ("tube B", 0.9)}, start=5)],
        [observe("vortex", start=10)],
    ]
    first, _ = make(protocol, script, sinks=[JsonlSink(tmp_path)])
    original = first.run()

    replay = ReplayPerceiver.from_jsonl(tmp_path / "observations.jsonl")
    again = Pipeline(protocol, FakeSource(15), replay, window_seconds=5).run()

    assert [(d.kind, d.step_id) for d in again.deviations] == [
        (d.kind, d.step_id) for d in original.deviations
    ]
    assert (tmp_path / "summary.json").exists()


def test_evaluation_scores_hits_misses_and_false_alarms(protocol):
    script = [
        [observe("add-diluent", values={"volume": (500, 0.9)})],
        [observe("transfer-sample", values={"destination": ("tube A", 0.9)}, start=5)],
        [observe("vortex", start=10)],
    ]
    result = make(protocol, script)[0].run()
    truth = GroundTruth(
        clip_id="clip",
        deviations=[
            ExpectedDeviation(kind=DeviationKind.WRONG_VALUE, step_id="add-diluent"),
            ExpectedDeviation(kind=DeviationKind.OUT_OF_ORDER, step_id="vortex"),
        ],
    )
    report = evaluate(truth, result.deviations)

    assert report.detected == 1 and report.recall == 0.5
    assert [m.kind for m in report.missed] == [DeviationKind.OUT_OF_ORDER]
    assert [d.step_id for d in report.false_alarms] == ["transfer-sample"]
