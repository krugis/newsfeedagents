"""Base fetcher: shared httpx client, bounded exponential retries.

Every fetcher implements ``fetch(source) -> list[RawItem]``. Fetchers are thin
adapters over a source API; normalization, dedup, and labeling happen
downstream. A failing source must never break a run: callers isolate each
source call in its own try/except.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import UTC, datetime

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from newspipe.models.schemas import RawItem, Source, SourceMethod

USER_AGENT = "newspipe/0.1 (hourly GenAI/ML news ingestion pipeline)"
RETRY_ATTEMPTS = 3


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


def default_client() -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(20.0, connect=10.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


def link_hash(link: str) -> str:
    """Stable external id for feeds without a GUID."""
    return hashlib.sha256(link.encode("utf-8")).hexdigest()


class BaseFetcher(ABC):
    """Common fetch machinery; subclasses handle one source method."""

    method: SourceMethod

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or default_client()

    @abstractmethod
    def fetch(self, source: Source) -> list[RawItem]:
        """Return raw items from ``source``. Entry-level parse problems must
        not raise; the caller wraps the whole call per source."""

    @retry(
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, max=8),
        retry=retry_if_exception(_should_retry),
        reraise=True,
    )
    def _get(self, url: str, params: dict[str, str] | None = None) -> httpx.Response:
        response = self.client.get(url, params=params)
        if response.status_code >= 500:
            response.raise_for_status()
        return response
