"""Fetcher registry: one fetcher class per method type, not per source."""

from __future__ import annotations

from newspipe.fetchers.base import BaseFetcher
from newspipe.fetchers.google_news_rss import GoogleNewsRSSFetcher
from newspipe.fetchers.hn_algolia import HNAlgoliaFetcher
from newspipe.fetchers.rss import RSSFetcher

FETCHERS: dict[str, type[BaseFetcher]] = {
    "rss": RSSFetcher,
    "hn_algolia": HNAlgoliaFetcher,
    "google_news_rss": GoogleNewsRSSFetcher,
}


def get_fetcher(method: str) -> BaseFetcher:
    try:
        cls = FETCHERS[method]
    except KeyError:
        raise ValueError(f"no fetcher registered for method {method!r}") from None
    return cls()
