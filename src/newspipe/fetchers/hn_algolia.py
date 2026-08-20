"""Hacker News fetcher via the Algolia API.

Two queries per fetch:
  - `search_by_date` for AI/LLM/GenAI keywords, windowed to since the source's
    last poll (capped at 24h) so only new items are returned;
  - the current front page, whose hits are flagged in `raw["hn_front_page"]`
    so dedup can set `stories.hn_front_page`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx

from newspipe.fetchers.base import RawItem, build_client, get_with_retry
from newspipe.models.schemas import Source

MAX_WINDOW = timedelta(hours=24)


def fetch(
    source_row: Source, client: httpx.Client | None = None, *, backfill: bool = False
) -> list[RawItem]:
    """Fetch HN items matching the configured keywords since the poll window.

    ``backfill=True`` (the once-daily job) forces the full 24h window instead
    of "since last poll" — insurance against Algolia indexing lag or a missed
    hourly tick.
    """
    client = client or build_client()
    base_url = source_row.config.get("base_url", "https://hn.algolia.com/api/v1/")
    keywords = source_row.config.get("keywords", ["AI", "LLM", "generative AI"])
    hits_per_page = source_row.config.get("hits_per_page", 100)

    since = datetime.now(UTC) - MAX_WINDOW if backfill else _window_start(source_row)
    front_page_ids = _fetch_front_page_ids(client, base_url)

    by_id: dict[str, RawItem] = {}
    for keyword in keywords:
        params = {
            "query": keyword,
            "tags": "story",
            "hitsPerPage": hits_per_page,
            "numericFilters": f"created_at_i>{int(since.timestamp())}",
        }
        resp = get_with_retry(client, f"{base_url}search_by_date", params=params)
        for hit in resp.json().get("hits", []):
            item = _hit_to_item(hit)
            if item is not None:
                by_id[item.external_id] = item

    for item in by_id.values():
        if item.external_id in front_page_ids:
            item.raw["hn_front_page"] = True

    return list(by_id.values())


def _window_start(source_row: Source) -> datetime:
    """Poll window: since last_polled_at, never older than 24h."""
    now = datetime.now(UTC)
    last = source_row.last_polled_at
    if last is None:
        return now - MAX_WINDOW
    return max(last, now - MAX_WINDOW)


def _fetch_front_page_ids(client: httpx.Client, base_url: str) -> set[str]:
    resp = get_with_retry(
        client, f"{base_url}search", params={"tags": "front_page", "hitsPerPage": 100}
    )
    return {str(hit["objectID"]) for hit in resp.json().get("hits", [])}


def _hit_to_item(hit: dict) -> RawItem | None:
    object_id = str(hit.get("objectID", "")).strip()
    title = str(hit.get("story_title") or hit.get("title") or "").strip()
    if not object_id or not title:
        return None
    # Algolia's story-type hits (tags=story, what we always query) carry the
    # external link as "url" — "story_url" is a comment-hit field (pointing
    # at its parent story) that never appears here, so that key was always
    # silently missing and every link post fell back to the HN discussion
    # page instead of its real article URL.
    url = str(hit.get("url") or "").strip() or f"https://news.ycombinator.com/item?id={object_id}"
    published_at = None
    created_at_i = hit.get("created_at_i")
    if created_at_i:
        published_at = datetime.fromtimestamp(int(created_at_i), tz=UTC)
    raw = {
        "hn_object_id": object_id,
        "author": hit.get("author"),
        "points": hit.get("points"),
        "num_comments": hit.get("num_comments"),
    }
    return RawItem(
        external_id=object_id,
        url=url,
        title=title,
        published_at=published_at,
        raw=raw,
    )
