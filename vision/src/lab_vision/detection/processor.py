import logging
from collections.abc import Sequence
from dataclasses import replace

import cv2
import numpy as np

from lab_vision.detection.base import ObjectDetector
from lab_vision.detection.tracker import IoUTracker
from lab_vision.detection.types import TrackedDetection
from lab_vision.models import ProtocolStep
from lab_vision.video import Crop, Frame, FrameWindow

log = logging.getLogger(__name__)


class DetectionProcessor:
    """Finds the objects the protocol mentions, tracks them, and adds close-ups for the model.

    Runs on every sampled frame in a window. For each value the protocol asks to read
    (`Check.target`) it cuts the best detection out of the full-resolution frame and enlarges
    it, so a dial or display has more pixels than in the downscaled frame. Detections below
    `min_crop_score` are not cropped, because a crop of the wrong object is worse than none.
    The tracker keeps its state across windows, so track ids stay stable through the video.
    """

    def __init__(
        self,
        detector: ObjectDetector,
        tracker: IoUTracker | None = None,
        min_crop_score: float = 0.3,
        min_track_score: float = 0.2,
        max_crops: int = 2,
        crop_padding: float = 0.3,
        crop_min_side: int = 384,
        max_upscale: float = 3.0,
        jpeg_quality: int = 90,
    ) -> None:
        self.detector = detector
        self.tracker = tracker or IoUTracker()
        self.min_crop_score = min_crop_score
        self.min_track_score = min_track_score
        self.max_crops = max_crops
        self.crop_padding = crop_padding
        self.crop_min_side = crop_min_side
        self.max_upscale = max_upscale
        self.jpeg_quality = jpeg_quality

    def process(self, window: FrameWindow, steps: tuple[ProtocolStep, ...]) -> FrameWindow:
        queries = _queries(steps)
        frames = window.candidates or window.frames
        if not queries or not frames:
            return window

        images: dict[int, np.ndarray] = {}
        tracked: list[TrackedDetection] = []
        for frame in frames:
            image = _image(frame)
            images[frame.index] = image
            found = [
                d for d in self.detector.detect(image, queries) if d.score >= self.min_track_score
            ]
            tracked += self.tracker.update(frame.index, frame.timestamp_s, found)

        crops = self._crops(steps, tracked, images, {f.index: f for f in frames})
        notes = _notes(queries, tracked, len(frames), self.min_crop_score)
        return replace(window, crops=tuple(crops), notes=tuple(notes), detections=tuple(tracked))

    def _crops(
        self,
        steps: Sequence[ProtocolStep],
        tracked: Sequence[TrackedDetection],
        images: dict[int, np.ndarray],
        frames: dict[int, Frame],
    ) -> list[Crop]:
        crops: list[Crop] = []
        for target in dict.fromkeys(c.target for s in steps for c in s.checks if c.target):
            candidates = [
                d for d in tracked if d.label == target and d.score >= self.min_crop_score
            ]
            if not candidates:
                continue
            best = max(candidates, key=lambda d: d.score)
            jpeg = self._cut(images[best.frame_index], best)
            crops.append(
                Crop(
                    label=target,
                    track_id=best.track_id,
                    timestamp_s=frames[best.frame_index].timestamp_s,
                    score=best.score,
                    jpeg=jpeg,
                )
            )
        return sorted(crops, key=lambda c: c.score, reverse=True)[: self.max_crops]

    def _cut(self, image: np.ndarray, detection: TrackedDetection) -> bytes:
        height, width = image.shape[:2]
        box = detection.box.expanded(self.crop_padding)
        x0, y0 = int(box.x0 * width), int(box.y0 * height)
        x1, y1 = max(x0 + 1, int(box.x1 * width)), max(y0 + 1, int(box.y1 * height))
        patch = image[y0:y1, x0:x1]
        short_side = min(patch.shape[:2])
        scale = min(self.max_upscale, self.crop_min_side / short_side)
        if scale > 1:
            patch = cv2.resize(patch, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        ok, buffer = cv2.imencode(".jpg", patch, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
        if not ok:
            raise RuntimeError("JPEG encoding failed")
        return buffer.tobytes()


def _queries(steps: Sequence[ProtocolStep]) -> list[str]:
    """Check targets first, then the other apparatus the steps mention, without repeats."""
    targets = [c.target for s in steps for c in s.checks if c.target]
    objects = [o for s in steps for o in s.objects]
    return list(dict.fromkeys([*targets, *objects]))


def _image(frame: Frame) -> np.ndarray:
    if frame.image is not None:
        return frame.image
    return cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_COLOR)


def _notes(
    queries: Sequence[str], tracked: Sequence[TrackedDetection], frames: int, min_score: float
) -> list[str]:
    notes = []
    for query in queries:
        hits = [d for d in tracked if d.label == query and d.score >= min_score]
        if not hits:
            notes.append(f"{query}: not detected in any of the {frames} frames")
            continue
        seen = len({d.frame_index for d in hits})
        tracks = ", ".join(sorted({d.track_id for d in hits}))
        best = max(d.score for d in hits)
        notes.append(f"{query}: detected in {seen} of {frames} frames ({tracks}), best {best:.2f}")
    return notes
