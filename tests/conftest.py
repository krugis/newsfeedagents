"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from newspipe.db.engine import connect
from newspipe.db.migrate import run_migrations


@pytest.fixture(scope="session", autouse=True)
def _migrated() -> None:
    """Apply schema migrations once per test session (idempotent)."""
    run_migrations()


@pytest.fixture
def db_conn():
    """A fresh database connection for a single test."""
    with connect() as conn:
        yield conn
