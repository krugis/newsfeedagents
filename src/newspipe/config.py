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
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-chat"
    batch_concurrency: int = Field(default=5, ge=1, le=64)
    label_limit_per_run: int = Field(default=100, ge=0)
    schedule_cron: str = "5 0,12 * * *"  # 00:05 and 12:05 UTC daily = every 12h

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
