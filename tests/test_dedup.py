"""Dedup v1 integration tests against the real DB.

Uses dedicated ``__dedup_test__`` sources and ``https://dedup.test/`` URLs;
an autouse fixture removes all of them after each test so the live data
from fetch runs is never touched.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from newspipe.db.engine import get_engine
from newspipe.dedup import run_dedup
from newspipe.normalize import title_hash


def _new_source(name: str) -> int:
    with get_engine().begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO sources (name, method, config, poll_interval_minutes)"
                " VALUES (:name, 'rss', '{}'::jsonb, 0) RETURNING source_id"
            ),
            {"name": name},
        ).fetchone()
    return int(row[0])


def _insert_arrival(
    source_id: int,
    external_id: str,
    url: str,
    title: str,
    raw: dict | None = None,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO arrivals (source_id, external_id, url, url_canonical, title, raw)
                VALUES (:sid, :eid, :url, NULL, :title, CAST(:raw AS jsonb))
                """
            ),
            {
                "sid": source_id,
                "eid": external_id,
                "url": url,
                "title": title,
                "raw": json.dumps(raw or {}),
            },
        )


def _stories_of_source(source_id: int) -> list[dict]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT s.story_id, s.canonical_url, s.title, s.arrival_count, s.hn_front_page
                FROM stories s
                JOIN arrivals a ON a.story_id = s.story_id
                WHERE a.source_id = :sid
                GROUP BY s.story_id
                ORDER BY s.story_id
                """
            ),
            {"sid": source_id},
        ).mappings().fetchall()
    return [dict(row) for row in rows]


@pytest.fixture(autouse=True)
def _cleanup_dedup_data() -> None:
    yield
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "DELETE FROM arrivals WHERE source_id IN"
                " (SELECT source_id FROM sources WHERE name LIKE '__dedup_test__%')"
            )
        )
        conn.execute(
            text("DELETE FROM stories WHERE canonical_url LIKE 'https://dedup.test/%'")
        )
        conn.execute(text("DELETE FROM sources WHERE name LIKE '__dedup_test__%'"))


def test_same_url_two_sources_collapses() -> None:
    sid1 = _new_source("__dedup_test__a")
    sid2 = _new_source("__dedup_test__b")
    _insert_arrival(sid1, "e1", "https://dedup.test/story", "Alpha headline")
    _insert_arrival(sid2, "e2", "https://dedup.test/story", "Alpha headline")

    run_dedup()

    rows = _stories_of_source(sid1)
    assert len(rows) == 1
    assert rows[0]["arrival_count"] == 2


def test_same_title_different_url_collapses() -> None:
    sid = _new_source("__dedup_test__single")
    _insert_arrival(sid, "e1", "https://dedup.test/one", "Shared Headline")
    _insert_arrival(sid, "e2", "https://dedup.test/two", "  Shared   Headline ")

    run_dedup()

    rows = _stories_of_source(sid)
    assert len(rows) == 1
    assert rows[0]["arrival_count"] == 2
    assert rows[0]["canonical_url"] == "https://dedup.test/one"


def test_different_stories_do_not_collapse() -> None:
    sid = _new_source("__dedup_test__single")
    _insert_arrival(sid, "e1", "https://dedup.test/a", "Alpha headline")
    _insert_arrival(sid, "e2", "https://dedup.test/b", "Beta headline")

    run_dedup()

    assert len(_stories_of_source(sid)) == 2


def test_tracking_param_variants_collapse() -> None:
    sid = _new_source("__dedup_test__single")
    _insert_arrival(sid, "e1", "https://dedup.test/post?a=1&utm_source=x", "Tracking story")
    _insert_arrival(sid, "e2", "https://dedup.test/post?utm_medium=y&a=1", "Tracking story")

    run_dedup()

    with get_engine().connect() as conn:
        canonicals = conn.execute(
            text("SELECT DISTINCT url_canonical FROM arrivals WHERE source_id = :sid"),
            {"sid": sid},
        ).scalars().all()
    assert canonicals == ["https://dedup.test/post?a=1"]


def test_title_match_outside_window_does_not_collapse() -> None:
    sid = _new_source("__dedup_test__single")
    digest = title_hash("Old But Same Title")
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO stories (canonical_url, title, title_hash, first_seen_at, last_seen_at)
                VALUES ('https://dedup.test/old', 'Old But Same Title', :h,
                        now() - interval '96 hours', now() - interval '96 hours')
                """
            ),
            {"h": digest},
        )
    _insert_arrival(sid, "e1", "https://dedup.test/new", "Old But Same Title")

    run_dedup()

    # the new arrival must NOT attach to the 96h-old story; it gets its own
    rows = _stories_of_source(sid)
    assert len(rows) == 1
    assert rows[0]["canonical_url"] == "https://dedup.test/new"


def test_hn_front_page_propagates_on_create_and_match() -> None:
    sid = _new_source("__dedup_test__single")
    _insert_arrival(
        sid, "e1", "https://dedup.test/story", "Front story", raw={"hn_front_page": True}
    )
    _insert_arrival(sid, "e2", "https://dedup.test/story", "Front story", raw={})

    run_dedup()

    rows = _stories_of_source(sid)
    assert len(rows) == 1
    assert rows[0]["hn_front_page"] is True
    assert rows[0]["arrival_count"] == 2


def test_hn_front_page_flips_existing_story() -> None:
    sid = _new_source("__dedup_test__single")
    _insert_arrival(sid, "e1", "https://dedup.test/story", "Front story", raw={})
    _insert_arrival(
        sid, "e2", "https://dedup.test/story", "Front story", raw={"hn_front_page": True}
    )

    run_dedup()

    rows = _stories_of_source(sid)
    assert rows[0]["hn_front_page"] is True


def test_backfills_canonical_and_is_idempotent() -> None:
    sid = _new_source("__dedup_test__single")
    _insert_arrival(sid, "e1", "https://dedup.test/story/?utm_source=x", "Backfill story")

    run_dedup()

    with get_engine().connect() as conn:
        canonical = conn.execute(
            text("SELECT url_canonical FROM arrivals WHERE source_id = :sid"),
            {"sid": sid},
        ).scalar()
    assert canonical == "https://dedup.test/story"

    # after a full pass nothing is left unattached, so a second run is a no-op
    stats2 = run_dedup()
    assert stats2["arrivals_processed"] == 0
    assert stats2["stories_created"] == 0
    assert stats2["stories_updated"] == 0
