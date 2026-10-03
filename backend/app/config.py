from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    app_base_url: str = "http://localhost"
    postgres_url: str
    redis_url: str
    upload_dir: str = "/data/uploads"
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    amass_api_key: str | None = None
    jwt_secret: str

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
