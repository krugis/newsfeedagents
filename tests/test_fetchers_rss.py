"""RSS fetcher tests against the recorded TechCrunch fixture."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from newspipe.fetchers.rss import RSSFetcher
from newspipe.models.schemas import Source

FIXTURES = Path(__file__).parent / "fixtures"
TECHCRUNCH_FEED = FIXTURES / "rss_techcrunch.xml"


def _rss_source(**overrides: object) -> Source:
    defaults: dict[str, object] = {
        "source_id": 1,
        "name": "TechCrunch AI",
        "method": "rss",
        "config": {"feed_url": "https://techcrunch.com/category/artificial-intelligence/feed/"},
        "poll_interval_minutes": 60,
        "last_polled_at": None,
        "enabled": True,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Source(**defaults)  # type: ignore[arg-type]


def _client_with(content: bytes, status: int = 200) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, content=content))
    )


def test_fetches_items_from_fixture() -> None:
    items = RSSFetcher(client=_client_with(TECHCRUNCH_FEED.read_bytes())).fetch(_rss_source())
    assert len(items) == 20
    item = items[0]
    assert item.external_id
    assert item.url.startswith("https://")
    assert item.title
    assert item.published_at is not None
    assert item.raw["feed_title"]
    assert "entry" in item.raw


def test_external_id_falls_back_to_link_hash() -> None:
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>mini</title>
      <item><title>Hello</title><link>https://example.com/hello</link></item>
    </channel></rss>"""
    items = RSSFetcher(client=_client_with(xml)).fetch(_rss_source())
    assert len(items) == 1
    assert items[0].external_id == hashlib.sha256(b"https://example.com/hello").hexdigest()


def test_entry_without_id_or_link_is_skipped() -> None:
    xml = b"""<?xml version="1.0"?><rss version="2.0"><channel>
      <title>mini</title>
      <item><title>No link no guid</title><description>desc</description></item>
    </channel></rss>"""
    items = RSSFetcher(client=_client_with(xml)).fetch(_rss_source())
    assert items == []


def test_empty_feed_returns_empty_list() -> None:
    xml = b'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title></channel></rss>'
    assert RSSFetcher(client=_client_with(xml)).fetch(_rss_source()) == []


@pytest.mark.live
def test_live_techcrunch_fetch() -> None:
    items = RSSFetcher().fetch(_rss_source())
    assert items
    assert all(item.url for item in items)
