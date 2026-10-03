import json
from collections.abc import Iterator

import cv2
import numpy as np
import pytest
from conftest import observe

from lab_vision.debug import FrameDumper
from lab_vision.detection import BoundingBox, Detection, IoUTracker
from lab_vision.detection.processor import DetectionProcessor
from lab_vision.models import Check, CheckKind, ProtocolStep, TimeSpan
from lab_vision.perception import PerceptionRequest
from lab_vision.perception.prompts import build_user_content
from lab_vision.pipeline import Pipeline
from lab_vision.video import Frame, FrameWindow, make_windows


def box(x0, y0, x1, y1) -> BoundingBox:
    return BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1)


def det(label, b, score=0.9) -> Detection:
    return Detection(label=label, box=b, score=score)


# -- boxes and tracker


def test_box_iou_and_expansion():
    a = box(0, 0, 0.5, 0.5)
    assert a.iou(a) == pytest.approx(1.0)
    assert a.iou(box(0.5, 0.5, 1, 1)) == 0.0
    assert a.iou(box(0.25, 0, 0.75, 0.5)) == pytest.approx(1 / 3)
    grown = box(0.4, 0.4, 0.6, 0.6).expanded(0.5)
    assert (grown.x0, grown.x1) == pytest.approx((0.3, 0.7))
    assert box(0, 0, 0.2, 0.2).expanded(1).x0 == 0  # clamped to the image


def test_tracker_keeps_identity_for_overlapping_boxes():
    tracker = IoUTracker()
    first = tracker.update(0, 0.0, [det("bucket", box(0.4, 0.4, 0.6, 0.6))])
    second = tracker.update(1, 1.0, [det("bucket", box(0.42, 0.41, 0.62, 0.61))])
    assert first[0].track_id == second[0].track_id == "bucket#1"


def test_track_ids_are_short_slugs_of_the_description():
    out = IoUTracker().update(
        0, 0.0, [det("handheld pipette with digital display", box(0, 0, 0.5, 0.5))]
    )
    assert out[0].track_id == "handheld-pipette-with#1"


def test_processor_ignores_weak_detections_and_caps_crops():
    steps = (
        ProtocolStep(
            id="s",
            description="Read two things.",
            checks=[
                Check(id=n, question="?", kind=CheckKind.NUMERIC, expected=1, target=n)
                for n in ("a", "b", "c")
            ],
        ),
    )
    detector = FakeDetector(
        [
            [
                det("a", box(0, 0, 0.3, 0.3), 0.9),
                det("b", box(0.4, 0.4, 0.7, 0.7), 0.6),
                det("c", box(0.6, 0, 0.9, 0.3), 0.5),
                det("a", box(0.5, 0.5, 0.6, 0.6), 0.1),  # below the tracking floor
            ]
        ]
    )
    window = DetectionProcessor(detector, max_crops=2).process(window_of(frame(0, 0.0)), steps)
    assert [c.label for c in window.crops] == ["a", "b"]
    assert len(window.detections) == 3


def test_tracker_separates_labels_and_new_objects():
    tracker = IoUTracker()
    out = tracker.update(
        0,
        0.0,
        [
            det("tube", box(0, 0, 0.2, 0.2)),
            det("tube", box(0.6, 0.6, 0.8, 0.8)),
            det("bucket", box(0, 0, 0.2, 0.2)),
        ],
    )
    assert sorted(t.track_id for t in out) == ["bucket#1", "tube#1", "tube#2"]


def test_tracker_survives_a_short_occlusion_but_not_a_long_one():
    tracker = IoUTracker(max_misses=2)
    tracker.update(0, 0.0, [det("tube", box(0.4, 0.4, 0.6, 0.6))])
    tracker.update(1, 1.0, [])
    back = tracker.update(2, 2.0, [det("tube", box(0.4, 0.4, 0.6, 0.6))])
    assert back[0].track_id == "tube#1"

    for i in range(3, 7):
        tracker.update(i, float(i), [])
    later = tracker.update(7, 7.0, [det("tube", box(0.4, 0.4, 0.6, 0.6))])
    assert later[0].track_id == "tube#2"


# -- processor


class FakeDetector:
    """Returns prepared detections per call, and records what it was asked for."""

    def __init__(self, per_frame):
        self.per_frame, self.calls = list(per_frame), []

    def detect(self, image, labels):
        self.calls.append((image.shape, list(labels)))
        return self.per_frame.pop(0)


def frame(index, t, size=(120, 160), keep=True) -> Frame:
    image = np.full((*size, 3), 90, dtype=np.uint8)
    ok, jpeg = cv2.imencode(".jpg", image)
    return Frame(index, t, jpeg.tobytes(), image if keep else None)


def reading_step() -> ProtocolStep:
    check = Check(
        id="volume",
        question="What volume is set?",
        kind=CheckKind.NUMERIC,
        expected=5,
        target="pipette",
    )
    return ProtocolStep(id="s1", description="Add DNA.", objects=["ice bucket"], checks=[check])


def window_of(*frames) -> FrameWindow:
    return FrameWindow(
        TimeSpan(start_s=0, end_s=len(frames)), tuple(frames[:1]), candidates=tuple(frames)
    )


def test_processor_adds_an_enlarged_crop_of_the_best_target_detection():
    detector = FakeDetector(
        [
            [det("pipette", box(0.1, 0.1, 0.2, 0.2), 0.5)],
            [
                det("pipette", box(0.4, 0.4, 0.5, 0.5), 0.8),
                det("ice bucket", box(0, 0.5, 0.5, 1), 0.6),
            ],
        ]
    )
    window = DetectionProcessor(detector, crop_min_side=200).process(
        window_of(frame(0, 0.0), frame(1, 1.0)), (reading_step(),)
    )

    assert [c.label for c in window.crops] == ["pipette"]
    crop = window.crops[0]
    assert crop.timestamp_s == 1.0 and crop.score == 0.8  # the higher-scoring frame
    decoded = cv2.imdecode(np.frombuffer(crop.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (57, 75)  # a 19x25 px patch, enlarged by the 3x cap
    assert detector.calls[0][1] == ["pipette", "ice bucket"]  # target first, then objects
    assert len(window.detections) == 3


def test_processor_skips_low_confidence_crops_and_says_so():
    detector = FakeDetector([[det("pipette", box(0.1, 0.1, 0.3, 0.3), 0.2)]])
    window = DetectionProcessor(detector, min_crop_score=0.3).process(
        window_of(frame(0, 0.0)), (reading_step(),)
    )
    assert window.crops == ()
    assert any("ice bucket: not detected" in n for n in window.notes)
    assert not any(n.startswith("pipette: detected") for n in window.notes)


def test_processor_notes_report_track_ids_and_counts():
    detector = FakeDetector([[det("ice bucket", box(0, 0.5, 0.5, 1), 0.6)]] * 2)
    window = DetectionProcessor(detector).process(
        window_of(frame(0, 0.0), frame(1, 1.0)), (reading_step(),)
    )
    assert "ice bucket: detected in 2 of 2 frames (ice-bucket#1), best 0.60" in window.notes


def test_processor_works_from_the_jpeg_when_no_full_resolution_image():
    detector = FakeDetector([[]])
    window = DetectionProcessor(detector).process(
        window_of(frame(0, 0.0, keep=False)), (reading_step(),)
    )
    assert detector.calls[0][0] == (120, 160, 3) and window.crops == ()


def test_processor_leaves_a_window_alone_when_there_is_nothing_to_look_for():
    bare = ProtocolStep(id="s", description="Wait.")
    original = window_of(frame(0, 0.0))
    assert DetectionProcessor(FakeDetector([])).process(original, (bare,)) is original


def test_make_windows_keeps_every_sampled_frame_as_a_candidate():
    frames = [Frame(i, float(i), b"") for i in range(12)]
    first = next(make_windows(frames, window_seconds=5, max_frames=3))
    assert len(first.frames) == 3 and len(first.candidates) == 5


# -- prompt, dumper, pipeline


def test_prompt_includes_crops_and_detector_notes(protocol):
    window = FrameWindow(
        TimeSpan(start_s=0, end_s=4),
        (Frame(0, 0.0, b"\xff\xd8x"),),
        crops=(
            __import__("lab_vision.video", fromlist=["Crop"]).Crop(
                "pipette", "pipette#1", 2.0, 0.8, b"\xff\xd8c"
            ),
        ),
        notes=("pipette: detected in 2 of 5 frames (pipette#1), best 0.80",),
    )
    content = build_user_content(PerceptionRequest(window, tuple(protocol.steps[:1]), "run"))
    texts = [p["text"] for p in content if p["type"] == "text"]

    assert sum(p["type"] == "image" for p in content) == 2
    assert any("Close-up of pipette (pipette#1)" in t for t in texts)
    assert any("Automatic detector notes" in t and "pipette#1" in t for t in texts)


def test_frame_dumper_writes_frames_overlays_crops_and_metadata(tmp_path):
    detector = FakeDetector([[det("pipette", box(0.1, 0.1, 0.4, 0.4), 0.9)]])
    window = DetectionProcessor(detector).process(window_of(frame(0, 0.0)), (reading_step(),))
    FrameDumper(tmp_path).on_window(window, (reading_step(),))

    folder = tmp_path / "windows" / "00000.0s"
    names = sorted(p.name for p in folder.iterdir())
    assert "frame_000000.jpg" in names and "overlay_000000.jpg" in names
    assert any(n.startswith("crop_0_") for n in names)
    info = json.loads((folder / "window.json").read_text())
    assert info["steps_in_focus"] == ["s1"] and info["crops"][0]["track"] == "pipette#1"


class Boom:
    def process(self, window, steps):
        raise RuntimeError("detector exploded")


class Source:
    def frames(self) -> Iterator[Frame]:
        yield from (Frame(s, float(s), b"") for s in range(5))


class OneWindow:
    def observe(self, request):
        return [observe("add-diluent", values={"volume": (900, 0.9)})]


class Spy:
    def __init__(self):
        self.seen = []

    def on_window(self, window, steps):
        self.seen.append((window.span.start_s, [s.id for s in steps]))


def test_a_failing_processor_does_not_lose_the_window(protocol):
    spy = Spy()
    result = Pipeline(
        protocol,
        Source(),
        OneWindow(),
        processors=[Boom()],
        window_observers=[spy],
        window_seconds=5,
    ).run()
    assert result.summary.failed_windows == 0 and result.summary.observations == 1
    assert spy.seen == [(0.0, ["add-diluent", "transfer-sample", "vortex"])]
