"""RSS fetcher based on feedparser."""

from __future__ import annotations

from datetime import UTC, datetime

import feedparser

from newspipe.fetchers.base import BaseFetcher, link_hash
from newspipe.models.schemas import RawItem, Source, SourceMethod


def _parsed_to_dt(struct_time) -> datetime | None:
    if struct_time is None:
        return None
    return datetime(*struct_time[:6], tzinfo=UTC)


class RSSFetcher(BaseFetcher):
    """Fetch a single RSS/Atom feed from ``source.config["feed_url"]``."""

    method: SourceMethod = "rss"

    def feed_url(self, source: Source) -> str:
        url = source.config.get("feed_url")
        if not url:
            raise ValueError(f"source {source.name!r} has no config.feed_url")
        return str(url)

    def fetch(self, source: Source) -> list[RawItem]:
        feed = feedparser.parse(self._get(self.feed_url(source)).content)
        items: list[RawItem] = []
        for entry in feed.entries:
            link = entry.get("link") or ""
            external_id = entry.get("id") or (link_hash(link) if link else "")
            if not external_id:
                continue
            published = entry.get("published_parsed") or entry.get("updated_parsed")
            items.append(
                RawItem(
                    external_id=external_id,
                    url=link,
                    title=entry.get("title") or "",
                    published_at=_parsed_to_dt(published),
                    raw={
                        "feed_title": feed.feed.get("title", ""),
                        "entry": dict(entry),
                    },
                )
            )
        return items
