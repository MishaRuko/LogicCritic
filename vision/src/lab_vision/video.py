import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from lab_vision.detection.types import TrackedDetection
from lab_vision.models import TimeSpan

log = logging.getLogger(__name__)


class VideoError(RuntimeError):
    pass


@dataclass(frozen=True)
class Frame:
    index: int
    timestamp_s: float
    jpeg: bytes
    # Full-resolution BGR image, kept only while a window is processed, so crops can be cut
    # from more pixels than the downscaled JPEG holds. Not set unless the source is asked to.
    image: np.ndarray | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class Crop:
    """A close-up of a detected object, sent to the model alongside the full frames."""

    label: str
    track_id: str
    timestamp_s: float
    score: float
    jpeg: bytes


@dataclass(frozen=True)
class FrameWindow:
    span: TimeSpan
    frames: tuple[Frame, ...]  # what the model is shown
    candidates: tuple[Frame, ...] = ()  # every sampled frame in the window, for processors
    crops: tuple[Crop, ...] = ()
    notes: tuple[str, ...] = ()  # automatic observations, such as what a detector found
    detections: tuple[TrackedDetection, ...] = ()


class FrameSource(Protocol):
    """Anything that yields timestamped frames in order. A live stream is another source."""

    def frames(self) -> Iterator[Frame]: ...


class VideoFileSource:
    """Samples frames from a video file at a fixed rate and encodes them as JPEG."""

    def __init__(
        self,
        path: Path,
        sample_fps: float = 1.0,
        max_side: int = 1024,
        jpeg_quality: int = 85,
        keep_images: bool = False,
    ) -> None:
        self.path = path
        self.sample_fps = sample_fps
        self.max_side = max_side
        self.jpeg_quality = jpeg_quality
        self.keep_images = keep_images

    def frames(self) -> Iterator[Frame]:
        if not self.path.is_file():
            raise VideoError(f"video not found: {self.path}")
        capture = cv2.VideoCapture(str(self.path))
        if not capture.isOpened():
            raise VideoError(f"cannot open video: {self.path}")
        try:
            native_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
            stride = max(1, round(native_fps / self.sample_fps))
            index = 0
            while capture.grab():
                if index % stride == 0:
                    ok, image = capture.retrieve()
                    if ok:
                        yield Frame(
                            index,
                            index / native_fps,
                            self._encode(image),
                            image if self.keep_images else None,
                        )
                    else:
                        log.warning("failed to decode frame %d of %s", index, self.path)
                index += 1
        finally:
            capture.release()

    def _encode(self, image) -> bytes:
        height, width = image.shape[:2]
        scale = self.max_side / max(height, width)
        if scale < 1:
            image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            raise VideoError("JPEG encoding failed")
        return buffer.tobytes()


def make_windows(
    frames: Iterable[Frame], window_seconds: float, max_frames: int
) -> Iterator[FrameWindow]:
    """Group a frame stream into consecutive windows of at most `max_frames` frames each."""
    bucket: list[Frame] = []
    bucket_id = 0
    for frame in frames:
        frame_bucket = int(frame.timestamp_s // window_seconds)
        if bucket and frame_bucket != bucket_id:
            yield _window(bucket, bucket_id, window_seconds, max_frames)
            bucket = []
        bucket_id = frame_bucket
        bucket.append(frame)
    if bucket:
        yield _window(bucket, bucket_id, window_seconds, max_frames)


def _window(bucket: list[Frame], bucket_id: int, size: float, max_frames: int) -> FrameWindow:
    everything = bucket
    if len(bucket) > max_frames:
        step = (len(bucket) - 1) / (max_frames - 1) if max_frames > 1 else 0
        bucket = [bucket[round(i * step)] for i in range(max_frames)]
    span = TimeSpan(
        start_s=bucket_id * size, end_s=max(everything[-1].timestamp_s, bucket_id * size)
    )
    return FrameWindow(span=span, frames=tuple(bucket), candidates=tuple(everything))
