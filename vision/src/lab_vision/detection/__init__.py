"""Object detection and tracking.

`DetectionProcessor` lives in `lab_vision.detection.processor` and is not re-exported here,
because it depends on `lab_vision.video`, which depends on the types in this package.
"""

from lab_vision.detection.base import ObjectDetector
from lab_vision.detection.tracker import IoUTracker
from lab_vision.detection.types import BoundingBox, Detection, TrackedDetection

__all__ = ["BoundingBox", "Detection", "IoUTracker", "ObjectDetector", "TrackedDetection"]
