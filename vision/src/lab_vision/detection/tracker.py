import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from lab_vision.detection.types import BoundingBox, Detection, TrackedDetection


def slug(label: str, words: int = 3, limit: int = 24) -> str:
    """A short readable name for a track id, from the first few words of a description."""
    parts = re.findall(r"[a-z0-9]+", label.lower())[:words]
    return "-".join(parts)[:limit] or "object"


@dataclass
class _Track:
    id: str
    label: str
    box: BoundingBox
    misses: int = 0


class IoUTracker:
    """Keeps object identities across frames by matching boxes of the same label.

    Greedy highest-overlap matching. Identity holds for objects that move little between
    frames (a bucket, a rack, an instrument) and for hand-held ones at higher frame rates. At
    low rates a fast-moving tube may be given a new id. A track survives `max_misses`
    consecutive frames without a match, so a brief occlusion keeps its id.
    """

    def __init__(self, iou_threshold: float = 0.15, max_misses: int = 3) -> None:
        self.iou_threshold = iou_threshold
        self.max_misses = max_misses
        self._tracks: list[_Track] = []
        self._counts: dict[str, int] = defaultdict(int)

    def update(
        self, frame_index: int, timestamp_s: float, detections: Sequence[Detection]
    ) -> list[TrackedDetection]:
        pairs = sorted(
            (
                (track.box.iou(det.box), ti, di)
                for ti, track in enumerate(self._tracks)
                for di, det in enumerate(detections)
                if track.label == det.label
            ),
            reverse=True,
        )
        matched_tracks: set[int] = set()
        assigned: dict[int, _Track] = {}
        for overlap, ti, di in pairs:
            if overlap < self.iou_threshold:
                break
            if ti in matched_tracks or di in assigned:
                continue
            matched_tracks.add(ti)
            assigned[di] = self._tracks[ti]

        for ti, track in enumerate(self._tracks):
            track.misses = 0 if ti in matched_tracks else track.misses + 1
        self._tracks = [t for t in self._tracks if t.misses <= self.max_misses]

        results = []
        for di, det in enumerate(detections):
            track = assigned.get(di)
            if track is None:
                self._counts[det.label] += 1
                name = f"{slug(det.label)}#{self._counts[det.label]}"
                track = _Track(name, det.label, det.box)
                self._tracks.append(track)
            track.box = det.box
            results.append(
                TrackedDetection(
                    **det.model_dump(),
                    track_id=track.id,
                    frame_index=frame_index,
                    timestamp_s=timestamp_s,
                )
            )
        return results
