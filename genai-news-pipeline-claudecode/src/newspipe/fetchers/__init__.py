"""Fetcher registry: maps a source `method` to its fetch function.

One fetcher per method type (not per source), so adding a new source of an
existing type only requires a new registry row.
"""

from __future__ import annotations

from collections.abc import Callable

from newspipe.fetchers import google_news_rss, hn_algolia, rss, sitemap
from newspipe.fetchers.base import RawItem

Fetcher = Callable[..., list[RawItem]]

_FETCHERS: dict[str, Fetcher] = {
    "rss": rss.fetch,
    "hn_algolia": hn_algolia.fetch,
    "google_news_rss": google_news_rss.fetch,
    "sitemap": sitemap.fetch,
}


def get_fetcher(method: str) -> Fetcher:
    """Return the fetch function for a source method."""
    try:
        return _FETCHERS[method]
    except KeyError:
        raise ValueError(f"unknown source method: {method!r}") from None
