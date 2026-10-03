import json
from pathlib import Path

import cv2
import numpy as np

from lab_vision.detection.tracker import slug
from lab_vision.models import ProtocolStep
from lab_vision.video import FrameWindow

_COLOURS = [(0, 200, 0), (0, 140, 255), (255, 120, 0), (200, 0, 200), (0, 200, 200), (60, 60, 255)]


class FrameDumper:
    """Writes what the model is shown for each window, so a run can be inspected by eye.

    Per window: the frames as sent, a copy of each with detections and track ids drawn on it,
    the close-up crops, and a `window.json` with the steps in focus and detector notes.
    """

    def __init__(self, directory: Path) -> None:
        self._root = directory / "windows"
        self._root.mkdir(parents=True, exist_ok=True)

    def on_window(self, window: FrameWindow, steps: tuple[ProtocolStep, ...]) -> None:
        folder = self._root / f"{window.span.start_s:07.1f}s"
        folder.mkdir(exist_ok=True)
        colours: dict[str, tuple[int, int, int]] = {}
        for frame in window.frames:
            (folder / f"frame_{frame.index:06d}.jpg").write_bytes(frame.jpeg)
            boxes = [d for d in window.detections if d.frame_index == frame.index]
            if boxes:
                canvas = cv2.imdecode(np.frombuffer(frame.jpeg, np.uint8), cv2.IMREAD_COLOR)
                _draw(canvas, boxes, colours)
                cv2.imwrite(str(folder / f"overlay_{frame.index:06d}.jpg"), canvas)
        for number, crop in enumerate(window.crops):
            (folder / f"crop_{number}_{slug(crop.track_id)}.jpg").write_bytes(crop.jpeg)
        info = {
            "span": window.span.model_dump(),
            "frames_sent": [f.index for f in window.frames],
            "steps_in_focus": [s.id for s in steps],
            "notes": list(window.notes),
            "crops": [
                {"label": c.label, "track": c.track_id, "t": c.timestamp_s, "score": c.score}
                for c in window.crops
            ],
            "detections": [d.model_dump() for d in window.detections],
        }
        (folder / "window.json").write_text(json.dumps(info, indent=2), encoding="utf-8")


def _draw(canvas: np.ndarray, boxes, colours: dict[str, tuple[int, int, int]]) -> None:
    height, width = canvas.shape[:2]
    for d in boxes:
        colour = colours.setdefault(d.label, _COLOURS[len(colours) % len(_COLOURS)])
        p0 = (int(d.box.x0 * width), int(d.box.y0 * height))
        p1 = (int(d.box.x1 * width), int(d.box.y1 * height))
        cv2.rectangle(canvas, p0, p1, colour, 2)
        cv2.putText(
            canvas,
            f"{d.track_id[:18]} {d.score:.2f}",
            (p0[0] + 3, p0[1] + 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            colour,
            2,
        )
