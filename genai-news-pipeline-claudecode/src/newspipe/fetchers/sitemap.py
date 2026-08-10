"""XML sitemap fetcher.

Adaptation for sources with no public RSS feed (Anthropic Blog): parses an XML
sitemap and returns the URLs matching a path filter (e.g. ``/news/``). Titles
are derived from the URL slug as best-effort; labels improve them at Gate 1.3.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

import httpx

from newspipe.fetchers.base import RawItem, build_client, get_with_retry
from newspipe.models.schemas import Source

_SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


def fetch(source_row: Source, client: httpx.Client | None = None) -> list[RawItem]:
    """Return sitemap URLs matching ``config["path_filter"]`` as RawItems."""
    client = client or build_client()
    sitemap_url = source_row.config["sitemap_url"]
    path_filter = source_row.config.get("path_filter")
    resp = get_with_retry(client, sitemap_url)
    root = ET.fromstring(resp.content)
    items: list[RawItem] = []
    for url_el in root.findall(f"{{{_SITEMAP_NS}}}url"):
        loc_el = url_el.find(f"{{{_SITEMAP_NS}}}loc")
        if loc_el is None or not (loc_el.text or "").strip():
            continue
        loc = loc_el.text.strip()
        if path_filter and path_filter not in loc:
            continue
        lastmod = url_el.findtext(f"{{{_SITEMAP_NS}}}lastmod")
        items.append(
            RawItem(
                external_id=hashlib.sha256(loc.encode("utf-8")).hexdigest(),
                url=loc,
                title=_title_from_slug(loc),
                published_at=_parse_lastmod(lastmod),
                raw={"sitemap": True, "lastmod": lastmod or None},
            )
        )
    return items


def _parse_lastmod(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _title_from_slug(url: str) -> str:
    """Best-effort readable title from a URL slug (e.g. ``/news/100k-context-windows``)."""
    path = url.rstrip("/").rsplit("/", 1)[-1]
    words = [word for word in path.replace("-", " ").split() if word]
    if not words:
        return url
    return " ".join(words).capitalize()
