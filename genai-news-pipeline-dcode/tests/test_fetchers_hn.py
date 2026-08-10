"""HN Algolia fetcher tests against recorded fixtures."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from newspipe.fetchers.hn_algolia import MAX_BACKFILL, HNAlgoliaFetcher
from newspipe.models.schemas import Source

FIXTURES = Path(__file__).parent / "fixtures"
SEARCH_FIXTURE = FIXTURES / "hn_search_by_date.json"
FRONT_PAGE_FIXTURE = FIXTURES / "hn_front_page.json"


def _hn_source(**overrides: object) -> Source:
    defaults: dict[str, object] = {
        "source_id": 6,
        "name": "Hacker News",
        "method": "hn_algolia",
        "config": {
            "api_base": "https://hn.algolia.com/api/v1/",
            "keywords": ["AI"],
            "front_page": True,
        },
        "poll_interval_minutes": 60,
        "last_polled_at": None,
        "enabled": True,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return Source(**defaults)  # type: ignore[arg-type]


def _client() -> httpx.Client:
    search_bytes = SEARCH_FIXTURE.read_bytes()
    front_bytes = FRONT_PAGE_FIXTURE.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search_by_date"):
            return httpx.Response(200, content=search_bytes)
        if request.url.path.endswith("/search"):
            return httpx.Response(200, content=front_bytes)
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetches_items_from_fixture() -> None:
    items = HNAlgoliaFetcher(client=_client()).fetch(_hn_source())
    assert items
    for item in items:
        assert item.external_id
        assert item.url.startswith("http")
        assert item.title
        assert item.published_at is not None
        assert item.raw["keyword"] == "AI"
        assert "hn_item" in item.raw


def test_front_page_hits_are_marked() -> None:
    search_data = json.loads(SEARCH_FIXTURE.read_text())
    front_data = json.loads(FRONT_PAGE_FIXTURE.read_text())
    first_id = search_data["hits"][0]["objectID"]
    front_data["hits"][0]["objectID"] = first_id

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/search_by_date"):
            return httpx.Response(200, content=json.dumps(search_data))
        return httpx.Response(200, content=json.dumps(front_data))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    items = HNAlgoliaFetcher(client=client).fetch(_hn_source())
    marked = {item.external_id for item in items if item.raw.get("hn_front_page")}
    assert first_id in marked


def test_window_start_defaults_to_24h_back() -> None:
    now = datetime.now(UTC)
    fetcher = HNAlgoliaFetcher(client=_client())
    start = fetcher.window_start(_hn_source(last_polled_at=None))
    assert abs((now - start) - MAX_BACKFILL) < timedelta(seconds=10)


def test_window_start_respects_last_polled() -> None:
    last = datetime.now(UTC) - timedelta(hours=2)
    fetcher = HNAlgoliaFetcher(client=_client())
    assert fetcher.window_start(_hn_source(last_polled_at=last)) == last


def test_window_start_capped_at_24h() -> None:
    old = datetime.now(UTC) - timedelta(days=3)
    fetcher = HNAlgoliaFetcher(client=_client())
    start = fetcher.window_start(_hn_source(last_polled_at=old))
    assert datetime.now(UTC) - start < timedelta(hours=25)


@pytest.mark.live
def test_live_hn_fetch() -> None:
    items = HNAlgoliaFetcher().fetch(_hn_source())
    assert items
