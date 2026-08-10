"""URL and title normalization for deduplication.

Canonicalization policy:
- lowercase scheme and host
- resolve ``http`` -> ``https``
- drop default ports (443 for https, 80 for http)
- strip tracking params: ``utm_*``, ``fbclid``, ``gclid``, ``mc_cid``,
  ``mc_eid``, and Google News redirect params (``oc``, ``hl``, ``gl``, ``ceid``)
- strip fragments
- trailing-slash policy: remove trailing slashes (root included)
- sort remaining query params so order-insensitive URLs collapse
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = frozenset({"fbclid", "gclid", "mc_cid", "mc_eid", "oc", "hl", "gl", "ceid"})


def canonicalize_url(url: str) -> str:
    """Return the canonical form of ``url``; malformed input passes through."""
    stripped = url.strip()
    if not stripped:
        return stripped
    try:
        parsed = urlsplit(stripped)
    except ValueError:
        return stripped
    if not parsed.netloc:
        return stripped

    scheme = parsed.scheme.lower() or "https"
    if scheme == "http":
        scheme = "https"

    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None and (
        (scheme == "https" and port == 443) or (scheme == "http" and port == 80)
    ):
        port = None

    netloc = host
    if port is not None:
        netloc = f"{host}:{port}"
    if parsed.username:
        userinfo = parsed.username
        if parsed.password:
            userinfo = f"{userinfo}:{parsed.password}"
        netloc = f"{userinfo}@{netloc}"

    path = parsed.path
    while path.endswith("/") and len(path) > 1:
        path = path[:-1]
    if path == "/":
        path = ""

    params = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not (key.lower().startswith("utm_") or key.lower() in TRACKING_PARAMS)
    ]
    params.sort(key=lambda pair: pair[0])
    query = urlencode(params)

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_title(title: str) -> str:
    """NFKC-normalize and collapse whitespace."""
    normalized = unicodedata.normalize("NFKC", title)
    return re.sub(r"\s+", " ", normalized).strip()


def title_hash(title: str) -> str:
    """SHA-256 of the normalized title."""
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()
