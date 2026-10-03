from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = "http://localhost"
    postgres_url: str
    redis_url: str
    upload_dir: str = "/data/uploads"
    max_upload_bytes: int = 10 * 1024 * 1024
    claude_api_key: str | None = None
    claude_model: str = "claude-sonnet-4-5-20250929"
    max_extraction_context_chars: int = 12_000
    extraction_worker_poll_seconds: float = 1.0
    extraction_max_attempts: int = 3
    extraction_retry_base_seconds: int = 5
    extraction_stale_after_seconds: int = 300
    amass_api_key: str | None = None
    jwt_secret: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_async_database_url() -> str:
    url = get_settings().postgres_url
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise ValueError("POSTGRES_URL must use the postgresql scheme")
