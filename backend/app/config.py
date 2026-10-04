from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = "http://localhost"
    postgres_url: str
    redis_url: str
    upload_dir: str = "/data/uploads"
    max_upload_bytes: int = 10 * 1024 * 1024
    max_experiment_upload_bytes: int = 100 * 1024 * 1024
    vision_model: str = "claude-opus-5-5"
    vision_effort: str | None = "medium"
    vision_fallbacks: bool = True
    vision_strategy: Literal["agent", "windows"] = "agent"
    claude_api_key: str | None = None
    claude_model: str = "claude-sonnet-5"
    max_extraction_context_chars: int = 12_000
    extraction_worker_poll_seconds: float = 1.0
    extraction_max_attempts: int = 3
    extraction_retry_base_seconds: int = 5
    extraction_stale_after_seconds: int = 300
    amass_api_key: str | None = None
    agent_model: str = "claude-sonnet-5-5"
    agent_effort: str | None = "medium"
    agent_judge_model: str = "claude-sonnet-5"
    agent_max_turns: int = 10
    agent_max_web_searches: int = 10
    agent_turn_max_tokens: int = 16000
    agent_user_agent: str = "LogicCritic-ResearchAgent/1.0 (evidence gathering for research review)"
    eval_generator_model: str = "claude-sonnet-5-5"
    eval_judge_model: str = "claude-opus-5-5"
    eval_max_turns: int = 6
    eval_guarded_max_turns: int = 12
    eval_max_web_searches: int = 0
    eval_max_cost_usd: float = 50.0
    amass_base_url: str = "https://api.amass.tech/api/v1"
    amass_timeout_seconds: float = 30.0
    amass_requests_per_minute: int = 60
    amass_cache_ttl_hours: int = 24
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
