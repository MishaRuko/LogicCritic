from collections.abc import Sequence

import numpy as np

from lab_vision.detection.types import BoundingBox, Detection

DEFAULT_MODEL = "google/owlv2-base-patch16-ensemble"


class DetectorUnavailable(RuntimeError):
    """The detection extras are not installed."""


class Owlv2Detector:
    """Open-vocabulary detection with OWLv2 (Hugging Face transformers).

    Needs `pip install -e ".[detection]"`. The model downloads on first use. Runs on Apple
    silicon (MPS) or CUDA when available, else CPU.
    """

    def __init__(
        self,
        model_name: str = DEFAULT_MODEL,
        threshold: float = 0.15,
        per_label: int = 1,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        self.threshold = threshold
        self.per_label = per_label
        self._device = device
        self._model = None
        self._processor = None

    def load(self) -> None:
        """Load the model now rather than on first use, so a problem surfaces early."""
        if self._model is not None:
            return
        try:
            import torch
            from transformers import Owlv2ForObjectDetection, Owlv2Processor
        except ImportError as exc:
            raise DetectorUnavailable(
                'Install the detection extras: pip install -e ".[detection]"'
            ) from exc
        if self._device is None:
            if torch.cuda.is_available():
                self._device = "cuda"
            elif torch.backends.mps.is_available():
                self._device = "mps"
            else:
                self._device = "cpu"
        self._processor = Owlv2Processor.from_pretrained(self.model_name)
        self._model = Owlv2ForObjectDetection.from_pretrained(self.model_name).to(self._device)
        self._model.eval()

    def detect(self, image: np.ndarray, labels: Sequence[str]) -> list[Detection]:
        import cv2
        import torch
        from PIL import Image

        if not labels:
            return []
        self.load()
        height, width = image.shape[:2]
        pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        queries = [f"a photo of a {label}" for label in labels]
        # The text encoder takes 16 tokens: longer queries are truncated, shorter ones padded.
        inputs = self._processor(
            text=[queries],
            images=pil,
            return_tensors="pt",
            padding="max_length",
            truncation=True,
            max_length=16,
        ).to(self._device)
        with torch.no_grad():
            outputs = self._model(**inputs)
        # OWLv2 pads the image to a square, so boxes come back in that square's pixels.
        side = max(height, width)
        results = self._processor.post_process_object_detection(
            outputs, threshold=self.threshold, target_sizes=torch.tensor([[side, side]])
        )[0]

        found: dict[str, list[Detection]] = {label: [] for label in labels}
        for score, label_index, box in zip(
            results["scores"], results["labels"], results["boxes"], strict=True
        ):
            x0, y0, x1, y1 = (float(v) for v in box)
            normalised = BoundingBox(
                x0=_unit(x0 / width),
                y0=_unit(y0 / height),
                x1=_unit(x1 / width),
                y1=_unit(y1 / height),
            )
            if normalised.area <= 0:
                continue
            label = labels[int(label_index)]
            found[label].append(Detection(label=label, box=normalised, score=float(score)))

        detections = []
        for items in found.values():
            items.sort(key=lambda d: d.score, reverse=True)
            detections.extend(items[: self.per_label])
        return detections


def _unit(value: float) -> float:
    return min(1.0, max(0.0, value))
