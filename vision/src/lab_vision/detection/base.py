from collections.abc import Sequence
from typing import Protocol

import numpy as np

from lab_vision.detection.types import Detection


class ObjectDetector(Protocol):
    """Finds named objects in an image. Labels are free text, so no training is needed."""

    def detect(self, image: np.ndarray, labels: Sequence[str]) -> list[Detection]:
        """`image` is BGR, as OpenCV returns it. Boxes are normalised to the image."""
        ...
