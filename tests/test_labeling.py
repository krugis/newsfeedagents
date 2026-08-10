"""Tests for LLM labeling (Sub-phase 1.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from newspipe.config import Settings, get_settings
from newspipe.db.arrivals import insert_arrivals
from newspipe.db.labels import insert_label, select_unlabeled_stories
from newspipe.dedup import run_dedup
from newspipe.fetchers.base import RawItem
from newspipe.labeling.labeler import PROMPT_VERSION, build_prompt, label_unlabeled
from newspipe.labeling.schema import HeadlineLabel


@pytest.fixture
def fake_api_key(monkeypatch):
    """Let labeler code pass the API-key guard without hitting the network."""
    patched = Settings(anthropic_api_key="test-key")
    monkeypatch.setattr("newspipe.labeling.labeler.get_settings", lambda: patched)


def _make_story(db_conn, source_scope, name: str, items: list[RawItem]) -> list[int]:
    """Insert arrivals through a scoped source and dedup them into stories."""
    sid = source_scope(name)
    insert_arrivals(db_conn, sid, items)
    db_conn.commit()
    run_dedup()
    rows = db_conn.execute(
        """
        SELECT a.external_id, s.story_id
          FROM arrivals a JOIN stories s ON s.story_id = a.story_id
         WHERE a.external_id = ANY(%s)
        """,
        ([it.external_id for it in items],),
    ).fetchall()
    assert len(rows) == len(items)
    return [row["story_id"] for row in rows]


def _label(**overrides) -> HeadlineLabel:
    base = {
        "is_hot": True,
        "importance": 7,
        "category": "model_release",
        "is_genai_ml_relevant": True,
        "rationale": "A major event.",
    }
    base.update(overrides)
    return HeadlineLabel(**base)


# ---- schema -----------------------------------------------------------


def test_headline_label_valid():
    label = HeadlineLabel(
        is_hot=True,
        importance=8,
        category="model_release",
        is_genai_ml_relevant=True,
        rationale="A major release.",
    )
    assert label.importance == 8
    assert label.category == "model_release"


@pytest.mark.parametrize("importance", [0, 11])
def test_headline_label_importance_out_of_range(importance):
    with pytest.raises(ValidationError):
        HeadlineLabel(
            is_hot=True,
            importance=importance,
            category="other",
            is_genai_ml_relevant=True,
            rationale="x",
        )


def test_headline_label_rejects_unknown_category():
    with pytest.raises(ValidationError):
        HeadlineLabel(
            is_hot=True,
            importance=5,
            category="not_a_real_category",
            is_genai_ml_relevant=True,
            rationale="x",
        )


def test_headline_label_requires_all_fields():
    with pytest.raises(ValidationError):
        HeadlineLabel()


# ---- prompt -----------------------------------------------------------


def test_build_prompt_includes_cross_source_signal():
    prompt = build_prompt(
        {
            "title": "OpenAI releases GPT-6",
            "sources": ["TechCrunch", "The Verge"],
            "arrival_count": 2,
            "hn_front_page": True,
        }
    )
    assert "OpenAI releases GPT-6" in prompt
    assert "TechCrunch, The Verge" in prompt
    # template wraps the phrase across lines — normalize whitespace before asserting
    assert "cross-source arrival is an explicit importance signal" in " ".join(
        prompt.lower().split()
    )
    assert "HN_FRONT_PAGE: true" in prompt


# ---- persistence layer -------------------------------------------------


def test_insert_label_roundtrip(db_conn, source_scope, fake_api_key):
    (story_id,) = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-insert",
        [RawItem(external_id="zz-insert-1", url="https://example.com/i", title="Insert Me")],
    )
    label_id = insert_label(
        db_conn,
        story_id,
        is_hot=False,
        importance=4,
        category="industry",
        is_genai_ml_relevant=True,
        rationale="meh",
        model="m",
        prompt_version="p1",
    )
    row = db_conn.execute("SELECT * FROM labels WHERE label_id = %s", (label_id,)).fetchone()
    assert row is not None
    assert row["story_id"] == story_id
    assert row["is_hot"] is False
    assert row["importance"] == 4
    assert row["category"] == "industry"
    assert row["model"] == "m"
    assert row["prompt_version"] == "p1"


def test_select_unlabeled_stories_joins_source_names(db_conn, source_scope):
    sid_a = source_scope("zz-test-label-srca")
    sid_b = source_scope("zz-test-label-srcb")
    insert_arrivals(
        db_conn,
        sid_a,
        [RawItem(external_id="zz-s1", url="https://example.com/x", title="Shared Title One")],
    )
    insert_arrivals(
        db_conn,
        sid_b,
        [RawItem(external_id="zz-s2", url="https://example.com/y", title="Shared Title One")],
    )
    db_conn.commit()
    run_dedup()

    # target the test story explicitly — the dev DB carries thousands of real
    # unlabeled stories, so a plain `limit` would return those, not ours
    row = db_conn.execute(
        """
        SELECT s.* FROM stories s
        JOIN arrivals a ON a.story_id = s.story_id
        WHERE a.external_id = 'zz-s1'
        """
    ).fetchone()
    assert row is not None
    rows = select_unlabeled_stories(db_conn, story_ids=[row["story_id"]])
    assert len(rows) == 1
    match = rows[0]
    assert set(match["sources"]) == {"zz-test-label-srca", "zz-test-label-srcb"}
    assert match["arrival_count"] == 2


def test_select_unlabeled_stories_excludes_labeled(db_conn, source_scope, fake_api_key):
    (story_id,) = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-exclude",
        [RawItem(external_id="zz-ex-1", url="https://example.com/e", title="Already Labeled")],
    )
    insert_label(
        db_conn,
        story_id,
        is_hot=True,
        importance=5,
        category="other",
        is_genai_ml_relevant=True,
        rationale="done",
        model="m",
        prompt_version="p1",
    )
    db_conn.commit()
    rows = select_unlabeled_stories(db_conn, story_ids=[story_id])
    assert rows == []


# ---- batch labeling (model mocked) --------------------------------------


def test_label_unlabeled_persists_labels(db_conn, source_scope, fake_api_key, monkeypatch):
    (story_id,) = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-a",
        [RawItem(external_id="zz-l1", url="https://example.com/l1", title="Label Test Story One")],
    )

    async def fake_batch(stories):  # noqa: ARG001
        return [_label()]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch)
    stats = label_unlabeled(story_ids=[story_id])

    assert stats.stories_attempted == 1
    assert stats.labels_created == 1
    assert stats.failed == 0
    assert stats.stories == [
        {
            "title": "Label Test Story One",
            "is_hot": True,
            "importance": 7,
            "category": "model_release",
        }
    ]

    row = db_conn.execute(
        """
        SELECT l.* FROM labels l
        JOIN stories s ON s.story_id = l.story_id
        WHERE s.title = 'Label Test Story One'
        """
    ).fetchone()
    assert row is not None
    assert row["importance"] == 7
    assert row["category"] == "model_release"
    assert row["prompt_version"] == PROMPT_VERSION
    assert row["model"] == get_settings().model_name


def test_label_failure_leaves_story_unlabeled(db_conn, source_scope, fake_api_key, monkeypatch):
    story_ids = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-fail",
        [
            RawItem(external_id="zz-f1", url="https://example.com/f1", title="Good Story"),
            RawItem(external_id="zz-f2", url="https://example.com/f2", title="Bad Story"),
        ],
    )

    async def fake_batch(stories):
        return [_label(), RuntimeError("boom")]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch)
    stats = label_unlabeled(story_ids=story_ids)

    assert stats.labels_created == 1
    assert stats.failed == 1

    # the failed story stays unlabeled and is retried on the next run
    async def fake_batch_ok(stories):
        return [_label(importance=3, category="research") for _ in stories]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch_ok)
    stats2 = label_unlabeled(story_ids=story_ids)
    assert stats2.stories_attempted == 1
    assert stats2.labels_created == 1
    assert stats2.failed == 0


def test_label_unlabeled_story_ids_filter(db_conn, source_scope, fake_api_key, monkeypatch):
    story_ids = _make_story(
        db_conn,
        source_scope,
        "zz-test-label-ids",
        [
            RawItem(external_id="zz-i1", url="https://example.com/i1", title="Id Story One"),
            RawItem(external_id="zz-i2", url="https://example.com/i2", title="Id Story Two"),
            RawItem(external_id="zz-i3", url="https://example.com/i3", title="Id Story Three"),
        ],
    )

    async def fake_batch(stories):
        return [_label() for _ in stories]

    monkeypatch.setattr("newspipe.labeling.labeler._batch_label", fake_batch)
    stats = label_unlabeled(story_ids=story_ids[:2])

    assert stats.stories_attempted == 2
    assert stats.labels_created == 2
    assert stats.failed == 0

    unlabeled = select_unlabeled_stories(db_conn, story_ids=[story_ids[2]])
    assert [r["story_id"] for r in unlabeled] == [story_ids[2]]


def test_label_unlabeled_requires_api_key(monkeypatch):
    patched = Settings(anthropic_api_key=None)
    monkeypatch.setattr("newspipe.labeling.labeler.get_settings", lambda: patched)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        label_unlabeled()


# ---- live (real model, requires ANTHROPIC_API_KEY) -----------------------


@pytest.mark.live
def test_label_three_stories_live(db_conn, source_scope):
    if not get_settings().anthropic_api_key:
        pytest.skip("ANTHROPIC_API_KEY not set — set it in .env to run live labeling")
    story_ids = _make_story(
        db_conn,
        source_scope,
        "zz-live-label",
        [
            RawItem(
                external_id="zz-live-1",
                url="https://example.com/live/1",
                title="OpenAI releases a new frontier model",
            ),
            RawItem(
                external_id="zz-live-2",
                url="https://example.com/live/2",
                title="EU publishes draft AI regulation amendments",
            ),
            RawItem(
                external_id="zz-live-3",
                url="https://example.com/live/3",
                title="New research improves diffusion model sampling speed",
            ),
        ],
    )
    stats = label_unlabeled(story_ids=story_ids)
    assert stats.stories_attempted == 3
    assert stats.labels_created == 3
    assert stats.failed == 0
    for row in stats.stories:
        assert row["category"] in {
            "model_release",
            "research",
            "industry",
            "funding",
            "policy_regulation",
            "tooling_infra",
            "other",
        }
