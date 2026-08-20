"""Google News RSS fetcher.

Google News RSS item links are Google redirect URLs. When the target URL can
be decoded from the token we use it; otherwise the Google URL is kept as the
item URL (the spec's fallback). As of 2026-08, Google's tokens are opaque and
neither base64 decoding nor redirect-following yields the real article URL, so
in practice the Google URL is used and cross-source dedup relies on title-hash.

Google News always formats titles as "<article title> - <publisher>" (e.g.
"... - The Japan Times"); we strip that suffix (see `_strip_source_suffix`)
so a story that also arrived from its original source's own feed title-hash
matches instead of permanently forking into two stories.
"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

import feedparser
import httpx

from newspipe.fetchers import rss
from newspipe.fetchers.base import RawItem, build_client, get_with_retry
from newspipe.models.schemas import Source


def fetch(
    source_row: Source, client: httpx.Client | None = None, *, backfill: bool = False
) -> list[RawItem]:
    """Fetch a Google News RSS search feed and parse its items.

    ``backfill`` is accepted for interface parity with the other fetchers but
    unused here: a search feed has no time-window query to widen.
    """
    client = client or build_client()
    feed_url = source_row.config["feed_url"]
    resp = get_with_retry(client, feed_url)
    feed = feedparser.parse(resp.content)
    items: list[RawItem] = []
    for entry in feed.entries:
        google_url = str(entry.get("link", "")).strip() if entry.get("link") else ""
        title = str(entry.get("title", "")).strip() if entry.get("title") else ""
        if not google_url and not title:
            continue
        title = _strip_source_suffix(title)
        target = extract_google_news_target(google_url)
        raw = rss.entry_to_raw(entry)
        raw["google_news_url"] = google_url
        if target:
            raw["google_news_target_url"] = target
        items.append(
            RawItem(
                external_id=_external_id_for(entry, google_url),
                url=target or google_url,
                title=title,
                published_at=rss.published_at_for(entry),
                raw=raw,
            )
        )
    return items


def extract_google_news_target(url: str) -> str | None:
    """Best-effort decode of a Google News redirect URL to the article URL.

    Older tokens embed the target URL in a base64 protobuf payload. Returns
    None when the token is opaque (the current format) or malformed.
    """
    match = re.search(r"/articles/([A-Za-z0-9_-]+)", url)
    if not match:
        return None
    token = match.group(1)
    try:
        padded = token + "=" * (-len(token) % 4)
        data = base64.urlsafe_b64decode(padded)
    except ValueError:
        return None
    inner = re.search(rb"https?://[^\x00-\x1f\"'<>]+", data)
    if inner:
        return inner.group(0).decode("utf-8", "ignore").strip()
    return None


def _strip_source_suffix(title: str) -> str:
    """Strip Google News's trailing " - <publisher>" suffix from a title.

    Google News RSS always appends the publisher as the final " - "-separated
    segment, so trimming after the *last* occurrence is safe even when the
    article title itself contains a hyphenated clause earlier on.
    """
    if " - " not in title:
        return title
    return title.rsplit(" - ", 1)[0].strip()


def _external_id_for(entry: Any, google_url: str) -> str:
    guid = entry.get("guid")
    if isinstance(guid, str) and guid.strip():
        return guid.strip()
    if google_url:
        return hashlib.sha256(google_url.encode("utf-8")).hexdigest()
    return hashlib.sha256(str(entry.get("title", "")).encode("utf-8")).hexdigest()
