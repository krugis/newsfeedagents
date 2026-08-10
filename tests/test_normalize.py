"""Unit tests for URL/title normalization."""

from __future__ import annotations

from newspipe.normalize import canonicalize_url, normalize_title, title_hash


def test_canonicalize_lowercases_host():
    assert canonicalize_url("HTTP://Example.COM/Path") == "https://example.com/Path"


def test_canonicalize_strips_tracking_params():
    assert (
        canonicalize_url("https://example.com/a?utm_source=x&id=1&fbclid=y&gclid=z")
        == "https://example.com/a?id=1"
    )


def test_canonicalize_strips_fragment():
    assert canonicalize_url("https://example.com/a#section") == "https://example.com/a"


def test_canonicalize_http_to_https():
    assert canonicalize_url("http://example.com/a") == "https://example.com/a"


def test_canonicalize_trailing_slash_policy():
    assert canonicalize_url("https://example.com/a/") == "https://example.com/a"
    assert canonicalize_url("https://example.com") == "https://example.com/"


def test_canonicalize_is_idempotent():
    url = "https://example.com/a?utm_source=x"
    assert canonicalize_url(canonicalize_url(url)) == canonicalize_url(url)


def test_normalize_title_collapses_whitespace():
    assert normalize_title("  AI\tNews   Story  ") == "AI News Story"


def test_normalize_title_nfkc():
    # full-width unicode letters normalize to ASCII
    assert normalize_title("ＣＵＲ") == "CUR"


def test_title_hash_deterministic():
    assert title_hash("Same Title") == title_hash("  Same   Title ")
