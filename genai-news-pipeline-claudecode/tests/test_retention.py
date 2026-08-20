"""Tests for data retention (opt-in hard delete, RETENTION_DAYS)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from newspipe.config import Settings
from newspipe.db.arrivals import insert_arrivals
from newspipe.db.labels import insert_label
from newspipe.dedup import run_dedup
from newspipe.fetchers.base import RawItem
from newspipe.retention import purge_expired

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def retention_settings(monkeypatch):
    """Patch retention.get_settings to a fixed retention_days value."""

    def _patch(retention_days):
        patched = Settings(retention_days=retention_days)
        monkeypatch.setattr("newspipe.retention.get_settings", lambda: patched)
        return patched

    return _patch


def _make_story(db_conn, source_scope, name: str, external_id: str, title: str) -> int:
    sid = source_scope(name)
    insert_arrivals(
        db_conn,
        sid,
        [RawItem(external_id=external_id, url=f"https://example.com/{external_id}", title=title)],
    )
    db_conn.commit()
    run_dedup()
    row = db_conn.execute(
        """
        SELECT s.story_id FROM stories s
        JOIN arrivals a ON a.story_id = s.story_id
        WHERE a.external_id = %s
        """,
        (external_id,),
    ).fetchone()
    assert row is not None
    return row["story_id"]


def _backdate_story(db_conn, story_id: int, when: datetime) -> None:
    db_conn.execute(
        "UPDATE stories SET first_seen_at = %s, last_seen_at = %s WHERE story_id = %s",
        (when, when, story_id),
    )
    db_conn.commit()


def test_purge_expired_skipped_when_retention_unset(retention_settings):
    retention_settings(None)
    stats = purge_expired(now=NOW)
    assert stats.skipped is True
    assert stats.stories_deleted == 0


def test_purge_expired_deletes_expired_story(db_conn, source_scope, retention_settings):
    retention_settings(30)
    story_id = _make_story(db_conn, source_scope, "zz-retention-old", "zz-ret-1", "Old Story")
    insert_label(
        db_conn,
        story_id,
        is_hot=False,
        importance=3,
        category="other",
        is_genai_ml_relevant=True,
        rationale="x",
        model="m",
        prompt_version="p1",
    )
    db_conn.commit()
    _backdate_story(db_conn, story_id, NOW - timedelta(days=40))

    stats = purge_expired(now=NOW)

    assert stats.skipped is False
    assert stats.stories_deleted == 1
    assert stats.arrivals_deleted == 1
    assert stats.labels_deleted == 1
    assert (
        db_conn.execute("SELECT 1 FROM stories WHERE story_id = %s", (story_id,)).fetchone() is None
    )
    assert (
        db_conn.execute("SELECT 1 FROM labels WHERE story_id = %s", (story_id,)).fetchone() is None
    )


def test_purge_expired_preserves_recent_story(db_conn, source_scope, retention_settings):
    retention_settings(30)
    story_id = _make_story(db_conn, source_scope, "zz-retention-fresh", "zz-ret-2", "Fresh Story")
    db_conn.commit()

    stats = purge_expired(now=NOW)

    assert stats.stories_deleted == 0
    assert (
        db_conn.execute("SELECT 1 FROM stories WHERE story_id = %s", (story_id,)).fetchone()
        is not None
    )


def test_purge_expired_dry_run_deletes_nothing(db_conn, source_scope, retention_settings):
    retention_settings(30)
    story_id = _make_story(
        db_conn, source_scope, "zz-retention-dryrun", "zz-ret-3", "Dry Run Story"
    )
    db_conn.commit()
    _backdate_story(db_conn, story_id, NOW - timedelta(days=40))

    stats = purge_expired(now=NOW, dry_run=True)

    assert stats.dry_run is True
    assert stats.stories_deleted == 1  # counted, not deleted
    assert (
        db_conn.execute("SELECT 1 FROM stories WHERE story_id = %s", (story_id,)).fetchone()
        is not None
    )


def test_purge_expired_deletes_orphan_arrivals(db_conn, source_scope, retention_settings):
    retention_settings(30)
    sid = source_scope("zz-retention-orphan")
    insert_arrivals(
        db_conn,
        sid,
        [RawItem(external_id="zz-ret-orphan", url="https://example.com/orphan", title="Orphan")],
    )
    db_conn.commit()
    db_conn.execute(
        "UPDATE arrivals SET fetched_at = %s WHERE external_id = 'zz-ret-orphan'",
        (NOW - timedelta(days=40),),
    )
    db_conn.commit()

    stats = purge_expired(now=NOW)

    assert stats.orphan_arrivals_deleted == 1
    assert (
        db_conn.execute("SELECT 1 FROM arrivals WHERE external_id = 'zz-ret-orphan'").fetchone()
        is None
    )
