"""Database-backed tests for dedup v1 (exact-match only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from newspipe.db.arrivals import insert_arrivals
from newspipe.dedup import run_dedup
from newspipe.fetchers.base import RawItem

# Unique content so test arrivals never collide with real pipeline data.


def _insert_and_dedup(db_conn, sid: int, items: list[RawItem]):
    insert_arrivals(db_conn, sid, items)
    db_conn.commit()  # visible to run_dedup's own connection
    return run_dedup()


def _story_count_for(db_conn, external_ids: list[str]) -> int:
    row = db_conn.execute(
        """
        SELECT count(DISTINCT s.story_id) AS n
        FROM stories s
        JOIN arrivals a ON a.story_id = s.story_id
        WHERE a.external_id = ANY(%s)
        """,
        (external_ids,),
    ).fetchone()
    return row["n"]


def _arrival_count_for(db_conn, external_id: str) -> int:
    row = db_conn.execute(
        """
        SELECT s.arrival_count
        FROM stories s
        JOIN arrivals a ON a.story_id = s.story_id
        WHERE a.external_id = %s
        """,
        (external_id,),
    ).fetchone()
    return row["arrival_count"]


def test_same_url_collapses(db_conn, source_scope):
    sid1 = source_scope("zz-test-dedup-a")
    sid2 = source_scope("zz-test-dedup-b")
    _insert_and_dedup(
        db_conn,
        sid1,
        [
            RawItem(
                external_id="zz-e1",
                url="https://example.com/story?utm_source=x",
                title="Same Story",
            )
        ],
    )
    _insert_and_dedup(
        db_conn,
        sid2,
        [RawItem(external_id="zz-e2", url="https://example.com/story", title="Same Story")],
    )
    assert _story_count_for(db_conn, ["zz-e1", "zz-e2"]) == 1
    assert _arrival_count_for(db_conn, "zz-e1") == 2
    unattached = db_conn.execute(
        "SELECT count(*) AS n FROM arrivals WHERE story_id IS NULL AND external_id LIKE 'zz-%'"
    ).fetchone()["n"]
    assert unattached == 0


def test_tracking_param_variants_collapse_in_one_run(db_conn, source_scope):
    sid = source_scope("zz-test-dedup-a")
    stats = _insert_and_dedup(
        db_conn,
        sid,
        [
            RawItem(
                external_id="zz-t1", url="https://example.com/foo?utm_source=tw", title="Foo Story"
            ),
            RawItem(
                external_id="zz-t2",
                url="https://example.com/foo?utm_source=fb&fbclid=abc",
                title="Foo Story",
            ),
        ],
    )
    assert stats.stories_created == 1
    assert stats.arrivals_attached == 2
    row = db_conn.execute(
        "SELECT canonical_url FROM stories WHERE canonical_url = 'https://example.com/foo'"
    ).fetchone()
    assert row is not None


def test_same_title_different_url_collapses(db_conn, source_scope):
    sid1 = source_scope("zz-test-dedup-a")
    sid2 = source_scope("zz-test-dedup-b")
    _insert_and_dedup(
        db_conn,
        sid1,
        [
            RawItem(
                external_id="zz-u1",
                url="https://site-a.example/article/1",
                title="OpenAI Releases New Model",
            )
        ],
    )
    _insert_and_dedup(
        db_conn,
        sid2,
        [
            RawItem(
                external_id="zz-u2",
                url="https://site-b.example/news/2026/model",
                title="OpenAI Releases New Model",
            )
        ],
    )
    assert _story_count_for(db_conn, ["zz-u1", "zz-u2"]) == 1
    assert _arrival_count_for(db_conn, "zz-u1") == 2


def test_different_stories_do_not_collapse(db_conn, source_scope):
    sid = source_scope("zz-test-dedup-a")
    stats = _insert_and_dedup(
        db_conn,
        sid,
        [
            RawItem(external_id="zz-d1", url="https://example.com/a", title="Story Alpha"),
            RawItem(external_id="zz-d2", url="https://example.com/b", title="Story Beta"),
        ],
    )
    assert stats.stories_created == 2
    assert _story_count_for(db_conn, ["zz-d1", "zz-d2"]) == 2


def test_title_match_respects_72h_window(db_conn, source_scope):
    sid = source_scope("zz-test-dedup-a")
    old = datetime.now(UTC) - timedelta(hours=100)
    insert_arrivals(
        db_conn,
        sid,
        [RawItem(external_id="zz-w1", url="https://example.com/old", title="Window Test Story")],
    )
    db_conn.commit()
    run_dedup()  # storify zz-w1
    # push the story's first_seen_at outside the 72h window
    row = db_conn.execute(
        """
        UPDATE stories SET first_seen_at = %s
        WHERE canonical_url = 'https://example.com/old'
        RETURNING story_id
        """,
        (old,),
    ).fetchone()
    assert row is not None
    # a new arrival with the same title but a different URL must NOT match
    insert_arrivals(
        db_conn,
        sid,
        [
            RawItem(
                external_id="zz-w2", url="https://example.com/new-url", title="Window Test Story"
            )
        ],
    )
    db_conn.commit()
    stats = run_dedup()
    assert stats.title_matches == 0
    assert stats.stories_created == 1
    assert _story_count_for(db_conn, ["zz-w1", "zz-w2"]) == 2
