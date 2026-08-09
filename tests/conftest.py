"""Shared pytest fixtures and options."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from newspipe.config import get_settings
from newspipe.db.engine import get_engine


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run live network integration tests (@pytest.mark.live)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="live test; run with --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(scope="session")
def engine() -> Engine:
    get_settings.cache_clear()
    get_engine.cache_clear()
    return get_engine()
