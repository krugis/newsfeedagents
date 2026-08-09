"""Application configuration, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DATABASE_URL = "postgresql://newspipe:newspipe@localhost:5432/newspipe"


class Settings(BaseSettings):
    """Env-driven application settings.

    All values may be overridden via environment variables or a local
    ``.env`` file (see ``.env.example``).
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = _DEFAULT_DATABASE_URL
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"
    batch_concurrency: int = Field(default=5, ge=1, le=64)

    @property
    def sqlalchemy_url(self) -> str:
        """Same DSN in SQLAlchemy psycopg3 dialect form."""
        url = self.database_url
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
