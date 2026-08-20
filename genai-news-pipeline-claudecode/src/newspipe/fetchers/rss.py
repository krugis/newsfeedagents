"""RSS/Atom fetcher built on feedparser."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import feedparser
import httpx

from newspipe.fetchers.base import RawItem, build_client, get_with_retry
from newspipe.models.schemas import Source


def fetch(
    source_row: Source, client: httpx.Client | None = None, *, backfill: bool = False
) -> list[RawItem]:
    """Fetch and parse the feed configured in ``source_row.config["feed_url"]``.

    ``backfill`` is accepted for interface parity with the other fetchers but
    unused here: a plain feed poll always returns whatever's currently
    published, with no time-window query to widen.
    """
    client = client or build_client()
    feed_url = source_row.config["feed_url"]
    resp = get_with_retry(client, feed_url)
    return parse_feed(resp.content)


def parse_feed(xml_bytes: bytes) -> list[RawItem]:
    """Parse feed XML bytes into RawItems (reused by the Google News fetcher)."""
    feed = feedparser.parse(xml_bytes)
    items: list[RawItem] = []
    for entry in feed.entries:
        url = str(entry.get("link", "")).strip() if entry.get("link") else ""
        title = str(entry.get("title", "")).strip() if entry.get("title") else ""
        if not url and not title:
            continue
        items.append(
            RawItem(
                external_id=external_id_for(entry),
                url=url,
                title=title,
                published_at=published_at_for(entry),
                raw=entry_to_raw(entry),
            )
        )
    return items


def entry_to_raw(entry: Any) -> dict[str, Any]:
    """Extract a JSON-safe subset of a feedparser entry for the `arrivals.raw` column."""
    raw: dict[str, Any] = {}
    for field in ("id", "guid", "link", "title", "summary", "published", "updated"):
        if entry.get(field) is not None:
            raw[field] = str(entry[field])
    author = entry.get("author_detail") or entry.get("author")
    if isinstance(author, dict) and author.get("name"):
        raw["author"] = str(author["name"])
    elif author:
        raw["author"] = str(author)
    tags = [t.get("term") for t in (entry.get("tags") or []) if t.get("term")]
    if tags:
        raw["tags"] = tags
    return raw


def published_at_for(entry: Any) -> datetime | None:
    """Return the entry's published (or updated) timestamp, if any."""
    for key in ("published_parsed", "updated_parsed"):
        parsed = entry.get(key)
        if parsed:
            return datetime(*parsed[:6], tzinfo=UTC)
    return None


def external_id_for(entry: Any) -> str:
    """Stable ID: the entry id/guid when present, else a hash of link (then title)."""
    for key in ("id", "guid"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    link = entry.get("link")
    if link:
        return hashlib.sha256(str(link).encode("utf-8")).hexdigest()
    return hashlib.sha256(str(entry.get("title", "")).encode("utf-8")).hexdigest()
