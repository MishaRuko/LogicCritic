from pydantic import BaseModel, Field, model_validator


class BoundingBox(BaseModel):
    """A box in normalised image coordinates: 0 to 1, origin at the top left."""

    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "BoundingBox":
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError("box corners are out of order")
        return self

    @property
    def area(self) -> float:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def iou(self, other: "BoundingBox") -> float:
        width = min(self.x1, other.x1) - max(self.x0, other.x0)
        height = min(self.y1, other.y1) - max(self.y0, other.y0)
        if width <= 0 or height <= 0:
            return 0.0
        inter = width * height
        return inter / (self.area + other.area - inter)

    def expanded(self, fraction: float) -> "BoundingBox":
        """Grow each side by `fraction` of the box size, clamped to the image."""
        pad_x, pad_y = (self.x1 - self.x0) * fraction, (self.y1 - self.y0) * fraction
        return BoundingBox(
            x0=max(0.0, self.x0 - pad_x),
            y0=max(0.0, self.y0 - pad_y),
            x1=min(1.0, self.x1 + pad_x),
            y1=min(1.0, self.y1 + pad_y),
        )


class Detection(BaseModel):
    label: str
    box: BoundingBox
    score: float = Field(ge=0, le=1)


class TrackedDetection(Detection):
    """A detection that belongs to a track. `track_id` is stable across frames and windows."""

    track_id: str
    frame_index: int
    timestamp_s: float
