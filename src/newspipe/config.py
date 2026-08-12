"""Application configuration, driven entirely by environment variables / .env."""

from __future__ import annotations

import secrets
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Kept here rather than in labeling/schema.py (which needs this default too)
# to avoid a circular import: importing newspipe.labeling.schema runs the
# labeling package's __init__, which imports labeler.py, which imports this
# module — so this module must not import anything from labeling.
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "model_release",
    "research",
    "industry",
    "funding",
    "policy_regulation",
    "tooling_infra",
    "other",
)


class Settings(BaseSettings):
    """Runtime settings. Every value can be overridden via environment or .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql://newspipe:newspipe@localhost:5433/newspipe"

    # LLM (any OpenAI-compatible endpoint — DeepSeek, OpenAI, Groq, Together,
    # Fireworks, etc.). `deepseek-chat` (DeepSeek-V3) is the default: a
    # cost-effective model well suited to one-sentence labeling.
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-chat"
    batch_concurrency: int = 8
    # Cap on how many unlabeled stories a single `label` run will label, so a
    # backfill burst never blows the API budget in one shot.
    label_limit_per_run: int = 100

    # How often the labeling step actually runs, independent of the fetch
    # cadence. 0 = label on every pipeline tick (today's behavior).
    label_interval_minutes: int = 0

    # The category set and importance scale the labeling model is constrained
    # to via structured output (see labeling/schema.py).
    label_categories: tuple[str, ...] = DEFAULT_CATEGORIES
    importance_min: int = 1
    importance_max: int = 10

    # Scheduler cadence (APScheduler CronTrigger fields, e.g. "*/15", "3,33").
    # Defaults reproduce the original hourly-at-minute-5 schedule.
    scheduler_cron_minute: str = "5"
    scheduler_cron_hour: str = "*"

    # How long to keep news data. Unset (None) = keep forever. When set, a
    # daily job hard-deletes stories (and their arrivals/labels) that have had
    # no new arrival in `retention_days` days.
    retention_days: int | None = None

    # Web UI (see web/ package). Bound to localhost by default — see the
    # README's "Web UI" section for what changing this implies.
    web_host: str = "127.0.0.1"
    web_port: int = 8010
    admin_username: str = "admin"
    # None = login is refused until this is set — no default-password footgun.
    admin_password: str | None = None
    # Signs the session cookie. Regenerated every process start if unset in
    # .env, which means sessions don't survive a restart — fine for an admin
    # tool; set it explicitly if that's undesirable.
    web_session_secret: str = Field(default_factory=lambda: secrets.token_hex(32))

    @field_validator("label_categories", mode="before")
    @classmethod
    def _parse_categories(cls, value: object) -> object:
        """Accept a comma-separated env string (`LABEL_CATEGORIES=a,b,c`)."""
        if isinstance(value, str):
            categories = tuple(c.strip() for c in value.split(",") if c.strip())
            if not categories:
                raise ValueError("label_categories must not be empty")
            return categories
        return value

    @model_validator(mode="after")
    def _validate_importance_range(self) -> Settings:
        if not (1 <= self.importance_min < self.importance_max <= 100):
            raise ValueError(
                "importance_min/importance_max must satisfy "
                "1 <= importance_min < importance_max <= 100"
            )
        return self

    @field_validator("retention_days")
    @classmethod
    def _validate_retention_days(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("retention_days must be a positive integer, or unset")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton settings object."""
    return Settings()
