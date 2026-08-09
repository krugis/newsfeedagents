"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine

from newspipe.config import get_settings
from newspipe.db.engine import get_engine


@pytest.fixture(scope="session")
def engine() -> Engine:
    get_settings.cache_clear()
    get_engine.cache_clear()
    return get_engine()
