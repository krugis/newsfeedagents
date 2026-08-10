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
    # DeepSeek (OpenAI-compatible API). `deepseek-chat` = DeepSeek-V3, the
    # cost-effective model; `deepseek-reasoner` (R1) is pricier per token and
    # overkill for one-sentence labeling.
    deepseek_api_key: str | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-chat"
    batch_concurrency: int = 8
    # Cap on how many unlabeled stories a single `label` run will label, so a
    # backfill burst never blows the API budget in one shot.
    label_limit_per_run: int = 100


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton settings object."""
    return Settings()
