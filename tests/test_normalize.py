"""Normalization unit tests."""

from __future__ import annotations

from newspipe.normalize import canonicalize_url, normalize_title, title_hash


def test_lowercases_host() -> None:
    assert canonicalize_url("https://EXAMPLE.com/Path") == "https://example.com/Path"


def test_http_becomes_https() -> None:
    assert canonicalize_url("http://example.com/a") == "https://example.com/a"


def test_strips_tracking_params() -> None:
    url = "https://example.com/a?utm_source=x&fbclid=abc&gclid=def&id=1"
    assert canonicalize_url(url) == "https://example.com/a?id=1"


def test_strips_fragment() -> None:
    assert canonicalize_url("https://example.com/a#section") == "https://example.com/a"


def test_trailing_slash_removed() -> None:
    assert canonicalize_url("https://example.com/a/") == "https://example.com/a"
    assert canonicalize_url("https://example.com/") == "https://example.com"


def test_google_news_redirect_params_stripped() -> None:
    url = "https://news.google.com/rss/articles/CBTOKEN?oc=5&hl=en-US&gl=US&ceid=US:en"
    assert canonicalize_url(url) == "https://news.google.com/rss/articles/CBTOKEN"


def test_query_params_sorted() -> None:
    assert canonicalize_url("https://example.com/a?b=2&a=1") == "https://example.com/a?a=1&b=2"


def test_default_port_dropped() -> None:
    assert canonicalize_url("https://example.com:443/a") == "https://example.com/a"


def test_normalize_title_nfkc_and_whitespace() -> None:
    assert normalize_title("  Hello   World\u00a0! ") == "Hello World !"
    assert normalize_title("\uff21\uff22\uff23") == "ABC"  # full-width -> ASCII


def test_title_hash_deterministic() -> None:
    assert title_hash("Foo Bar") == title_hash("  Foo  Bar ")
    assert len(title_hash("x")) == 64
