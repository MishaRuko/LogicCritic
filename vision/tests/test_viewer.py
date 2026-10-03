import json

import cv2
import numpy as np
import pytest

from lab_vision.models import Deviation, DeviationKind, Observation, Provenance, TimeSpan
from lab_vision.models.observation import ObservedValue, StepStatus
from lab_vision.viewer import (
    PANEL_W,
    TIMELINE_H,
    Composer,
    PlayerState,
    RunView,
    ascii_text,
    render,
    wrap,
)

FPS = 10


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (160, 120))
    for i in range(100):  # 10 seconds
        writer.write(np.full((120, 160, 3), 40 + i, dtype=np.uint8))
    writer.release()
    return path


@pytest.fixture
def run_dir(tmp_path):
    directory = tmp_path / "run"
    folder = directory / "windows" / "00000.0s"
    folder.mkdir(parents=True)
    crop = np.full((20, 30, 3), 200, dtype=np.uint8)
    cv2.imwrite(str(folder / "crop_0_pipette-1.jpg"), crop)
    (folder / "window.json").write_text(
        json.dumps(
            {
                "span": {"start_s": 0.0, "end_s": 4.0},
                "frames_sent": [0, 20, 40],
                "steps_in_focus": ["s1"],
                "notes": ["pipette: detected in 1 of 5 frames"],
                "crops": [{"label": "pipette", "track": "pipette#1", "t": 2.0, "score": 0.8}],
                "detections": [
                    {
                        "label": "pipette",
                        "box": {"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4},
                        "score": 0.8,
                        "track_id": "pipette#1",
                        "frame_index": 20,
                        "timestamp_s": 2.0,
                    }
                ],
            }
        )
    )
    obs = Observation(
        step_id="s1",
        status=StepStatus.IN_PROGRESS,
        values=[ObservedValue(check_id="volume", value="5", confidence=0.4)],
        description="Hand holds a pipette near a tube.",
        confidence=0.7,
        span=TimeSpan(start_s=0, end_s=4),
        frame_indices=[0, 20, 40],
        produced_by=Provenance(actor_type="extractor", actor_id="t"),
    )
    later = obs.model_copy(update={"id": "later", "span": TimeSpan(start_s=5, end_s=9)})
    deviation = Deviation(
        kind=DeviationKind.WRONG_VALUE,
        rule="value_mismatch",
        step_id="s1",
        message="Expected 5, observed 50.",
        span=TimeSpan(start_s=0, end_s=4),
    )
    (directory / "observations.jsonl").write_text(
        "\n".join(o.model_dump_json() for o in (obs, later)) + "\n"
    )
    (directory / "deviations.jsonl").write_text(deviation.model_dump_json() + "\n")
    return directory


def test_ascii_text_swaps_lab_symbols():
    assert ascii_text("5 μL at 42°C – done") == "5 uL at 42 degC - done"
    assert ascii_text("café") == "caf?"


def test_wrap_truncates_with_an_ellipsis():
    lines = wrap("word " * 40, width=20, max_lines=2)
    assert len(lines) == 2 and lines[-1].endswith("...") and len(lines[-1]) <= 20
    assert wrap("", 20, 2) == [""]


def test_run_view_loads_windows_observations_and_deviations(run_dir):
    run = RunView.load(run_dir)
    assert [w.start_s for w in run.windows] == [0.0, 5.0]
    first = run.windows[0]
    assert first.frames_sent == [0, 20, 40] and len(first.observations) == 1
    assert first.crop_files[0].name == "crop_0_pipette-1.jpg"
    assert run.windows[1].frames_sent == [0, 20, 40]  # taken from the observation
    assert len(run.deviations) == 1 and run.duration_s >= 9
    assert run.window_at(2.0) is first and run.window_at(5.5) is run.windows[1]


def test_run_view_with_only_observations_still_loads(run_dir):
    import shutil

    shutil.rmtree(run_dir / "windows")
    run = RunView.load(run_dir)
    assert [w.start_s for w in run.windows] == [0.0, 5.0] and run.windows[0].detections == []


def test_composed_frame_has_the_footage_panel_and_timeline(clip, run_dir):
    composer = Composer.open(clip, RunView.load(run_dir))
    width, height = composer.canvas_size
    assert (width, height) == (composer.video_w + PANEL_W, composer.video_h + TIMELINE_H)

    sent = composer.compose(composer.frame_at(20), 20)  # a frame the model was given
    unsent = composer.compose(composer.frame_at(25), 25)
    assert sent.shape == (height, width, 3)
    # the green "sent" border is on the picture's edge only for frames the model received
    assert tuple(sent[3, composer.video_w // 2]) == (90, 200, 90)
    assert tuple(unsent[3, composer.video_w // 2]) != (90, 200, 90)
    assert not np.array_equal(sent[:, composer.video_w :], unsent[:, composer.video_w :])
    composer.close()


def test_composing_a_frame_outside_any_window_does_not_fail(clip, run_dir):
    composer = Composer.open(clip, RunView.load(run_dir))
    out = composer.compose(composer.frame_at(95), 95)  # 9.5s: past the last window's reach
    assert out.shape[0] == composer.canvas_size[1]
    composer.close()


def test_render_writes_a_video_of_the_requested_length(clip, run_dir, tmp_path):
    composer = Composer.open(clip, RunView.load(run_dir))
    output = tmp_path / "view.mp4"
    frames = render(composer, output, start_s=1.0, end_s=2.0)
    composer.close()

    assert frames == 11  # frames at 1.0s through 2.0s inclusive, at 10 fps
    capture = cv2.VideoCapture(str(output))
    assert int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) == composer.canvas_size[0]
    capture.release()


def test_player_keys(run_dir):
    state = PlayerState(fps=FPS, total_frames=100, run=RunView.load(run_dir))
    state.handle(ord("d"))
    assert state.index == 10
    state.handle(ord("a"))
    state.handle(ord("a"))
    assert state.index == 0  # clamped
    state.handle(ord(" "))
    assert state.paused
    state.handle(ord("s"))
    assert state.index == 50  # next window starts at 5s
    state.handle(ord("w"))
    assert state.index == 0
    state.handle(ord("."))
    assert state.index == 1 and state.paused
    state.handle(ord("p"))
    assert state.save_still
    state.handle(ord("q"))
    assert state.quit
