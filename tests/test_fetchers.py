"""Unit tests for the fetchers, using saved fixture payloads (no network)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from newspipe.fetchers import base, get_fetcher, google_news_rss, hn_algolia, rss, sitemap
from newspipe.models.schemas import Source

FIXTURES = Path(__file__).parent / "fixtures"


def _source(method: str, config: dict, **overrides) -> Source:
    fields = {
        "source_id": 1,
        "name": f"test-{method}",
        "method": method,
        "config": config,
        "created_at": datetime.now(UTC),
    }
    fields.update(overrides)
    return Source(**fields)


def _fake_response(path: str, content_type: str = "application/xml") -> httpx.Response:
    return httpx.Response(
        200,
        content=(FIXTURES / path).read_bytes(),
        headers={"content-type": content_type},
    )


def _stub_get_with_retry(monkeypatch, module, paths_by_fragment: dict[str, str]) -> None:
    """Serve fixtures by URL fragment in place of module.get_with_retry."""

    def fake(client, url, *, params=None):
        for fragment, path in paths_by_fragment.items():
            if fragment in url:
                ct = "application/json" if path.endswith(".json") else "application/xml"
                return _fake_response(path, ct)
        raise AssertionError(f"no fixture for url: {url}")

    monkeypatch.setattr(module, "get_with_retry", fake)


# --- RSS ---------------------------------------------------------------------


def test_rss_fetcher_parses_fixture(monkeypatch):
    _stub_get_with_retry(monkeypatch, rss, {"techcrunch.com": "techcrunch_ai.xml"})
    src = _source(
        "rss", {"feed_url": "https://techcrunch.com/category/artificial-intelligence/feed/"}
    )
    items = rss.fetch(src)
    assert len(items) > 0
    for item in items:
        assert item.external_id
        assert item.title
        assert item.url.startswith("http")
    assert any(item.published_at is not None for item in items)


def test_rss_external_ids_are_deterministic(monkeypatch):
    _stub_get_with_retry(monkeypatch, rss, {"techcrunch.com": "techcrunch_ai.xml"})
    src = _source(
        "rss", {"feed_url": "https://techcrunch.com/category/artificial-intelligence/feed/"}
    )
    first = rss.fetch(src)
    second = rss.fetch(src)
    assert [i.external_id for i in first] == [i.external_id for i in second]
    assert [i.url for i in first] == [i.url for i in second]


# --- Hacker News -------------------------------------------------------------


def test_hn_marks_front_page_hits(monkeypatch):
    search = json.loads((FIXTURES / "hn_search.json").read_text())
    hit = search["hits"][0]
    front = {"hits": [{"objectID": hit["objectID"]}]}

    def fake(client, url, *, params=None):
        if "search_by_date" in url:
            return httpx.Response(
                200, content=json.dumps(search), headers={"content-type": "application/json"}
            )
        if url.rstrip("/").endswith("/search"):
            return httpx.Response(
                200, content=json.dumps(front), headers={"content-type": "application/json"}
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(hn_algolia, "get_with_retry", fake)
    src = _source("hn_algolia", {"base_url": "https://hn.algolia.com/api/v1/", "keywords": ["LLM"]})
    items = hn_algolia.fetch(src)
    assert items
    hit_items = [i for i in items if i.external_id == hit["objectID"]]
    assert hit_items
    assert hit_items[0].raw.get("hn_front_page") is True
    assert all(i.url.startswith("http") for i in items)


def test_hn_window_uses_last_polled_at(monkeypatch):
    # 2h ago, so the 24h cap never kicks in regardless of wall-clock date.
    last = datetime.now(UTC) - timedelta(hours=2)
    captured: dict = {}

    def fake(client, url, *, params=None):
        captured["params"] = params
        return httpx.Response(
            200, content=json.dumps({"hits": []}), headers={"content-type": "application/json"}
        )

    monkeypatch.setattr(hn_algolia, "get_with_retry", fake)
    src = _source(
        "hn_algolia",
        {"base_url": "https://hn.algolia.com/api/v1/", "keywords": ["AI"]},
        last_polled_at=last,
    )
    hn_algolia.fetch(src)
    assert captured["params"]["numericFilters"] == f"created_at_i>{int(last.timestamp())}"


def test_hn_window_is_capped_at_24h():
    old = datetime(2020, 1, 1, tzinfo=UTC)
    src = _source("hn_algolia", {}, last_polled_at=old)
    start = hn_algolia._window_start(src)
    assert datetime.now(UTC) - start <= timedelta(hours=25)


# --- Google News -------------------------------------------------------------


def test_google_news_keeps_opaque_url_and_records_source(monkeypatch):
    _stub_get_with_retry(monkeypatch, google_news_rss, {"news.google.com": "google_news_genai.xml"})
    src = _source(
        "google_news_rss", {"feed_url": "https://news.google.com/rss/search?q=generative+AI"}
    )
    items = google_news_rss.fetch(src)
    assert len(items) > 0
    for item in items:
        assert item.raw["google_news_url"]
        # current Google format is opaque -> Google URL retained as fallback
        assert item.url.startswith("https://news.google.com/")
        assert item.title


def test_extract_google_news_target_decodes_old_format():
    payload = b"\x08\x01\x12\x20https://example.com/article/123\x00"
    token = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    url = f"https://news.google.com/rss/articles/{token}"
    assert google_news_rss.extract_google_news_target(url) == "https://example.com/article/123"


def test_extract_google_news_target_returns_none_for_opaque_or_invalid():
    opaque = (
        "https://news.google.com/rss/articles/CBMioAFBVV95cUxONVBSRTItcUtrR2NBMXQ0MkFYc2RCM2ZJZEtD"
    )
    assert google_news_rss.extract_google_news_target(opaque) is None
    assert (
        google_news_rss.extract_google_news_target(
            "https://news.google.com/rss/articles/not-base64!!"
        )
        is None
    )
    assert google_news_rss.extract_google_news_target("https://example.com/not-google") is None


# --- Sitemap (Anthropic adaptation) ------------------------------------------


def test_sitemap_fetcher_filters_by_path(monkeypatch):
    _stub_get_with_retry(monkeypatch, sitemap, {"anthropic.com": "anthropic_sitemap.xml"})
    src = _source(
        "sitemap",
        {"sitemap_url": "https://www.anthropic.com/sitemap.xml", "path_filter": "/news/"},
    )
    items = sitemap.fetch(src)
    assert items
    assert all("/news/" in i.url for i in items)
    assert all(i.external_id and i.title for i in items)


# --- Registry + retry helper --------------------------------------------------


def test_get_fetcher_registry():
    assert get_fetcher("rss") is rss.fetch
    assert get_fetcher("sitemap") is sitemap.fetch
    with pytest.raises(ValueError):
        get_fetcher("bogus")


def test_get_with_retry_succeeds_immediately():
    class Stub:
        def get(self, url, params=None):
            return httpx.Response(200, content=b"ok", request=httpx.Request("GET", url))

    resp = base.get_with_retry(Stub(), "https://example.com/x")
    assert resp.content == b"ok"


def test_get_with_retry_retries_transient_then_succeeds(monkeypatch):
    calls = []

    class Stub:
        def get(self, url, params=None):
            calls.append(url)
            req = httpx.Request("GET", url)
            if len(calls) == 1:
                return httpx.Response(503, content=b"", request=req)
            return httpx.Response(200, content=b"ok", request=req)

    monkeypatch.setattr(base.time, "sleep", lambda _: None)
    resp = base.get_with_retry(Stub(), "https://example.com/x")
    assert resp.status_code == 200
    assert len(calls) == 2


def test_get_with_retry_does_not_retry_4xx():
    calls = []

    class Stub:
        def get(self, url, params=None):
            calls.append(url)
            return httpx.Response(404, request=httpx.Request("GET", url))

    with pytest.raises(httpx.HTTPStatusError):
        base.get_with_retry(Stub(), "https://example.com/x")
    assert len(calls) == 1


def test_get_with_retry_gives_up_after_max_attempts(monkeypatch):
    calls = []

    class Stub:
        def get(self, url, params=None):
            calls.append(url)
            return httpx.Response(502, request=httpx.Request("GET", url))

    monkeypatch.setattr(base.time, "sleep", lambda _: None)
    with pytest.raises(httpx.HTTPStatusError):
        base.get_with_retry(Stub(), "https://example.com/x")
    assert len(calls) == base.MAX_RETRIES
