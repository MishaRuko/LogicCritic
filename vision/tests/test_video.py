import cv2
import numpy as np
import pytest

from lab_vision.video import Frame, VideoError, VideoFileSource, make_windows


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
