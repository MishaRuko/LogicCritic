from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from lab_vision.llm import DEFAULT_MODEL


class Settings(BaseSettings):
    """Runtime configuration, read from the environment or a `.env` file."""

    # Shares CLAUDE_API_KEY with the backend. With no key set, the Anthropic SDK resolves
    # credentials itself (environment or login). The model is separate from the backend's
    # CLAUDE_MODEL: vision needs a model that reads images and accepts the effort parameter.
    claude_api_key: str | None = None
    vision_model: str = DEFAULT_MODEL
    vision_effort: str | None = "medium"  # empty to omit, for models without effort levels
    vision_fallbacks: bool = True

    detection_model: str = "google/owlv2-base-patch16-ensemble"
    detection_threshold: float = Field(default=0.15, ge=0, le=1)
    crop_min_score: float = Field(default=0.3, ge=0, le=1)

    sample_fps: float = Field(default=1.0, gt=0)
    window_seconds: float = Field(default=5.0, gt=0)
    max_frames_per_window: int = Field(default=5, ge=1)
    max_image_side: int = Field(default=1024, ge=64)
    lookahead_steps: int = Field(default=10, ge=1)
    min_confidence: float = Field(default=0.6, ge=0, le=1)

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
