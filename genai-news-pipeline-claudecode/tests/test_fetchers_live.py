"""Live integration tests — hit the real endpoints.

Skipped by default (see addopts). Run explicitly with:
    uv run pytest -m live
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from newspipe.fetchers import get_fetcher
from newspipe.models.schemas import Source
from newspipe.seeding import SOURCES

pytestmark = pytest.mark.live


@pytest.mark.parametrize("source_def", SOURCES, ids=[s["name"] for s in SOURCES])
def test_fetch_source_live(source_def: dict) -> None:
    src = Source(
        source_id=0,
        name=source_def["name"],
        method=source_def["method"],
        config=source_def["config"],
        created_at=datetime.now(UTC),
    )
    items = get_fetcher(src.method)(src)
    assert items, f"expected at least one item from {source_def['name']}"
    for item in items:
        assert item.title
        assert item.url.startswith("http")
