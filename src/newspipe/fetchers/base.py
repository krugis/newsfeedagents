"""Shared fetcher infrastructure: the RawItem domain model, HTTP client, retry logic."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

USER_AGENT = "newspipe/0.1 (GenAI/ML news ingestion pipeline)"
DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
MAX_RETRIES = 3
RETRY_BASE_SECONDS = 1.0
RETRY_MAX_SECONDS = 8.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class RawItem(BaseModel):
    """A raw news item as emitted by a fetcher, before persistence and dedup."""

    external_id: str
    url: str
    title: str
    published_at: datetime | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


def build_client() -> httpx.Client:
    """Return a shared HTTP client with a custom User-Agent and sane defaults."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
    )


def get_with_retry(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> httpx.Response:
    """GET a URL with bounded exponential-backoff retries on transient failures.

    Retries on transport errors and retryable HTTP statuses (429, 5xx).
    Non-retryable statuses (4xx) raise immediately via ``raise_for_status``.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = client.get(url, params=params)
        except httpx.TransportError as exc:
            if attempt >= MAX_RETRIES:
                raise
            delay = _backoff(attempt)
            logger.warning(
                "GET %s failed (attempt %d/%d): %s; retrying in %.1fs",
                url,
                attempt,
                MAX_RETRIES,
                exc,
                delay,
            )
            time.sleep(delay)
            continue
        if resp.status_code not in RETRYABLE_STATUS_CODES:
            resp.raise_for_status()
            return resp
        if attempt >= MAX_RETRIES:
            resp.raise_for_status()
        delay = _backoff(attempt)
        logger.warning(
            "GET %s returned %d (attempt %d/%d); retrying in %.1fs",
            url,
            resp.status_code,
            attempt,
            MAX_RETRIES,
            delay,
        )
        time.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _backoff(attempt: int) -> float:
    return min(RETRY_BASE_SECONDS * (2 ** (attempt - 1)), RETRY_MAX_SECONDS)
