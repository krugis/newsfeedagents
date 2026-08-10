"""Pydantic domain models shared across the pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceMethod = Literal["rss", "hn_algolia", "google_news_rss"]


class RawItem(BaseModel):
    """A single raw item produced by a fetcher, before normalization."""

    external_id: str
    url: str
    title: str
    published_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    """A row from the ``sources`` registry."""

    source_id: int
    name: str
    method: SourceMethod
    config: dict[str, Any] = Field(default_factory=dict)
    poll_interval_minutes: int = 60
    last_polled_at: datetime | None = None
    enabled: bool = True
    created_at: datetime
