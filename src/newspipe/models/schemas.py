"""Pydantic domain models mirroring the database schema.

These are the plain, framework-free models used at the boundaries of the
pipeline (fetchers -> persistence -> dedup -> labeling). Framework adapters
live at the edges and never leak into these.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Method = Literal["rss", "hn_algolia", "google_news_rss", "sitemap"]
# The category set is configurable (Settings.label_categories) and enforced
# as an enum at the LLM structured-output boundary (labeling/schema.py), not
# here — the DB column is plain TEXT, so a static Literal would only ever be
# a stale compile-time hint.
Category = str


class Source(BaseModel):
    """A row from the `sources` registry."""

    source_id: int
    name: str
    method: Method
    config: dict[str, Any] = Field(default_factory=dict)
    poll_interval_minutes: int = 60
    last_polled_at: datetime | None = None
    enabled: bool = True
    created_at: datetime


class Arrival(BaseModel):
    """A row from the append-only `arrivals` table."""

    arrival_id: int
    source_id: int
    external_id: str
    url: str
    url_canonical: str | None = None
    title: str
    published_at: datetime | None = None
    fetched_at: datetime
    raw: dict[str, Any] = Field(default_factory=dict)
    story_id: int | None = None


class Story(BaseModel):
    """A canonical deduplicated story from the `stories` table."""

    story_id: int
    canonical_url: str | None = None
    title: str
    title_hash: str
    first_seen_at: datetime
    last_seen_at: datetime
    arrival_count: int = 1
    hn_front_page: bool = False


class Label(BaseModel):
    """One labeling of a story (the `labels` table)."""

    label_id: int
    story_id: int
    is_hot: bool
    # The exact configured scale (Settings.importance_min/max) is enforced at
    # the LLM structured-output boundary; this mirrors the DB's widened CHECK
    # as a generous ceiling for whatever range is configured.
    importance: int = Field(ge=1, le=100)
    category: Category
    is_genai_ml_relevant: bool = True
    rationale: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    labeled_at: datetime


class PipelineRun(BaseModel):
    """One pipeline execution (the `pipeline_runs` table)."""

    run_id: int
    thread_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    stats: dict[str, Any] = Field(default_factory=dict)
