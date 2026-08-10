"""URL and title normalization used by dedup.

URL canonicalization collapses tracking-parameter variants and cosmetic
differences (host case, http→https, fragments, trailing slashes) so the same
article from different sources maps to one canonical URL. Title normalization
produces a stable `title_hash` (sha256 of the NFKC-normalized, whitespace-
collapsed title) used for title-based matching within a time window.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

#: Params dropped from URLs during canonicalization (besides any `utm_*`).
TRACKING_PARAMS = {"fbclid", "gclid", "dclid", "mc_cid", "mc_eid", "igshid"}

_WS_RE = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    """Canonical form of a URL: https, lowercase host, no tracking/fragment,
    consistent trailing slash. Returns the input unchanged if it can't be parsed."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.hostname:
        return url

    host = parts.hostname.lower()
    scheme = "https"
    port = ""
    if parts.port:
        is_default = (scheme == "https" and parts.port == 443) or (
            scheme == "http" and parts.port == 80
        )
        if not is_default:
            port = f":{parts.port}"

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")  # trailing-slash policy: drop except root

    query = _strip_tracking_params(parts.query)
    return urlunsplit((scheme, f"{host}{port}", path, query, ""))  # fragment always dropped


def normalize_title(title: str) -> str:
    """NFKC-normalize and collapse whitespace in a title."""
    return _WS_RE.sub(" ", unicodedata.normalize("NFKC", title)).strip()


def title_hash(title: str) -> str:
    """sha256 of the normalized title, for title-based dedup."""
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def _strip_tracking_params(query: str) -> str:
    if not query:
        return ""
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    return urlencode(kept)
