import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from lab_vision.models import Deviation, Observation

PANEL_W = 560
TIMELINE_H = 64
FONT = cv2.FONT_HERSHEY_SIMPLEX

BG = (28, 28, 30)
FG = (230, 230, 230)
DIM = (140, 140, 145)
GREEN = (90, 200, 90)
AMBER = (40, 170, 255)
RED = (70, 70, 235)
BLUE = (255, 160, 70)

STATUS_COLOUR = {
    "performed": GREEN,
    "in_progress": AMBER,
    "unclear": DIM,
    "not_observed": DIM,
}

_ASCII = str.maketrans(
    {"μ": "u", "°": " deg", "–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"', "×": "x"}
)


def ascii_text(text: str) -> str:
    """OpenCV's fonts are ASCII only, so swap common lab symbols for readable stand-ins."""
    return text.translate(_ASCII).encode("ascii", "replace").decode("ascii")


def wrap(text: str, width: int, max_lines: int) -> list[str]:
    lines = textwrap.wrap(ascii_text(text), width) or [""]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][: max(0, width - 3)].rstrip() + "..."
    return lines


@dataclass
class WindowRecord:
    start_s: float
    end_s: float
    frames_sent: list[int] = field(default_factory=list)
    steps_in_focus: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    crops: list[dict] = field(default_factory=list)
    crop_files: list[Path] = field(default_factory=list)
    detections: list[dict] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)


@dataclass
class RunView:
    """Everything a run wrote that the viewer needs. Missing pieces just show less."""

    windows: list[WindowRecord]
    deviations: list[Deviation]
    duration_s: float

    @classmethod
    def load(cls, directory: Path) -> "RunView":
        observations = _read_jsonl(directory / "observations.jsonl", Observation)
        deviations = _read_jsonl(directory / "deviations.jsonl", Deviation)

        windows: dict[float, WindowRecord] = {}
        for path in sorted((directory / "windows").glob("*/window.json")):
            info = json.loads(path.read_text(encoding="utf-8"))
            record = WindowRecord(
                start_s=info["span"]["start_s"],
                end_s=info["span"]["end_s"],
                frames_sent=info["frames_sent"],
                steps_in_focus=info["steps_in_focus"],
                notes=info["notes"],
                crops=info["crops"],
                crop_files=[
                    next(iter(sorted(path.parent.glob(f"crop_{n}_*.jpg"))), None)
                    for n in range(len(info["crops"]))
                ],
                detections=info["detections"],
            )
            windows[record.start_s] = record

        for obs in observations:
            record = windows.setdefault(
                obs.span.start_s, WindowRecord(obs.span.start_s, obs.span.end_s)
            )
            record.end_s = max(record.end_s, obs.span.end_s)
            record.observations.append(obs)
            if not record.frames_sent:
                record.frames_sent = obs.frame_indices

        ordered = [windows[k] for k in sorted(windows)]
        duration = max(
            [w.end_s for w in ordered] + [d.span.end_s for d in deviations if d.span] + [1.0]
        )
        return cls(ordered, deviations, duration)

    def window_at(self, t: float) -> WindowRecord | None:
        for window in self.windows:
            if window.start_s <= t < window.start_s + _span(window):
                return window
        return None

    def window_index(self, t: float) -> int:
        current = self.window_at(t)
        return self.windows.index(current) if current in self.windows else 0


def _span(window: WindowRecord) -> float:
    """Windows are contiguous, so a window runs until the next one starts."""
    return max(window.end_s - window.start_s, 0.0) + 1.0


def _read_jsonl(path: Path, model):
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [model.model_validate_json(line) for line in lines if line.strip()]


class Composer:
    """Draws one frame of the viewer: the footage, what the model was given, and its answer."""

    def __init__(self, video: Path, run: RunView, fps: float, size: tuple[int, int]) -> None:
        self.run = run
        self._capture_path = video
        self.fps = fps
        self.video_w, self.video_h = size
        self._capture = cv2.VideoCapture(str(video))
        self._thumb_cache: dict[tuple[float, int], np.ndarray] = {}
        self._crop_cache: dict[Path, np.ndarray] = {}

    @classmethod
    def open(cls, video: Path, run: RunView, max_size: tuple[int, int] = (960, 720)) -> "Composer":
        capture = cv2.VideoCapture(str(video))
        if not capture.isOpened():
            raise FileNotFoundError(f"cannot open video: {video}")
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        capture.release()
        scale = min(max_size[0] / width, max_size[1] / height)
        return cls(video, run, fps, (int(width * scale) // 2 * 2, int(height * scale) // 2 * 2))

    @property
    def canvas_size(self) -> tuple[int, int]:
        return self.video_w + PANEL_W, self.video_h + TIMELINE_H

    def close(self) -> None:
        self._capture.release()

    def frame_at(self, index: int) -> np.ndarray | None:
        self._capture.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, image = self._capture.read()
        return image if ok else None

    def compose(self, image: np.ndarray, frame_index: int) -> np.ndarray:
        t = frame_index / self.fps
        canvas = np.full((self.video_h + TIMELINE_H, self.video_w + PANEL_W, 3), BG, np.uint8)
        window = self.run.window_at(t)

        video = cv2.resize(image, (self.video_w, self.video_h), interpolation=cv2.INTER_AREA)
        self._draw_boxes(video, window, t)
        sent = self._is_sent(window, frame_index)
        if sent:
            cv2.rectangle(video, (0, 0), (self.video_w - 1, self.video_h - 1), GREEN, 6)
        _label(video, "SENT TO MODEL" if sent else "not sent", (14, 30), GREEN if sent else DIM)
        canvas[: self.video_h, : self.video_w] = video

        self._draw_panel(canvas, window, t, frame_index)
        self._draw_timeline(canvas, t)
        return canvas

    # -- video overlay

    def _is_sent(self, window: WindowRecord | None, frame_index: int) -> bool:
        if window is None:
            return False
        tolerance = max(1, round(self.fps / 4))
        return any(abs(frame_index - sent) <= tolerance for sent in window.frames_sent)

    def _draw_boxes(self, video: np.ndarray, window: WindowRecord | None, t: float) -> None:
        if window is None or not window.detections:
            return
        nearest = min(
            {d["frame_index"] for d in window.detections}, key=lambda i: abs(i / self.fps - t)
        )
        if abs(nearest / self.fps - t) > 0.75:
            return  # detections only describe their own frame; don't draw stale boxes
        labels: dict[str, tuple[int, int, int]] = {}
        palette = [GREEN, AMBER, BLUE, (200, 90, 200), (200, 200, 60), RED]
        for d in window.detections:
            if d["frame_index"] != nearest:
                continue
            colour = labels.setdefault(d["label"], palette[len(labels) % len(palette)])
            box = d["box"]
            p0 = (int(box["x0"] * self.video_w), int(box["y0"] * self.video_h))
            p1 = (int(box["x1"] * self.video_w), int(box["y1"] * self.video_h))
            cv2.rectangle(video, p0, p1, colour, 2)
            _label(video, f"{d['track_id'][:20]} {d['score']:.2f}", (p0[0] + 3, p0[1] + 15), colour)

    # -- right-hand panel

    def _draw_panel(self, canvas, window: WindowRecord | None, t: float, frame_index: int) -> None:
        x0 = self.video_w + 14
        width = PANEL_W - 28
        y = 24
        if window is None:
            _text(canvas, f"t = {t:5.1f}s   (no model window here)", (x0, y), DIM, 0.55)
            return
        _text(
            canvas,
            f"t = {t:5.1f}s    window {window.start_s:.0f}-{window.end_s:.0f}s",
            (x0, y),
            FG,
            0.55,
        )
        y += 26

        y = self._draw_inputs(canvas, window, x0, y, width, frame_index)
        y = self._draw_notes(canvas, window, x0, y, width)
        y = self._draw_answer(canvas, window, x0, y, width)
        self._draw_deviations(canvas, window, x0, y, width)

    def _draw_inputs(self, canvas, window, x0, y, width, frame_index) -> int:
        _text(canvas, "MODEL INPUT: frames sent", (x0, y), BLUE, 0.5)
        y += 8
        sent = window.frames_sent[:6]
        if sent:
            thumb_w = (width - 6 * (len(sent) - 1)) // len(sent)
            thumb_h = int(thumb_w * self.video_h / self.video_w)
            for n, index in enumerate(sent):
                thumb = self._thumb(index, thumb_w, thumb_h)
                left = x0 + n * (thumb_w + 6)
                if thumb is not None:
                    canvas[y : y + thumb_h, left : left + thumb_w] = thumb
                if abs(index - frame_index) <= max(1, round(self.fps / 4)):
                    cv2.rectangle(canvas, (left, y), (left + thumb_w, y + thumb_h), GREEN, 2)
                _text(canvas, f"{index / self.fps:.0f}s", (left + 3, y + thumb_h + 14), DIM, 0.4)
            y += thumb_h + 24
        else:
            _text(canvas, "(no frames recorded)", (x0, y + 14), DIM, 0.45)
            y += 26

        if window.crops:
            _text(canvas, "close-ups sent", (x0, y + 6), BLUE, 0.5)
            y += 14
            height = 96
            left = x0
            for meta, path in zip(window.crops, window.crop_files, strict=True):
                crop = self._crop(path, height)
                if crop is None:
                    continue
                if left + crop.shape[1] > x0 + width:
                    break
                canvas[y : y + height, left : left + crop.shape[1]] = crop
                _text(canvas, f"{meta['score']:.2f}", (left + 3, y + 13), AMBER, 0.45)
                left += crop.shape[1] + 6
            y += height + 18
        return y

    def _draw_notes(self, canvas, window, x0, y, width) -> int:
        if not window.notes:
            return y
        _text(canvas, "DETECTOR NOTES (automatic)", (x0, y), BLUE, 0.5)
        y += 17
        for note in window.notes[:5]:
            for line in wrap(note, 66, 1):
                _text(canvas, line, (x0, y), DIM, 0.4)
                y += 15
        return y + 6

    def _draw_answer(self, canvas, window, x0, y, width) -> int:
        _text(canvas, "MODEL ANSWER", (x0, y), BLUE, 0.5)
        y += 18
        focus = f"in focus: {', '.join(window.steps_in_focus[:6])}" if window.steps_in_focus else ""
        if focus:
            _text(canvas, ascii_text(focus), (x0, y), DIM, 0.4)
            y += 16
        if not window.observations:
            _text(canvas, "(no observations)", (x0, y), DIM, 0.45)
            return y + 22
        for obs in window.observations:
            if y > self.video_h - 90:
                _text(canvas, "...", (x0, y), DIM, 0.45)
                return y + 16
            status = obs.status.value if obs.step_id else "EVENT"
            colour = STATUS_COLOUR.get(status, AMBER)
            head = f"{obs.step_id or 'event'}  {status}  {obs.confidence:.2f}"
            readings = ", ".join(
                f"{v.check_id}={v.value if v.value is not None else '?'} ({v.confidence:.1f})"
                for v in obs.values
            )
            _text(canvas, head + (f"   {readings}" if readings else ""), (x0, y), colour, 0.45)
            y += 15
            for line in wrap(obs.description or "", 70, 2):
                if line:
                    _text(canvas, line, (x0 + 8, y), DIM, 0.4)
                    y += 14
            y += 3
        return y + 4

    def _draw_deviations(self, canvas, window, x0, y, width) -> None:
        here = [
            d
            for d in self.run.deviations
            if d.span is None
            or (d.span.start_s <= window.end_s + 1 and d.span.end_s >= window.start_s)
        ]
        if not here or y > self.video_h - 40:
            return
        _text(canvas, "DEVIATIONS", (x0, y), RED, 0.5)
        y += 17
        for d in here:
            if y > self.video_h - 20:
                break
            colour = AMBER if d.needs_review else RED
            flag = " [review]" if d.needs_review else ""
            for n, line in enumerate(wrap(f"{d.kind.value}{flag}: {d.message}", 66, 2)):
                _text(canvas, line, (x0 + (0 if n == 0 else 8), y), colour, 0.4)
                y += 14

    # -- timeline

    def _draw_timeline(self, canvas, t: float) -> None:
        top = self.video_h
        total_w = self.video_w + PANEL_W
        left, right = 14, total_w - 14
        bar_y = top + 24
        scale = (right - left) / self.run.duration_s
        cv2.rectangle(canvas, (left, bar_y), (right, bar_y + 14), (55, 55, 58), -1)
        for n, window in enumerate(self.run.windows):
            x0 = left + int(window.start_s * scale)
            x1 = left + int(min(window.start_s + _span(window) - 1, self.run.duration_s) * scale)
            shade = (78, 78, 82) if n % 2 else (66, 66, 70)
            cv2.rectangle(canvas, (x0, bar_y), (x1, bar_y + 14), shade, -1)
        for d in self.run.deviations:
            if d.span is None:
                continue
            x = left + int((d.span.start_s + d.span.end_s) / 2 * scale)
            colour = AMBER if d.needs_review else RED
            cv2.line(canvas, (x, bar_y - 6), (x, bar_y + 20), colour, 3)
        step = 10 if self.run.duration_s <= 180 else 30
        for second in range(0, int(self.run.duration_s) + 1, step):
            x = left + int(second * scale)
            _text(canvas, f"{second}s", (x - 8, bar_y + 36), DIM, 0.38)
        cursor = left + int(min(t, self.run.duration_s) * scale)
        cv2.line(canvas, (cursor, bar_y - 10), (cursor, bar_y + 24), FG, 2)
        _text(canvas, "red: deviation   amber: needs review", (left, top + 14), DIM, 0.4)

    # -- thumbnails

    def _thumb(self, index: int, width: int, height: int) -> np.ndarray | None:
        key = (self.fps, index)
        if key not in self._thumb_cache:
            frame = self.frame_at(index)
            if frame is None:
                return None
            self._thumb_cache[key] = cv2.resize(
                frame, (width, height), interpolation=cv2.INTER_AREA
            )
        cached = self._thumb_cache[key]
        if cached.shape[1] != width:
            cached = cv2.resize(cached, (width, height), interpolation=cv2.INTER_AREA)
        return cached

    def _crop(self, path: Path | None, height: int) -> np.ndarray | None:
        if path is None:
            return None
        if path not in self._crop_cache:
            image = cv2.imread(str(path))
            if image is None:
                return None
            scale = height / image.shape[0]
            self._crop_cache[path] = cv2.resize(
                image, (max(1, int(image.shape[1] * scale)), height), interpolation=cv2.INTER_AREA
            )
        return self._crop_cache[path]


def _text(canvas, text: str, origin: tuple[int, int], colour, scale: float) -> None:
    cv2.putText(canvas, ascii_text(text), origin, FONT, scale, colour, 1, cv2.LINE_AA)


def _label(image, text: str, origin: tuple[int, int], colour) -> None:
    text = ascii_text(text)
    (w, h), _ = cv2.getTextSize(text, FONT, 0.5, 2)
    x, y = origin
    x = max(3, min(x, image.shape[1] - w - 6))  # keep the label inside the picture
    cv2.rectangle(image, (x - 3, y - h - 4), (x + w + 3, y + 5), (0, 0, 0), -1)
    cv2.putText(image, text, origin, FONT, 0.5, colour, 2, cv2.LINE_AA)


# -- rendering and playback


def render(composer: Composer, output: Path, start_s: float = 0.0, end_s: float | None = None):
    """Write the viewer as a video. Returns the number of frames written."""
    width, height = composer.canvas_size
    writer = None
    for code in ("avc1", "mp4v"):
        writer = cv2.VideoWriter(
            str(output), cv2.VideoWriter_fourcc(*code), composer.fps, (width, height)
        )
        if writer.isOpened():
            break
    if writer is None or not writer.isOpened():
        raise RuntimeError(f"cannot write video: {output}")

    capture = cv2.VideoCapture(str(composer._capture_path))
    capture.set(cv2.CAP_PROP_POS_FRAMES, int(start_s * composer.fps))
    index = int(start_s * composer.fps)
    written = 0
    try:
        while end_s is None or index / composer.fps <= end_s:
            ok, image = capture.read()
            if not ok:
                break
            writer.write(composer.compose(image, index))
            index += 1
            written += 1
    finally:
        capture.release()
        writer.release()
    return written


@dataclass
class PlayerState:
    """Playback position and keys, kept separate from the window so it can be tested."""

    fps: float
    total_frames: int
    run: RunView
    index: int = 0
    paused: bool = False
    quit: bool = False
    save_still: bool = False

    def handle(self, key: int) -> None:
        if key in (ord("q"), 27):
            self.quit = True
        elif key == ord(" "):
            self.paused = not self.paused
        elif key == ord("d"):
            self.seek(self.index + round(self.fps))
        elif key == ord("a"):
            self.seek(self.index - round(self.fps))
        elif key == ord("s"):
            self.jump_window(+1)
        elif key == ord("w"):
            self.jump_window(-1)
        elif key == ord("."):
            self.paused = True
            self.seek(self.index + 1)
        elif key == ord(","):
            self.paused = True
            self.seek(self.index - 1)
        elif key == ord("p"):
            self.save_still = True

    def seek(self, index: int) -> None:
        self.index = max(0, min(self.total_frames - 1, index))

    def jump_window(self, step: int) -> None:
        t = self.index / self.fps
        current = self.run.window_index(t)
        target = max(0, min(len(self.run.windows) - 1, current + step))
        if self.run.windows:
            self.seek(round(self.run.windows[target].start_s * self.fps))


def play(composer: Composer, still_dir: Path | None = None) -> None:
    """Interactive viewer. Needs an OpenCV build with GUI support (not the headless wheel)."""
    total = int(composer._capture.get(cv2.CAP_PROP_FRAME_COUNT))
    state = PlayerState(composer.fps, total, composer.run)
    title = "lab-vision: space pause, a/d -1s/+1s, w/s window, ,/. frame, p save still, q quit"
    delay = max(1, int(1000 / composer.fps))
    try:
        cv2.namedWindow(title, cv2.WINDOW_AUTOSIZE)
    except cv2.error as exc:
        raise RuntimeError(
            "This OpenCV build has no GUI support. Install `opencv-python` instead of the "
            "headless wheel, or use `--out` to render a video."
        ) from exc
    last = -1
    image = None
    while not state.quit:
        if state.index != last:
            image = composer.frame_at(state.index)
            last = state.index
        if image is None:
            break
        canvas = composer.compose(image, state.index)
        cv2.imshow(title, canvas)
        if state.save_still:
            state.save_still = False
            target = (still_dir or Path(".")) / f"view_{state.index / composer.fps:07.2f}s.png"
            cv2.imwrite(str(target), canvas)
            print(f"saved {target}")
        state.handle(cv2.waitKey(delay if not state.paused else 50) & 0xFF)
        if not state.paused:
            if state.index >= total - 1:
                state.paused = True
            else:
                state.seek(state.index + 1)
    cv2.destroyAllWindows()
