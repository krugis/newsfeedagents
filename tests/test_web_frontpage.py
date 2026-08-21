"""Tests for the public 'top news of the day' front page (no login required)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from newspipe.db.arrivals import insert_arrivals
from newspipe.db.labels import insert_label
from newspipe.db.pipeline_runs import finalize_pipeline_run, insert_pipeline_run
from newspipe.db.stories import select_most_recent_labeled_day, select_top_stories
from newspipe.dedup import run_dedup
from newspipe.fetchers.base import RawItem
from newspipe.web.app import create_app


def _backdate_story(db_conn, story_id, when):
    db_conn.execute(
        "UPDATE stories SET first_seen_at = %s, last_seen_at = %s WHERE story_id = %s",
        (when, when, story_id),
    )
    db_conn.commit()


def _make_labeled_story(db_conn, source_scope, name, external_id, title, **label_kwargs):
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
    story_id = row["story_id"]
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


def test_frontpage_accessible_without_login(client):
    resp = client.get("/")
    assert resp.status_code == 200


def test_frontpage_shows_todays_top_story(db_conn, source_scope, client):
    _make_labeled_story(
        db_conn,
        source_scope,
        "zz-front-a",
        "zz-front-1",
        "Zz Front Lead Story",
        is_hot=True,
        importance=9,
    )

    resp = client.get("/")

    assert resp.status_code == 200
    assert b"Zz Front Lead Story" in resp.data
    assert b"HOT" in resp.data


def test_frontpage_orders_by_hot_then_importance(db_conn, source_scope, client):
    _make_labeled_story(
        db_conn,
        source_scope,
        "zz-front-low",
        "zz-front-low-1",
        "Zz Front Low Importance Story",
        importance=2,
    )
    _make_labeled_story(
        db_conn,
        source_scope,
        "zz-front-high",
        "zz-front-high-1",
        "Zz Front High Importance Story",
        is_hot=True,
        importance=9,
    )

    resp = client.get("/")
    body = resp.data.decode()

    assert body.index("Zz Front High Importance Story") < body.index(
        "Zz Front Low Importance Story"
    )


def test_frontpage_excludes_non_genai_relevant(db_conn, source_scope, client):
    _make_labeled_story(
        db_conn,
        source_scope,
        "zz-front-irrelevant",
        "zz-front-irr-1",
        "Zz Front Irrelevant Story",
        is_genai_ml_relevant=False,
    )

    resp = client.get("/")

    assert b"Zz Front Irrelevant Story" not in resp.data


def test_select_top_stories_respects_day_bounds(db_conn, source_scope):
    story_id = _make_labeled_story(
        db_conn, source_scope, "zz-front-bounds", "zz-front-b-1", "Zz Front Bounds Story"
    )
    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    yesterday_start = day_start - timedelta(days=1)

    rows_today = select_top_stories(db_conn, day_start, day_end, limit=20)
    rows_yesterday = select_top_stories(db_conn, yesterday_start, day_start, limit=20)

    assert any(r["story_id"] == story_id for r in rows_today)
    assert not any(r["story_id"] == story_id for r in rows_yesterday)


def test_select_most_recent_labeled_day_finds_todays_story(db_conn, source_scope):
    _make_labeled_story(
        db_conn, source_scope, "zz-front-recent", "zz-front-r-1", "Zz Front Recent Story"
    )
    day = select_most_recent_labeled_day(db_conn)
    assert day == datetime.now(UTC).date()


def test_frontpage_day_picker_shows_last_7_days_newest_first(client):
    resp = client.get("/")
    body = resp.data.decode()
    today = datetime.now(UTC).date()
    expected_days = [(today - timedelta(days=n)).isoformat() for n in range(7)]

    positions = [body.index(f"day={d}") for d in expected_days]

    assert positions == sorted(positions)


def test_frontpage_day_param_selects_that_day(db_conn, source_scope, client):
    target_day = datetime.now(UTC).date() - timedelta(days=3)
    story_id = _make_labeled_story(
        db_conn,
        source_scope,
        "zz-front-day",
        "zz-front-day-1",
        "Zz Front Specific Day Story",
        is_hot=True,
        importance=7,
    )
    _backdate_story(
        db_conn,
        story_id,
        datetime(target_day.year, target_day.month, target_day.day, 12, tzinfo=UTC),
    )

    resp = client.get(f"/?day={target_day.isoformat()}")

    assert resp.status_code == 200
    assert b"Zz Front Specific Day Story" in resp.data
    assert target_day.strftime("%A, %B").encode() in resp.data


def test_frontpage_day_outside_window_falls_back_to_default(client):
    too_old_day = datetime.now(UTC).date() - timedelta(days=30)
    too_old_formatted = too_old_day.strftime("%A, %B %-d, %Y")

    resp = client.get(f"/?day={too_old_day.isoformat()}")

    assert resp.status_code == 200
    assert too_old_formatted.encode() not in resp.data


def test_frontpage_invalid_day_format_is_ignored(client):
    resp = client.get("/?day=not-a-date")
    assert resp.status_code == 200


def test_frontpage_shows_last_updated(db_conn, source_scope, client):
    source_scope("zz-front-updated")
    run_id = insert_pipeline_run(db_conn, "zz-front-updated-run", datetime.now(UTC))
    finalize_pipeline_run(db_conn, run_id, status="success", stats={})
    db_conn.commit()

    resp = client.get("/")

    assert b"Data updated" in resp.data
