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
    """Create sources for a test and clean them up afterwards.

    Also removes any stories those sources' arrivals were attached to (dedup
    tests). Tests must use unique content so they never share a story with
    other (non-test) data.
    """

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
        story_ids = [
            row["story_id"]
            for row in db_conn.execute(
                """
                SELECT DISTINCT story_id FROM arrivals
                WHERE source_id = ANY(%s) AND story_id IS NOT NULL
                """,
                (created,),
            ).fetchall()
        ]
        if story_ids:
            db_conn.execute(
                "UPDATE arrivals SET story_id = NULL WHERE story_id = ANY(%s)", (story_ids,)
            )
            db_conn.execute("DELETE FROM labels WHERE story_id = ANY(%s)", (story_ids,))
            db_conn.execute("DELETE FROM stories WHERE story_id = ANY(%s)", (story_ids,))
        db_conn.execute("DELETE FROM arrivals WHERE source_id = ANY(%s)", (created,))
        db_conn.execute("DELETE FROM sources WHERE source_id = ANY(%s)", (created,))
