"""Tests for /topic — public keyword search across all stories (no login)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from newspipe.config import Settings
from newspipe.db.arrivals import insert_arrivals
from newspipe.db.labels import insert_label
from newspipe.db.stories import select_stories_by_topic
from newspipe.dedup import run_dedup
from newspipe.fetchers.base import RawItem
from newspipe.web.app import create_app


def _backdate_story(db_conn, story_id, when):
    db_conn.execute(
        "UPDATE stories SET first_seen_at = %s, last_seen_at = %s WHERE story_id = %s",
        (when, when, story_id),
    )
    db_conn.commit()


def _make_story(db_conn, source_scope, name, external_id, title, label_kwargs=None):
    sid = source_scope(name)
    insert_arrivals(
        db_conn,
        sid,
        [RawItem(external_id=external_id, url=f"https://example.com/{external_id}", title=title)],
    )
    db_conn.commit()
    run_dedup()
    row = db_conn.execute(
        "SELECT s.story_id FROM stories s JOIN arrivals a ON a.story_id = s.story_id "
        "WHERE a.external_id = %s",
        (external_id,),
    ).fetchone()
    story_id = row["story_id"]
    if label_kwargs is not None:
        base = {
            "is_hot": False,
            "importance": 5,
            "category": "other",
            "is_genai_ml_relevant": True,
            "rationale": "test rationale",
            "model": "test",
            "prompt_version": "p1",
        }
        base.update(label_kwargs)
        insert_label(db_conn, story_id, **base)
        db_conn.commit()
    return story_id


@pytest.fixture
def client():
    app = create_app()
    app.testing = True
    return app.test_client()


def test_topic_page_accessible_without_login(client):
    resp = client.get("/topic")
    assert resp.status_code == 200


def test_topic_page_blank_without_query(client):
    resp = client.get("/topic")
    assert b"story-list" not in resp.data


def test_topic_finds_labeled_match(db_conn, source_scope, client):
    _make_story(
        db_conn,
        source_scope,
        "zz-topic-a",
        "zz-topic-1",
        "Zz Gemini Ships New Model",
        label_kwargs={"is_hot": True, "importance": 8},
    )

    resp = client.get("/topic?q=gemini")

    assert resp.status_code == 200
    assert b"Zz Gemini Ships New Model" in resp.data
    assert b"HOT" in resp.data


def test_topic_finds_unlabeled_match_and_badges_it(db_conn, source_scope, client):
    _make_story(db_conn, source_scope, "zz-topic-b", "zz-topic-2", "Zz Gemini Unlabeled Story")

    resp = client.get("/topic?q=gemini")

    assert resp.status_code == 200
    assert b"Zz Gemini Unlabeled Story" in resp.data
    assert b"Unlabeled" in resp.data


def test_topic_search_is_case_insensitive(db_conn, source_scope, client):
    _make_story(db_conn, source_scope, "zz-topic-c", "zz-topic-3", "Zz GEMINI All Caps Story")

    resp = client.get("/topic?q=gemini")

    assert b"Zz GEMINI All Caps Story" in resp.data


def test_topic_excludes_non_matching_title(db_conn, source_scope, client):
    _make_story(db_conn, source_scope, "zz-topic-d", "zz-topic-4", "Zz Totally Unrelated Story")

    resp = client.get("/topic?q=gemini")

    assert b"Zz Totally Unrelated Story" not in resp.data


def test_select_stories_by_topic_respects_day_bounds(db_conn, source_scope):
    story_id = _make_story(
        db_conn, source_scope, "zz-topic-bounds", "zz-topic-b-1", "Zz Topic Bounds Gemini Story"
    )
    now = datetime.now(UTC)

    rows_recent = select_stories_by_topic(db_conn, "gemini", now - timedelta(days=3), now, limit=20)
    rows_old_window = select_stories_by_topic(
        db_conn, "gemini", now - timedelta(days=10), now - timedelta(days=8), limit=20
    )

    assert any(r["story_id"] == story_id for r in rows_recent)
    assert not any(r["story_id"] == story_id for r in rows_old_window)


def test_select_stories_by_topic_default_window_excludes_older_than_3_days(db_conn, source_scope):
    story_id = _make_story(
        db_conn, source_scope, "zz-topic-old", "zz-topic-old-1", "Zz Old Gemini Story"
    )
    _backdate_story(db_conn, story_id, datetime.now(UTC) - timedelta(days=5))

    resp_default = select_stories_by_topic(
        db_conn, "gemini", datetime.now(UTC) - timedelta(days=3), datetime.now(UTC), limit=20
    )
    resp_wide = select_stories_by_topic(
        db_conn, "gemini", datetime.now(UTC) - timedelta(days=7), datetime.now(UTC), limit=20
    )

    assert not any(r["story_id"] == story_id for r in resp_default)
    assert any(r["story_id"] == story_id for r in resp_wide)


def test_topic_days_param_widens_window(db_conn, source_scope, client):
    story_id = _make_story(
        db_conn, source_scope, "zz-topic-days", "zz-topic-days-1", "Zz Days Gemini Story"
    )
    _backdate_story(db_conn, story_id, datetime.now(UTC) - timedelta(days=5))

    resp_default = client.get("/topic?q=gemini")
    resp_7 = client.get("/topic?q=gemini&days=7")

    assert b"Zz Days Gemini Story" not in resp_default.data
    assert b"Zz Days Gemini Story" in resp_7.data


def test_topic_days_param_clamped_to_max(db_conn, source_scope, client, monkeypatch):
    monkeypatch.setattr(
        "newspipe.web.topic.get_settings",
        lambda: Settings(topic_search_default_days=3, topic_search_max_days=7),
    )
    story_id = _make_story(
        db_conn, source_scope, "zz-topic-clamp", "zz-topic-clamp-1", "Zz Clamp Gemini Story"
    )
    _backdate_story(db_conn, story_id, datetime.now(UTC) - timedelta(days=6))

    resp = client.get("/topic?q=gemini&days=999")

    assert b"Zz Clamp Gemini Story" in resp.data
