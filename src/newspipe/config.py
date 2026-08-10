"""Application configuration, driven entirely by environment variables / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings. Every value can be overridden via environment or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://newspipe:newspipe@localhost:5433/newspipe"
    anthropic_api_key: str | None = None
    model_name: str = "claude-sonnet-4-6"
    batch_concurrency: int = 8


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton settings object."""
    return Settings()
