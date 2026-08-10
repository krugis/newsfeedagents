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


@pytest.fixture
def source_scope(db_conn):
    """Create sources for a test and clean them up (with their arrivals) afterwards."""

    def make(name: str, method: str = "rss", config: dict | None = None) -> int:
        from newspipe.db.sources import upsert_source

        upsert_source(db_conn, name, method, config or {"feed_url": "x"})
        row = db_conn.execute("SELECT source_id FROM sources WHERE name = %s", (name,)).fetchone()
        assert row, f"source {name!r} not found after upsert"
        created.append(row["source_id"])
        return row["source_id"]

    created: list[int] = []
    yield make
    if created:
        db_conn.execute("DELETE FROM arrivals WHERE source_id = ANY(%s)", (created,))
        db_conn.execute("DELETE FROM sources WHERE source_id = ANY(%s)", (created,))
