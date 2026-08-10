"""Hacker News fetcher via the Algolia HN API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from newspipe.fetchers.base import BaseFetcher
from newspipe.models.schemas import RawItem, Source, SourceMethod

DEFAULT_API_BASE = "https://hn.algolia.com/api/v1/"
DEFAULT_KEYWORDS = ["AI", "LLM", "GenAI", "artificial intelligence"]
MAX_BACKFILL = timedelta(hours=24)
HITS_PER_PAGE = 50


class HNAlgoliaFetcher(BaseFetcher):
    """Keyword search via ``search_by_date`` plus front-page cross-reference.

    The search window runs from the source's ``last_polled_at`` (capped at
    24 hours) to now, so first runs backfill at most one day. Front-page hits
    among the search results are flagged with ``raw["hn_front_page"] = True``
    so dedup can set ``stories.hn_front_page``.
    """

    method: SourceMethod = "hn_algolia"

    def api_base(self, source: Source) -> str:
        return str(source.config.get("api_base") or DEFAULT_API_BASE).rstrip("/")

    def fetch(self, source: Source) -> list[RawItem]:
        base = self.api_base(source)
        since = self.window_start(source)
        keywords = source.config.get("keywords") or DEFAULT_KEYWORDS

        items: list[RawItem] = []
        for keyword in keywords:
            params = {
                "query": keyword,
                "tags": "story",
                "numericFilters": f"created_at_i>{int(since.timestamp())}",
                "hitsPerPage": str(HITS_PER_PAGE),
            }
            payload = self._get(f"{base}/search_by_date", params=params).json()
            for hit in payload.get("hits", []):
                items.append(self._item_from_hit(hit, keyword=keyword))

        if source.config.get("front_page"):
            payload = self._get(
                f"{base}/search", params={"tags": "front_page", "hitsPerPage": str(HITS_PER_PAGE)}
            ).json()
            front_page_ids = {hit.get("objectID") for hit in payload.get("hits", [])}
            for item in items:
                if item.external_id in front_page_ids:
                    item.raw["hn_front_page"] = True

        return items

    def window_start(self, source: Source) -> datetime:
        """Earliest timestamp to query: last poll time, capped at 24h back."""
        now = datetime.now(UTC)
        if source.last_polled_at is None:
            return now - MAX_BACKFILL
        return max(source.last_polled_at, now - MAX_BACKFILL)

    def _item_from_hit(self, hit: dict[str, object], keyword: str) -> RawItem:
        object_id = str(hit.get("objectID") or "")
        url = str(hit.get("url") or f"https://news.ycombinator.com/item?id={object_id}")
        title = str(hit.get("title") or hit.get("story_title") or "")
        created = hit.get("created_at_i")
        published_at = datetime.fromtimestamp(int(created), tz=UTC) if created else None
        return RawItem(
            external_id=object_id,
            url=url,
            title=title,
            published_at=published_at,
            raw={"keyword": keyword, "hn_item": hit},
        )
