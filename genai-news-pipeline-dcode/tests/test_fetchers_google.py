"""Google News RSS fetcher tests against the recorded fixture."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from newspipe.fetchers.google_news_rss import GoogleNewsRSSFetcher, extract_real_url
from newspipe.models.schemas import Source

FIXTURES = Path(__file__).parent / "fixtures"
GOOGLE_FIXTURE = FIXTURES / "google_news_rss.xml"


def _google_source(**overrides: object) -> Source:
    defaults: dict[str, object] = {
        "source_id": 7,
        "name": "Google News: generative AI",
        "method": "google_news_rss",
        "config": {"query": "generative AI"},
        "poll_interval_minutes": 60,
        "last_polled_at": None,
        "enabled": True,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Source(**defaults)  # type: ignore[arg-type]


def _client() -> httpx.Client:
    content = GOOGLE_FIXTURE.read_bytes()
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    )


def test_extract_real_url_returns_none_for_opaque_token() -> None:
    url = "https://news.google.com/rss/articles/CBMihAFBVV95cUxQV3pGdUV6T252?oc=5"
    assert extract_real_url(url) is None


def test_extract_real_url_handles_decodable_token() -> None:
    payload = b"https://example.com/actual-article\x00\x01\x02"
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    url = f"https://news.google.com/rss/articles/{token}?oc=5"
    assert extract_real_url(url) == "https://example.com/actual-article"


def test_fixture_items_keep_google_url_as_fallback() -> None:
    items = GoogleNewsRSSFetcher(client=_client()).fetch(_google_source())
    assert items
    for item in items:
        assert item.external_id
        assert item.title
        # modern Google tokens are opaque -> URL stays the Google redirect URL
        assert item.url.startswith("https://news.google.com/rss/articles/")
        assert item.published_at is not None


def test_feed_url_built_from_query() -> None:
    fetcher = GoogleNewsRSSFetcher(client=_client())
    url = fetcher.feed_url(_google_source())
    assert url.startswith("https://news.google.com/rss/search?q=")
    assert "generative+AI" in url


@pytest.mark.live
def test_live_google_news_fetch() -> None:
    items = GoogleNewsRSSFetcher().fetch(_google_source())
    assert items
