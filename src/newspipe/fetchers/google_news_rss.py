"""Google News RSS fetcher.

Google News wraps every article in a ``news.google.com/rss/articles/...``
redirect URL. Since ~2024 the token is opaque: the base64 payload no longer
contains the target URL and the redirect chain stays on news.google.com. We
therefore attempt best-effort extraction and keep the Google URL as fallback;
dedup v1's title-hash matching covers cross-source collapse for these items.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import urlencode, urlparse

from newspipe.fetchers.rss import RSSFetcher
from newspipe.models.schemas import RawItem, Source, SourceMethod

_ARTICLES_RE = re.compile(r"/articles/([^/?]+)")
_URL_RE = re.compile(r"https?://[^\s\x00-\x1f\x7f\"']+")


def extract_real_url(google_url: str) -> str | None:
    """Return the real article URL if decodable from the token, else None."""
    match = _ARTICLES_RE.search(urlparse(google_url).path)
    if not match:
        return None
    token = match.group(1)
    try:
        padded = token + "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore")
    except Exception:
        return None
    url_match = _URL_RE.search(decoded)
    if not url_match:
        return None
    url = url_match.group(0).rstrip(".,;)]}\"'")
    return url or None


class GoogleNewsRSSFetcher(RSSFetcher):
    method: SourceMethod = "google_news_rss"

    def feed_url(self, source: Source) -> str:
        url = source.config.get("feed_url")
        if url:
            return str(url)
        query = source.config.get("query")
        if not query:
            raise ValueError(f"source {source.name!r} has no config.query or config.feed_url")
        return "https://news.google.com/rss/search?" + urlencode({"q": str(query)})

    def fetch(self, source: Source) -> list[RawItem]:
        items = super().fetch(source)
        for item in items:
            real = extract_real_url(item.url)
            if real and real != item.url:
                item.raw["google_news_url"] = item.url
                item.url = real
        return items
