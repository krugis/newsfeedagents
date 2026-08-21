"""Application configuration, driven entirely by environment variables / .env."""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

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

    # Labeler provider selection: which of the three credential sets below
    # (local/glm/deepseek) actually gets used. `labeler_fallback_provider`
    # kicks in only when the primary has no key configured or fails a quick
    # reachability probe (see labeling/labeler.py::resolve_labeler_provider)
    # — "none" disables fallback. Both selectable independently of each other
    # so any of the three can be primary, fallback, or unused.
    labeler_provider: Literal["local", "glm", "deepseek"] = "local"
    labeler_fallback_provider: Literal["local", "glm", "deepseek", "none"] = "glm"

    # DeepSeek (any OpenAI-compatible endpoint really — Groq, Together,
    # Fireworks, etc. work too). `deepseek-chat` (DeepSeek-V3) is a
    # cost-effective model well suited to one-sentence labeling.
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.deepseek.com"
    model_name: str = "deepseek-chat"

    # Local self-hosted OpenAI-compatible endpoint (e.g. a llama.cpp server).
    local_llm_api_key: str | None = None
    local_llm_base_url: str = "https://api.agate.tr/v1"
    local_model_name: str = "local"

    # GLM (Z.ai). Defaults to "thinking" (extended reasoning) mode disabled —
    # it's ~10x slower and adds no labeling-quality benefit for this task, see
    # labeling/labeler.py's provider config.
    glm_llm_api_key: str | None = None
    glm_llm_base_url: str = "https://api.z.ai/api/paas/v4"
    glm_model_name: str = "glm-4.7-flashx"

    batch_concurrency: int = 8
    # Cap on how many unlabeled stories a single `label` run will label, so a
    # backfill burst never blows the API budget in one shot.
    label_limit_per_run: int = 100

    # How often the labeling step actually runs, independent of the fetch
    # cadence. 0 = label on every pipeline tick (today's behavior).
    label_interval_minutes: int = 0

    # Which unlabeled stories a run picks, when not targeting specific
    # story_ids: "newest_per_source" round-robins across sources so fresh
    # news gets labeled promptly and no single chatty source can fill the
    # whole budget (needed for the front page to ever show today's news
    # instead of stalling behind an old backlog); "oldest_first" is the
    # original pure-FIFO behavior.
    label_order: Literal["newest_per_source", "oldest_first"] = "newest_per_source"

    # The category set and importance scale the labeling model is constrained
    # to via structured output (see labeling/schema.py).
    label_categories: tuple[str, ...] = DEFAULT_CATEGORIES
    importance_min: int = 1
    importance_max: int = 10

    # Scheduler cadence (APScheduler CronTrigger fields, e.g. "*/15", "3,33").
    # Defaults reproduce the original hourly-at-minute-5 schedule. This is the
    # incremental tick: every run fetches all due sources' current feed items
    # and inserts only genuinely new ones (dedup on external_id/title-hash),
    # so in effect it's "what's new since last check".
    scheduler_cron_minute: str = "5"
    scheduler_cron_hour: str = "*"

    # Once-a-day job that forces a full 24h catch-up window instead of the
    # incremental one (currently only changes behavior for the Hacker News
    # fetcher — see fetchers/hn_algolia.py — whose query window is normally
    # capped to "since last poll"). Acts as insurance against index/feed lag
    # or a missed hourly tick; other sources just get an extra dedup'd fetch.
    daily_backfill_cron_hour: str = "6"
    daily_backfill_cron_minute: str = "30"

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
    # Where the login form lives. Not linked from any page (base.html has no
    # "Admin Login" link) — set this to an unguessable path in .env so the
    # login form itself isn't discoverable by casually browsing the site.
    # `/admin`/`/news` still redirect here when unauthenticated (so a
    # bookmarked `/admin` still works for the operator), which does reveal
    # the path to anyone who tries those URLs directly — this is obscurity on
    # top of the password, not a substitute for it.
    admin_login_path: str = "/login"

    # Topic search (/topic web page): title search across all stories
    # (labeled or not) in the last N days. `topic_search_max_days` caps how
    # far back a request may reach.
    topic_search_default_days: int = 3
    topic_search_max_days: int = 7
    topic_search_limit: int = 30

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

    @model_validator(mode="after")
    def _validate_topic_search_days(self) -> Settings:
        if not (1 <= self.topic_search_default_days <= self.topic_search_max_days):
            raise ValueError(
                "topic_search_default_days must satisfy "
                "1 <= topic_search_default_days <= topic_search_max_days"
            )
        return self

    @field_validator("admin_login_path")
    @classmethod
    def _validate_admin_login_path(cls, value: str) -> str:
        if not value.startswith("/") or value.startswith("//"):
            raise ValueError("admin_login_path must be an absolute path, e.g. /login")
        reserved = {"/", "/topic", "/admin", "/news", "/logout"}
        if value in reserved or value.startswith("/admin/"):
            raise ValueError(f"admin_login_path must not collide with a reserved route: {value}")
        return value

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
