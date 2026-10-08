import cv2
import numpy as np
import pytest

from lab_vision.video import Frame, LiveFrameSource, VideoError, VideoFileSource, make_windows


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for i in range(100):  # 10 seconds at 10 fps
        writer.write(np.full((48, 64, 3), i, dtype=np.uint8))
    writer.release()
    return path


def test_samples_at_requested_rate(clip):
    frames = list(VideoFileSource(clip, sample_fps=1.0).frames())
    assert len(frames) == 10
    assert [round(f.timestamp_s) for f in frames] == list(range(10))
    assert frames[0].jpeg[:2] == b"\xff\xd8"


def test_downscales_large_frames(tmp_path):
    path = tmp_path / "big.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 5, (400, 200))
    for _ in range(5):
        writer.write(np.zeros((200, 400, 3), dtype=np.uint8))
    writer.release()
    frame = next(VideoFileSource(path, sample_fps=1, max_side=100).frames())
    decoded = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) == 100


def test_missing_video(tmp_path):
    with pytest.raises(VideoError):
        list(VideoFileSource(tmp_path / "nope.mp4").frames())


def test_windows_group_and_thin_frames():
    frames = [Frame(i, float(i), b"") for i in range(12)]
    windows = list(make_windows(frames, window_seconds=5, max_frames=3))
    assert [(w.span.start_s, len(w.frames)) for w in windows] == [(0, 3), (5, 3), (10, 2)]
    assert [f.index for f in windows[0].frames] == [0, 2, 4]


def _jpeg() -> bytes:
    ok, buffer = cv2.imencode(".jpg", np.zeros((8, 8, 3), dtype=np.uint8))
    assert ok
    return buffer.tobytes()


def test_live_source_yields_frames_in_time_order_as_they_arrive(tmp_path):
    arrivals = [["000002000.jpg", "000001000.jpg"], [], ["000003500.jpg", "notes.txt"]]
    ended = {"value": False}

    def sleep(_):
        batch = arrivals.pop(0) if arrivals else []
        for name in batch:
            (tmp_path / name).write_bytes(_jpeg())
        if not arrivals:
            ended["value"] = True

    sleep(0)  # the first frames are already there
    source = LiveFrameSource(tmp_path, ended=lambda: ended["value"], sleep=sleep)
    frames = list(source.frames())
    assert [f.timestamp_s for f in frames] == [1.0, 2.0, 3.5]
    assert [f.index for f in frames] == [0, 1, 2]


def test_live_source_stops_at_the_cap_and_when_the_camera_goes_quiet(tmp_path):
    for ms in (1000, 2000, 905000):
        (tmp_path / f"{ms:09d}.jpg").write_bytes(_jpeg())
    capped = LiveFrameSource(tmp_path, ended=lambda: False, max_seconds=900, sleep=lambda _: None)
    assert [f.timestamp_s for f in capped.frames()] == [1.0, 2.0]

    now = {"t": 0.0}

    def wait(seconds):
        now["t"] += seconds

    quiet = LiveFrameSource(
        tmp_path / "empty", ended=lambda: False, idle_seconds=5, clock=lambda: now["t"], sleep=wait
    )
    (tmp_path / "empty").mkdir()
    assert list(quiet.frames()) == []
    assert now["t"] > 5
