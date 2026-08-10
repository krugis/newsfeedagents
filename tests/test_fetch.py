"""Tests for fetch orchestration + arrivals persistence against the database."""

from __future__ import annotations

from datetime import UTC, datetime

from newspipe.db.arrivals import insert_arrivals
from newspipe.db.sources import select_due_sources, update_last_polled, upsert_source
from newspipe.fetchers.base import RawItem
from newspipe.seeding import SOURCES, seed


def test_seed_is_idempotent(db_conn):
    assert seed(db_conn) == len(SOURCES)
    assert seed(db_conn) == len(SOURCES)
    rows = db_conn.execute("SELECT count(*) AS n FROM sources").fetchone()
    assert rows["n"] == len(SOURCES)


def test_insert_arrivals_is_idempotent(db_conn, source_scope):
    sid = source_scope("zz-test-idem-src")
    items = [RawItem(external_id="e1", url="https://example.com/a", title="A")]
    assert insert_arrivals(db_conn, sid, items) == 1
    assert insert_arrivals(db_conn, sid, items) == 0


def test_select_due_sources_respects_poll_interval(db_conn, source_scope):
    now = datetime.now(UTC)
    source_scope("zz-test-due-now")
    not_due_id = source_scope("zz-test-not-due")
    update_last_polled(db_conn, not_due_id, now)
    due = select_due_sources(db_conn, now)
    names = {s.name for s in due}
    assert "zz-test-due-now" in names
    assert "zz-test-not-due" not in names


def test_upsert_source_updates_config(db_conn, source_scope):
    sid = source_scope("zz-test-upsert")
    upsert_source(db_conn, "zz-test-upsert", "rss", {"feed_url": "https://updated.example/feed"})
    row = db_conn.execute("SELECT config FROM sources WHERE source_id = %s", (sid,)).fetchone()
    assert row["config"]["feed_url"] == "https://updated.example/feed"
